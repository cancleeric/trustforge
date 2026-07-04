#!/usr/bin/env bash
# TrustForge → 前後端分離 Phase 3（task #28）：在既有 EC2 實例（由
# deploy/deploy_ec2.sh 建立）上「加一層 nginx」，把 python 收斂成只對內
# 監聽（127.0.0.1:8080），並把 React 前端 build 好的靜態檔案上傳備用。
#
# ⛔ 這支腳本**不會**把使用者導向 React 前端——安裝完之後，nginx 預設啟用
# `deploy/nginx-legacy.conf`（bootstrap：純 HTTP，全部原樣轉發給 python），
# 功能上跟「沒有 nginx、python 直接對外聽 :80」逐字等價，只是多一層 nginx
# （TLS 就緒、單一對外入口，見 deploy/TLS-SETUP.md）。
#
# 真正切到 React 前端拓樸（cutover）是**另一個獨立、需要 CEO+CISO+CPO
# 三審+老闆簽核才能執行**的步驟，見 deploy/cutover_switch.sh。這支腳本只
# 負責「把拓樸架好、隨時可以切、但預設不切」。
#
# 前置：deploy/deploy_ec2.sh 已跑過至少一次（有一個 tag Name=trustforge-demo
# 的既有實例）。本腳本用 SSM Session Manager 對它下指令，不需要 SSH key。
#
# 可調環境變數：REGION（預設同 deploy_ec2.sh，ap-southeast-2）
set -euo pipefail
cd "$(dirname "$0")/.."

REGION="${REGION:-ap-southeast-2}"
ACCT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="trustforge-deploy-${ACCT}"
SG=trustforge-ec2-sg

echo "[fe-nginx] region=$REGION account=$ACCT bucket=$BUCKET"

poll_ssm_terminal_status() {
  local cmdid="$1" iid="$2" max_wait="${3:-180}" interval="${4:-5}"
  local waited=0 status
  while :; do
    status=$(aws ssm get-command-invocation --region "$REGION" \
      --command-id "$cmdid" --instance-id "$iid" --query Status --output text 2>/dev/null || echo "")
    case "$status" in
      Success|Failed|Cancelled|TimedOut)
        echo "$status"
        return 0
        ;;
    esac
    if [ "$waited" -ge "$max_wait" ]; then
      echo "${status:-Unknown}"
      return 1
    fi
    sleep "$interval"
    waited=$((waited + interval))
  done
}

# 1) 找既有實例（不建新的；沒有就叫人先跑 deploy_ec2.sh）------------------
if ! MATCHES=$(aws ec2 describe-instances --region "$REGION" \
  --filters Name=tag:Name,Values=trustforge-demo \
    Name=instance-state-name,Values=running,stopped \
  --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output text); then
  echo "[fe-nginx] ❌ 查詢既有實例失敗，中止" >&2
  exit 1
fi
MATCH_COUNT=$(printf '%s\n' "$MATCHES" | grep -c . || true)
if [ "$MATCH_COUNT" -ne 1 ]; then
  echo "[fe-nginx] ❌ 找到 $MATCH_COUNT 個相符實例（tag Name=trustforge-demo，running/stopped），需要剛好 1 個。" >&2
  echo "[fe-nginx]    請先跑 deploy/deploy_ec2.sh 建好基礎 EC2，再跑本腳本疊加 nginx 層。" >&2
  exit 1
fi
IID=$(printf '%s\n' "$MATCHES" | awk '{print $1}')
STATE=$(printf '%s\n' "$MATCHES" | awk '{print $2}')
if [ "$STATE" = "stopped" ]; then
  echo "[fe-nginx] 既有實例 $IID 已停機 → 先開機"
  aws ec2 start-instances --region "$REGION" --instance-ids "$IID" >/dev/null
  aws ec2 wait instance-running --region "$REGION" --instance-ids "$IID"
fi
echo "[fe-nginx] 目標實例 $IID"

# 2) 本機 build 前端（Vite 純靜態輸出，$0 runtime；不佔 EC2 CPU）----------
echo "[fe-nginx] build 前端（npm ci && npm run build）…"
( cd frontend && npm ci && npm run build )
if [ ! -d frontend/dist ]; then
  echo "[fe-nginx] ❌ frontend/dist 不存在，build 失敗" >&2
  exit 1
