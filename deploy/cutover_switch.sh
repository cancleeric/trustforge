#!/usr/bin/env bash
# TrustForge — Phase 3 cutover 切換器（task #28）。
#
# ⛔ 這是**唯一**真正把使用者流量切到 React 前端（或切回 SSR）的地方。
# `react` 模式**需要 CEO+CISO+CPO 三審 + 老闆簽核才能執行**（見
# docs/PLAN-frontend-backend-split.md P3）。前置：deploy/deploy_frontend_
# nginx.sh 已跑過（nginx 層 + 兩份 conf + 前端 dist 都已在實例上）。
#
# 用法：
#   deploy/cutover_switch.sh react    # cutover：nginx 改服務 React 靜態檔
#   deploy/cutover_switch.sh legacy   # 回滾：換回 SSR 全轉發（緊急用，秒切）
#
# 做的事（都在同一台既有 EC2 上，透過 SSM，不重建/不動 AWS 資源）——
# **guarded transaction**（codex 複審，robustness 補強）：
#   1. 候選設定驗證（`nginx -t` 對候選 conf）——完全不動 live symlink，
#      驗證用一個獨立的 scratch harness conf（只 include 候選 conf），失敗
#      就直接中止，現有服務完全不受影響（連 symlink 都還沒碰）。
#   2. 驗證通過才記錄「切換前狀態」（live symlink 目標、python 的
#      TRUSTFORGE_CSP_MODE 那一行），並掛上失敗 trap——之後任一步
#      （symlink 切換／`nginx -t`／`systemctl restart trustforge`／
#      `systemctl reload nginx`）失敗，一律自動回滾到切換前狀態，不留
#      「nginx 已切但 python 沒切」或「python 已切但 nginx 沒 reload」這種
#      半殘狀態。
#   3. 完成後主動驗證：active nginx conf symlink、python 的 CSP_MODE、
#      `/healthz` 都要確實是目標狀態，任一項對不上也視為失敗、觸發同一套
#      回滾（而不是命令都跑完就假設成功）。
#
# 不做：不動 TLS 憑證（見 deploy/TLS-SETUP.md，憑證跟站台切換相互獨立）、
# 不動 SSR 是否移除（P3「一週觀察期」後才移除，屬另一個獨立、需另行確認
# 的步驟，不在本腳本範圍）。
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${1:-}"
if [ "$MODE" != "react" ] && [ "$MODE" != "legacy" ]; then
  echo "用法：$0 react|legacy" >&2
  exit 2
fi

REGION="${REGION:-ap-southeast-2}"

if [ "$MODE" = "react" ]; then
  # 這幾行狀態訊息刻意都印到 stderr（不是 stdout）：`TF_CUTOVER_DRY_RUN=1`
  # 時 stdout 只能是純粹的遠端指令內容（給 test_cutover_switch.sh 用
  # `$(...)` 擷取去本機沙箱執行），混進來會讓擷取到的內容不是合法 shell
  # script。
  echo "⚠️  即將切換到 React 前端拓樸（cutover）。" >&2
  echo "    這一步需要 CEO+CISO+CPO 三審 + 老闆簽核才能執行。" >&2
  echo "    請確認：deploy/TLS-SETUP.md 的憑證已就緒、" >&2
  echo "    deploy/nginx.conf 的 ssl_certificate 路徑/domain 跟實際簽發一致。" >&2
  if [ "${TRUSTFORGE_CUTOVER_CONFIRMED:-}" != "yes" ]; then
    echo "❌ 未設 TRUSTFORGE_CUTOVER_CONFIRMED=yes，視為未取得簽核，中止。" >&2
    echo "   三審+簽核完成後，執行：TRUSTFORGE_CUTOVER_CONFIRMED=yes $0 react" >&2
    exit 1
  fi
fi

