#!/usr/bin/env bash
# TrustForge — Phase 3 cutover 切換器（task #28）。
#
# ⛔ 這是**唯一**真正把使用者流量切到 React 前端（或切回 SSR）的地方。
# `react` 模式**需要 CEO+CISO+CPO 三審 + 老闆簽核才能執行**（見
# docs/PLAN-frontend-backend-split.md P3）。前置：deploy/deploy_frontend_
# nginx.sh 已跑過（nginx 層 + 兩份 conf + 前端 dist 都已在實例上）。
#
# 用法：
#   deploy/cutover_switch.sh react       # cutover：nginx 改服務 React 靜態檔
#                                        # （TLS 版，deploy/nginx.conf，需要
#                                        # 已有 domain + certbot 簽發的憑證）
#   deploy/cutover_switch.sh react-http  # cutover：nginx 改服務 React 靜態檔
#                                        # （HTTP-only 版，deploy/nginx-
#                                        # react-http.conf，bare-IP 現況、
#                                        # 無 domain 時用——見 deploy/README.md）
#                                        # ⛔ codex 複審 HIGH：production 不該用
#                                        # 這個（無 TLS，MITM 可竄改），預設拒絕，
#                                        # 需 TF_ALLOW_INSECURE_HTTP_CUTOVER=yes
#                                        # 才放行（例外路徑）
#   deploy/cutover_switch.sh legacy      # 回滾：換回 SSR 全轉發（緊急用，秒切）
#
# production React 唯一路徑：legacy（ACME challenge）→ deploy/setup_tls.sh
# 簽發憑證 → react（TLS）cutover。react-http 不是 production 路徑。
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
#      回滾（而不是命令都跑完就假設成功）。**加上 public nginx（port 80）
#      smoke check**（codex 五次複審，HIGH：以前完成後驗證只打 python 直連
#      `127.0.0.1:8080/healthz`，從沒經過 nginx——`nginx -t` 只驗語法，
#      不證 React dist 目錄真的存在可服務、SPA 路由/try_files 沒壞、
#      location 沒設錯；dist 缺失/損毀或 nginx 層本身有問題時，python 直連
#      依然健康，腳本會謊報 cutover 成功、使用者卻看到錯誤）：清除 ERR
#      trap 之前，legacy 打 `http://127.0.0.1/healthz`（驗 SSR 全轉發鏈路）、
#      react/react-http 打 `http://127.0.0.1/`＋`/analyze`（驗 React
#      index.html 真的被服務、含 CSP header 跟 `<div id="root">` dist
#      特徵、SPA fallback 正常）＋`/api/health`（驗 nginx→python 的
#      `/api/` proxy 正常）——任一失敗都沿用同一顆 ERR trap 觸發既有
#      rollback，不是新的失敗路徑，也不會謊報成功。
#   4. **rollback 本身也不可靠、不可盲目信任**（codex 二次複審，HIGH）：
#      回滾動作（symlink 還原／CSP_MODE 還原／daemon-reload／restart
#      trustforge／nginx -t／reload nginx）每一步都個別追蹤成功與否，
#      不用 `|| true` 吞掉；全部跑完後，回滾自己也要主動驗證（symlink
#      真的指回切換前目標、service file CSP_MODE 真的是切換前的值、
#      `/healthz` 真的有回應）。只有「每一步都成功」+「驗證通過」才印
#      「已回滾...驗證通過」；只要有任何一步失敗或驗證不過，一律印
#      distinct 的 `ROLLBACK-FAILED` 狀態、附上具體手動復原指令、非零
#      結束——絕不假裝救回來了。
#   5. **無交易鎖時，兩個並行呼叫會 race、互相破壞 symlink/service 狀態**
#      （codex 三次複審，HIGH）：在 Step 1 候選驗證/記錄切換前狀態**之前**，
#      先搶一個 host-wide 的 `flock -n`（固定 lock 檔，預設
#      `/var/lock/tf-cutover.lock`），一路持有到腳本結束（成功收尾或
#      rollback 完成）才釋放（靠 fd 隨 process 結束自動釋放，不提前
#      關閉）。拿不到鎖（代表已經有另一個 cutover 在跑）→ 印 distinct
#      的「另一個 cutover 進行中」訊息、exit 98（跟一般失敗 exit 1、
#      ROLLBACK-FAILED exit 97 都不同），**完全不做任何 mutation**就中止。
#   6. **production SSM wrapper 以前會把 distinct exit code 全部塌成 1**
#      （codex 四次複審，HIGH）：遠端腳本發的 97/98/1 是靠 SSM
#      `get-command-invocation` 的 `Status` + `ResponseCode` 帶回本機，以前
#      wrapper 只看 Status、非 Success 一律 exit 1，讓 contention（98）跟
#      ROLLBACK-FAILED（97）在 production path 分不出來。改讀 ResponseCode
#      並把 97/98 原樣傳遞成 wrapper 自己的 top-level exit code，其餘失敗
#      維持 exit 1 fallback（見本檔案 SSM 呼叫段落、deploy/README.md exit
#      code 慣例）。同一輪也修了 **absent/unreadable live symlink 的
#      pre-state**：切換前若本來就沒有 symlink（或讀不到），以前 rollback
#      只看 PREV_LINK 是否非空來決定要不要動作，空字串被誤當成「這步不用
#      做」而略過，導致切換時新建的 symlink 沒被清掉；現在用明確的
#      `PREV_LINK_EXISTED` flag 區分「原本有」跟「原本無/讀不到」，後者
#      rollback 時主動 `rm -f` 還原成無 symlink，並且回滾後驗證也涵蓋
#      這個分支（不再因為 PREV_LINK 是空字串就整段跳過驗證）。
#
# 不做：不動 TLS 憑證（見 deploy/TLS-SETUP.md，憑證跟站台切換相互獨立）、
# 不動 SSR 是否移除（P3「一週觀察期」後才移除，屬另一個獨立、需另行確認
# 的步驟，不在本腳本範圍）。
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${1:-}"
if [ "$MODE" != "react" ] && [ "$MODE" != "react-http" ] && [ "$MODE" != "legacy" ]; then
  echo "用法：$0 react|react-http|legacy" >&2
  exit 2
