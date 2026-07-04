#!/usr/bin/env bash
# cutover_switch.sh guarded-transaction 邏輯測試（禁真 AWS/SSM、禁真
# nginx/systemctl）：用 `TF_CUTOVER_DRY_RUN=1` 把腳本產生的遠端指令內容
# （原本要送 SSM 執行的那段 shell）擷取出來，塞進一個**沙箱化的假
# /etc 樹**（`TF_CUTOVER_ETC` 覆蓋）搭配假 `nginx`/`systemctl`/`curl`/`sed`
# 二進位，真的執行那段內容（不是只 grep 字串），驗證：
#
#   1. 候選設定驗證失敗（Step 1）→ 完全不動 live symlink/service file、
#      非零結束（因為還沒開始 transaction，理論上不需要也不會觸發回滾）。
#   2. swap 後 `nginx -t`（Step 3 收尾驗證）失敗 → 觸發 trap 回滾，
#      symlink/CSP_MODE 都退回切換前的值、非零結束、不留半殘。
#   3. `systemctl restart trustforge` 失敗 → 同上，觸發回滾。
#   4. `systemctl reload nginx` 失敗 → 同上，觸發回滾。
#   5. 無注入失敗（happy path）→ symlink/CSP_MODE 都真的换到目標 mode、
#      exit 0。
#   6-9. rollback 自己的 daemon-reload／restart／nginx -t／reload nginx
#      各自注入失敗（codex 二次複審，HIGH：rollback 不可用 `|| true`
#      吞掉失敗、不可無條件宣稱成功）→ 斷言印出 distinct 的
#      `ROLLBACK-FAILED` 狀態 + 具體手動復原指示、exit=97（非零），
#      不是誤導性的「已回滾」訊息。
#   10. 並行呼叫（codex 三次複審，HIGH）：host-wide `flock` 已被另一個
#      cutover 持有 → 第二個呼叫在 Step 1 之前就被 reject，印出 distinct
#      的「另一個 cutover 進行中」訊息、exit=98、完全不動 symlink/
#      service file（連候選驗證都沒開始）。
#   11. 鎖沒被持有時的正常單一呼叫（就是場景 5 happy path 本身）→
#      證明鎖不影響正常流程；額外驗證腳本結束後鎖確實釋放（能被
#      重新取得），不會卡死後續呼叫。
#   12. absent live symlink 的 pre-state（codex 四次複審，HIGH）：切換前
#      live symlink 本來就不存在 → pre-state 捕捉要能正確區分「原本無」
#      跟「原本有」，rollback 觸發時要把切換時新建的 symlink 移除、還原
#      成「無 symlink」，不能因為 PREV_LINK 是空字串就誤判成「這步不用
#      做」而把新建的 symlink 留著（半殘狀態）。
#   13. absent live symlink + 無失敗注入 → 正常 cutover 應該照常成功（同一
#      個 pre-state 分支不能只在失敗路徑測，happy path 也要確認沒被新加
#      的判斷式誤傷）。
#   14-16. react-http mode（bare-IP HTTP-only 版）：happy path（candidate=
#      react-http.conf、CSP_MODE 沿用 react 的值）、一個代表性失敗注入
#      觸發回滾、鎖競爭 reject——控制流程跟 react 共用，只需驗證
#      mode-specific 的 candidate/CSP_MODE 值正確。
#   17-23. Step 4b：public nginx（port 80）smoke check 失敗注入（codex 五次
#      複審，HIGH）——即使 candidate 驗證、symlink 切換、nginx -t、
#      systemctl、python 直連 /healthz 全部成功，只要 public nginx 面看到
#      的東西不對（dist 缺失/404、CSP header 沒套用、內容不像 React
#      index.html、SPA fallback（/analyze）壞、/api/health proxy 壞或回應
#      內容不對），都要沿用同一顆 ERR trap 觸發既有 rollback，不能謊報
#      成功；react-http mode 額外驗證一個代表性失敗也走同一套。
#   24-25. legacy mode（從 react 降級）：public /healthz（經 nginx 轉發）
#      失敗注入 → 觸發 rollback、正確退回切換前的 react 狀態；無失敗注入
#      → 正常切回 legacy，且必須先過 public smoke check 才報成功。
#
# 依賴：真的 `flock`（util-linux；Amazon Linux/大多數 Linux 預設就有）。
# macOS 本機測試需要 `brew install flock`（discoteq/flock 的 fd 型
# `flock -n 9` 語法跟 util-linux 相容，已驗證）；沒有就整份測試跳過
# （印訊息到 stderr、exit 0，不是假裝測試通過）。
#
# 用法：bash deploy/test_cutover_switch.sh
set -euo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

if ! command -v flock >/dev/null 2>&1; then
  echo "找不到 flock，跳過整份 deploy/test_cutover_switch.sh" >&2
  echo "（cutover_switch.sh 的 host-wide 交易鎖需要真的 flock；" >&2
  echo " macOS 可用: brew install flock；Amazon Linux 預設已有）" >&2
  exit 0
fi

PASS=0
FAIL=0

