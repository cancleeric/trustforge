#!/usr/bin/env bash
# TrustForge — Let's Encrypt/certbot 簽發（task #28 Phase 3，react-TLS domain
# cutover）。
#
# ⛔ **順序鐵則（certbot 前 nginx 必須先在 80 可服務 HTTP-01 challenge）**：
#   1. `deploy/deploy_frontend_nginx.sh` 先跑過至少一次——這一步預設啟用
#      `deploy/nginx-legacy.conf`（或 bare-IP 現況先切
#      `deploy/cutover_switch.sh react-http`），讓 nginx 在 80 port 上真的
#      能服務（SSR 全轉發或 React HTTP-only 版都可以，重點是「nginx 已經
#      在跑、80 有回應」）。
#   2. **本腳本**（`deploy/setup_tls.sh`）：對已指到 EC2 的 domain 跑
#      `certbot certonly --webroot`，走 HTTP-01 challenge（ACME 會打 domain
#      的 80 port 驗證所有權）——**這一步的前提就是第 1 步的 nginx 已經在
#      80 上可服務 challenge**，順序反了 certbot 會直接簽發失敗。
#   3. 簽發成功、憑證就位（`/etc/letsencrypt/live/<domain>/`）後，才執行
#      `deploy/cutover_switch.sh react`，把 nginx 換成 `deploy/nginx.conf`
#      （TLS 版，讀取本腳本簽出的憑證路徑）。
#
# ⛔ **`certonly --webroot`，不是 `--nginx` plugin**（codex 複審 HIGH）：
# `--nginx` plugin 在 non-interactive 模式需要精準比對到 `server_name
# <domain>` 的 server block 才簽得出來，但 `deploy/nginx-legacy.conf`／
# `deploy/nginx-react-http.conf` 的 `server_name` 一律寫死是 `_`（從未被
# 任何部署腳本自動改寫成真實 domain——先前文件誤以為 certbot --nginx 會
# 自動處理，實際上這只是遺留的手動假設，從沒真的自動化），`--nginx`
# non-interactive 對這種情況配對不到，會直接簽發失敗或留下半殘狀態。改用
# `certonly --webroot -w /var/www/certbot`：**只取憑證，完全不碰 nginx
# config**，HTTP-01 challenge 檔案由 `deploy/nginx-legacy.conf`／
# `deploy/nginx-react-http.conf`／`deploy/nginx.conf`（cutover 後，續簽用）
# 裡新增的 `location ^~ /.well-known/acme-challenge/ { root
# /var/www/certbot; }` 直接從檔案系統回應，跟 `server_name` 是不是真實
# domain 完全無關（同一個 port 上只有一個 server block 時，nginx 一律用
# 它服務任何 Host）。
#
# 本腳本**不是**cutover 的一部分（不改 nginx 的 live symlink/CSP_MODE），
# 只負責「把憑證簽出來 + auto-renew timer」——真正切到 React TLS 拓樸仍是
# `deploy/cutover_switch.sh react` 的職責，兩者刻意分開（比照
# `deploy/deploy_frontend_nginx.sh`「架好但不切」／`cutover_switch.sh`
# 「真正切」的既有分工）。
#
# domain：trustforge.hurricanesoft.com.tw（DNS A record 已指到 EC2
# 13.211.110.218，見 deploy/nginx.conf／deploy/TLS-SETUP.md）。
#
# ⛔ **certbot 是否真的執行是可選 step，預設不跑**（config-only 任務，禁真跑
# AWS/certbot；CEO 真部署時才決定要不要跑）：本腳本預設只印出「會執行什麼」
# 並中止，只有同時滿足以下兩個條件才會真的透過 SSM 對 EC2 下 certbot 指令：
#   - `TRUSTFORGE_RUN_CERTBOT=yes`（比照 deploy/cutover_switch.sh 的
#     `TRUSTFORGE_CUTOVER_CONFIRMED=yes` 慣例，代表「這是刻意要真跑」）
#   - `ADMIN_EMAIL` 已設成真實 email（見下方 ⚠️ 佔位提醒——certbot 用它接收
#     憑證到期/撤銷通知，不能留空或用假信箱）
#
# 可調環境變數：
#   REGION        （預設同 deploy_ec2.sh，ap-southeast-2）
#   DOMAIN        （預設 trustforge.hurricanesoft.com.tw）
#   ADMIN_EMAIL   （⚠️ 佔位，無預設值——CEO 真跑前必須填一個真實可收信的
#                  email，見下方用法）
#   TRUSTFORGE_RUN_CERTBOT（預設空，需設成 "yes" 才會真的跑 certbot）
#   TF_SETUP_TLS_DRY_RUN=1（測試用逃生口，只印出組好的遠端指令內容、不呼叫
#                  `aws ssm send-command`，比照 deploy/cutover_switch.sh、
#                  deploy/deploy_frontend_nginx.sh 同款設計）
#
# 用法（CEO 真跑時）：
#   ADMIN_EMAIL=ops@hurricanesoft.com.tw TRUSTFORGE_RUN_CERTBOT=yes \
#     bash deploy/setup_tls.sh
set -euo pipefail
cd "$(dirname "$0")/.."