fi

# ---- react-http 預設禁止（codex 複審 HIGH：production 應只走 react(TLS)）--
# 現在已經有 domain + certbot 憑證（deploy/setup_tls.sh），react-http（React
# 前端跑在純 HTTP、無 TLS）不該再是 production cutover 的選項——中間人可
# 竄改沒有 TLS 保護的 JS/API 回應本身，CSP header 只能限制「已收到」的內容
# 要怎麼執行，擋不住封包被竄改這件事。react-http 唯一合理用途是還沒申請/
# 生效 domain 時的暫時 bare-IP fallback（見 deploy/README.md），不是常態
# production 路徑。這裡預設拒絕，需要明確設 TF_ALLOW_INSECURE_HTTP_CUTOVER=
# yes 才放行（例外路徑）；legacy／react（TLS）完全不受這道關卡影響。
if [ "$MODE" = "react-http" ] && [ "${TF_ALLOW_INSECURE_HTTP_CUTOVER:-}" != "yes" ]; then
  echo "❌ [cutover] react-http（純 HTTP、無 TLS）預設禁止用於 production。" >&2
  echo "   production 請用 react（TLS，需先跑 deploy/setup_tls.sh 簽發憑證）。" >&2
  echo "   react-http 只是 bare-IP（還沒有 domain）時的暫時 fallback——沒有" >&2
  echo "   TLS，MITM 可竄改 JS/API 回應內容，CSP 擋不住。" >&2
  echo "   確定要用（例如還沒有 domain）：" >&2
  echo "   TF_ALLOW_INSECURE_HTTP_CUTOVER=yes $0 react-http" >&2
  exit 1
fi

REGION="${REGION:-ap-southeast-2}"

# react（TLS）mode 的 public smoke check 要打真正的 domain（見
# deploy/nginx.conf server_name），不能打 bare 127.0.0.1——TLS conf 把 80
# port 除 /healthz 外全部 301 導去 https，若 smoke check 打 http://127.0.0.1/
# 只會拿到 301（curl -f 不把 3xx 當失敗，仍是 exit 0），CSP/body 斷言就會
# 因為 301 回應本來就沒有這些內容而失敗，永遠觸發 rollback、cutover 永遠
# 切不成功（codex 複審，HIGH：react smoke check 打 HTTP 但 TLS 把 HTTP 全
# 301，被 mock 的假 200 掩蓋過去，真環境會 100% rollback）。
REACT_TLS_DOMAIN="trustforge.hurricanesoft.com.tw"

# react-http 的 python 端 CSP_MODE 跟 react（TLS 版）共用同一個值
# `react`——web.py::_send() 只認 CSP_MODE == "react"（見模組頂部說明），
# react-http 只是 nginx 層少了 TLS/redirect/HSTS，前端拓樸/CSP 指令集本身
# 跟 react 完全一致，故不新增第三個 python 端 CSP_MODE 值，避免 web.py 要
# 多分支。nginx candidate conf 檔名則是各自獨立的
# `${MODE}.conf`（react.conf / react-http.conf / legacy.conf）。
CSP_MODE_ENV="$MODE"
if [ "$MODE" = "react-http" ]; then
  CSP_MODE_ENV="react"
fi

# ---- public nginx smoke check（Step 4b，codex 五次複審，HIGH）----------
# 以前 Step 4 完成後驗證只打 python 直連 127.0.0.1:8080/healthz，從沒經過
# nginx。`nginx -t` 只驗語法、不證 dist 目錄存在可服務、SPA 路由/try_files
# 沒壞、location 沒設錯——candidate conf 語法合法但 React dist 缺失/損毀、
# 或 nginx 層本身有問題時，python 直連依然健康，腳本照樣宣告 cutover 成功、
# 使用者卻看到錯誤。這裡在 Step 4「清除 ERR trap 之前」補上打 public
# nginx endpoint（port 80）的驗證，任一失敗都沿用同一顆 ERR trap 觸發既有
# rollback（不是新的失敗路徑），legacy／react-http／react（TLS）各用各自拓樸
# 對應的檢查（legacy 驗 SSR 全轉發、react-http 驗 HTTP 版 React 真的被服務、
# react（TLS）驗 HTTPS 版 React + HTTP→HTTPS redirect）。跟前面所有段落一樣：
# `\$`／`\"` 是刻意跳脫、留給遠端 shell 執行時才展開/求值，不在本機構造字串
# 時就處理掉。
if [ "$MODE" = "legacy" ]; then
  PUBLIC_SMOKE_BLOCK="