# 遠端指令內容：`${MODE}` 是本機這支腳本的變數，故意在本機就展開（送到遠端
# 的內容已經是具體的 react/legacy 字面值）。其餘 `\$xxx`／`\${xxx}` 都是刻意
# 用反斜線跳脫，讓它們在本機構造字串時維持原樣、只在遠端 shell 真正執行時
# 才展開（遠端變數，本機沒有定義，本機 `set -u` 下若沒跳脫會直接炸掉）。
#
# `\${TF_CUTOVER_ETC:-/etc}`：預設 `/etc`（生產行為不變，遠端 SSM 執行環境
# 本來就不會有這個變數）；只有 `deploy/test_cutover_switch.sh` 在本機沙箱
# 執行擷取出來的指令內容時，會覆寫成沙箱路徑，藉此在不動真實 /etc、不需要
# root、不需要真 nginx/systemd 的情況下測試這支腳本的控制流程（guarded
# transaction／失敗回滾）是否正確。
CMD="set -e
ETC=\"\${TF_CUTOVER_ETC:-/etc}\"
CANDIDATE=\"\$ETC/nginx/trustforge-sites/${MODE}.conf\"
LIVE_LINK=\"\$ETC/nginx/conf.d/trustforge.conf\"
SERVICE_FILE=\"\$ETC/systemd/system/trustforge.service\"

# ---- Step 1：候選設定驗證，完全不動 live symlink ----
VALIDATE_CONF=\"/tmp/tf-cutover-validate-\$\$.conf\"
cat > \"\$VALIDATE_CONF\" <<VALIDATE_EOF
worker_processes 1;
error_log /tmp/tf-cutover-validate-\$\$.err.log;
pid /tmp/tf-cutover-validate-\$\$.pid;
events { worker_connections 16; }
http {
  include \$CANDIDATE;
}
VALIDATE_EOF
if ! nginx -t -c \"\$VALIDATE_CONF\" 2>/tmp/tf-cutover-validate-\$\$.stderr; then
  echo '❌ [cutover] 候選設定驗證失敗（未動 live symlink，現有服務不受影響）：' >&2
  cat /tmp/tf-cutover-validate-\$\$.stderr >&2 2>/dev/null || true
  rm -f \"\$VALIDATE_CONF\" \"/tmp/tf-cutover-validate-\$\$.err.log\" \"/tmp/tf-cutover-validate-\$\$.pid\" \"/tmp/tf-cutover-validate-\$\$.stderr\"
  exit 1
fi
rm -f \"\$VALIDATE_CONF\" \"/tmp/tf-cutover-validate-\$\$.err.log\" \"/tmp/tf-cutover-validate-\$\$.pid\" \"/tmp/tf-cutover-validate-\$\$.stderr\"
echo '[cutover] 候選設定驗證通過（未動 live symlink）'

# ---- Step 2：記錄切換前狀態，掛失敗回滾 ----
PREV_LINK=\"\$(readlink \"\$LIVE_LINK\" 2>/dev/null || true)\"
PREV_MODE_LINE=\"\$(grep '^Environment=TRUSTFORGE_CSP_MODE=' \"\$SERVICE_FILE\" 2>/dev/null || true)\"

