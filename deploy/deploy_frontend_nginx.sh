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

# 這幾行狀態訊息刻意都印到 stderr（不是 stdout）：`TF_BOOTSTRAP_DRY_RUN=1`
# 逃生口是用 `$(...)` 擷取這支腳本的 stdout 當成組好的遠端指令內容（見下面
# CMDS 那段），如果進度訊息混進 stdout 會污染擷取結果（比照 deploy/cutover_switch.sh
# 同一個設計）。
echo "[fe-nginx] region=$REGION account=$ACCT bucket=$BUCKET" >&2

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
  echo "[fe-nginx] 既有實例 $IID 已停機 → 先開機" >&2
  aws ec2 start-instances --region "$REGION" --instance-ids "$IID" >/dev/null
  aws ec2 wait instance-running --region "$REGION" --instance-ids "$IID"
fi
echo "[fe-nginx] 目標實例 $IID" >&2

# 2) 本機 build 前端（Vite 純靜態輸出，$0 runtime；不佔 EC2 CPU）----------
echo "[fe-nginx] build 前端（npm ci && npm run build）…" >&2
( cd frontend && npm ci && npm run build )
if [ ! -d frontend/dist ]; then
  echo "[fe-nginx] ❌ frontend/dist 不存在，build 失敗" >&2
  exit 1
fi

# 3) 打包上傳：前端 dist + 四份 nginx conf（直接用 repo 裡實際被
#    `nginx -t` 驗證過的檔案，避免跟 SSM 內嵌字串產生 drift）-------------
#    legacy=SSR 全轉發（HTTP-only）、react=React+TLS（有 domain 才能用）、
#    react-http=React HTTP-only（bare-IP 現況用，見 deploy/nginx-react-http.conf）、
#    legacy-tls=SSR 全轉發（443 HTTPS，保留 HSTS）——codex 複審 HIGH：react→
#    legacy 緊急回滾時若憑證已存在，cutover_switch.sh 會改選這份，避免
#    HSTS 破壞 HTTP-only 回滾（見 deploy/nginx-legacy-tls.conf 檔頭註解）。
DIST_ZIP="$(pwd)/build/trustforge_frontend_dist.zip"
mkdir -p build
( cd frontend/dist && zip -qr "$DIST_ZIP" . )
aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" 2>/dev/null || \
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
aws s3 cp "$DIST_ZIP" "s3://$BUCKET/trustforge_frontend_dist.zip" --region "$REGION" >/dev/null
aws s3 cp deploy/nginx-legacy.conf "s3://$BUCKET/nginx-legacy.conf" --region "$REGION" >/dev/null
aws s3 cp deploy/nginx.conf "s3://$BUCKET/nginx-react.conf" --region "$REGION" >/dev/null
aws s3 cp deploy/nginx-react-http.conf "s3://$BUCKET/nginx-react-http.conf" --region "$REGION" >/dev/null
aws s3 cp deploy/nginx-legacy-tls.conf "s3://$BUCKET/nginx-legacy-tls.conf" --region "$REGION" >/dev/null
echo "[fe-nginx] 已上傳前端 dist + 四份 nginx conf 到 s3://$BUCKET/" >&2