# ---- Step 4b：public nginx smoke check（legacy 拓樸，codex 複審 HIGH：cutover
#      後只驗 python 直連，沒驗 public nginx 面——nginx 面掛掉/conf 有問題時，
#      python 直連依然健康，腳本會謊報成功。這裡改打 public nginx endpoint
#      （port 80），在清除 ERR trap 之前失敗會被同一顆 trap 接住觸發 rollback。
#      legacy 拓樸：nginx location / 全部原樣轉發給 python，用 /healthz 驗證
#      nginx→python 這段代理鏈路是通的）----
if ! curl -fsS -o /dev/null http://127.0.0.1/healthz; then
  echo \"❌ [cutover] 完成後驗證失敗：public nginx（port 80）/healthz 沒有回應（legacy SSR 全轉發鏈路異常）\" >&2
fi
curl -fsS -o /dev/null http://127.0.0.1/healthz
echo \"[cutover] public nginx smoke check 通過（/healthz 經 nginx 轉發正常）\"
"
elif [ "$MODE" = "react-http" ]; then
  PUBLIC_SMOKE_BLOCK="
# ---- Step 4b：public nginx smoke check（React 拓樸，HTTP-only／bare-IP
#      fallback，codex 複審 HIGH：cutover 後只驗 python 直連，沒驗 public
#      nginx 面——dist 缺失/SPA 路由壞/nginx 面失敗時，python 直連依然健康，
#      腳本會謊報成功。這裡改打 public nginx endpoint（port 80，沒有 TLS，
#      跟 react（TLS）版不同，不涉及 301 redirect），任一失敗都在清除 ERR
#      trap 之前，會被同一顆 trap 接住觸發 rollback，不會謊報成功）----
SMOKE_DIR=\"/tmp/tf-cutover-smoke-\$\$\"
mkdir -p \"\$SMOKE_DIR\"

_tf_check_react_page() {
  local path=\"\$1\" label=\"\$2\"
  local hdr=\"\$SMOKE_DIR/hdr-\${label}\" body=\"\$SMOKE_DIR/body-\${label}\"
  if ! curl -fsS -D \"\$hdr\" -o \"\$body\" \"http://127.0.0.1\${path}\"; then
    echo \"❌ [cutover] 完成後驗證失敗：public nginx \${path}（\${label}）沒有 200 回應（React dist 是否缺失/nginx 面是否正常？）\" >&2
    return 1
  fi
  if ! grep -qi \"^content-security-policy:\" \"\$hdr\"; then
    echo \"❌ [cutover] 完成後驗證失敗：public nginx \${path}（\${label}）沒有 CSP header（安全 header 是否真的套用到這個 React 路由？）\" >&2
    return 1
  fi
  if ! grep -q '<div id=\"root\"' \"\$body\"; then
    echo \"❌ [cutover] 完成後驗證失敗：public nginx \${path}（\${label}）回應內容不像 React index.html（dist 是否缺失/損毀？）\" >&2
    return 1
  fi
  return 0
}

_tf_check_react_page \"/\" \"root\"
_tf_check_react_page \"/analyze\" \"spa-fallback\"

if ! curl -fsS -o \"\$SMOKE_DIR/api-health-body\" \"http://127.0.0.1/api/health\"; then
  echo \"❌ [cutover] 完成後驗證失敗：public nginx /api/health 沒有 200 回應（nginx /api/ proxy 是否正常？）\" >&2
fi
curl -fsS -o \"\$SMOKE_DIR/api-health-body\" \"http://127.0.0.1/api/health\"
if ! grep -q '\"ok\": true' \"\$SMOKE_DIR/api-health-body\"; then
  echo \"❌ [cutover] 完成後驗證失敗：public nginx /api/health 回應內容不是預期 JSON\" >&2
fi
grep -q '\"ok\": true' \"\$SMOKE_DIR/api-health-body\"

rm -rf \"\$SMOKE_DIR\"
echo \"[cutover] public nginx smoke check 通過（/、/analyze、/api/health 皆正常且安全 header 有套用）\"
"
else
  # MODE = react（TLS）：nginx.conf 把 80 port 除 /healthz 外全部 301 導去
  # https（見 deploy/nginx.conf），所以這裡「必須」打 HTTPS（用 --resolve
  # 把 domain 指回 127.0.0.1，走本機憑證驗證、不用 -k），不能像
  # react-http 那樣直接打 http://127.0.0.1/（打了只會拿到 301，curl -f
  # 不把 3xx 當失敗，CSP/body 斷言會因為 301 回應本來就沒有這些內容而
  # 失敗，變成每次 cutover 都觸發 rollback——codex 複審 HIGH）。額外補一段
  # HTTP(80)→HTTPS redirect 的驗證，確保 80 port 真的有把非-/healthz 路徑
  # 導去 https，而不是漏掉/導錯 domain。
  PUBLIC_SMOKE_BLOCK="