ROLLBACK() {
  local ec=\"\${1:-1}\"
  trap - ERR
  echo \"❌ [cutover] 切換中失敗（exit=\${ec}），回滾到切換前狀態…\" >&2
  if [ -n \"\$PREV_LINK\" ]; then
    ln -sfn \"\$PREV_LINK\" \"\$LIVE_LINK\"
  fi
  if [ -n \"\$PREV_MODE_LINE\" ]; then
    sed -i \"s|^Environment=TRUSTFORGE_CSP_MODE=.*|\$PREV_MODE_LINE|\" \"\$SERVICE_FILE\"
  fi
  systemctl daemon-reload || true
  systemctl restart trustforge || true
  if nginx -t >/tmp/tf-cutover-rollback-\$\$.stderr 2>&1; then
    systemctl reload nginx || true
  else
    echo '⚠️ [cutover] 回滾後 nginx -t 仍失敗，請人工介入：' >&2
    cat /tmp/tf-cutover-rollback-\$\$.stderr >&2 2>/dev/null || true
  fi
  rm -f \"/tmp/tf-cutover-rollback-\$\$.stderr\"
  echo \"❌ [cutover] 已回滾到切換前狀態（不留半殘），exit=\$ec\" >&2
  exit \"\$ec\"
}
trap 'ROLLBACK \$?' ERR

# ---- Step 3：實際切換；任一步失敗（symlink/nginx -t/restart/reload）
#      都會被上面的 ERR trap 接住，自動回滾 ----
ln -sfn \"\$CANDIDATE\" \"\$LIVE_LINK\"
nginx -t
sed -i \"s|^Environment=TRUSTFORGE_CSP_MODE=.*|Environment=TRUSTFORGE_CSP_MODE=${MODE}|\" \"\$SERVICE_FILE\"
systemctl daemon-reload
systemctl restart trustforge
systemctl reload nginx

# ---- Step 4：完成後主動驗證（active config + python mode 真的是目標
#      狀態）——用 bare [ ... ] 斷言（不是 exit 1），失敗才會被上面
#      同一顆 ERR trap 接住觸發回滾，而不是命令都跑完就假設成功 ----
ACTIVE_LINK=\"\$(readlink \"\$LIVE_LINK\" 2>/dev/null || true)\"
if [ \"\$ACTIVE_LINK\" != \"\$CANDIDATE\" ]; then
  echo \"❌ [cutover] 完成後驗證失敗：active symlink=\$ACTIVE_LINK ≠ candidate=\$CANDIDATE\" >&2
fi
[ \"\$ACTIVE_LINK\" = \"\$CANDIDATE\" ]

ACTIVE_MODE_LINE=\"\$(grep '^Environment=TRUSTFORGE_CSP_MODE=' \"\$SERVICE_FILE\" 2>/dev/null || true)\"
if [ \"\$ACTIVE_MODE_LINE\" != \"Environment=TRUSTFORGE_CSP_MODE=${MODE}\" ]; then
  echo \"❌ [cutover] 完成後驗證失敗：CSP_MODE=\$ACTIVE_MODE_LINE ≠ 預期 Environment=TRUSTFORGE_CSP_MODE=${MODE}\" >&2
fi
[ \"\$ACTIVE_MODE_LINE\" = \"Environment=TRUSTFORGE_CSP_MODE=${MODE}\" ]

if ! curl -fsS -o /dev/null http://127.0.0.1:8080/healthz; then
  echo '❌ [cutover] 完成後驗證失敗：python /healthz 未回應' >&2
fi
curl -fsS -o /dev/null http://127.0.0.1:8080/healthz

trap - ERR
echo '[cutover] 已切換到 ${MODE}（nginx conf + python CSP_MODE 同步，完成後驗證通過）'
"

# 測試用 dry-run 逃生口：只印出上面組好的遠端指令內容、不真的呼叫
# `aws ssm send-command`（不連真 AWS）。給 deploy/test_cutover_switch.sh
# 擷取這段內容後在本機沙箱（假 /etc + mock nginx/systemctl）實際執行，驗證
# guarded transaction／失敗回滾的控制流程真的正確——不是只 grep 關鍵字。
# 生產環境不會設這個變數，行為不受影響。
if [ "${TF_CUTOVER_DRY_RUN:-}" = "1" ]; then
  printf '%s' "$CMD"
  exit 0
fi

MATCHES=$(aws ec2 describe-instances --region "$REGION" \
  --filters Name=tag:Name,Values=trustforge-demo \
    Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].InstanceId' --output text)
IID=$(printf '%s\n' "$MATCHES" | awk '{print $1}')
if [ -z "$IID" ] || [ "$IID" = "None" ]; then
  echo "❌ 找不到 running 中的 trustforge-demo 實例，中止" >&2
  exit 1
fi
echo "[cutover] 目標實例 ${IID}，切換到 mode=$MODE"

CMDID=$(aws ssm send-command --region "$REGION" --instance-ids "$IID" \
  --document-name AWS-RunShellScript \
  --parameters "commands=$(python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().splitlines()))' <<<"$CMD")" \
  --query 'Command.CommandId' --output text)
aws ssm wait command-executed --region "$REGION" --command-id "$CMDID" --instance-id "$IID" 2>/dev/null || true
STATUS=$(aws ssm get-command-invocation --region "$REGION" --command-id "$CMDID" --instance-id "$IID" --query Status --output text)
if [ "$STATUS" != "Success" ]; then
  echo "❌ 切換失敗：Status=$STATUS" >&2
  aws ssm get-command-invocation --region "$REGION" --command-id "$CMDID" --instance-id "$IID" \
    --query 'StandardErrorContent' --output text >&2 2>/dev/null || true
  exit 1
fi
echo "✅ 已切換到 ${MODE}（CommandId=${CMDID}）"