pass() { echo "  [PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); }

assert_grep_log() {
  local needle="$1" desc="$2"
  if grep -qF -- "$needle" "$STATE/last_run.log"; then
    pass "$desc"
  else
    fail "$desc — log 裡找不到: $needle"
  fi
}

assert_eq() {
  local actual="$1" expected="$2" desc="$3"
  if [ "$actual" = "$expected" ]; then
    pass "$desc"
  else
    fail "$desc — 實際值：${actual}（預期：${expected}）"
  fi
}

MOCKDIR=$(mktemp -d)
SANDBOX=$(mktemp -d)
STATE=$(mktemp -d)

# ── 假 /etc 樹：pre-switch 狀態固定是 legacy（跟實際部署的預設一致）──────
reset_sandbox() {
  rm -rf "${SANDBOX:?}"/etc
  mkdir -p "$SANDBOX/etc/nginx/trustforge-sites" \
    "$SANDBOX/etc/nginx/conf.d" "$SANDBOX/etc/systemd/system"
  echo "# legacy conf stub" > "$SANDBOX/etc/nginx/trustforge-sites/legacy.conf"
  echo "# react conf stub" > "$SANDBOX/etc/nginx/trustforge-sites/react.conf"
  echo "# react-http conf stub" > "$SANDBOX/etc/nginx/trustforge-sites/react-http.conf"
  # legacy-tls conf stub：跟production一致，deploy/deploy_frontend_nginx.sh
  # 現在會把 deploy/nginx-legacy-tls.conf 也上傳到這個位置（不管憑證存不
  # 存在都會先放著），cutover_switch.sh 偵測到憑證存在時才會真的選它。
  echo "# legacy-tls conf stub" > "$SANDBOX/etc/nginx/trustforge-sites/legacy-tls.conf"
  ln -sfn "$SANDBOX/etc/nginx/trustforge-sites/legacy.conf" \
    "$SANDBOX/etc/nginx/conf.d/trustforge.conf"
  cat > "$SANDBOX/etc/systemd/system/trustforge.service" <<'EOF'
[Service]
Environment=PORT=8080
Environment=TRUSTFORGE_BIND_HOST=127.0.0.1
Environment=TRUSTFORGE_TRUST_PROXY=1
Environment=TRUSTFORGE_CSP_MODE=legacy
EOF
  rm -rf "${STATE:?}"/*
}

# ── codex 複審 HIGH（HSTS-safe legacy 回滾）：cutover_switch.sh 偵測
#    `$ETC/letsencrypt/live/<domain>/fullchain.pem` 是否存在，決定 legacy
#    模式的 CANDIDATE 是 legacy.conf 還是 legacy-tls.conf；這裡模擬「憑證
#    已存在／尚未存在」兩種現況，domain 跟 cutover_switch.sh 的
#    REACT_TLS_DOMAIN 一致 ──────────────────────────────────────────────
CERT_DIR="$SANDBOX/etc/letsencrypt/live/trustforge.hurricanesoft.com.tw"
create_fake_cert() {
  mkdir -p "$CERT_DIR"
  echo "# fake fullchain（測試用，非真憑證）" > "$CERT_DIR/fullchain.pem"
}
remove_fake_cert() {
  rm -rf "${SANDBOX:?}/etc/letsencrypt"
}

# gsed（GNU sed）：這支腳本的 `sed -i` 語法是寫給遠端 Amazon Linux（GNU
# sed）用的，本機 macOS 內建 sed 是 BSD 變體、`-i` 語法不相容，用 homebrew
# 的 gsed 才能忠實重現遠端行為（跟改腳本邏輯無關，純粹是本機測試環境的
# gotcha）。找不到 gsed 就直接跳過整份測試（沒有 false pass 的空間）。
GSED_BIN="$(command -v gsed || true)"
if [ -z "$GSED_BIN" ]; then
  echo "找不到 gsed（GNU sed），無法忠實模擬遠端 Amazon Linux 的 sed -i 行為，跳過本測試。" >&2
  echo "macOS 可用: brew install gnu-sed" >&2
  exit 0
fi
ln -sf "$GSED_BIN" "$MOCKDIR/sed"

# ── mock nginx：分辨「候選驗證」呼叫（-c 指到 tf-cutover-validate 暫存檔）
# 跟「live」呼叫（沒有 -c，測目前 symlink 指到的內容），各自可用環境變數
# 控制失敗與否；「live」呼叫另外用呼叫計數器控制只讓第 N 次失敗（這樣可以
# 驗證回滾流程裡「回滾動作本身的重試」是會成功的，不是整個環境都壞掉）──
cat > "$MOCKDIR/nginx" <<'NGINXEOF'
#!/usr/bin/env bash
STATE_DIR="${MOCK_STATE_DIR:?MOCK_STATE_DIR not set}"
mkdir -p "$STATE_DIR"
is_precheck=0
for a in "$@"; do
  case "$a" in
    *tf-cutover-validate*) is_precheck=1 ;;
  esac
done
if [ "$is_precheck" = "1" ]; then
  if [ "${MOCK_NGINX_PRECHECK_FAIL:-0}" = "1" ]; then
    echo "mock nginx: precheck configured to fail" >&2
    exit 1
  fi
  exit 0
fi
COUNT_FILE="$STATE_DIR/nginx_live_call_count"
N=1
[ -f "$COUNT_FILE" ] && N=$(( $(cat "$COUNT_FILE") + 1 ))
echo "$N" > "$COUNT_FILE"
FAIL_AT="${MOCK_NGINX_LIVE_FAIL_AT:-0}"
if [ "$FAIL_AT" != "0" ] && [ "$N" = "$FAIL_AT" ]; then
  echo "mock nginx: live call #$N configured to fail" >&2
  exit 1
fi
exit 0
NGINXEOF
chmod +x "$MOCKDIR/nginx"

# ── mock systemctl：daemon-reload / restart trustforge / reload nginx
# 各自用呼叫計數器控制只讓第 N 次失敗 ────────────────────────────────────
cat > "$MOCKDIR/systemctl" <<'SYSTEMCTLEOF'
#!/usr/bin/env bash
STATE_DIR="${MOCK_STATE_DIR:?MOCK_STATE_DIR not set}"
mkdir -p "$STATE_DIR"
sub="$1"; shift || true
target="${1:-}"
case "$sub $target" in
  "daemon-reload"*|"daemon-reload")
    COUNT_FILE="$STATE_DIR/systemctl_daemon_reload_count"
    N=1
    [ -f "$COUNT_FILE" ] && N=$(( $(cat "$COUNT_FILE") + 1 ))
    echo "$N" > "$COUNT_FILE"
    FAIL_AT="${MOCK_SYSTEMCTL_DAEMON_RELOAD_FAIL_AT:-0}"
    if [ "$FAIL_AT" != "0" ] && [ "$N" = "$FAIL_AT" ]; then
      echo "mock systemctl: daemon-reload call #$N configured to fail" >&2
      exit 1
    fi
    exit 0 ;;
  "restart trustforge")
    COUNT_FILE="$STATE_DIR/systemctl_restart_count"
    N=1
    [ -f "$COUNT_FILE" ] && N=$(( $(cat "$COUNT_FILE") + 1 ))
    echo "$N" > "$COUNT_FILE"
    FAIL_AT="${MOCK_SYSTEMCTL_RESTART_FAIL_AT:-0}"
    if [ "$FAIL_AT" != "0" ] && [ "$N" = "$FAIL_AT" ]; then
      echo "mock systemctl: restart trustforge call #$N configured to fail" >&2
      exit 1
    fi
    exit 0 ;;
  "reload nginx")
    COUNT_FILE="$STATE_DIR/systemctl_reload_count"
    N=1
    [ -f "$COUNT_FILE" ] && N=$(( $(cat "$COUNT_FILE") + 1 ))
    echo "$N" > "$COUNT_FILE"
    FAIL_AT="${MOCK_SYSTEMCTL_RELOAD_FAIL_AT:-0}"
    if [ "$FAIL_AT" != "0" ] && [ "$N" = "$FAIL_AT" ]; then
      echo "mock systemctl: reload nginx call #$N configured to fail" >&2
      exit 1
    fi
    exit 0 ;;
  *)
    exit 0 ;;
esac
SYSTEMCTLEOF
chmod +x "$MOCKDIR/systemctl"

# ── mock curl：既要模擬 python 直連 /healthz 探測，也要模擬 Step 4b 新加的
# public nginx smoke check——後者會真的檢查 `-D`（header 檔）/ `-o`（body
# 檔）內容（CSP header、React dist 特徵 `<div id="root">`、`/api/health` 的
# JSON body），不是只看 curl 自己的 exit code，所以這隻 mock 除了「成功/
# 失敗」還要能依 URL 產生「看起來合理」的 header/body 內容，且可用專屬環境
# 變數選擇性只讓某一個 public 檢查失敗（模擬 dist 缺失/CSP 沒套用/SPA
# fallback 壞掉/api proxy 壞掉等各自獨立的失效模式），而不會牽動其他檢查
# （否則所有 happy-path 場景都會連帶炸掉）。
#
# react（TLS）模式的 root/analyze/api-health 檢查改打
# `https://trustforge.hurricanesoft.com.tw${path}`（--resolve 到
# 127.0.0.1），跟 react-http 打 `http://127.0.0.1${path}` 分屬不同 case，
# 用同一組 MOCK_CURL_REACT_*/MOCK_CURL_API_HEALTH_* 環境變數控制（同一輪
# 測試只會跑其中一個 mode，不會撞名）。另外 react（TLS）多一段對
# `http://127.0.0.1/` 的 HTTP→HTTPS redirect 檢查（用 `-H
# "X-TF-Cutover-Check: http-redirect"` 當標記，跟 react-http 對同一個 URL
# 的「拿 React 首頁」檢查區分開來，模擬真實 nginx 對非-/healthz 路徑一律
# 301 導去 https 的行為，預設回 301 + 正確 Location，可用專屬環境變數注入
# 「完全沒回應／回 200 不是 301／Location 導錯 domain」三種失效模式，這正
# 是 codex 複審 HIGH 指出的回歸：react smoke check 不能只打 HTTP 就被 mock
# 的假 200 掩蓋過去）──────────────────────────────────────────────────
cat > "$MOCKDIR/curl" <<'CURLEOF'
#!/usr/bin/env bash
if [ "${MOCK_CURL_FAIL:-0}" = "1" ]; then
  exit 1
fi

HDR_FILE=""
BODY_FILE=""
URL=""
IS_REDIRECT_CHECK=0
prev=""
for a in "$@"; do
  case "$prev" in
    -D) HDR_FILE="$a" ;;
    -o) BODY_FILE="$a" ;;
  esac
  if [ "$prev" = "-H" ] && [ "$a" = "X-TF-Cutover-Check: http-redirect" ]; then
    IS_REDIRECT_CHECK=1
  fi
  prev="$a"
done
# URL 一律是最後一個參數（cutover_switch.sh 呼叫 curl 的方式都是這樣）
if [ "$#" -gt 0 ]; then
  URL="${*: -1}"
fi

# react（TLS）mode 的 HTTP(80)→HTTPS redirect 驗證：跟 react-http 對
# http://127.0.0.1/ 的「拿 React 首頁」檢查是同一個 URL，靠上面的
# `-H "X-TF-Cutover-Check: http-redirect"` 標記區分，不能只看 URL。
if [ "$IS_REDIRECT_CHECK" = "1" ] && [ "$URL" = "http://127.0.0.1/" ]; then
  if [ "${MOCK_CURL_REDIRECT_FAIL:-0}" = "1" ]; then
    exit 1
  fi
  if [ -n "$HDR_FILE" ]; then
    if [ "${MOCK_CURL_REDIRECT_NOT_301:-0}" = "1" ]; then
      printf 'HTTP/1.1 200 OK\r\n' > "$HDR_FILE"
    elif [ "${MOCK_CURL_REDIRECT_WRONG_LOCATION:-0}" = "1" ]; then
      printf 'HTTP/1.1 301 Moved Permanently\r\nLocation: https://wrong-domain.example.com/\r\n' > "$HDR_FILE"
    else
      printf 'HTTP/1.1 301 Moved Permanently\r\nLocation: https://trustforge.hurricanesoft.com.tw/\r\n' > "$HDR_FILE"
    fi
  fi
  exit 0