REGION="${REGION:-ap-southeast-2}"
DOMAIN="${DOMAIN:-trustforge.hurricanesoft.com.tw}"
ADMIN_EMAIL="${ADMIN_EMAIL:-}"

# ---- 嚴格驗證 DOMAIN/ADMIN_EMAIL 格式（codex 複審 HIGH：這兩個值最終會
#      被送進遠端 shell 執行——即使下面改成 positional args 傳遞（見
#      `bash -s -- "$DOMAIN" "$ADMIN_EMAIL"`），格式驗證仍是第一道防線：
#      不符合合法 domain/email 格式（含任何 `;`/`$`/反引號/引號/空白等
#      shell 特殊字元）一律直接拒絕、不執行，不依賴「反正有 quoting 擋
#      著」這種單一防線）----
DOMAIN_RE='^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$'
if [ -z "$DOMAIN" ] || [ "${#DOMAIN}" -gt 253 ] || ! [[ "$DOMAIN" =~ $DOMAIN_RE ]]; then
  echo "❌ [setup-tls] DOMAIN 格式不合法（只允許字母/數字/連字號的合法網域名稱，" >&2
  echo "   例如 trustforge.hurricanesoft.com.tw；不允許空白/引號/\$/反引號/;" >&2
  echo "   等 shell 特殊字元）：DOMAIN=${DOMAIN}" >&2
  exit 1
fi

echo "[setup-tls] domain=${DOMAIN} region=${REGION}" >&2
echo "[setup-tls] 前置確認：deploy/deploy_frontend_nginx.sh（或" >&2
echo "[setup-tls]   deploy/cutover_switch.sh react-http）已跑過，nginx 目前" >&2
echo "[setup-tls]   確實在 80 port 上可服務（certbot HTTP-01 challenge 的前提）。" >&2

if [ -z "$ADMIN_EMAIL" ]; then
  echo "❌ [setup-tls] 未設 ADMIN_EMAIL（⚠️ 佔位，讓 CEO 填一個真實可收信的" >&2
  echo "   email，certbot 用它接收憑證到期/撤銷通知）。範例：" >&2
  echo "   ADMIN_EMAIL=ops@hurricanesoft.com.tw TRUSTFORGE_RUN_CERTBOT=yes bash $0" >&2
  exit 1
fi

# ---- 嚴格驗證 ADMIN_EMAIL 格式（codex 複審 HIGH，理由同上 DOMAIN 驗證：
#      拒絕任何非合法 email 格式，含 shell 特殊字元一律擋下）----
EMAIL_RE='^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'
if ! [[ "$ADMIN_EMAIL" =~ $EMAIL_RE ]]; then
  echo "❌ [setup-tls] ADMIN_EMAIL 格式不合法（需是合法 email 格式，例如" >&2
  echo "   ops@hurricanesoft.com.tw；不允許空白/引號/\$/反引號/; 等 shell" >&2
  echo "   特殊字元）：ADMIN_EMAIL=${ADMIN_EMAIL}" >&2
  exit 1
fi

if [ "${TRUSTFORGE_RUN_CERTBOT:-}" != "yes" ]; then
  echo "❌ [setup-tls] 未設 TRUSTFORGE_RUN_CERTBOT=yes，視為「先看看會跑什麼、" >&2
  echo "   還不要真的簽」，中止（不做任何 mutation）。確認 nginx 已在 80" >&2
  echo "   可服務、domain 已指好之後，執行：" >&2
  echo "   ADMIN_EMAIL=<email> TRUSTFORGE_RUN_CERTBOT=yes $0" >&2
  exit 1
fi

