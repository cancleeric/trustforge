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
# 做的事（都在同一台既有 EC2 上，透過 SSM，不重建/不動 AWS 資源）：
#   1. symlink 切換 /etc/nginx/conf.d/trustforge.conf → 對應 conf
#      （legacy.conf／react.conf，兩份都已由 deploy_frontend_nginx.sh 佈署）
#   2. `nginx -t` 通過才 reload（失敗就中止、不影響現有服務——nginx 沒
#      reload 就還是原本那份 conf 在跑，不會變成兩邊都不通）
#   3. 同步切 python 的 `TRUSTFORGE_CSP_MODE`（legacy/react 兩者要一致，
#      否則 python 的 JSON API 回應會用錯誤的 CSP 語意，見 web.py 模組
#      頂部 CSP_MODE 說明）
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
  echo "⚠️  即將切換到 React 前端拓樸（cutover）。"
  echo "    這一步需要 CEO+CISO+CPO 三審 + 老闆簽核才能執行。"
  echo "    請確認：deploy/TLS-SETUP.md 的憑證已就緒、"
  echo "    deploy/nginx.conf 的 ssl_certificate 路徑/domain 跟實際簽發一致。"
  if [ "${TRUSTFORGE_CUTOVER_CONFIRMED:-}" != "yes" ]; then
    echo "❌ 未設 TRUSTFORGE_CUTOVER_CONFIRMED=yes，視為未取得簽核，中止。" >&2
    echo "   三審+簽核完成後，執行：TRUSTFORGE_CUTOVER_CONFIRMED=yes $0 react" >&2
    exit 1
  fi
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

CMD="set -e
ln -sfn /etc/nginx/trustforge-sites/${MODE}.conf /etc/nginx/conf.d/trustforge.conf
nginx -t
sed -i 's|^Environment=TRUSTFORGE_CSP_MODE=.*|Environment=TRUSTFORGE_CSP_MODE=${MODE}|' /etc/systemd/system/trustforge.service
systemctl daemon-reload
systemctl restart trustforge
systemctl reload nginx
echo '[cutover] 已切換到 ${MODE}（nginx conf + python CSP_MODE 同步）'
"

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