fi

case "$URL" in
  http://127.0.0.1:8080/healthz)
    # python 直連健康檢查（Step 4 原本就有的那顆，跟 Step 4b public 檢查
    # 是兩回事，不用任何 MOCK_CURL_REACT_*/MOCK_CURL_PUBLIC_* 控制）
    exit 0
    ;;
  http://127.0.0.1/healthz)
    # legacy 模式的 public nginx smoke check（驗 nginx→python 全轉發鏈路）
    if [ "${MOCK_CURL_PUBLIC_LEGACY_FAIL:-0}" = "1" ]; then
      exit 1
    fi
    # cutover_switch.sh 加了 retry（撐過 nginx reload 的短暫窗口）之後，
    # 這裡額外支援「前 N 次呼叫失敗、之後成功」的瞬斷模擬（跟上面
    # MOCK_CURL_PUBLIC_LEGACY_FAIL 的「永遠失敗」不同），驗證 retry 真的
    # 生效、不會被暫時性失敗誤觸發 rollback。
    TRANSIENT_N="${MOCK_CURL_PUBLIC_LEGACY_TRANSIENT_FAILS:-0}"
    if [ "$TRANSIENT_N" != "0" ]; then
      COUNT_FILE="${MOCK_STATE_DIR:?MOCK_STATE_DIR not set}/curl_legacy_healthz_call_count"
      N=1
      [ -f "$COUNT_FILE" ] && N=$(( $(cat "$COUNT_FILE") + 1 ))
      echo "$N" > "$COUNT_FILE"
      if [ "$N" -le "$TRANSIENT_N" ]; then
        exit 1
      fi
    fi
    exit 0
    ;;
  https://trustforge.hurricanesoft.com.tw/healthz)
    # codex 複審 HIGH（自動 trap rollback 繞過 legacy-tls 保護）：rollback
    # 還原成 legacy-tls.conf 後的 443 HTTPS SSR 可達性驗證
    if [ "${MOCK_CURL_ROLLBACK_HTTPS_HEALTHZ_FAIL:-0}" = "1" ]; then
      exit 1
    fi
    exit 0
    ;;
  http://127.0.0.1/|http://127.0.0.1/analyze|https://trustforge.hurricanesoft.com.tw/|https://trustforge.hurricanesoft.com.tw/analyze)
    case "$URL" in
      http://127.0.0.1/|https://trustforge.hurricanesoft.com.tw/)
        [ "${MOCK_CURL_REACT_ROOT_FAIL:-0}" = "1" ] && exit 1
        # 同上（legacy /healthz）：支援「前 N 次呼叫失敗、之後成功」的瞬斷
        # 模擬，驗證 _tf_check_react_page 外層包的 retry 真的生效。
        TRANSIENT_N="${MOCK_CURL_REACT_ROOT_TRANSIENT_FAILS:-0}"
        if [ "$TRANSIENT_N" != "0" ]; then
          COUNT_FILE="${MOCK_STATE_DIR:?MOCK_STATE_DIR not set}/curl_react_root_call_count"
          N=1
          [ -f "$COUNT_FILE" ] && N=$(( $(cat "$COUNT_FILE") + 1 ))
          echo "$N" > "$COUNT_FILE"
          if [ "$N" -le "$TRANSIENT_N" ]; then
            exit 1
          fi
        fi
        ;;
      *)
        [ "${MOCK_CURL_REACT_ANALYZE_FAIL:-0}" = "1" ] && exit 1
        ;;
    esac
    if [ -n "$HDR_FILE" ]; then
      if [ "${MOCK_CURL_REACT_NO_CSP:-0}" = "1" ]; then
        printf 'HTTP/1.1 200 OK\r\n' > "$HDR_FILE"
      else
        printf 'HTTP/1.1 200 OK\r\nContent-Security-Policy: default-src '\''self'\''\r\n' > "$HDR_FILE"
      fi
    fi
    if [ -n "$BODY_FILE" ]; then
      if [ "${MOCK_CURL_REACT_BAD_BODY:-0}" = "1" ]; then
        printf '<html><body>not react (dist missing/corrupted)</body></html>' > "$BODY_FILE"
      else
        printf '<!doctype html><html><body><div id="root"></div></body></html>' > "$BODY_FILE"
      fi
    fi
    exit 0
    ;;
  http://127.0.0.1/api/health|https://trustforge.hurricanesoft.com.tw/api/health)
    if [ "${MOCK_CURL_API_HEALTH_FAIL:-0}" = "1" ]; then
      exit 1
    fi
    if [ -n "$BODY_FILE" ]; then
      if [ "${MOCK_CURL_API_HEALTH_BAD_BODY:-0}" = "1" ]; then
        printf '{"unexpected":"payload"}' > "$BODY_FILE"
      else
        printf '{"ok": true, "data": {"status": "ok"}}' > "$BODY_FILE"
      fi
    fi
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
CURLEOF
chmod +x "$MOCKDIR/curl"

# 擷取一次「react」「react-http」cutover 的遠端指令內容（dry-run，不真送
# SSM）──react-http 是 bare-IP（無 domain）現況用的 HTTP-only 版本，candidate
# conf 檔名不同（react-http.conf）但 python 端 CSP_MODE 沿用 react（見
# cutover_switch.sh CSP_MODE_ENV 說明），guarded transaction/rollback 邏輯
# 兩者共用同一段（差別只在 CANDIDATE/目標 CSP_MODE 值），故只需額外驗證
# react-http 的 candidate/CSP_MODE 值正確、happy path + 一個代表性失敗注入
# 觸發回滾即可，不需要重跑 react 那一輪已經覆蓋過的全部失敗分支 ──────────
CMD_REACT=$(TF_CUTOVER_DRY_RUN=1 TRUSTFORGE_CUTOVER_CONFIRMED=yes \
  bash "$REPO_ROOT/deploy/cutover_switch.sh" react)
# react-http 現在預設被擋（codex 複審 HIGH，production 應只走 react(TLS)），
# 這裡是測「react-http cutover 邏輯本身」（candidate/CSP_MODE/guarded
# transaction），故明確帶上例外 override 讓 dry-run 走到底──「不帶 override
# 會被擋」本身另有專門的獨立場景驗證（見下方）
CMD_REACT_HTTP=$(TF_CUTOVER_DRY_RUN=1 TRUSTFORGE_CUTOVER_CONFIRMED=yes \
  TF_ALLOW_INSECURE_HTTP_CUTOVER=yes \
  bash "$REPO_ROOT/deploy/cutover_switch.sh" react-http)
# legacy 模式不需要 TRUSTFORGE_CUTOVER_CONFIRMED（回滾/降級用途，見
# cutover_switch.sh：只有 react/react-http 才會擋在三審+簽核那關）
CMD_LEGACY=$(TF_CUTOVER_DRY_RUN=1 bash "$REPO_ROOT/deploy/cutover_switch.sh" legacy)

active_conf() { basename "$(readlink "$SANDBOX/etc/nginx/conf.d/trustforge.conf")"; }
active_csp() { grep '^Environment=TRUSTFORGE_CSP_MODE=' \
  "$SANDBOX/etc/systemd/system/trustforge.service" | cut -d= -f3; }

# ── 給 legacy 模式的 public smoke check 測試用：legacy 模式的「有意義」
# 情境是從 react 切回 legacy（緊急降級），所以這裡另外準備一個 pre-state=
# react 的 sandbox reset（跟預設 reset_sandbox 的 pre-state=legacy 相反），
# 讓 legacy 的 happy path/rollback 有真的東西可切、可退 ──────────────────
reset_sandbox_react_active() {
  reset_sandbox
  ln -sfn "$SANDBOX/etc/nginx/trustforge-sites/react.conf" \
    "$SANDBOX/etc/nginx/conf.d/trustforge.conf"
  "$GSED_BIN" -i \
    "s|^Environment=TRUSTFORGE_CSP_MODE=.*|Environment=TRUSTFORGE_CSP_MODE=react|" \
    "$SANDBOX/etc/systemd/system/trustforge.service"
}