# ---- Step 4b：public nginx smoke check（React 拓樸，TLS 版，codex 複審
#      HIGH：cutover 後只驗 python 直連，沒驗 public nginx 面——dist 缺失/
#      SPA 路由壞/nginx 面失敗時，python 直連依然健康，腳本會謊報成功。這裡
#      改打 public nginx HTTPS endpoint（--resolve 把 domain 指回
#      127.0.0.1，走正式簽發的憑證驗證、不加 -k），任一失敗都在清除 ERR
#      trap 之前，會被同一顆 trap 接住觸發 rollback，不會謊報成功）----
SMOKE_DIR=\"/tmp/tf-cutover-smoke-\$\$\"
mkdir -p \"\$SMOKE_DIR\"

_tf_check_react_page() {
  local path=\"\$1\" label=\"\$2\"
  local hdr=\"\$SMOKE_DIR/hdr-\${label}\" body=\"\$SMOKE_DIR/body-\${label}\"
  if ! curl -fsS --resolve \"${REACT_TLS_DOMAIN}:443:127.0.0.1\" -D \"\$hdr\" -o \"\$body\" \"https://${REACT_TLS_DOMAIN}\${path}\"; then
    echo \"❌ [cutover] 完成後驗證失敗：public nginx https://${REACT_TLS_DOMAIN}\${path}（\${label}）沒有 200 回應（React dist 是否缺失/nginx 面是否正常？）\" >&2
    return 1
  fi
  if ! grep -qi \"^content-security-policy:\" \"\$hdr\"; then
    echo \"❌ [cutover] 完成後驗證失敗：public nginx https://${REACT_TLS_DOMAIN}\${path}（\${label}）沒有 CSP header（安全 header 是否真的套用到這個 React 路由？）\" >&2
    return 1
  fi
  if ! grep -q '<div id=\"root\"' \"\$body\"; then
    echo \"❌ [cutover] 完成後驗證失敗：public nginx https://${REACT_TLS_DOMAIN}\${path}（\${label}）回應內容不像 React index.html（dist 是否缺失/損毀？）\" >&2
    return 1
  fi
  return 0
}

_tf_check_react_page \"/\" \"root\"
_tf_check_react_page \"/analyze\" \"spa-fallback\"

if ! curl -fsS --resolve \"${REACT_TLS_DOMAIN}:443:127.0.0.1\" -o \"\$SMOKE_DIR/api-health-body\" \"https://${REACT_TLS_DOMAIN}/api/health\"; then
  echo \"❌ [cutover] 完成後驗證失敗：public nginx /api/health 沒有 200 回應（nginx /api/ proxy 是否正常？）\" >&2
fi
curl -fsS --resolve \"${REACT_TLS_DOMAIN}:443:127.0.0.1\" -o \"\$SMOKE_DIR/api-health-body\" \"https://${REACT_TLS_DOMAIN}/api/health\"
if ! grep -q '\"ok\": true' \"\$SMOKE_DIR/api-health-body\"; then
  echo \"❌ [cutover] 完成後驗證失敗：public nginx /api/health 回應內容不是預期 JSON\" >&2
fi
grep -q '\"ok\": true' \"\$SMOKE_DIR/api-health-body\"

# ---- HTTP(80) → HTTPS redirect 驗證（codex 複審 HIGH：react（TLS）的
#      nginx.conf 把 80 port 除 /healthz 外全部 301 導去 https，這段沒驗到
#      的話，有可能部署出一份 80 port 沒有正確導頁的 conf，使用者打 http
#      網址進來卻卡住、不會被導去 https，而上面對 https 的檢查完全測不出
#      這個問題。故意帶 -H \\\"Host: ${REACT_TLS_DOMAIN}\\\"：探測器/curl 直
#      接打 IP 時不見得會帶正確 Host，明確帶上才能確保打中的是這個
#      server_name（同一台機器上若還有其他 vhost/預設 server block，光靠
#      IP 連線不保證命中我們要驗的這個 server block）——同時這也是
#      codex 複審 HIGH 抓到的另一個回歸的前提：conf 端 redirect target
#      以前用 \\\`\$host\\\`，會直接照抄請求時的 Host（不管是不是
#      canonical），這裡刻意把 Host 設成跟 canonical domain 一致，藉此驗
#      證 conf 端真的是寫死 literal domain、不是把 \$host 原樣转出去）----
REDIRECT_HDR=\"\$SMOKE_DIR/redirect-hdr\"
if ! curl -fsS -D \"\$REDIRECT_HDR\" -o /dev/null -H \"Host: ${REACT_TLS_DOMAIN}\" -H \"X-TF-Cutover-Check: http-redirect\" \"http://127.0.0.1/\"; then
  echo \"❌ [cutover] 完成後驗證失敗：public nginx port 80 對 / 沒有回應（HTTP→HTTPS redirect 是否正常？）\" >&2