# 遠端指令：安裝 certbot（base 套件即可，**不裝** python3-certbot-nginx，
# 這裡不用 `--nginx` plugin）+ 執行 `certonly --webroot` 簽發。
#
# ⛔ codex 複審 HIGH（`--nginx` plugin 配對不到 server_name，見上方大段
# 說明）：改用 `certonly --webroot -w ${CERTBOT_WEBROOT}`，只取憑證、
# 完全不碰 nginx config；HTTP-01 challenge 檔案由
# deploy/nginx-legacy.conf／deploy/nginx-react-http.conf／
# deploy/nginx.conf 裡新增的 `location ^~ /.well-known/acme-challenge/
# { root ${CERTBOT_WEBROOT}; }` 直接從檔案系統回應。因為真正的 TLS 拓樸是
# `deploy/nginx.conf`（cutover_switch.sh react 才會切上去），這裡簽發完
# 之後不依賴 certbot 改過任何 nginx conf，cutover 時 `deploy/nginx.conf`
# 自己已經寫死讀同一個憑證路徑（/etc/letsencrypt/live/${DOMAIN}/），只要
# 憑證檔案就位即可。
#
# ⛔ codex 複審另一個 HIGH（注入防護，defense-in-depth）：DOMAIN/ADMIN_EMAIL
# 上面已經過嚴格 regex 驗證，但不只依賴這一層——這裡刻意不把
# DOMAIN/ADMIN_EMAIL 到處內插進 script 字串（例如直接寫進 certbot 指令
# 行），而是只在唯一一個位置把它們當成**兩個獨立、有加引號的 shell 參數**
# 傳給 `bash -s -- "${DOMAIN}" "${ADMIN_EMAIL}"`，真正的 certbot 邏輯（用
# `<<'REMOTE_TLS_EOF'`、單引號 heredoc 界定字，本機建構 CMD 字串時完全不
# 展開）只透過 `$1`/`$2` 這兩個 shell 內建的 positional parameter 取值，
# 不會再把 DOMAIN/ADMIN_EMAIL 的值重新拼進其他任何字串位置——即使驗證有
# 漏網之魚，injected 內容頂多變成 argv[1]/argv[2] 的「資料」，不會被當成
# shell 語法的一部分執行。
CERTBOT_WEBROOT="/var/www/certbot"
CMD="set -e
dnf install -y certbot
mkdir -p ${CERTBOT_WEBROOT}/.well-known/acme-challenge
bash -s -- \"${DOMAIN}\" \"${ADMIN_EMAIL}\" <<'REMOTE_TLS_EOF'
set -e
TF_DOMAIN=\"\$1\"
TF_ADMIN_EMAIL=\"\$2\"
certbot certonly --webroot -w ${CERTBOT_WEBROOT} -d \"\$TF_DOMAIN\" \\
  --non-interactive --agree-tos -m \"\$TF_ADMIN_EMAIL\"
echo \"[setup-tls] certbot 簽發完成，憑證路徑：/etc/letsencrypt/live/\$TF_DOMAIN/\"
systemctl list-timers 'certbot-renew.timer' --no-pager || true
REMOTE_TLS_EOF
"

if [ "${TF_SETUP_TLS_DRY_RUN:-}" = "1" ]; then
  printf '%s' "$CMD"
  exit 0
fi

# ---- 找目標實例（codex 複審 HIGH：以前 awk '{print $1}' 在 0 台/多台
#      相符時會靜默選第一行（0 台時是空字串、多台時默默只挑其中一台）——
#      裝錯主機/裝到非 prod 實例，正牌 prod 卻沒裝到憑證，之後 cutover
#      react 會失敗。比照 deploy/deploy_frontend_nginx.sh 已有的做法：
#      算相符實例數，非「剛好 1 台」一律 fail-closed 中止、不猜、不亂選）----
if ! MATCHES=$(aws ec2 describe-instances --region "$REGION" \
  --filters Name=tag:Name,Values=trustforge-demo \
    Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].[InstanceId]' --output text); then
  echo "❌ [setup-tls] 查詢 trustforge-demo 實例失敗，中止" >&2
  exit 1
fi
MATCH_COUNT=$(printf '%s\n' "$MATCHES" | grep -c . || true)
if [ "$MATCH_COUNT" -ne 1 ]; then
  echo "❌ [setup-tls] 找到 ${MATCH_COUNT} 個相符實例（tag Name=trustforge-demo，running），需要剛好 1 個，中止（不會亂猜裝到哪一台）。" >&2
  echo "   0 台：請先確認 EC2 是否真的在跑（deploy/deploy_ec2.sh）；多台：請先手動確認/收斂到剛好一台 running 的 trustforge-demo 實例。" >&2
  exit 1
fi
IID=$(printf '%s\n' "$MATCHES" | awk '{print $1}')
echo "[setup-tls] 目標實例 ${IID}，簽發 ${DOMAIN} 的憑證" >&2

CMDID=$(aws ssm send-command --region "$REGION" --instance-ids "$IID" \
  --document-name AWS-RunShellScript \
  --parameters "commands=$(python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().splitlines()))' <<<"$CMD")" \
  --query 'Command.CommandId' --output text)
aws ssm wait command-executed --region "$REGION" --command-id "$CMDID" --instance-id "$IID" 2>/dev/null || true
STATUS=$(aws ssm get-command-invocation --region "$REGION" --command-id "$CMDID" --instance-id "$IID" --query Status --output text)
if [ "$STATUS" = "Success" ]; then
  echo "✅ [setup-tls] 憑證簽發完成（CommandId=${CMDID}），下一步：" >&2
  echo "   TRUSTFORGE_CUTOVER_CONFIRMED=yes deploy/cutover_switch.sh react" >&2
  exit 0
fi
echo "❌ [setup-tls] 憑證簽發失敗：Status=$STATUS" >&2
aws ssm get-command-invocation --region "$REGION" --command-id "$CMDID" --instance-id "$IID" \
  --query 'StandardErrorContent' --output text >&2 2>/dev/null || true
exit 1