run_cutover_cmd() {
  # $1 = 要執行的擷取指令內容（CMD_REACT / CMD_REACT_HTTP），其餘 "$@" 是
  # 額外的 MOCK_* 失敗注入環境變數（e.g. MOCK_NGINX_LIVE_FAIL_AT=1）
  #
  # TF_CUTOVER_SMOKE_RETRIES/DELAY 預設在這裡覆寫成小值（2 次、0 秒間隔）：
  # cutover_switch.sh 的 Step 4b public smoke check 現在會 retry（撐過
  # nginx reload 的短暫窗口，見腳本內註解），production 預設 10 次
  # ×2 秒；沙箱測試裡故意注入的失敗是「永遠失敗」，若沿用 production
  # 預設值，每個失敗注入場景都要真的等 ~20 秒退出，整份測試會被拖到
  # 数分鐘——這裡只驗證「重試用盡後仍會觸發既有 rollback」的控制流程，
  # 不需要真的等滿 production 的次數/間隔，故縮小成 2 次、0 秒，個別想
  # 測試「retry 真的生效」的場景可用 "$@" 覆寫回更大的值。
  local cmd="$1"; shift
  set +e
  env PATH="$MOCKDIR:$PATH" MOCK_STATE_DIR="$STATE" \
    TF_CUTOVER_ETC="$SANDBOX/etc" TF_CUTOVER_LOCK="$STATE/tf-cutover.lock" \
    TF_CUTOVER_SMOKE_RETRIES=2 TF_CUTOVER_SMOKE_DELAY=0 "$@" \
    bash -c "$cmd" >"$STATE/last_run.log" 2>&1
  local ec=$?
  set -e
  return $ec
}

run_cutover() {
  # 額外的 MOCK_* 失敗注入環境變數透過 "$@" 傳進來（e.g. MOCK_NGINX_LIVE_FAIL_AT=1）
  run_cutover_cmd "$CMD_REACT" "$@"
}

run_cutover_http() {
  run_cutover_cmd "$CMD_REACT_HTTP" "$@"
}

run_cutover_legacy() {
  run_cutover_cmd "$CMD_LEGACY" "$@"
}

echo "== 場景 1：候選設定驗證（Step 1）失敗 → 完全不動 live symlink/service，非零結束 =="
reset_sandbox
if run_cutover MOCK_NGINX_PRECHECK_FAIL=1; then
  fail "候選驗證失敗時應該非零結束"
else
  pass "候選驗證失敗時非零結束"
fi
assert_grep_log "候選設定驗證失敗" "有印候選設定驗證失敗訊息"
assert_eq "$(active_conf)" "legacy.conf" "live symlink 仍是 legacy.conf（沒被動過）"
assert_eq "$(active_csp)" "legacy" "service file CSP_MODE 仍是 legacy（沒被動過）"

echo "== 場景 2：swap 後 nginx -t（Step 3 收尾驗證）失敗 → 觸發回滾 =="
reset_sandbox
if run_cutover MOCK_NGINX_LIVE_FAIL_AT=1; then
  fail "swap 後 nginx -t 失敗時應該非零結束"
else
  pass "swap 後 nginx -t 失敗時非零結束"
fi
assert_grep_log "已回滾到切換前狀態" "有印回滾完成訊息"
assert_eq "$(active_conf)" "legacy.conf" "回滾後 live symlink 退回 legacy.conf（不留半殘）"
assert_eq "$(active_csp)" "legacy" "回滾後 service file CSP_MODE 退回 legacy（不留半殘）"

echo "== 場景 3：systemctl restart trustforge 失敗 → 觸發回滾 =="
reset_sandbox
if run_cutover MOCK_SYSTEMCTL_RESTART_FAIL_AT=1; then
  fail "restart trustforge 失敗時應該非零結束"
else
  pass "restart trustforge 失敗時非零結束"
fi
assert_grep_log "已回滾到切換前狀態" "有印回滾完成訊息"
assert_eq "$(active_conf)" "legacy.conf" "回滾後 live symlink 退回 legacy.conf（不留半殘）"
assert_eq "$(active_csp)" "legacy" "回滾後 service file CSP_MODE 退回 legacy（不留半殘）"

echo "== 場景 4：systemctl reload nginx 失敗 → 觸發回滾 =="
reset_sandbox
if run_cutover MOCK_SYSTEMCTL_RELOAD_FAIL_AT=1; then
  fail "reload nginx 失敗時應該非零結束"
else
  pass "reload nginx 失敗時非零結束"
fi
assert_grep_log "已回滾到切換前狀態" "有印回滾完成訊息"
assert_eq "$(active_conf)" "legacy.conf" "回滾後 live symlink 退回 legacy.conf（不留半殘）"
assert_eq "$(active_csp)" "legacy" "回滾後 service file CSP_MODE 退回 legacy（不留半殘）"

echo "== 場景 6：rollback 自己的 daemon-reload 失敗 → ROLLBACK-FAILED，非零結束（不謊報成功）=="
reset_sandbox
if run_cutover MOCK_NGINX_LIVE_FAIL_AT=1 MOCK_SYSTEMCTL_DAEMON_RELOAD_FAIL_AT=1; then
  RC_EC=0
else
  RC_EC=$?
fi
assert_eq "$RC_EC" "97" "rollback 自己的 daemon-reload 失敗時非零結束（distinct exit code=97，不是隨便一個非零值）"
assert_grep_log "ROLLBACK-FAILED" "有印 distinct 的 ROLLBACK-FAILED 狀態"
assert_grep_log "rollback：systemctl daemon-reload 失敗" "有印出具體是 daemon-reload 這一步失敗"
assert_grep_log "手動修" "有印手動復原指示（symlink）"
assert_grep_log "systemctl daemon-reload && systemctl restart trustforge" "有印手動復原指示（daemon-reload/restart）"
assert_grep_log "systemctl status nginx" "有印手動復原指示（狀態確認）"
if ! grep -qF "已回滾到切換前狀態且驗證通過" "$STATE/last_run.log"; then
  pass "沒有誤報『已回滾...驗證通過』（daemon-reload 失敗時不能假裝救回來了）"
else
  fail "不該印出『已回滾...驗證通過』（daemon-reload 已失敗卻謊報成功）"
fi

echo "== 場景 7：rollback 自己的 restart trustforge 失敗 → ROLLBACK-FAILED，非零結束 =="
reset_sandbox
if run_cutover MOCK_NGINX_LIVE_FAIL_AT=1 MOCK_SYSTEMCTL_RESTART_FAIL_AT=1; then
  RC_EC=0
else
  RC_EC=$?
fi
assert_eq "$RC_EC" "97" "rollback 自己的 restart trustforge 失敗時非零結束（distinct exit code=97，不是隨便一個非零值）"
assert_grep_log "ROLLBACK-FAILED" "有印 distinct 的 ROLLBACK-FAILED 狀態"
assert_grep_log "rollback：systemctl restart trustforge 失敗" "有印出具體是 restart trustforge 這一步失敗"
if ! grep -qF "已回滾到切換前狀態且驗證通過" "$STATE/last_run.log"; then
  pass "沒有誤報『已回滾...驗證通過』（restart 失敗時不能假裝救回來了）"
else
  fail "不該印出『已回滾...驗證通過』（restart 已失敗卻謊報成功）"
fi

echo "== 場景 8：rollback 自己的 nginx -t 失敗 → ROLLBACK-FAILED，非零結束（不 reload 一個沒過 -t 的設定）=="
reset_sandbox
if run_cutover MOCK_SYSTEMCTL_RESTART_FAIL_AT=1 MOCK_NGINX_LIVE_FAIL_AT=2; then
  RC_EC=0
else
  RC_EC=$?
fi
assert_eq "$RC_EC" "97" "rollback 自己的 nginx -t 失敗時非零結束（distinct exit code=97，不是隨便一個非零值）"
assert_grep_log "ROLLBACK-FAILED" "有印 distinct 的 ROLLBACK-FAILED 狀態"
assert_grep_log "rollback：nginx -t 失敗" "有印出具體是 nginx -t 這一步失敗"
if ! grep -qF "已回滾到切換前狀態且驗證通過" "$STATE/last_run.log"; then
  pass "沒有誤報『已回滾...驗證通過』（rollback 自己的 nginx -t 沒過時不能假裝救回來了）"
else
  fail "不該印出『已回滾...驗證通過』（nginx -t 已失敗卻謊報成功）"
fi

echo "== 場景 9：rollback 自己的 reload nginx 失敗 → ROLLBACK-FAILED，非零結束 =="
reset_sandbox
if run_cutover MOCK_SYSTEMCTL_RESTART_FAIL_AT=1 MOCK_SYSTEMCTL_RELOAD_FAIL_AT=1; then
  RC_EC=0
else
  RC_EC=$?
fi
assert_eq "$RC_EC" "97" "rollback 自己的 reload nginx 失敗時非零結束（distinct exit code=97，不是隨便一個非零值）"
assert_grep_log "ROLLBACK-FAILED" "有印 distinct 的 ROLLBACK-FAILED 狀態"
assert_grep_log "rollback：systemctl reload nginx 失敗" "有印出具體是 reload nginx 這一步失敗"
if ! grep -qF "已回滾到切換前狀態且驗證通過" "$STATE/last_run.log"; then
  pass "沒有誤報『已回滾...驗證通過』（reload nginx 失敗時不能假裝救回來了）"