fi
curl -fsS -D \"\$REDIRECT_HDR\" -o /dev/null -H \"Host: ${REACT_TLS_DOMAIN}\" -H \"X-TF-Cutover-Check: http-redirect\" \"http://127.0.0.1/\"
if ! grep -qi '^HTTP/[0-9.]* 301' \"\$REDIRECT_HDR\"; then
  echo \"❌ [cutover] 完成後驗證失敗：public nginx port 80 對 / 沒有回 301（預期全轉 HTTPS，實際首行：\$(head -1 \"\$REDIRECT_HDR\")）\" >&2
fi
grep -qi '^HTTP/[0-9.]* 301' \"\$REDIRECT_HDR\"
if ! grep -qi \"^location: https://${REACT_TLS_DOMAIN}\" \"\$REDIRECT_HDR\"; then
  echo \"❌ [cutover] 完成後驗證失敗：public nginx port 80 對 / 的 redirect Location 不是預期的 https://${REACT_TLS_DOMAIN}（canonical domain，不是 127.0.0.1/其他 Host）\" >&2
fi
grep -qi \"^location: https://${REACT_TLS_DOMAIN}\" \"\$REDIRECT_HDR\"

rm -rf \"\$SMOKE_DIR\"
echo \"[cutover] public nginx smoke check 通過（HTTPS /、/analyze、/api/health 皆正常且安全 header 有套用，HTTP→HTTPS redirect 也正常）\"
"
fi

if [ "$MODE" = "react" ] || [ "$MODE" = "react-http" ]; then
  # 這幾行狀態訊息刻意都印到 stderr（不是 stdout）：`TF_CUTOVER_DRY_RUN=1`
  # 時 stdout 只能是純粹的遠端指令內容（給 test_cutover_switch.sh 用
  # `$(...)` 擷取去本機沙箱執行），混進來會讓擷取到的內容不是合法 shell
  # script。
  echo "⚠️  即將切換到 React 前端拓樸（cutover，mode=${MODE}）。" >&2
  echo "    這一步需要 CEO+CISO+CPO 三審 + 老闆簽核才能執行。" >&2
  if [ "$MODE" = "react" ]; then
    echo "    請確認：deploy/TLS-SETUP.md 的憑證已就緒、" >&2
    echo "    deploy/nginx.conf 的 ssl_certificate 路徑/domain 跟實際簽發一致。" >&2
  else
    echo "    react-http 是 bare-IP（無 domain）現況用的 HTTP-only 版本，" >&2
    echo "    請確認：deploy/nginx-react-http.conf 已部署到候選位置" >&2
    echo "    （見 deploy/deploy_frontend_nginx.sh），且現況確實還沒有" >&2
    echo "    domain/TLS——有 domain 後應改用 react（TLS 版）。" >&2
  fi
  if [ "${TRUSTFORGE_CUTOVER_CONFIRMED:-}" != "yes" ]; then
    echo "❌ 未設 TRUSTFORGE_CUTOVER_CONFIRMED=yes，視為未取得簽核，中止。" >&2
    echo "   三審+簽核完成後，執行：TRUSTFORGE_CUTOVER_CONFIRMED=yes $0 ${MODE}" >&2
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