# 4) Security group：加開 443（80 應該已由 deploy_ec2.sh 開好）-----------
VPC=$(aws ec2 describe-vpcs --region "$REGION" --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
SGID=$(aws ec2 describe-security-groups --region "$REGION" --filters Name=group-name,Values=$SG Name=vpc-id,Values=$VPC --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo None)
if [ "$SGID" = "None" ] || [ -z "$SGID" ]; then
  echo "[fe-nginx] ❌ 找不到 security group ${SG}，deploy_ec2.sh 應該已經建過，中止" >&2
  exit 1
fi
if ! aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SGID" \
  --protocol tcp --port 443 --cidr 0.0.0.0/0 >/dev/null 2>&1; then
  echo "[fe-nginx] 443 規則已存在（authorize 回錯通常代表 duplicate，忽略）" >&2
fi
echo "[fe-nginx] SG=$SGID 已開 443（TLS 就緒，見 deploy/TLS-SETUP.md）" >&2

# 5) SSM：裝 nginx、佈署 conf（預設啟用 legacy）、前端 dist、收斂 python 綁定
#    ----------------------------------------------------------------------
# 設計（guarded transaction，比照 deploy/cutover_switch.sh，codex 五次複審
# HIGH：以前的順序是「改 live symlink+systemd service → restart python 綁
# 127.0.0.1 → 之後才驗證/啟用 nginx」，任一步失敗都沒有回滾——python 已經
# 只聽 localhost（外部連不到）、nginx 又還沒真的起來，就是真 outage，而且
# 不會自己好）：
#   1. 先把 nginx 套件裝好、React dist/兩份候選 conf 佈署到 staging 位置
#      （/etc/nginx/trustforge-sites/、/opt/trustforge/frontend/dist）——
#      這段完全不動目前在跑的 python/nginx，失敗也不需要回滾。
#   2. 候選設定驗證：用 scratch nginx harness 對 legacy.conf 跑 `nginx -t`，
#      完全不碰 live 的 conf.d/trustforge.conf。驗證失敗直接中止，一律
#      exit 1（還沒開始 mutation，不需要也不會觸發回滾）。
#   3. 記錄跑前狀態（trustforge.service 備份、live symlink 是否存在＋
#      指向誰、default.conf 是否存在＋內容、nginx/trustforge 目前的
#      active/enabled 狀態）——之後才開始動 live 狀態，掛 ERR trap。
#   4. **收斂 python 綁定（narrow exposure）+ 起 nginx**：因為 nginx（80）
#      跟目前 python（也是 80）搶同一個 port，物理上不可能讓 nginx 先
#      綁好 80 才收窄 python——這段唯一可行、且已經做到的順序保證是：
#      candidate config 100% 通過 `nginx -t` 驗證＋跑前狀態已完整快照，
#      都在**這段唯一會動 live 狀態的區段之前**完成；這段本身（narrow
#      python bind → 起 nginx → post-switch 驗證）整段掛在同一個 ERR
#      trap 下，任一步（含 public path healthz 驗證失敗）都會觸發下面
#      的 ROLLBACK，把 trustforge.service／live symlink／default.conf／
#      nginx 服務狀態全部還原成跑前的樣子，不會停在「python 已收窄、
#      nginx 沒起來」的半殘 outage 狀態。
#   5. ROLLBACK 比照 cutover_switch.sh：每一步都不用 `|| true` 吞掉失敗，
#      全部跑完才誠實印「已回滾」；回滾本身也失敗 → 印 distinct 的
#      `ROLLBACK-FAILED`（exit 97，跟一般失敗 exit 1 不同）+ 具體手動
#      復原指示，不謊報成功。
#
# TF_BOOTSTRAP_DRY_RUN=1：測試用逃生口，只印出組好的遠端指令內容、不真的
# 呼叫 `aws ssm send-command`。給 deploy/test_deploy_frontend_nginx_transaction.sh
# 擷取這段內容後在本機沙箱（假 /etc + mock nginx/systemctl/dnf/unzip/curl）
# 實際執行，驗證 guarded transaction／失敗回滾的控制流程真的正確。生產
# 環境不會設這個變數，行為不受影響。
CMDS=$(cat <<'CMDEOF'
set -e
ETC="${TF_BOOTSTRAP_ETC:-/etc}"
OPT_DIR="${TF_BOOTSTRAP_OPT:-/opt/trustforge}"
LIVE_LINK="$ETC/nginx/conf.d/trustforge.conf"
DEFAULT_CONF="$ETC/nginx/conf.d/default.conf"
CANDIDATE="$ETC/nginx/trustforge-sites/legacy.conf"
SERVICE_FILE="$ETC/systemd/system/trustforge.service"
BACKUP_SERVICE_FILE="/tmp/tf-bootstrap-service.bak.$$"
BACKUP_DEFAULT_CONF="/tmp/tf-bootstrap-default-conf.bak.$$"
DNF_LOG="${TF_BOOTSTRAP_DNF_LOG:-/var/log/tf-nginx-setup.log}"

# ---- Step 1：安裝 nginx + 佈署靜態檔/candidate conf 到 staging 位置
#      （全部非破壞性，不動目前在跑的 python/nginx，失敗不用回滾）----
dnf install -y nginx unzip >"$DNF_LOG" 2>&1
mkdir -p "$ETC/nginx/trustforge-sites" "$OPT_DIR/frontend"
aws s3 cp s3://__BUCKET__/nginx-legacy.conf "$CANDIDATE" --region __REGION__
aws s3 cp s3://__BUCKET__/nginx-react.conf "$ETC/nginx/trustforge-sites/react.conf" --region __REGION__
aws s3 cp s3://__BUCKET__/nginx-react-http.conf "$ETC/nginx/trustforge-sites/react-http.conf" --region __REGION__
aws s3 cp s3://__BUCKET__/nginx-legacy-tls.conf "$ETC/nginx/trustforge-sites/legacy-tls.conf" --region __REGION__
aws s3 cp s3://__BUCKET__/trustforge_frontend_dist.zip "$OPT_DIR/frontend/dist.zip" --region __REGION__
rm -rf "$OPT_DIR/frontend/dist" && mkdir -p "$OPT_DIR/frontend/dist"
unzip -o -q "$OPT_DIR/frontend/dist.zip" -d "$OPT_DIR/frontend/dist"
echo '[fe-nginx] 已裝 nginx + 佈署靜態檔/candidate conf 到 staging 位置（未動 live conf.d/systemd）'

# ---- Step 2：候選設定驗證（scratch harness），完全不動 live conf.d ----
VALIDATE_CONF="/tmp/tf-bootstrap-validate-$$.conf"
cat > "$VALIDATE_CONF" <<VALIDATE_EOF
worker_processes 1;
error_log /tmp/tf-bootstrap-validate-$$.err.log;
pid /tmp/tf-bootstrap-validate-$$.pid;
events { worker_connections 16; }
http { include $CANDIDATE; }
VALIDATE_EOF
if ! nginx -t -c "$VALIDATE_CONF" 2>/tmp/tf-bootstrap-validate-$$.stderr; then
  echo '❌ [fe-nginx] 候選 nginx 設定驗證失敗（legacy.conf），完全沒動 live conf.d/systemd，中止' >&2
  cat /tmp/tf-bootstrap-validate-$$.stderr >&2 2>/dev/null || true
  rm -f "$VALIDATE_CONF" "/tmp/tf-bootstrap-validate-$$.err.log" "/tmp/tf-bootstrap-validate-$$.pid" "/tmp/tf-bootstrap-validate-$$.stderr"
  exit 1
fi
rm -f "$VALIDATE_CONF" "/tmp/tf-bootstrap-validate-$$.err.log" "/tmp/tf-bootstrap-validate-$$.pid" "/tmp/tf-bootstrap-validate-$$.stderr"
echo '[fe-nginx] 候選設定驗證通過（未動 live conf.d/systemd）'

# ---- Step 3：記錄跑前狀態，掛失敗回滾 ----
PREV_DEFAULT_CONF_EXISTED=0
if [ -f "$DEFAULT_CONF" ]; then
  PREV_DEFAULT_CONF_EXISTED=1
  cp "$DEFAULT_CONF" "$BACKUP_DEFAULT_CONF"
fi
if [ -L "$LIVE_LINK" ] && PREV_LINK="$(readlink "$LIVE_LINK" 2>/dev/null)" && [ -n "$PREV_LINK" ]; then
  PREV_LINK_EXISTED=1
else
  PREV_LINK_EXISTED=0
  PREV_LINK=""
fi
cp "$SERVICE_FILE" "$BACKUP_SERVICE_FILE"
PREV_PORT="$(grep '^Environment=PORT=' "$SERVICE_FILE" | head -1 | cut -d= -f3)"
PREV_PORT="${PREV_PORT:-80}"
PREV_TRUSTFORGE_ACTIVE="$(systemctl is-active trustforge 2>/dev/null || echo inactive)"
PREV_NGINX_ACTIVE="$(systemctl is-active nginx 2>/dev/null || echo inactive)"
PREV_NGINX_ENABLED="$(systemctl is-enabled nginx 2>/dev/null || echo disabled)"

ROLLBACK() {
  local ec=$?
  trap - ERR
  echo "❌ [fe-nginx] bootstrap 中失敗（exit=${ec}），回滾到跑前狀態…" >&2
  local ROLLBACK_OK=1

  if ! cp "$BACKUP_SERVICE_FILE" "$SERVICE_FILE"; then
    echo "❌ [fe-nginx] rollback：trustforge.service 還原失敗" >&2
    ROLLBACK_OK=0
  fi
  if ! systemctl daemon-reload; then
    echo "❌ [fe-nginx] rollback：daemon-reload 失敗" >&2
    ROLLBACK_OK=0
  fi
  if [ "$PREV_TRUSTFORGE_ACTIVE" = "active" ]; then
    if ! systemctl restart trustforge; then
      echo "❌ [fe-nginx] rollback：trustforge 重啟失敗（還原跑前 bind/port）" >&2
      ROLLBACK_OK=0
    fi
  else
    systemctl stop trustforge 2>/dev/null || true
  fi

  if [ "$PREV_LINK_EXISTED" = 1 ]; then
    if ! ln -sfn "$PREV_LINK" "$LIVE_LINK"; then
      echo "❌ [fe-nginx] rollback：live symlink 還原失敗（ln -sfn ${PREV_LINK} -> ${LIVE_LINK}）" >&2
      ROLLBACK_OK=0
    fi
  else
    # 跑前本來就沒有（或讀不到）live symlink，正確的還原目標是「移除」
    # 這次新建的 symlink，不是留著不管（那樣才是誤還原）
    if ! rm -f "$LIVE_LINK"; then
      echo "❌ [fe-nginx] rollback：live symlink 移除失敗（跑前無 symlink，理應還原成無：${LIVE_LINK}）" >&2
      ROLLBACK_OK=0
    fi
  fi

  if [ "$PREV_DEFAULT_CONF_EXISTED" = 1 ]; then
    if ! cp "$BACKUP_DEFAULT_CONF" "$DEFAULT_CONF"; then
      echo "❌ [fe-nginx] rollback：default.conf 還原失敗" >&2
      ROLLBACK_OK=0
    fi
  else
    rm -f "$DEFAULT_CONF" || true
  fi

  if [ "$PREV_NGINX_ACTIVE" = "active" ]; then
    if ! { nginx -t && systemctl reload nginx; }; then
      echo "❌ [fe-nginx] rollback：nginx reload 失敗（跑前 nginx 本來在跑）" >&2
      ROLLBACK_OK=0
    fi
  else
    systemctl stop nginx 2>/dev/null || true
    if [ "$PREV_NGINX_ENABLED" != "enabled" ]; then
      systemctl disable nginx 2>/dev/null || true
    fi
  fi

  # 回滾後主動驗證：python 直接對外（跑前的 port，通常是 80）healthz
  # 真的有回應——不是只看每一步指令的 exit code，指令都成功不代表服務
  # 真的健康（比照 cutover_switch.sh 的回滾誠實性設計）
  sleep 1
  if ! curl -fsS "http://localhost:${PREV_PORT}/healthz" >/dev/null 2>&1; then
    echo "❌ [fe-nginx] rollback 驗證失敗：healthz（port ${PREV_PORT}，跑前狀態）沒有回應" >&2
    ROLLBACK_OK=0
  fi

  rm -f "$BACKUP_SERVICE_FILE" "$BACKUP_DEFAULT_CONF"

  if [ "$ROLLBACK_OK" = 1 ]; then
    echo '✅ [fe-nginx] 已回滾到跑前狀態（bootstrap 失敗，但服務已還原成執行前的樣子，不留半殘）' >&2
    exit "$ec"
  fi

  echo '🆘 [fe-nginx] ROLLBACK-FAILED：自動回滾沒有完全成功，請立即人工介入，不要假設服務已還原！' >&2
  echo "   1) 檢查 python service：systemctl status trustforge；grep Environment= ${SERVICE_FILE}" >&2
  echo "      （跑前備份，若還在：${BACKUP_SERVICE_FILE}）" >&2
  echo "   2) 檢查 nginx symlink：ls -l ${LIVE_LINK}（跑前 PREV_LINK_EXISTED=${PREV_LINK_EXISTED} PREV_LINK=${PREV_LINK}）" >&2
  echo "   3) 檢查 healthz：curl -v http://localhost:${PREV_PORT}/healthz" >&2
  exit 97
}
trap 'ROLLBACK' ERR

# ---- Step 4：收斂 python 綁定（narrow exposure）+ 起 nginx——唯一會動
#      live 狀態的區段，全程在上面的 ERR trap 保護下 ----
rm -f "$DEFAULT_CONF"
ln -sfn "$CANDIDATE" "$LIVE_LINK"
# 收斂 python 綁定：只對內監聽，nginx 是唯一對外入口（見 web.py::main() 說明）。
sed -i "s|^Environment=PORT=.*|Environment=PORT=8080|" "$SERVICE_FILE"
grep -q '^Environment=TRUSTFORGE_BIND_HOST=' "$SERVICE_FILE" || \
  sed -i '/^Environment=PORT=/a Environment=TRUSTFORGE_BIND_HOST=127.0.0.1' "$SERVICE_FILE"
grep -q '^Environment=TRUSTFORGE_TRUST_PROXY=' "$SERVICE_FILE" || \
  sed -i '/^Environment=TRUSTFORGE_BIND_HOST=/a Environment=TRUSTFORGE_TRUST_PROXY=1' "$SERVICE_FILE"
grep -q '^Environment=TRUSTFORGE_CSP_MODE=' "$SERVICE_FILE" || \
  sed -i '/^Environment=TRUSTFORGE_TRUST_PROXY=/a Environment=TRUSTFORGE_CSP_MODE=legacy' "$SERVICE_FILE"
systemctl daemon-reload
systemctl restart trustforge
nginx -t
systemctl enable --now nginx
systemctl reload nginx || systemctl restart nginx

# ---- Step 5：post-switch 驗證（bare test/不用 `|| true` 吞掉——public
#      path 驗證失敗要能觸發上面的 ROLLBACK，這正是 codex 五次複審點名的
#      「public-path 驗證失敗、任一 mutation 邊界後失敗都要能回滾」）----
sleep 1
[ "$(systemctl is-active trustforge)" = "active" ]
[ "$(systemctl is-active nginx)" = "active" ]
curl -fsS http://localhost/healthz >/dev/null

trap - ERR
echo '[fe-nginx] nginx+python 拓樸已就緒（legacy 生效，功能與 cutover 前逐字等價）'
CMDEOF
)
CMDS="${CMDS//__BUCKET__/$BUCKET}"
CMDS="${CMDS//__REGION__/$REGION}"

if [ "${TF_BOOTSTRAP_DRY_RUN:-}" = "1" ]; then
  printf '%s' "$CMDS"
  exit 0
fi

CMDID=$(aws ssm send-command --region "$REGION" --instance-ids "$IID" \
  --document-name AWS-RunShellScript \
  --parameters "commands=$(python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().splitlines()))' <<<"$CMDS")" \
  --query 'Command.CommandId' --output text)
if [ -z "$CMDID" ] || [ "$CMDID" = "None" ]; then
  echo "[fe-nginx] ❌ SSM send-command 未取得 CommandId，中止" >&2
  exit 1
fi
STATUS=$(poll_ssm_terminal_status "$CMDID" "$IID" 180 5) || true
# codex 五次複審：跟 cutover_switch.sh 同一個 wrapper 慣例——不能只看
# Status（連 ResponseCode 一起驗），且失敗時遠端腳本會發 distinct exit
# code（97=ROLLBACK-FAILED），讀 ResponseCode 把它原樣傳遞到這層 wrapper
# 的 top-level exit code，別全塌成 1（見 deploy/README.md exit code 慣例）。
RESPONSE_CODE=$(aws ssm get-command-invocation --region "$REGION" --command-id "$CMDID" --instance-id "$IID" --query ResponseCode --output text 2>/dev/null || true)
if [ "$STATUS" = "Success" ] && [ "$RESPONSE_CODE" = "0" ]; then
  echo "[fe-nginx] ✅ nginx 層 + python 內收斂完成、healthz 走 nginx 驗證成功（Status=${STATUS}）"
  echo "[fe-nginx] 目前拓樸：nginx(legacy, http-only) → python(127.0.0.1:8080)"
  echo "[fe-nginx] 下一步（需三審+簽核）："
  echo "[fe-nginx]   bare-IP 現況（無 domain）：deploy/cutover_switch.sh react-http"
  echo "[fe-nginx]   有 domain（trustforge.hurricanesoft.com.tw 已指好）："
  echo "[fe-nginx]     先 deploy/setup_tls.sh 簽 TLS 憑證，再 deploy/cutover_switch.sh react"
  exit 0
fi
echo "[fe-nginx] ❌ nginx+python 拓樸部署失敗：CommandId=$CMDID Status=${STATUS} ResponseCode=${RESPONSE_CODE}" >&2
aws ssm get-command-invocation --region "$REGION" --command-id "$CMDID" --instance-id "$IID" \
  --query 'StandardErrorContent' --output text >&2 2>/dev/null || true
if [ "$RESPONSE_CODE" = "97" ]; then
  exit 97
fi
exit 1