else
  fail "不該印出『已回滾...驗證通過』（reload nginx 已失敗卻謊報成功）"
fi

echo "== 場景 5：無注入失敗（happy path）→ 真的切到 react、exit 0 =="
reset_sandbox
if run_cutover; then
  pass "無注入失敗時 exit 0"
else
  fail "無注入失敗時仍非零結束"
  cat "$STATE/last_run.log"
fi
assert_grep_log "完成後驗證通過" "有印完成後驗證通過訊息"
assert_eq "$(active_conf)" "react.conf" "happy path 後 live symlink 真的换到 react.conf"
assert_eq "$(active_csp)" "react" "happy path 後 service file CSP_MODE 真的换到 react"
assert_grep_log "public nginx smoke check 通過" "happy path 必須先過 public nginx smoke check（/、/analyze、/api/health）才報成功（codex 五次複審 HIGH）"

echo "== 場景 10：並行呼叫 — 鎖已被持有 → reject，完全不做任何 mutation =="
reset_sandbox
LOCKFILE="$STATE/tf-cutover.lock"
rm -f "$STATE/holder_ready"
(
  exec 9>"$LOCKFILE"
  flock 9
  : > "$STATE/holder_ready"
  sleep 5
) &
HOLDER_PID=$!
for _ in $(seq 1 50); do
  [ -f "$STATE/holder_ready" ] && break
  sleep 0.1
done
if [ ! -f "$STATE/holder_ready" ]; then
  fail "測試設置失敗：背景 holder 沒有在時限內拿到鎖（測試環境問題，非受測程式問題）"
fi
if run_cutover; then
  RC_EC=0
else
  RC_EC=$?
fi
assert_eq "$RC_EC" "98" "鎖被持有時 reject，exit=98（跟一般失敗 exit=1、ROLLBACK-FAILED exit=97 都不同）"
assert_grep_log "另一個 cutover 進行中" "有印 distinct 的『另一個 cutover 進行中』訊息"
assert_eq "$(active_conf)" "legacy.conf" "鎖被持有時完全沒動 live symlink（連候選驗證都沒開始）"
assert_eq "$(active_csp)" "legacy" "鎖被持有時完全沒動 service file CSP_MODE"
kill "$HOLDER_PID" 2>/dev/null || true
wait "$HOLDER_PID" 2>/dev/null || true
rm -f "$LOCKFILE" "$STATE/holder_ready"

echo "== 場景 11：沒有並行衝突時正常取得鎖、執行、exit 0；結束後鎖確實釋放（不卡死下一次呼叫）=="
reset_sandbox
if run_cutover; then
  pass "沒有並行衝突時正常取得鎖、執行、exit 0"
else
  fail "沒有並行衝突時應該正常結束"
  cat "$STATE/last_run.log"
fi
if ( exec 9>"$STATE/tf-cutover.lock"; flock -n 9 ); then
  pass "run_cutover 結束後鎖確實釋放（能被重新取得，不會卡死下一次呼叫）"
else
  fail "run_cutover 結束後鎖沒有釋放（會卡死下一次呼叫，是嚴重的 bug）"
fi

echo "== 場景 12：切換前 live symlink 本來就不存在（absent pre-state，codex 四次複審 HIGH）=="
reset_sandbox
rm -f "$SANDBOX/etc/nginx/conf.d/trustforge.conf"
if run_cutover MOCK_NGINX_LIVE_FAIL_AT=1; then
  fail "absent pre-state 下，swap 後 nginx -t 失敗時應該非零結束"
else
  pass "absent pre-state 下，swap 後 nginx -t 失敗時非零結束（觸發回滾）"
fi
assert_grep_log "已回滾到切換前狀態" "有印回滾完成訊息（absent pre-state 也走完整套回滾流程，不會因為讀不到 symlink 就中途壞掉）"
if [ -e "$SANDBOX/etc/nginx/conf.d/trustforge.conf" ] || [ -L "$SANDBOX/etc/nginx/conf.d/trustforge.conf" ]; then
  fail "回滾後 live symlink 應該還原成不存在（切換前本來就沒有），但目前仍存在（誤還原：把切換時新建的 symlink 留著了）"
else
  pass "回滾後 live symlink 正確還原成不存在（沒有誤留切換時新建的 symlink）"
fi
assert_eq "$(active_csp)" "legacy" "absent pre-state 下 rollback 後 service file CSP_MODE 仍正確退回 legacy"

echo "== 場景 13：切換前 live symlink 本來就不存在，且無失敗注入 → 正常 cutover 成功建立新 symlink =="
reset_sandbox
rm -f "$SANDBOX/etc/nginx/conf.d/trustforge.conf"
if run_cutover; then
  pass "absent pre-state 下，無失敗注入時正常 cutover 成功"
else
  fail "absent pre-state 下，無失敗注入時應該正常成功"
  cat "$STATE/last_run.log"
fi
assert_eq "$(active_conf)" "react.conf" "absent pre-state 下正常 cutover 成功後 live symlink 正確指向 react.conf"

echo "== 場景 14：react-http mode（bare-IP HTTP-only 版）happy path → symlink 指向 react-http.conf，CSP_MODE=react =="
reset_sandbox
if run_cutover_http; then
  pass "react-http 無注入失敗時 exit 0"
else
  fail "react-http 無注入失敗時仍非零結束"
  cat "$STATE/last_run.log"
fi
assert_grep_log "完成後驗證通過" "react-http 有印完成後驗證通過訊息"
assert_eq "$(active_conf)" "react-http.conf" "react-http happy path 後 live symlink 換到 react-http.conf（不是 react.conf）"
assert_eq "$(active_csp)" "react" "react-http happy path 後 service file CSP_MODE 換到 react（沿用 react 的 CSP_MODE 值，不是字面 react-http）"
assert_grep_log "public nginx smoke check 通過" "react-http happy path 必須先過 public nginx smoke check 才報成功"

echo "== 場景 15：react-http mode，swap 後 nginx -t 失敗 → 觸發回滾（guarded transaction 對 react-http 也生效）=="
reset_sandbox
if run_cutover_http MOCK_NGINX_LIVE_FAIL_AT=1; then
  fail "react-http swap 後 nginx -t 失敗時應該非零結束"
else
  pass "react-http swap 後 nginx -t 失敗時非零結束"
fi
assert_grep_log "已回滾到切換前狀態" "react-http 有印回滾完成訊息"
assert_eq "$(active_conf)" "legacy.conf" "react-http 回滾後 live symlink 退回 legacy.conf（不留半殘）"
assert_eq "$(active_csp)" "legacy" "react-http 回滾後 service file CSP_MODE 退回 legacy（不留半殘）"

echo "== 場景 16：react-http mode，並行呼叫 — 鎖已被持有 → reject，完全不做任何 mutation（flock 對 react-http 也生效）=="
reset_sandbox
LOCKFILE="$STATE/tf-cutover.lock"
rm -f "$STATE/holder_ready"
(
  exec 9>"$LOCKFILE"
  flock 9
  : > "$STATE/holder_ready"
  sleep 5
) &
HOLDER_PID=$!
for _ in $(seq 1 50); do
  [ -f "$STATE/holder_ready" ] && break
  sleep 0.1
done
if [ ! -f "$STATE/holder_ready" ]; then
  fail "測試設置失敗：背景 holder 沒有在時限內拿到鎖（測試環境問題，非受測程式問題）"
fi
if run_cutover_http; then
  RC_EC=0
else
  RC_EC=$?
fi
assert_eq "$RC_EC" "98" "react-http 鎖被持有時 reject，exit=98"
assert_grep_log "另一個 cutover 進行中" "react-http 有印 distinct 的『另一個 cutover 進行中』訊息"
assert_eq "$(active_conf)" "legacy.conf" "react-http 鎖被持有時完全沒動 live symlink"
kill "$HOLDER_PID" 2>/dev/null || true
wait "$HOLDER_PID" 2>/dev/null || true
rm -f "$LOCKFILE" "$STATE/holder_ready"

# ── 場景 17-23：Step 4b public nginx smoke check 失敗注入（codex 五次複審，
# HIGH）：即使 candidate 驗證通過、symlink/nginx -t/systemctl 全部成功、
# python 直連 /healthz 也健康，只要 public nginx（port 80）面看到的東西不
# 對（dist 缺失、CSP 沒套用、內容不像 React、SPA fallback 壞、/api/ proxy
# 壞），一律要觸發既有 rollback，不能謊報成功 ──────────────────────────
echo "== 場景 17：react mode，public smoke check：/ 沒有 200 回應（dist 缺失/404）→ 觸發 rollback =="
reset_sandbox
if run_cutover MOCK_CURL_REACT_ROOT_FAIL=1; then
  fail "public / 沒有 200 回應時應該非零結束"