# ---- codex 複審 HIGH（HSTS 破壞 HTTP-only legacy 回滾）：deploy/nginx.conf
#      （react TLS 版）送一年 HSTS，瀏覽器記住後該 domain 之後一年內一律
#      強制走 https，nginx 端完全看不到 80 port 的請求。legacy.conf 只聽
#      80——react→legacy 緊急回滾若切上這份，回訪過的使用者連不上任何東西，
#      回滾本身失去意義。修法：legacy 模式下先偵測憑證是否已存在（同一份
#      certbot 簽發的憑證，deploy/nginx.conf／deploy/setup_tls.sh 共用同一
#      路徑），有 → 改用 legacy-tls.conf（443 服務 SSR、保留 HSTS）；沒有
#      （pre-cert ACME bootstrap 現況，還沒簽過憑證）→ 維持原本 HTTP-only
#      legacy.conf，行為不變。\$ETC 沿用既有的 sandbox override 慣例（
#      letsencrypt 憑證路徑本來就在 /etc 底下，不需要另開一個變數） ----
# CERT_FILE 一律計算（不只 MODE=legacy 才算）：codex 複審 HIGH（自動 trap
# rollback 繞過 legacy-tls 保護）——react/react-http cutover 失敗時的自動
# ERR trap rollback（見下方 ROLLBACK()）也需要用同一套憑證偵測邏輯來決定
# 「切換前是 legacy.conf 的話，回滾要還原成 legacy.conf 還是
# legacy-tls.conf」，不是只有 explicit「cutover_switch.sh legacy」才要判斷。
CERT_FILE=\"\$ETC/letsencrypt/live/${REACT_TLS_DOMAIN}/fullchain.pem\"
if [ \"${MODE}\" = \"legacy\" ]; then
  if [ -f \"\$CERT_FILE\" ]; then
    CANDIDATE=\"\$ETC/nginx/trustforge-sites/legacy-tls.conf\"
    echo \"[cutover] 偵測到憑證已存在（\${CERT_FILE}），legacy 回滾改用 legacy-tls.conf（443 HTTPS 服務 SSR，保留 HSTS，避免破壞回滾）\" >&2
  else
    echo \"[cutover] 尚未偵測到憑證（\${CERT_FILE} 不存在），legacy 回滾用 HTTP-only legacy.conf（pre-cert ACME bootstrap 現況）\" >&2
  fi
fi

# ---- Step 0：host-wide 交易鎖（codex 三次複審，HIGH：沒有鎖，兩個並行
#      cutover 呼叫會 race、互相破壞 symlink/service 狀態）——在候選驗證
#      /記錄切換前狀態**之前**先搶鎖，一路持有到腳本結束（成功收尾或
#      rollback 完成）才靠 process 結束自動釋放 fd。拿不到鎖就直接中止，
#      不執行任何後續 mutation。預設路徑 \${TF_CUTOVER_LOCK:-/var/lock/tf-cutover.lock}
#      （生產行為不變）；沙箱測試可覆寫成 sandbox 內路徑 ----
LOCKFILE=\"\${TF_CUTOVER_LOCK:-/var/lock/tf-cutover.lock}\"
exec 9>\"\$LOCKFILE\"
if ! flock -n 9; then
  echo \"❌ [cutover] 另一個 cutover 進行中（拿不到 host-wide lock：\${LOCKFILE}），本次不做任何變更，直接中止。\" >&2
  exit 98
fi

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
# PREV_LINK_EXISTED（codex 四次複審，HIGH：absent/unreadable symlink 的
# pre-state 沒有 flag 區分「原本就沒有」跟「原本有但讀不到」，rollback 時
# 只看 PREV_LINK 是否非空——若切換前 live symlink 本來就不存在，這個 flag
# 讓 rollback 知道『正確的還原目標是移除 symlink』，而不是誤判成『這步不用
# 做任何事』留著切換時新建的 symlink 半殘）：只有『真的是 symlink 且讀得到
# 目標』才算 PREV_LINK_EXISTED=1；不是 symlink／讀不到目標，一律視為
# PREV_LINK_EXISTED=0（『原本無 symlink，或原本有但讀不到』統一當成
# rollback 目標=移除，不會因為讀不到就誤還原成別的東西）----
if [ -L \"\$LIVE_LINK\" ] && PREV_LINK=\"\$(readlink \"\$LIVE_LINK\" 2>/dev/null)\" && [ -n \"\$PREV_LINK\" ]; then
  PREV_LINK_EXISTED=1
else
  PREV_LINK_EXISTED=0
  PREV_LINK=\"\"
fi
PREV_MODE_LINE=\"\$(grep '^Environment=TRUSTFORGE_CSP_MODE=' \"\$SERVICE_FILE\" 2>/dev/null || true)\"

ROLLBACK() {
  local ec=\"\${1:-1}\"
  trap - ERR
  echo \"❌ [cutover] 切換中失敗（exit=\${ec}），回滾到切換前狀態…\" >&2
  local ROLLBACK_OK=1
  local RESTORE_LINK=\"\$PREV_LINK\"

  if [ \"\$PREV_LINK_EXISTED\" = 1 ]; then
    # ---- codex 複審 HIGH（自動 trap rollback 繞過 legacy-tls 保護）：react/
    #      react-http cutover 在完成 nginx reload 之後、public smoke check
    #      清掉 ERR trap 之前若失敗，觸發的是這顆自動 ERR trap，不是 explicit
    #      「cutover_switch.sh legacy」——若只照抄 PREV_LINK 逐字還原，切換前
    #      是 legacy.conf（HTTP-only）+ 憑證已存在的情況下，會把「這段短暫
    #      窗口內已經吃到 react 的 HSTS」的使用者回滾後晾在只聽 80 的
    #      legacy.conf，跟 explicit legacy 回滾同一個問題。修法：這裡跟
    #      explicit legacy 用同一套 CERT_FILE 偵測，PREV_LINK 是 legacy.conf
    #      且憑證已存在時，還原目標改成 legacy-tls.conf ----
    if [ \"\$(basename \"\$PREV_LINK\" 2>/dev/null)\" = \"legacy.conf\" ] && [ -f \"\$CERT_FILE\" ]; then
      RESTORE_LINK=\"\$ETC/nginx/trustforge-sites/legacy-tls.conf\"
      echo \"[cutover] rollback：偵測到憑證已存在（\${CERT_FILE}），還原目標從 legacy.conf 改成 legacy-tls.conf（443 HTTPS 服務，避免 HSTS 讓回訪用戶連不上，codex 複審 HIGH）\" >&2
    fi
    if ! ln -sfn \"\$RESTORE_LINK\" \"\$LIVE_LINK\"; then
      echo \"❌ [cutover] rollback：symlink 還原失敗（ln -sfn \${RESTORE_LINK} -> \${LIVE_LINK}）\" >&2
      ROLLBACK_OK=0
    fi
  else
    # 切換前本來就沒有（或讀不到）live symlink，正確的還原目標是「移除」
    # 切換時新建的 symlink，不是留著不管（那樣才是誤還原）
    if ! rm -f \"\$LIVE_LINK\"; then
      echo \"❌ [cutover] rollback：symlink 移除失敗（切換前無 symlink/讀不到，理應還原成無 symlink：\${LIVE_LINK}）\" >&2
      ROLLBACK_OK=0
    fi
  fi

  if [ -n \"\$PREV_MODE_LINE\" ]; then
    if ! sed -i \"s|^Environment=TRUSTFORGE_CSP_MODE=.*|\$PREV_MODE_LINE|\" \"\$SERVICE_FILE\"; then
      echo \"❌ [cutover] rollback：service file CSP_MODE 還原失敗（\${SERVICE_FILE}）\" >&2
      ROLLBACK_OK=0
    fi
  fi

  if ! systemctl daemon-reload; then
    echo '❌ [cutover] rollback：systemctl daemon-reload 失敗' >&2
    ROLLBACK_OK=0
  fi

  if ! systemctl restart trustforge; then
    echo '❌ [cutover] rollback：systemctl restart trustforge 失敗' >&2
    ROLLBACK_OK=0
  fi

  if nginx -t >/tmp/tf-cutover-rollback-\$\$.stderr 2>&1; then
    if ! systemctl reload nginx; then
      echo '❌ [cutover] rollback：systemctl reload nginx 失敗' >&2
      ROLLBACK_OK=0
    fi
  else
    echo '❌ [cutover] rollback：nginx -t 失敗（沒有 reload，未觸碰目前運作中的 nginx）：' >&2
    cat /tmp/tf-cutover-rollback-\$\$.stderr >&2 2>/dev/null || true
    ROLLBACK_OK=0
  fi
  rm -f \"/tmp/tf-cutover-rollback-\$\$.stderr\"

  # ---- rollback 後主動驗證：每一步「看起來」有跑完不代表真的回到切換前
  #      狀態，這裡實測 symlink／CSP_MODE／/healthz，跟命令本身的 exit
  #      code 是兩件事、都要過 ----
  local RB_ACTIVE_LINK
  RB_ACTIVE_LINK=\"\$(readlink \"\$LIVE_LINK\" 2>/dev/null || true)\"
  if [ \"\$PREV_LINK_EXISTED\" = 1 ]; then
    if [ \"\$RB_ACTIVE_LINK\" != \"\$RESTORE_LINK\" ]; then
      echo \"❌ [cutover] rollback 驗證失敗：symlink=\$RB_ACTIVE_LINK ≠ 還原目標=\$RESTORE_LINK\" >&2
      ROLLBACK_OK=0
    fi
  else
    if [ -e \"\$LIVE_LINK\" ] || [ -L \"\$LIVE_LINK\" ]; then
      echo \"❌ [cutover] rollback 驗證失敗：切換前無 symlink，但回滾後 \${LIVE_LINK} 仍存在\" >&2
      ROLLBACK_OK=0
    fi
  fi

  local RB_MODE_LINE
  RB_MODE_LINE=\"\$(grep '^Environment=TRUSTFORGE_CSP_MODE=' \"\$SERVICE_FILE\" 2>/dev/null || true)\"
  if [ -n \"\$PREV_MODE_LINE\" ] && [ \"\$RB_MODE_LINE\" != \"\$PREV_MODE_LINE\" ]; then
    echo \"❌ [cutover] rollback 驗證失敗：CSP_MODE=\$RB_MODE_LINE ≠ 切換前=\$PREV_MODE_LINE\" >&2
    ROLLBACK_OK=0
  fi

  if ! curl -fsS -o /dev/null http://127.0.0.1:8080/healthz; then
    echo '❌ [cutover] rollback 驗證失敗：/healthz 沒有回應' >&2
    ROLLBACK_OK=0
  fi

  # ---- codex 複審 HIGH：還原成 legacy-tls.conf 時，不能只看 symlink 指對
  #      路徑就當作成功——實測 443 真的能 serve SSR，不然 HSTS-safe rollback
  #      只是紙面上正確，回訪用戶還是連不上 ----
  if [ \"\$RESTORE_LINK\" = \"\$ETC/nginx/trustforge-sites/legacy-tls.conf\" ]; then
    if ! curl -fsS --resolve \"${REACT_TLS_DOMAIN}:443:127.0.0.1\" -o /dev/null \"https://${REACT_TLS_DOMAIN}/healthz\"; then
      echo \"❌ [cutover] rollback 驗證失敗：還原成 legacy-tls.conf 後，https://${REACT_TLS_DOMAIN}/healthz（443）沒有回應（HSTS-safe rollback 沒有真的可達）\" >&2
      ROLLBACK_OK=0
    fi
  fi

  if [ \"\$ROLLBACK_OK\" = 1 ]; then
    echo \"❌ [cutover] 已回滾到切換前狀態且驗證通過（不留半殘），exit=\$ec\" >&2
    exit \"\$ec\"
  fi

  # ---- 任一步失敗或驗證不過：絕不假裝救回來了，印 distinct 的
  #      ROLLBACK-FAILED 狀態 + 具體手動復原指令，用跟一般失敗（exit=\${ec}）
  #      不同的 exit code（97）方便維運腳本/監控明確分辨「這是回滾本身
  #      壞掉，不是單純切換失敗」----
  echo '🆘 [cutover] ROLLBACK-FAILED：自動回滾沒有完全成功，請立即人工介入，不要假設服務已還原！' >&2
  if [ \"\$PREV_LINK_EXISTED\" = 1 ]; then
    echo \"   1) 檢查 nginx symlink：ls -l \${LIVE_LINK}（預期指向：\${RESTORE_LINK}）\" >&2
    echo \"      不對就手動修：ln -sfn \${RESTORE_LINK} \${LIVE_LINK} && nginx -t && systemctl reload nginx\" >&2
  else
    echo \"   1) 檢查 nginx symlink：ls -l \${LIVE_LINK}（預期：切換前不存在，應該不存在／已移除）\" >&2
    echo \"      不對就手動修：rm -f \${LIVE_LINK} && nginx -t && systemctl reload nginx\" >&2
  fi
  echo \"   2) 檢查 python service 的 CSP_MODE：grep TRUSTFORGE_CSP_MODE \${SERVICE_FILE}\" >&2
  echo \"      （預期：\${PREV_MODE_LINE}）\" >&2
  echo '      不對就手動改該行後：systemctl daemon-reload && systemctl restart trustforge' >&2
  echo '   3) 確認服務：curl -fsS http://127.0.0.1:8080/healthz' >&2
  if [ \"\$RESTORE_LINK\" = \"\$ETC/nginx/trustforge-sites/legacy-tls.conf\" ]; then
    echo \"   4) 確認 HTTPS（HSTS-safe rollback）：curl -fsS --resolve ${REACT_TLS_DOMAIN}:443:127.0.0.1 https://${REACT_TLS_DOMAIN}/healthz\" >&2
    echo '   5) 確認狀態：systemctl status nginx；systemctl status trustforge' >&2
  else
    echo '   4) 確認狀態：systemctl status nginx；systemctl status trustforge' >&2
  fi
  exit 97
}
trap 'ROLLBACK \$?' ERR

# ---- Step 3：實際切換；任一步失敗（symlink/nginx -t/restart/reload）
#      都會被上面的 ERR trap 接住，自動回滾 ----
ln -sfn \"\$CANDIDATE\" \"\$LIVE_LINK\"
nginx -t
sed -i \"s|^Environment=TRUSTFORGE_CSP_MODE=.*|Environment=TRUSTFORGE_CSP_MODE=${CSP_MODE_ENV}|\" \"\$SERVICE_FILE\"
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
if [ \"\$ACTIVE_MODE_LINE\" != \"Environment=TRUSTFORGE_CSP_MODE=${CSP_MODE_ENV}\" ]; then
  echo \"❌ [cutover] 完成後驗證失敗：CSP_MODE=\$ACTIVE_MODE_LINE ≠ 預期 Environment=TRUSTFORGE_CSP_MODE=${CSP_MODE_ENV}\" >&2
fi
[ \"\$ACTIVE_MODE_LINE\" = \"Environment=TRUSTFORGE_CSP_MODE=${CSP_MODE_ENV}\" ]

if ! curl -fsS -o /dev/null http://127.0.0.1:8080/healthz; then
  echo '❌ [cutover] 完成後驗證失敗：python /healthz 未回應' >&2
fi
curl -fsS -o /dev/null http://127.0.0.1:8080/healthz
${PUBLIC_SMOKE_BLOCK}
trap - ERR
echo '[cutover] 已切換到 ${MODE}（nginx conf + python CSP_MODE 同步，完成後驗證通過，含 public nginx smoke check）'
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
# codex 四次複審 HIGH：遠端腳本會發 distinct exit code（97=ROLLBACK-FAILED、
# 98=lock contention、一般失敗=其他非零值），但這裡以前只看 Status，
# 全部非 Success 都 exit 1，把三種情況塌成同一個訊號，監控/自動化分不出來。
# 改讀 get-command-invocation 的 ResponseCode（遠端指令真正的 exit code），
# 把 97/98 原樣傳遞到這層 wrapper 的 top-level exit code，其餘（包含
# TimedOut/Cancelled 等 ResponseCode 可能是 -1 的情況）維持保守 fallback
# exit 1，不臆測。exit code 慣例見 deploy/README.md。
RESPONSE_CODE=$(aws ssm get-command-invocation --region "$REGION" --command-id "$CMDID" --instance-id "$IID" --query ResponseCode --output text 2>/dev/null || true)
if [ "$STATUS" = "Success" ] && [ "$RESPONSE_CODE" = "0" ]; then
  echo "✅ 已切換到 ${MODE}（CommandId=${CMDID}）"
  exit 0
fi
echo "❌ 切換失敗：Status=$STATUS ResponseCode=$RESPONSE_CODE" >&2
aws ssm get-command-invocation --region "$REGION" --command-id "$CMDID" --instance-id "$IID" \
  --query 'StandardErrorContent' --output text >&2 2>/dev/null || true
case "$RESPONSE_CODE" in
  97) exit 97 ;;
  98) exit 98 ;;
  *) exit 1 ;;
esac