fi

# 3) 打包上傳：前端 dist + 兩份 nginx conf（直接用 repo 裡實際被
#    `nginx -t` 驗證過的檔案，避免跟 SSM 內嵌字串產生 drift）-------------
DIST_ZIP="$(pwd)/build/trustforge_frontend_dist.zip"
mkdir -p build
( cd frontend/dist && zip -qr "$DIST_ZIP" . )
aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" 2>/dev/null || \
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
aws s3 cp "$DIST_ZIP" "s3://$BUCKET/trustforge_frontend_dist.zip" --region "$REGION" >/dev/null
aws s3 cp deploy/nginx-legacy.conf "s3://$BUCKET/nginx-legacy.conf" --region "$REGION" >/dev/null
aws s3 cp deploy/nginx.conf "s3://$BUCKET/nginx-react.conf" --region "$REGION" >/dev/null
echo "[fe-nginx] 已上傳前端 dist + 兩份 nginx conf 到 s3://$BUCKET/"

# 4) Security group：加開 443（80 應該已由 deploy_ec2.sh 開好）-----------
VPC=$(aws ec2 describe-vpcs --region "$REGION" --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
SGID=$(aws ec2 describe-security-groups --region "$REGION" --filters Name=group-name,Values=$SG Name=vpc-id,Values=$VPC --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo None)
if [ "$SGID" = "None" ] || [ -z "$SGID" ]; then
  echo "[fe-nginx] ❌ 找不到 security group ${SG}，deploy_ec2.sh 應該已經建過，中止" >&2
  exit 1
fi
if ! aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SGID" \
  --protocol tcp --port 443 --cidr 0.0.0.0/0 >/dev/null 2>&1; then
  echo "[fe-nginx] 443 規則已存在（authorize 回錯通常代表 duplicate，忽略）"
fi
echo "[fe-nginx] SG=$SGID 已開 443（TLS 就緒，見 deploy/TLS-SETUP.md）"

# 5) SSM：裝 nginx、佈署 conf（預設啟用 legacy）、前端 dist、收斂 python 綁定
#    ----------------------------------------------------------------------
# 設計：
#   - /etc/nginx/trustforge-sites/{legacy,react}.conf：兩份都放上去，方便
#     `deploy/cutover_switch.sh` 事後用 symlink 秒切、秒回滾，不用重新
#     SSM 部署。
#   - /etc/nginx/conf.d/trustforge.conf → symlink，預設指向 legacy.conf。
#   - 移除 AL2023 nginx 套件內建的 conf.d/default.conf（避免它自己的
#     `listen 80 default_server` 跟我們的 server block 衝突）。
#   - trustforge.service：PORT 改 8080、綁 127.0.0.1、開 TRUST_PROXY、
#     明確寫 CSP_MODE=legacy（雖然是預設值，寫清楚方便 ops 一眼看到現況）。
CMDS=$(cat <<'CMDEOF'
set -e
dnf install -y nginx unzip >/var/log/tf-nginx-setup.log 2>&1
mkdir -p /etc/nginx/trustforge-sites /opt/trustforge/frontend
aws s3 cp s3://__BUCKET__/nginx-legacy.conf /etc/nginx/trustforge-sites/legacy.conf --region __REGION__
aws s3 cp s3://__BUCKET__/nginx-react.conf /etc/nginx/trustforge-sites/react.conf --region __REGION__
aws s3 cp s3://__BUCKET__/trustforge_frontend_dist.zip /opt/trustforge/frontend/dist.zip --region __REGION__
rm -rf /opt/trustforge/frontend/dist && mkdir -p /opt/trustforge/frontend/dist
unzip -o -q /opt/trustforge/frontend/dist.zip -d /opt/trustforge/frontend/dist
rm -f /etc/nginx/conf.d/default.conf
ln -sfn /etc/nginx/trustforge-sites/legacy.conf /etc/nginx/conf.d/trustforge.conf
# 收斂 python 綁定：只對內監聽，nginx 是唯一對外入口（見 web.py::main() 說明）。
sed -i "s|^Environment=PORT=.*|Environment=PORT=8080|" /etc/systemd/system/trustforge.service
grep -q '^Environment=TRUSTFORGE_BIND_HOST=' /etc/systemd/system/trustforge.service || \
  sed -i '/^Environment=PORT=/a Environment=TRUSTFORGE_BIND_HOST=127.0.0.1' /etc/systemd/system/trustforge.service
grep -q '^Environment=TRUSTFORGE_TRUST_PROXY=' /etc/systemd/system/trustforge.service || \
  sed -i '/^Environment=TRUSTFORGE_BIND_HOST=/a Environment=TRUSTFORGE_TRUST_PROXY=1' /etc/systemd/system/trustforge.service
grep -q '^Environment=TRUSTFORGE_CSP_MODE=' /etc/systemd/system/trustforge.service || \
  sed -i '/^Environment=TRUSTFORGE_TRUST_PROXY=/a Environment=TRUSTFORGE_CSP_MODE=legacy' /etc/systemd/system/trustforge.service
systemctl daemon-reload
systemctl restart trustforge
nginx -t
systemctl enable --now nginx
systemctl reload nginx || systemctl restart nginx
echo "[fe-nginx] nginx+python 拓樸已就緒（legacy 生效，功能與 cutover 前逐字等價）"
CMDEOF
)
CMDS="${CMDS//__BUCKET__/$BUCKET}"
CMDS="${CMDS//__REGION__/$REGION}"

CMDID=$(aws ssm send-command --region "$REGION" --instance-ids "$IID" \
  --document-name AWS-RunShellScript \
  --parameters "commands=$(python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().splitlines()))' <<<"$CMDS")" \
  --query 'Command.CommandId' --output text)
if [ -z "$CMDID" ] || [ "$CMDID" = "None" ]; then
  echo "[fe-nginx] ❌ SSM send-command 未取得 CommandId，中止" >&2
  exit 1
fi
STATUS=$(poll_ssm_terminal_status "$CMDID" "$IID" 180 5) || true
if [ "$STATUS" != "Success" ]; then
  echo "[fe-nginx] ❌ nginx+python 拓樸部署失敗：CommandId=$CMDID Status=${STATUS}" >&2
  aws ssm get-command-invocation --region "$REGION" --command-id "$CMDID" --instance-id "$IID" \
    --query 'StandardErrorContent' --output text >&2 2>/dev/null || true
  exit 1
fi
echo "[fe-nginx] ✅ nginx 層 + python 內收斂完成（Status=${STATUS}）"

# 6) 驗證：healthz 走 nginx（port 80 → proxy 給 127.0.0.1:8080）----------
# shellcheck disable=SC2016  # 單引號內的 $(seq..)/$i 刻意留給遠端展開（比照
# deploy_ec2.sh 既有 verify_web_healthz 同一模式）。
HCMDID=$(aws ssm send-command --region "$REGION" --instance-ids "$IID" \
  --document-name AWS-RunShellScript --parameters 'commands=["for i in $(seq 1 12); do systemctl is-active --quiet nginx && systemctl is-active --quiet trustforge && curl -fsS http://localhost/healthz >/dev/null 2>&1 && exit 0; sleep 3; done; echo \"[fe-nginx] healthz 檢查失敗\" >&2; journalctl -u nginx -u trustforge -n 40 --no-pager >&2; exit 1"]' \
  --query 'Command.CommandId' --output text)
HSTATUS=$(poll_ssm_terminal_status "$HCMDID" "$IID" 120 5) || true
if [ "$HSTATUS" != "Success" ]; then
  echo "[fe-nginx] ❌ healthz（走 nginx）驗證失敗：Status=${HSTATUS}" >&2
  aws ssm get-command-invocation --region "$REGION" --command-id "$HCMDID" --instance-id "$IID" \
    --query 'StandardErrorContent' --output text >&2 2>/dev/null || true
  exit 1
fi
echo "[fe-nginx] ✅ healthz 走 nginx 驗證成功"
echo "[fe-nginx] 目前拓樸：nginx(legacy, http-only) → python(127.0.0.1:8080)"
echo "[fe-nginx] 下一步（需三審+簽核）：deploy/TLS-SETUP.md 設 TLS，"
echo "[fe-nginx]   之後才考慮 deploy/cutover_switch.sh react 切到 React 前端。"