else
  pass "public / 沒有 200 回應時非零結束（觸發 rollback）"
fi
assert_grep_log "public nginx https://trustforge.hurricanesoft.com.tw/（root）沒有 200 回應" "有印出具體是 public HTTPS /（root）沒有 200 回應（React dist 是否缺失）"
assert_grep_log "已回滾到切換前狀態" "有印回滾完成訊息"
assert_eq "$(active_conf)" "legacy.conf" "回滾後 live symlink 退回 legacy.conf（不留半殘）"
assert_eq "$(active_csp)" "legacy" "回滾後 service file CSP_MODE 退回 legacy（不留半殘）"

echo "== 場景 18：react mode，public smoke check：/ 有 200 但沒有 CSP header → 觸發 rollback =="
reset_sandbox
if run_cutover MOCK_CURL_REACT_NO_CSP=1; then
  fail "public / 沒有 CSP header 時應該非零結束"
else
  pass "public / 沒有 CSP header 時非零結束（觸發 rollback）"
fi
assert_grep_log "沒有 CSP header" "有印出具體是 public / 沒有 CSP header（安全 header 沒套用到 React 路由）"
assert_grep_log "已回滾到切換前狀態" "有印回滾完成訊息"
assert_eq "$(active_conf)" "legacy.conf" "回滾後 live symlink 退回 legacy.conf（不留半殘）"

echo "== 場景 19：react mode，public smoke check：/ 回應內容不像 React index.html（無 <div id=\"root\">）→ 觸發 rollback =="
reset_sandbox
if run_cutover MOCK_CURL_REACT_BAD_BODY=1; then
  fail "public / 內容不像 React 時應該非零結束"
else
  pass "public / 內容不像 React 時非零結束（觸發 rollback）"
fi
assert_grep_log "回應內容不像 React index.html" "有印出具體是 public / 內容不像 React index.html（dist 缺失/損毀）"
assert_grep_log "已回滾到切換前狀態" "有印回滾完成訊息"
assert_eq "$(active_conf)" "legacy.conf" "回滾後 live symlink 退回 legacy.conf（不留半殘）"

echo "== 場景 20：react mode，public smoke check：/analyze（SPA fallback）沒有 200 回應 → 觸發 rollback =="
reset_sandbox
if run_cutover MOCK_CURL_REACT_ANALYZE_FAIL=1; then
  fail "public /analyze 沒有 200 回應時應該非零結束"
else
  pass "public /analyze 沒有 200 回應時非零結束（觸發 rollback）"
fi
assert_grep_log "public nginx https://trustforge.hurricanesoft.com.tw/analyze（spa-fallback）沒有 200 回應" "有印出具體是 public HTTPS /analyze SPA fallback 沒有 200 回應"
assert_grep_log "已回滾到切換前狀態" "有印回滾完成訊息"
assert_eq "$(active_conf)" "legacy.conf" "回滾後 live symlink 退回 legacy.conf（不留半殘）"

echo "== 場景 21：react mode，public smoke check：/api/health 沒有 200 回應（nginx /api/ proxy 壞）→ 觸發 rollback =="
reset_sandbox
if run_cutover MOCK_CURL_API_HEALTH_FAIL=1; then
  fail "public /api/health 沒有 200 回應時應該非零結束"
else
  pass "public /api/health 沒有 200 回應時非零結束（觸發 rollback）"
fi
assert_grep_log "public nginx /api/health 沒有 200 回應" "有印出具體是 public /api/health 沒有 200 回應（nginx /api/ proxy 異常）"
assert_grep_log "已回滾到切換前狀態" "有印回滾完成訊息"
assert_eq "$(active_conf)" "legacy.conf" "回滾後 live symlink 退回 legacy.conf（不留半殘）"

echo "== 場景 22：react mode，public smoke check：/api/health 回應 200 但內容不是預期 JSON → 觸發 rollback =="
reset_sandbox
if run_cutover MOCK_CURL_API_HEALTH_BAD_BODY=1; then
  fail "public /api/health 回應內容不對時應該非零結束"
else
  pass "public /api/health 回應內容不對時非零結束（觸發 rollback）"
fi
assert_grep_log "public nginx /api/health 回應內容不是預期 JSON" "有印出具體是 public /api/health 回應內容不是預期 JSON"
assert_grep_log "已回滾到切換前狀態" "有印回滾完成訊息"
assert_eq "$(active_conf)" "legacy.conf" "回滾後 live symlink 退回 legacy.conf（不留半殘）"

# ── 場景 22a-22d：react（TLS）mode 的 HTTP→HTTPS redirect 回歸測試（codex
# 複審 HIGH：react smoke check 以前直接打 http://127.0.0.1/，而 TLS
# nginx.conf 把 80 port 除 /healthz 外全部 301 導去 https，curl -f 不把 3xx
# 當失敗，所以會拿到「200 假象」被掩蓋——這裡的 mock 刻意讓 http://127.0.0.1
# 真的回 301（不是 200），藉此證明：(a) react smoke check 現在改走 HTTPS
# 才能通過 happy path、(b) HTTP(80) 對 / 有正確回 301+Location 也會被獨立
# 驗到、(c) redirect 相關的三種失效模式（無回應／不是 301／Location 導錯
# domain）都會觸發 rollback，不會被誤判成功 ──────────────────────────────
echo "== 場景 22a：react mode，happy path 下 HTTP(80) 對 / 真的回 301（不是 200）→ smoke check 仍需走 HTTPS 才能通過 =="
reset_sandbox
if run_cutover; then
  pass "HTTP(80) 對 / 回 301 時（真實 redirect 行為），react smoke check 改走 HTTPS 仍能正常通過"
else
  fail "HTTP(80) 對 / 回 301（真實 redirect 行為）時，react smoke check 不應該失敗（代表還在打 HTTP 被 301 卡住）"
  cat "$STATE/last_run.log"
fi
assert_grep_log "public nginx smoke check 通過" "react happy path（HTTP 對 / 是真 301）也必須先過 public nginx smoke check 才報成功"
assert_grep_log "HTTP→HTTPS redirect 也正常" "有印 HTTP→HTTPS redirect 驗證通過訊息"
assert_eq "$(active_conf)" "react.conf" "happy path 後 live symlink 真的换到 react.conf"

echo "== 場景 22b：react mode，public smoke check：HTTP(80) 對 / 完全沒有回應 → 觸發 rollback =="
reset_sandbox
if run_cutover MOCK_CURL_REDIRECT_FAIL=1; then
  fail "HTTP(80) 對 / 沒有回應時應該非零結束"
else
  pass "HTTP(80) 對 / 沒有回應時非零結束（觸發 rollback）"
fi
assert_grep_log "public nginx port 80 對 / 沒有回應" "有印出具體是 HTTP(80) 對 / 沒有回應（HTTP→HTTPS redirect 是否正常）"
assert_grep_log "已回滾到切換前狀態" "有印回滾完成訊息"
assert_eq "$(active_conf)" "legacy.conf" "回滾後 live symlink 退回 legacy.conf（不留半殘）"

echo "== 場景 22c：react mode，public smoke check：HTTP(80) 對 / 回 200（不是 301）→ 觸發 rollback（正是 codex 複審抓到的那個回歸：以前這裡被 mock 的假 200 掩蓋過）=="
reset_sandbox
if run_cutover MOCK_CURL_REDIRECT_NOT_301=1; then
  fail "HTTP(80) 對 / 回 200（不是 301）時應該非零結束"
else
  pass "HTTP(80) 對 / 回 200（不是 301）時非零結束（觸發 rollback，不再被假 200 掩蓋）"
fi
assert_grep_log "public nginx port 80 對 / 沒有回 301" "有印出具體是 HTTP(80) 對 / 沒有回 301"
assert_grep_log "已回滾到切換前狀態" "有印回滾完成訊息"
assert_eq "$(active_conf)" "legacy.conf" "回滾後 live symlink 退回 legacy.conf（不留半殘）"

echo "== 場景 22d：react mode，public smoke check：HTTP(80) 對 / 回 301 但 Location 導錯 domain → 觸發 rollback =="
reset_sandbox
if run_cutover MOCK_CURL_REDIRECT_WRONG_LOCATION=1; then
  fail "HTTP(80) redirect Location 導錯 domain 時應該非零結束"
else
  pass "HTTP(80) redirect Location 導錯 domain 時非零結束（觸發 rollback）"
fi
assert_grep_log "redirect Location 不是預期的 https://trustforge.hurricanesoft.com.tw" "有印出具體是 redirect Location 導錯 domain"
assert_grep_log "已回滾到切換前狀態" "有印回滾完成訊息"
assert_eq "$(active_conf)" "legacy.conf" "回滾後 live symlink 退回 legacy.conf（不留半殘）"

echo "== 場景 23：react-http mode，public smoke check：/ 沒有 200 回應（dist 缺失）→ 觸發 rollback（確認 react-http 也走同一套）=="
reset_sandbox
if run_cutover_http MOCK_CURL_REACT_ROOT_FAIL=1; then
  fail "react-http public / 沒有 200 回應時應該非零結束"
else
  pass "react-http public / 沒有 200 回應時非零結束（觸發 rollback）"
fi
assert_grep_log "已回滾到切換前狀態" "react-http 有印回滾完成訊息"
assert_eq "$(active_conf)" "legacy.conf" "react-http 回滾後 live symlink 退回 legacy.conf（不留半殘）"
assert_eq "$(active_csp)" "legacy" "react-http 回滾後 service file CSP_MODE 退回 legacy（不留半殘）"

echo "== 場景 24：legacy mode（從 react 降級），public smoke check：public /healthz（經 nginx）沒有回應 → 觸發 rollback、退回 react =="
reset_sandbox_react_active
if run_cutover_legacy MOCK_CURL_PUBLIC_LEGACY_FAIL=1; then
  fail "legacy public /healthz 沒有回應時應該非零結束"
else
  pass "legacy public /healthz 沒有回應時非零結束（觸發 rollback）"
fi
assert_grep_log "public nginx（port 80）/healthz 沒有回應" "有印出具體是 legacy public /healthz（經 nginx）沒有回應"
assert_grep_log "已回滾到切換前狀態" "有印回滾完成訊息"
assert_eq "$(active_conf)" "react.conf" "legacy 切換失敗時，回滾正確退回切換前的 react.conf（不是誤退回 legacy.conf）"
assert_eq "$(active_csp)" "react" "legacy 切換失敗時，回滾正確退回切換前的 CSP_MODE=react"

echo "== 場景 25：legacy mode（從 react 降級），無失敗注入 → 正常切回 legacy，且必須先過 public smoke check 才報成功 =="
reset_sandbox_react_active
if run_cutover_legacy; then
  pass "legacy 無注入失敗時 exit 0"
else
  fail "legacy 無注入失敗時仍非零結束"
  cat "$STATE/last_run.log"
fi
assert_eq "$(active_conf)" "legacy.conf" "legacy happy path 後 live symlink 真的换到 legacy.conf"
assert_eq "$(active_csp)" "legacy" "legacy happy path 後 service file CSP_MODE 真的换到 legacy"
assert_grep_log "public nginx smoke check 通過" "legacy happy path 必須先過 public nginx smoke check（/healthz 經 nginx）才報成功"

# ── 場景 24a-24b：Step 4b public smoke check 加了 retry（撐過 nginx reload
# 的短暫窗口）之後——mock 前幾次 curl 失敗、之後成功，斷言 retry 真的生效、
# 最終仍走完整套 happy path（不誤觸發 rollback）。分別驗證「直接 curl 檢查」
# （legacy /healthz）跟「_tf_check_react_page 函式包 retry」（react root）
# 兩種寫法都正確 ──────────────────────────────────────────────────────
echo "== 場景 24a：legacy mode，public /healthz 前 2 次瞬斷失敗、第 3 次成功 → retry 生效，最終正常切換（不誤 rollback）=="
reset_sandbox_react_active
if run_cutover_legacy MOCK_CURL_PUBLIC_LEGACY_TRANSIENT_FAILS=2 TF_CUTOVER_SMOKE_RETRIES=5 TF_CUTOVER_SMOKE_DELAY=0; then
  pass "legacy public /healthz 前 2 次瞬斷失敗、retry 撐過後仍 exit 0"
else
  fail "legacy public /healthz 瞬斷失敗應該被 retry 撐過、不該非零結束"
  cat "$STATE/last_run.log"
fi
assert_eq "$(active_conf)" "legacy.conf" "retry 生效後正常切到 legacy.conf（沒有誤觸發 rollback）"
assert_eq "$(active_csp)" "legacy" "retry 生效後 CSP_MODE 正常切到 legacy（沒有誤觸發 rollback）"
assert_grep_log "public nginx smoke check 通過" "retry 撐過瞬斷失敗後，仍必須印出 smoke check 通過（真的驗證過，不是跳過）"

echo "== 場景 24b：react mode，public /（root）前 2 次瞬斷失敗、第 3 次成功 → _tf_check_react_page 外層的 retry 生效，最終正常 cutover（不誤 rollback）=="
reset_sandbox
if run_cutover MOCK_CURL_REACT_ROOT_TRANSIENT_FAILS=2 TF_CUTOVER_SMOKE_RETRIES=5 TF_CUTOVER_SMOKE_DELAY=0; then
  pass "react public /（root）前 2 次瞬斷失敗、retry 撐過後仍 exit 0"
else
  fail "react public /（root）瞬斷失敗應該被 retry 撐過、不該非零結束"
  cat "$STATE/last_run.log"
fi
assert_eq "$(active_conf)" "react.conf" "retry 生效後正常切到 react.conf（沒有誤觸發 rollback）"
assert_eq "$(active_csp)" "react" "retry 生效後 CSP_MODE 正常切到 react（沒有誤觸發 rollback）"
assert_grep_log "public nginx smoke check 通過" "retry 撐過瞬斷失敗後，仍必須印出 smoke check 通過（真的驗證過，不是跳過）"

echo "== 場景 26：react-http mode，沒有 TF_ALLOW_INSECURE_HTTP_CUTOVER=yes → 直接拒絕、非零結束、完全不 mutate（codex 複審 HIGH：production 不該用 react-http，只有 react(TLS) 才是 production 路徑）=="
reset_sandbox
set +e
env PATH="$MOCKDIR:$PATH" MOCK_STATE_DIR="$STATE" \
  TF_CUTOVER_ETC="$SANDBOX/etc" TF_CUTOVER_LOCK="$STATE/tf-cutover.lock" \
  TRUSTFORGE_CUTOVER_CONFIRMED=yes \
  bash "$REPO_ROOT/deploy/cutover_switch.sh" react-http >"$STATE/last_run.log" 2>&1
RC_EC=$?
set -e
if [ "$RC_EC" -ne 0 ]; then
  pass "react-http 沒有 override 時非零結束（exit=${RC_EC}）"
else
  fail "react-http 沒有 override 時應該非零結束，卻 exit 0"
fi
assert_grep_log "react-http（純 HTTP、無 TLS）預設禁止" "有印出 react-http 預設禁止用於 production 的明確訊息"
assert_grep_log "TF_ALLOW_INSECURE_HTTP_CUTOVER=yes" "有印出正確的 override 用法提示（例外路徑怎麼用）"
assert_eq "$(active_conf)" "legacy.conf" "react-http 被擋時完全沒動 live symlink（連候選驗證都沒開始）"
assert_eq "$(active_csp)" "legacy" "react-http 被擋時完全沒動 service file CSP_MODE"

echo "== 場景 27：react-http mode，帶 TF_ALLOW_INSECURE_HTTP_CUTOVER=yes → 這道 gate 本身不擋，直接呼叫腳本（dry-run，不真送 SSM）驗證沒有印出拒絕訊息、正常往下產生遠端指令 =="
reset_sandbox
set +e
env TRUSTFORGE_CUTOVER_CONFIRMED=yes TF_ALLOW_INSECURE_HTTP_CUTOVER=yes \
  TF_CUTOVER_DRY_RUN=1 \
  bash "$REPO_ROOT/deploy/cutover_switch.sh" react-http >"$STATE/last_run.log" 2>&1
RC_EC=$?
set -e
if [ "$RC_EC" -eq 0 ]; then
  pass "react-http 帶 override 時（dry-run）正常結束（exit 0），沒被自己的 gate 擋下"
else
  fail "react-http 帶 override 時（dry-run）應該正常結束，卻非零結束"
  cat "$STATE/last_run.log"
fi
if grep -qF -- "react-http（純 HTTP、無 TLS）預設禁止" "$STATE/last_run.log"; then
  fail "react-http 帶 override 時不應該印出拒絕訊息，但印出了"
else
  pass "react-http 帶 override 時沒有印出拒絕訊息（gate 正確放行例外路徑）"
fi
assert_grep_log "react-http.conf" "react-http 帶 override 時（dry-run）遠端指令內容正確含 react-http.conf candidate（沒被自己的 gate 誤擋，跟前面擷取 CMD_REACT_HTTP 的路徑一致）"

echo "== 場景 28：react（TLS）mode 與 legacy mode 完全不受 react-http 專屬的 TF_ALLOW_INSECURE_HTTP_CUTOVER 關卡影響（沒設定這個變數也應正常成功）=="
reset_sandbox
if run_cutover; then
  pass "react（TLS）mode 沒設 TF_ALLOW_INSECURE_HTTP_CUTOVER 時仍正常成功（這道關卡只針對 react-http）"
else
  fail "react（TLS）mode 不該被 react-http 專屬的新關卡擋到"
  cat "$STATE/last_run.log"
fi
assert_eq "$(active_conf)" "react.conf" "react（TLS）mode cutover 成功，不受 react-http 專屬關卡影響"

reset_sandbox
if run_cutover_legacy; then
  pass "legacy mode 沒設 TF_ALLOW_INSECURE_HTTP_CUTOVER 時仍正常成功（這道關卡只針對 react-http）"
else
  fail "legacy mode 不該被 react-http 專屬的新關卡擋到"
  cat "$STATE/last_run.log"
fi
assert_eq "$(active_conf)" "legacy.conf" "legacy mode cutover 成功，不受 react-http 專屬關卡影響"

echo "== 場景 29：legacy mode，憑證尚未存在（pre-cert ACME bootstrap 現況）→ 維持 HTTP-only legacy.conf，行為不變（codex 複審 HIGH：HSTS-safe rollback，見 nginx-legacy-tls.conf）=="
reset_sandbox
remove_fake_cert
if run_cutover_legacy; then
  pass "legacy mode 無憑證時正常成功"
else
  fail "legacy mode 無憑證時應該正常成功"
  cat "$STATE/last_run.log"
fi
assert_eq "$(active_conf)" "legacy.conf" "無憑證時 legacy 回滾維持 HTTP-only legacy.conf（pre-cert 現況，行為不變）"
assert_grep_log "尚未偵測到憑證" "有印出尚未偵測到憑證、維持 HTTP-only legacy.conf 的訊息"

echo "== 場景 30：legacy mode，憑證已存在 → 改用 legacy-tls.conf（443 HTTPS 服務 SSR，保留 HSTS，避免破壞回訪過 HTTPS+HSTS 的使用者的回滾）（codex 複審 HIGH）=="
reset_sandbox
create_fake_cert
if run_cutover_legacy; then
  pass "legacy mode 憑證已存在時正常成功"
else
  fail "legacy mode 憑證已存在時應該正常成功"
  cat "$STATE/last_run.log"
fi
assert_eq "$(active_conf)" "legacy-tls.conf" "憑證已存在時 legacy 回滾改用 legacy-tls.conf（443 HTTPS，保留 HSTS）"
assert_grep_log "偵測到憑證已存在" "有印出偵測到憑證已存在、改用 legacy-tls.conf 的訊息"
remove_fake_cert

echo "== 場景 31：react（TLS）／react-http mode 不受 legacy 專屬的憑證偵測邏輯影響（就算憑證存在，candidate 仍是 react.conf／react-http.conf，不會被誤導去 legacy-tls.conf）=="
reset_sandbox
create_fake_cert
if run_cutover; then
  pass "react（TLS）mode 憑證存在時仍正常成功"
else
  fail "react（TLS）mode 不該被 legacy 專屬的憑證偵測邏輯影響"
  cat "$STATE/last_run.log"
fi
assert_eq "$(active_conf)" "react.conf" "react（TLS）mode candidate 不受 legacy 憑證偵測邏輯影響，仍是 react.conf"

reset_sandbox
create_fake_cert
if run_cutover_http; then
  pass "react-http mode 憑證存在時仍正常成功"
else
  fail "react-http mode 不該被 legacy 專屬的憑證偵測邏輯影響"
  cat "$STATE/last_run.log"
fi
assert_eq "$(active_conf)" "react-http.conf" "react-http mode candidate 不受 legacy 憑證偵測邏輯影響，仍是 react-http.conf"
remove_fake_cert

# ── 場景 32-34：codex 複審 HIGH（自動 trap rollback 繞過 legacy-tls 保護）
# ── react cutover 在 nginx 已經 reload 成功之後、public smoke check 清掉
# ERR trap 之前失敗，觸發的是自動 ERR trap rollback（不是 explicit
# `cutover_switch.sh legacy`）；切換前 pre-state 是 legacy.conf
# （reset_sandbox 預設值）。若憑證已存在，rollback 不能只照抄 PREV_LINK
# 逐字還原回 legacy.conf（HTTP-only）——那段短暫窗口內已經吃到 react HSTS
# 的使用者回滾後會連不上，必須跟 explicit legacy 用同一套憑證偵測，改還原
# 成 legacy-tls.conf（443 服務、保留 HSTS），且要真的驗證 443 可達 ──────
echo "== 場景 32：react mode 失敗自動 rollback，憑證已存在 → 還原目標是 legacy-tls.conf（不是 legacy.conf），443 HTTPS SSR 可達性驗證通過，rollback 成功（不是 ROLLBACK-FAILED）（codex 複審 HIGH：自動 trap rollback 繞過 legacy-tls 保護）=="
reset_sandbox
create_fake_cert
if run_cutover MOCK_CURL_REACT_ROOT_FAIL=1; then
  fail "public / 沒有 200 回應時應該非零結束"
else
  pass "public / 沒有 200 回應時非零結束（觸發 rollback）"
fi
assert_grep_log "還原目標從 legacy.conf 改成 legacy-tls.conf" "有印出 rollback 偵測到憑證存在、改用 legacy-tls.conf 的訊息"
assert_grep_log "已回滾到切換前狀態" "有印回滾完成訊息（不是 ROLLBACK-FAILED）"
if grep -qF -- "ROLLBACK-FAILED" "$STATE/last_run.log"; then
  fail "憑證存在、HTTPS 驗證正常時不該印出 ROLLBACK-FAILED"
else
  pass "沒有印出 ROLLBACK-FAILED（自動 rollback 真的成功了，不是誤判）"
fi
assert_eq "$(active_conf)" "legacy-tls.conf" "自動 rollback 後 live symlink 是 legacy-tls.conf（443 服務，不是 HTTP-only legacy.conf）"
assert_eq "$(active_csp)" "legacy" "自動 rollback 後 CSP_MODE 仍是 legacy（legacy-tls.conf 拓樸跟 legacy.conf 一致，只是多包 TLS）"
remove_fake_cert

echo "== 場景 33：react mode 失敗自動 rollback，憑證存在但還原後 443 HTTPS SSR 驗證本身失敗 → ROLLBACK-FAILED（exit=97），不能謊報回滾成功（codex 複審 HIGH）=="
reset_sandbox
create_fake_cert
if run_cutover MOCK_CURL_REACT_ROOT_FAIL=1 MOCK_CURL_ROLLBACK_HTTPS_HEALTHZ_FAIL=1; then
  fail "443 HTTPS 驗證失敗時應該非零結束"
else
  pass "443 HTTPS 驗證失敗時非零結束"
fi
assert_grep_log "還原成 legacy-tls.conf 後" "有印出還原成 legacy-tls.conf 後 443 沒有回應的具體錯誤訊息"
assert_grep_log "ROLLBACK-FAILED" "有印出 ROLLBACK-FAILED（不能假裝救回來了——symlink 雖然指對，443 實際打不通）"
remove_fake_cert

echo "== 場景 34：react mode 失敗自動 rollback，憑證尚未存在（pre-cert 現況）→ 還原目標仍是 legacy.conf，行為不變（既有 17-23 已涵蓋，這裡再次明確標註跟場景 32 對照）=="
reset_sandbox
remove_fake_cert
if run_cutover MOCK_CURL_REACT_ROOT_FAIL=1; then
  fail "public / 沒有 200 回應時應該非零結束"
else
  pass "無憑證時 public / 沒有 200 回應時非零結束（觸發 rollback）"
fi
assert_eq "$(active_conf)" "legacy.conf" "無憑證時自動 rollback 還原目標仍是 HTTP-only legacy.conf，行為不變"
if grep -qF -- "還原目標從 legacy.conf 改成 legacy-tls.conf" "$STATE/last_run.log"; then
  fail "無憑證時不該印出改用 legacy-tls.conf 的訊息"
else
  pass "無憑證時沒有印出改用 legacy-tls.conf 的訊息（cert-aware 邏輯正確地沒有誤觸發）"
fi

rm -rf "$MOCKDIR" "$SANDBOX" "$STATE"

echo
echo "== 結果：$PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ]
