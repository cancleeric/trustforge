#!/usr/bin/env bash
# deploy_frontend_nginx.sh 的 SSM CMDS（bootstrap 遠端腳本）guarded-transaction
# 測試（禁真 AWS/SSM、禁真 dnf/nginx/systemctl）：用 `TF_BOOTSTRAP_DRY_RUN=1`
# 把腳本組好、原本要送 SSM 執行的那段遠端 shell 擷取出來，塞進一個**沙箱化
# 的假 /etc + /opt 樹**（`TF_BOOTSTRAP_ETC`/`TF_BOOTSTRAP_OPT` 覆蓋）搭配假
# `dnf`/`unzip`/`aws`(s3 cp)/`nginx`/`systemctl`/`curl` 二進位，真的執行那段
# 內容（不是只 grep 字串），驗證 codex 五次複審 HIGH 指出的問題已修好：
#
#   1. 候選設定驗證失敗（Step 2，`nginx -t -c` 對 scratch harness）→ 完全
#      不動 live symlink/service file，非零結束（還沒開始 mutation，理論上
#      不需要也不會觸發回滾）。
#   2. dnf install 失敗（Step 1，比 Step 2 更早）→ 同上，非零結束、無 mutation。
#   3-8. 在「narrow python + 起 nginx」這段（Step 4，唯一會動 live 狀態的
#      區段）逐一注入失敗：daemon-reload、restart trustforge、swap 後
#      nginx -t、enable --now nginx、reload nginx（且 fallback 的 restart
#      nginx 也失敗）→ 全部斷言觸發 ROLLBACK，trustforge.service／live
#      symlink／default.conf／nginx 服務狀態全部退回跑前值，不留半殘
#      （不會停在「python 已收窄、nginx 沒起來」的 outage）。
#   9. reload nginx 失敗但 fallback 的 restart nginx 成功 → **不**觸發
#      回滾（`reload || restart` 只要其中一個成功就算過）。
#   10. public-path 驗證失敗（Step 5 post-switch，`curl http://localhost/healthz`
#      走 nginx）→ 觸發回滾（codex 五次複審點名的「public-path 驗證失敗」
#      情境）。
#   11. 無注入失敗（happy path）→ symlink 指向 candidate legacy.conf、
#      service file 收斂完成（PORT=8080 等）、nginx active+enabled、exit 0。
#   12. rollback 自己的 daemon-reload／restart trustforge／nginx reload
#      （跑前 nginx 本來就在跑的情境）／healthz 驗證，各自注入失敗 →
#      斷言印出 distinct 的 `ROLLBACK-FAILED`（exit 97），不是誤導性的
#      「已回滾」訊息（比照 cutover_switch.sh 的回滾誠實性設計）。
#   13. absent 前置狀態（live symlink 本來不存在、default.conf 本來不存在，
#      對應剛跑完 deploy_ec2.sh、第一次跑本腳本的真實情境）+ 無失敗注入
#      → 正常成功，且 rollback 分支（若觸發）能正確處理「原本無」。
#
# 依賴：真的 gsed（GNU sed；本腳本的 `sed -i` 語法是寫給遠端 Amazon Linux
# 用的，BSD sed 不相容），找不到就跳過整份測試（不是假裝測試通過）。
#
# 用法：bash deploy/test_deploy_frontend_nginx_transaction.sh
set -euo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

GSED_BIN="$(command -v gsed || true)"
if [ -z "$GSED_BIN" ]; then
  echo "找不到 gsed（GNU sed），無法忠實模擬遠端 Amazon Linux 的 sed -i 行為，跳過本測試。" >&2
  echo "macOS 可用: brew install gnu-sed" >&2
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
CAPTURE=$(mktemp -d)

ln -sf "$GSED_BIN" "$MOCKDIR/sed"

# ── 假 /etc + /opt 樹：預設模擬「deploy_ec2.sh 剛跑完、第一次跑本腳本」的
# 跑前狀態——trustforge 已在跑（PORT=80，尚未收斂）、nginx 尚未安裝/啟用、
# 沒有 live symlink、有 default.conf（AL2023 nginx 套件內建的那份）──────────
reset_sandbox() {
  rm -rf "${SANDBOX:?}"/etc "${SANDBOX:?}"/opt
  mkdir -p "$SANDBOX/etc/nginx/trustforge-sites" \
    "$SANDBOX/etc/nginx/conf.d" "$SANDBOX/etc/systemd/system" \
    "$SANDBOX/opt/trustforge/frontend"
  echo "# AL2023 nginx 內建 default.conf stub" > "$SANDBOX/etc/nginx/conf.d/default.conf"
  cat > "$SANDBOX/etc/systemd/system/trustforge.service" <<'EOF'
[Service]
Environment=PORT=80
EOF
  rm -rf "${STATE:?}"/*
  echo active > "$STATE/svc_trustforge_active"
  echo inactive > "$STATE/svc_nginx_active"
  echo disabled > "$STATE/svc_nginx_enabled"
}

# ── mock dnf：純安裝，可注入失敗（Step 1，還沒開始 mutation）──────────────
cat > "$MOCKDIR/dnf" <<'DNFEOF'
#!/usr/bin/env bash
if [ "${MOCK_DNF_FAIL:-0}" = "1" ]; then
  echo "mock dnf: configured to fail" >&2
  exit 1
fi
exit 0
DNFEOF
chmod +x "$MOCKDIR/dnf"

# ── mock unzip：不解真的 zip，只在 -d 指定的目錄造一個 stub 檔；可用
#    MOCK_UNZIP_FAIL=1 模擬下載到一半損毀/中斷（codex 複審 HIGH：驗證
#    這種中斷完全不該動到現在活著的 frontend/current）────────────────────
cat > "$MOCKDIR/unzip" <<'UNZIPEOF'
#!/usr/bin/env bash
if [ "${MOCK_UNZIP_FAIL:-0}" = "1" ]; then
  echo "mock unzip: configured to fail（模擬下載/解壓中斷、zip 損毀）" >&2
  exit 1
fi
dir=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-d" ]; then dir="$a"; fi
  prev="$a"
done
[ -n "$dir" ] && mkdir -p "$dir" && echo stub > "$dir/index.html"
exit 0
UNZIPEOF
chmod +x "$MOCKDIR/unzip"

# ── mock aws：只需要支撐 CMDS 內文的 `aws s3 cp`（下載 conf/dist 到 staging
# 位置），不需要 ssm——因為我們用 TF_BOOTSTRAP_DRY_RUN 直接擷取遠端腳本
# 內容，重播時只會執行 CMDS 本身的內容，不會再走一次外層的 SSM 呼叫 ───────
cat > "$MOCKDIR/aws" <<'AWSEOF'
#!/usr/bin/env bash
case "$1 $2" in
  "s3 cp")
    dest="$4"
    mkdir -p "$(dirname "$dest")" 2>/dev/null || true
    echo stub > "$dest"
    exit 0 ;;
  *) exit 0 ;;
esac
AWSEOF
chmod +x "$MOCKDIR/aws"

# ── mock nginx：分辨「候選驗證」呼叫（-c 指到 tf-bootstrap-validate 暫存
# 檔）跟「live」呼叫（swap 後的 `nginx -t`／rollback 裡跑前 nginx 本來在跑
# 才會走到的 `nginx -t`），live 呼叫用共用計數器控制只讓第 N 次失敗（forward
# 跟 rollback 共用同一個計數器，這樣才能驗證「回滾動作本身的重試」）────────
cat > "$MOCKDIR/nginx" <<'NGINXEOF'
#!/usr/bin/env bash
STATE_DIR="${MOCK_STATE_DIR:?MOCK_STATE_DIR not set}"
mkdir -p "$STATE_DIR"
is_precheck=0
for a in "$@"; do
  case "$a" in *tf-bootstrap-validate*) is_precheck=1 ;; esac
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

# ── mock systemctl：stateful（is-active/is-enabled 反映真的狀態轉移）+
# 各動作獨立的失敗注入計數器 ─────────────────────────────────────────────
cat > "$MOCKDIR/systemctl" <<'SYSTEMCTLEOF'
#!/usr/bin/env bash
STATE_DIR="${MOCK_STATE_DIR:?MOCK_STATE_DIR not set}"
mkdir -p "$STATE_DIR"
ALL="$*"

get_state() { local f="$STATE_DIR/$1"; if [ -f "$f" ]; then cat "$f"; else printf '%s' "$2"; fi; }
set_state() { printf '%s' "$2" > "$STATE_DIR/$1"; }
bump_count() {
  local f="$STATE_DIR/$1" n=1
  [ -f "$f" ] && n=$(( $(cat "$f") + 1 ))
  echo "$n" > "$f"
  printf '%s' "$n"
}

case "$ALL" in
  "is-active trustforge")
    val="$(get_state svc_trustforge_active inactive)"
    echo "$val"
    [ "$val" = "active" ] && exit 0 || exit 3 ;;
  "is-active nginx")
    val="$(get_state svc_nginx_active inactive)"
    echo "$val"
    [ "$val" = "active" ] && exit 0 || exit 3 ;;
  "is-enabled nginx")
    val="$(get_state svc_nginx_enabled disabled)"
    echo "$val"
    [ "$val" = "enabled" ] && exit 0 || exit 1 ;;
  "daemon-reload")
    n=$(bump_count systemctl_daemon_reload_count)
    fa="${MOCK_SYSTEMCTL_DAEMON_RELOAD_FAIL_AT:-0}"
    if [ "$fa" != "0" ] && [ "$n" = "$fa" ]; then
      echo "mock systemctl: daemon-reload call #$n configured to fail" >&2
      exit 1
    fi
    exit 0 ;;
  "restart trustforge")
    n=$(bump_count systemctl_restart_trustforge_count)
    fa="${MOCK_SYSTEMCTL_RESTART_TRUSTFORGE_FAIL_AT:-0}"
    if [ "$fa" != "0" ] && [ "$n" = "$fa" ]; then
      echo "mock systemctl: restart trustforge call #$n configured to fail" >&2
      exit 1
    fi
    set_state svc_trustforge_active active
    exit 0 ;;
  "stop trustforge")
    set_state svc_trustforge_active inactive
    exit 0 ;;
  "enable --now nginx")
    n=$(bump_count systemctl_enable_now_nginx_count)
    fa="${MOCK_SYSTEMCTL_ENABLE_NOW_FAIL_AT:-0}"
    if [ "$fa" != "0" ] && [ "$n" = "$fa" ]; then
      echo "mock systemctl: enable --now nginx call #$n configured to fail" >&2
      exit 1
    fi
    set_state svc_nginx_active active
    set_state svc_nginx_enabled enabled
    exit 0 ;;
  "reload nginx")
    n=$(bump_count systemctl_reload_nginx_count)
    fa="${MOCK_SYSTEMCTL_RELOAD_NGINX_FAIL_AT:-0}"
    if [ "$fa" != "0" ] && [ "$n" = "$fa" ]; then
      echo "mock systemctl: reload nginx call #$n configured to fail" >&2
      exit 1
    fi
    exit 0 ;;
  "restart nginx")
    n=$(bump_count systemctl_restart_nginx_count)
    fa="${MOCK_SYSTEMCTL_RESTART_NGINX_FAIL_AT:-0}"
    if [ "$fa" != "0" ] && [ "$n" = "$fa" ]; then
      echo "mock systemctl: restart nginx call #$n configured to fail" >&2
      exit 1
    fi
    set_state svc_nginx_active active
    exit 0 ;;
  "stop nginx")
    set_state svc_nginx_active inactive
    exit 0 ;;
  "disable nginx")
    set_state svc_nginx_enabled disabled
    exit 0 ;;
  *)
    exit 0 ;;
esac
SYSTEMCTLEOF
chmod +x "$MOCKDIR/systemctl"

# ── mock curl：依 URL 分辨「public path（HTTP，走 nginx port 80）」、
# 「public path（HTTPS --resolve，react/legacy-tls 拓樸）」跟「rollback
# 驗證」（跑前 port，通常是 80，但用不同旗標區分好各自注入失敗）。兩個
# public path 都支援 MOCK_STATE_DIR 下的呼叫計數器＋TRANSIENT_FAILS，
# 專測 Step 5 healthz retry「前 N 次失敗、之後成功」不誤觸 rollback ─────
cat > "$MOCKDIR/curl" <<'CURLEOF'
#!/usr/bin/env bash
url=""
for a in "$@"; do
  case "$a" in http://*|https://*) url="$a" ;; esac
done
case "$url" in
  http://localhost/healthz)
    STATE_DIR="${MOCK_STATE_DIR:?MOCK_STATE_DIR not set}"
    COUNT_FILE="$STATE_DIR/curl_public_healthz_call_count"
    N=1
    if [ -f "$COUNT_FILE" ]; then N=$(( $(cat "$COUNT_FILE") + 1 )); fi
    echo "$N" > "$COUNT_FILE"
    TRANSIENT="${MOCK_CURL_PUBLIC_TRANSIENT_FAILS:-0}"
    if [ "$TRANSIENT" != "0" ] && [ "$N" -le "$TRANSIENT" ]; then
      exit 1
    fi
    [ "${MOCK_CURL_PUBLIC_FAIL:-0}" = "1" ] && exit 1
    # codex 複審 HIGH：驗證 retry 成功後不會再多打一次無保護的裸重複探測
    # ——FAIL_CALLS 指定的呼叫序號如果被打到，代表裸重複探測還在。
    FAIL_CALLS="${MOCK_CURL_PUBLIC_FAIL_CALLS:-}"
    case ",${FAIL_CALLS}," in
      *",${N},"*) exit 1 ;;
    esac
    exit 0 ;;
  https://trustforge.hurricanesoft.com.tw/healthz)
    STATE_DIR="${MOCK_STATE_DIR:?MOCK_STATE_DIR not set}"
    COUNT_FILE="$STATE_DIR/curl_react_healthz_call_count"
    N=1
    if [ -f "$COUNT_FILE" ]; then N=$(( $(cat "$COUNT_FILE") + 1 )); fi
    echo "$N" > "$COUNT_FILE"
    TRANSIENT="${MOCK_CURL_REACT_HEALTHZ_TRANSIENT_FAILS:-0}"
    if [ "$TRANSIENT" != "0" ] && [ "$N" -le "$TRANSIENT" ]; then
      exit 1
    fi
    [ "${MOCK_CURL_REACT_HEALTHZ_FAIL:-0}" = "1" ] && exit 1
    FAIL_CALLS="${MOCK_CURL_REACT_HEALTHZ_FAIL_CALLS:-}"
    case ",${FAIL_CALLS}," in
      *",${N},"*) exit 1 ;;
    esac
    exit 0 ;;
  *)
    [ "${MOCK_CURL_ROLLBACK_FAIL:-0}" = "1" ] && exit 1
    exit 0 ;;
esac
CURLEOF
chmod +x "$MOCKDIR/curl"

# ── mock npm：不真的 npm ci/install，只在 run build 時造出 frontend/dist ──
cat > "$MOCKDIR/npm" <<'NPMEOF'
#!/usr/bin/env bash
if [ "$1" = "run" ] && [ "$2" = "build" ]; then
  mkdir -p dist
  echo '<html>mock build</html>' > dist/index.html
fi
exit 0
NPMEOF
chmod +x "$MOCKDIR/npm"

# ── 擷取一次 CMDS（bootstrap 遠端腳本）內容：用另一組 mock aws（支撐外層
# 腳本本身的 EC2 查找/S3 上傳/SG 開放邏輯，跟上面重播用的 s3-cp-only mock
# 分開，避免互相干擾），配合 TF_BOOTSTRAP_DRY_RUN=1 印出組好的內容、不真
# 送 SSM ──────────────────────────────────────────────────────────────────
EXTRACT_MOCKDIR=$(mktemp -d)
ln -sf "$MOCKDIR/npm" "$EXTRACT_MOCKDIR/npm"
cat > "$EXTRACT_MOCKDIR/aws" <<AWSEXTRACTEOF
#!/usr/bin/env bash
ALL="\$*"
case "\$ALL" in
  "sts get-caller-identity"*) echo "123456789012" ;;
  "ec2 describe-instances"*) printf 'i-0123456789abcdef0\trunning\n' ;;
  "ec2 describe-vpcs"*) echo "vpc-0123456789abcdef0" ;;
  "ec2 describe-security-groups"*) echo "sg-0123456789abcdef0" ;;
  "ec2 authorize-security-group-ingress"*) exit 0 ;;
  "s3api head-bucket"*) exit 0 ;;
  "s3 cp"*) exit 0 ;;
  *) echo "[aws-mock] 未預期: \$ALL" >&2; exit 99 ;;
esac
AWSEXTRACTEOF
chmod +x "$EXTRACT_MOCKDIR/aws"

rm -rf "$REPO_ROOT/frontend/dist" "$REPO_ROOT/build/trustforge_frontend_dist.zip"
CMD_TRANSACTION=$(PATH="$EXTRACT_MOCKDIR:$PATH" TF_BOOTSTRAP_DRY_RUN=1 \
  bash "$REPO_ROOT/deploy/deploy_frontend_nginx.sh")
rm -rf "$REPO_ROOT/frontend/dist" "$REPO_ROOT/build/trustforge_frontend_dist.zip" "$EXTRACT_MOCKDIR"

if [ -z "$CMD_TRANSACTION" ]; then
  echo "[FATAL] TF_BOOTSTRAP_DRY_RUN 沒有擷取到任何內容，無法繼續測試" >&2
  exit 1
fi

active_conf() {
  if [ -L "$SANDBOX/etc/nginx/conf.d/trustforge.conf" ]; then
    basename "$(readlink "$SANDBOX/etc/nginx/conf.d/trustforge.conf")"
  else
    echo "(none)"
  fi
}
active_port() { grep '^Environment=PORT=' "$SANDBOX/etc/systemd/system/trustforge.service" | head -1 | cut -d= -f3; }
has_bind_host() { grep -q '^Environment=TRUSTFORGE_BIND_HOST=127.0.0.1' "$SANDBOX/etc/systemd/system/trustforge.service" && echo yes || echo no; }
csp_mode() { grep '^Environment=TRUSTFORGE_CSP_MODE=' "$SANDBOX/etc/systemd/system/trustforge.service" | head -1 | cut -d= -f3; }
call_count() { cat "$STATE/$1" 2>/dev/null || echo 0; }
# codex 複審 HIGH（dist swap 原子化）：frontend/current 指到哪個 release
# 目錄，是判斷「swap 有沒有真的發生/有沒有正確回滾」的唯一依據。
current_target() {
  if [ -L "$SANDBOX/opt/trustforge/frontend/current" ]; then
    readlink "$SANDBOX/opt/trustforge/frontend/current"
  else
    echo "(none)"
  fi
}
# 造一份「前一版 release」＋把 frontend/current 指過去——模擬跑前已經有
# 一次成功部署過（真實情境的常態），才有東西可以驗證 rollback「真的救得
# 回」，不是每次都從零開始。
PRIOR_RELEASE_DIR() { echo "$SANDBOX/opt/trustforge/frontend/releases/20260101000000-prior"; }
seed_prior_release() {
  mkdir -p "$(PRIOR_RELEASE_DIR)"
  echo '<html>prior release stub</html>' > "$(PRIOR_RELEASE_DIR)/index.html"
  ln -sfn "$(PRIOR_RELEASE_DIR)" "$SANDBOX/opt/trustforge/frontend/current"
}

run_transaction() {
  set +e
  # TF_BOOTSTRAP_SMOKE_RETRIES/DELAY 預設塞快速值（2 次、0 秒）：跑前這麼
  # 多失敗注入情境本來就預期會失敗，不需要真的等 retry 用完；per-call 可
  # 用 "$@" 覆蓋（env 後面同名覆蓋前面，見場景 24a/24b 專測 retry 本身）。
  env PATH="$MOCKDIR:$PATH" MOCK_STATE_DIR="$STATE" \
    TF_BOOTSTRAP_ETC="$SANDBOX/etc" TF_BOOTSTRAP_OPT="$SANDBOX/opt/trustforge" \
    TF_BOOTSTRAP_DNF_LOG="$STATE/dnf.log" \
    TF_BOOTSTRAP_SMOKE_RETRIES=2 TF_BOOTSTRAP_SMOKE_DELAY=0 "$@" \
    bash -c "$CMD_TRANSACTION" >"$STATE/last_run.log" 2>&1
  local ec=$?
  set -e
  return $ec
}

echo "== 場景 1：Step 1（dnf install）失敗 → 完全不動 live 狀態，非零結束（還沒開始 mutation）=="
reset_sandbox
if run_transaction MOCK_DNF_FAIL=1; then
  fail "dnf install 失敗時應該非零結束"
else
  pass "dnf install 失敗時非零結束"
fi
assert_eq "$(active_conf)" "(none)" "live symlink 仍不存在（沒被動過）"
assert_eq "$(active_port)" "80" "service file PORT 仍是 80（沒被動過）"

echo "== 場景 1b（codex 複審 HIGH：dist swap 原子化）：Step 1 的 unzip 中斷（模擬下載/解壓中間損毀）→ 完全不動 frontend/current，前一版 release 內容原封不動，非零結束（還沒開始 mutation，不需要也不會觸發回滾）=="
reset_sandbox
seed_prior_release
if run_transaction MOCK_UNZIP_FAIL=1; then
  fail "unzip（下載/解壓新版 dist）中斷時應該非零結束"
else
  pass "unzip（下載/解壓新版 dist）中斷時非零結束"
fi
assert_eq "$(current_target)" "$(PRIOR_RELEASE_DIR)" "unzip 中斷時 frontend/current 完全沒被動過，仍指向前一版（新版還沒解壓完就失敗，根本沒進到 Step 4 的 atomic swap）"
assert_eq "$(cat "$(PRIOR_RELEASE_DIR)/index.html")" "<html>prior release stub</html>" "前一版 release 目錄內容完整未變（沒有任何 rm -rf 動到現在活著的內容——codex 複審 HIGH 核心修復點）"
if grep -qF "已回滾到跑前狀態" "$STATE/last_run.log"; then
  fail "unzip 中斷發生在 ERR trap 安裝之前（Step 1），不該（也不需要）觸發 rollback 訊息"
else
  pass "unzip 中斷沒有誤觸發 rollback（本來就還沒開始 mutation，跟 dnf install 失敗同類）"
fi
assert_eq "$(active_conf)" "(none)" "live symlink（nginx conf.d）也沒被動過"

echo "== 場景 2：Step 2（候選設定驗證，scratch nginx -t）失敗 → 完全不動 live 狀態，非零結束 =="
reset_sandbox
if run_transaction MOCK_NGINX_PRECHECK_FAIL=1; then
  fail "候選設定驗證失敗時應該非零結束"
else
  pass "候選設定驗證失敗時非零結束"
fi
assert_grep_log "候選 nginx 設定驗證失敗" "有印候選設定驗證失敗訊息"
assert_eq "$(active_conf)" "(none)" "live symlink 仍不存在（沒被動過）"
assert_eq "$(active_port)" "80" "service file PORT 仍是 80（沒被動過）"

echo "== 場景 3：daemon-reload 失敗（Step 4 mutation 邊界）→ 觸發回滾 =="
reset_sandbox
if run_transaction MOCK_SYSTEMCTL_DAEMON_RELOAD_FAIL_AT=1; then
  fail "daemon-reload 失敗時應該非零結束"
else
  pass "daemon-reload 失敗時非零結束"
fi
assert_grep_log "已回滾到跑前狀態" "有印回滾完成訊息"
assert_eq "$(active_conf)" "(none)" "回滾後 live symlink 退回不存在（不留半殘）"
assert_eq "$(active_port)" "80" "回滾後 service file PORT 退回 80（不留半殘）"

echo "== 場景 4：restart trustforge 失敗（Step 4 mutation 邊界）→ 觸發回滾 =="
reset_sandbox
if run_transaction MOCK_SYSTEMCTL_RESTART_TRUSTFORGE_FAIL_AT=1; then
  fail "restart trustforge 失敗時應該非零結束"
else
  pass "restart trustforge 失敗時非零結束"
fi
assert_grep_log "已回滾到跑前狀態" "有印回滾完成訊息"
assert_eq "$(active_conf)" "(none)" "回滾後 live symlink 退回不存在（不留半殘）"
assert_eq "$(active_port)" "80" "回滾後 service file PORT 退回 80（不留半殘）"

echo "== 場景 5：swap 後 nginx -t 失敗（Step 4 mutation 邊界，narrow 完才驗證）→ 觸發回滾 =="
reset_sandbox
if run_transaction MOCK_NGINX_LIVE_FAIL_AT=1; then
  fail "swap 後 nginx -t 失敗時應該非零結束"
else
  pass "swap 後 nginx -t 失敗時非零結束"
fi
assert_grep_log "已回滾到跑前狀態" "有印回滾完成訊息"
assert_eq "$(active_conf)" "(none)" "回滾後 live symlink 退回不存在（不留半殘，這正是 codex 五次複審點名的「python 已收窄、nginx 沒起來」情境）"
assert_eq "$(active_port)" "80" "回滾後 service file PORT 退回 80（python 對外綁定也還原）"

echo "== 場景 6：enable --now nginx 失敗（Step 4 mutation 邊界）→ 觸發回滾 =="
reset_sandbox
if run_transaction MOCK_SYSTEMCTL_ENABLE_NOW_FAIL_AT=1; then
  fail "enable --now nginx 失敗時應該非零結束"
else
  pass "enable --now nginx 失敗時非零結束"
fi
assert_grep_log "已回滾到跑前狀態" "有印回滾完成訊息"
assert_eq "$(active_conf)" "(none)" "回滾後 live symlink 退回不存在（不留半殘）"

echo "== 場景 7：reload nginx 跟 fallback 的 restart nginx 都失敗 → 觸發回滾 =="
reset_sandbox
if run_transaction MOCK_SYSTEMCTL_RELOAD_NGINX_FAIL_AT=1 MOCK_SYSTEMCTL_RESTART_NGINX_FAIL_AT=1; then
  fail "reload+restart nginx 都失敗時應該非零結束"
else
  pass "reload+restart nginx 都失敗時非零結束"
fi
assert_grep_log "已回滾到跑前狀態" "有印回滾完成訊息"
assert_eq "$(active_conf)" "(none)" "回滾後 live symlink 退回不存在（不留半殘）"
assert_eq "$(current_target)" "(none)" "回滾後 frontend/current 也退回不存在（跑前本來就沒有，不留半殘的新 release 指標）"

echo "== 場景 7b（codex 複審 HIGH：dist swap 原子化，reload 後失敗）：已配置(post-cutover)+ 有前一版 release 在跑，reload nginx 跟 fallback restart 都失敗 → rollback 要把 frontend/current 切回前一版，前一版內容原封不動、真的可服務 =="
reset_sandbox
mkdir -p "$SANDBOX/etc/nginx/trustforge-sites"
echo "# legacy stub" > "$SANDBOX/etc/nginx/trustforge-sites/legacy.conf"
ln -sfn "$SANDBOX/etc/nginx/trustforge-sites/legacy.conf" "$SANDBOX/etc/nginx/conf.d/trustforge.conf"
echo active > "$STATE/svc_nginx_active"
echo enabled > "$STATE/svc_nginx_enabled"
seed_prior_release
if run_transaction MOCK_SYSTEMCTL_RELOAD_NGINX_FAIL_AT=1 MOCK_SYSTEMCTL_RESTART_NGINX_FAIL_AT=1; then
  fail "已配置情境下 reload+restart nginx 都失敗應該非零結束"
else
  pass "已配置情境下 reload+restart nginx 都失敗時非零結束"
fi
assert_grep_log "已回滾到跑前狀態" "有印回滾完成訊息"
assert_eq "$(active_conf)" "legacy.conf" "回滾後 live symlink（nginx conf.d）還原回跑前（本來就指向 legacy.conf）"
assert_eq "$(current_target)" "$(PRIOR_RELEASE_DIR)" "回滾後 frontend/current 切回前一版 release（Step 4 已經把它 atomic 切到新版，reload 失敗後 ROLLBACK 要切回去）"
assert_eq "$(cat "$(PRIOR_RELEASE_DIR)/index.html")" "<html>prior release stub</html>" "前一版 release 目錄內容完整未變、真的可服務（從頭到尾沒被碰過，只是指標被切走又切回來）"

echo "== 場景 8：reload nginx 失敗但 fallback 的 restart nginx 成功 → 不觸發回滾 =="
reset_sandbox
if run_transaction MOCK_SYSTEMCTL_RELOAD_NGINX_FAIL_AT=1; then
  pass "reload 失敗、restart fallback 成功時仍應正常結束（exit 0）"
else
  fail "reload 失敗、restart fallback 成功時不應該非零結束"
fi
assert_eq "$(active_conf)" "legacy.conf" "沒觸發回滾，symlink 正常切到 candidate legacy.conf"
assert_eq "$(active_port)" "8080" "沒觸發回滾，service file PORT 正常改成 8080"

echo "== 場景 9：public-path 驗證失敗（Step 5 post-switch，走 nginx 的 healthz）→ 觸發回滾 =="
reset_sandbox
if run_transaction MOCK_CURL_PUBLIC_FAIL=1; then
  fail "public-path healthz 驗證失敗時應該非零結束"
else
  pass "public-path healthz 驗證失敗時非零結束（codex 五次複審點名的 public-path 驗證失敗情境）"
fi
assert_grep_log "已回滾到跑前狀態" "有印回滾完成訊息"
assert_eq "$(active_conf)" "(none)" "回滾後 live symlink 退回不存在（不留半殘）"
assert_eq "$(active_port)" "80" "回滾後 service file PORT 退回 80（python 對外綁定也還原）"

echo "== 場景 10：無失敗注入（happy path）→ 正常成功，拓樸真的收斂 =="
reset_sandbox
if run_transaction; then
  pass "無失敗注入時正常結束（exit 0）"
else
  fail "無失敗注入時不應該非零結束"
  cat "$STATE/last_run.log" >&2
fi
assert_eq "$(active_conf)" "legacy.conf" "symlink 指向 candidate legacy.conf"
assert_eq "$(active_port)" "8080" "service file PORT 改成 8080"
assert_eq "$(has_bind_host)" "yes" "service file 有加 TRUSTFORGE_BIND_HOST=127.0.0.1"
assert_grep_log "nginx+python 拓樸已就緒" "有印拓樸就緒訊息"

echo "== 場景 11：rollback 自己的 daemon-reload 失敗 → ROLLBACK-FAILED（exit 97），不謊報成功 =="
# 注意：forward path 本身在 Step 4 就會呼叫一次 daemon-reload（call #1，
# 正常成功），所以要讓「rollback 自己的」那次失敗，FAIL_AT 要指向 call #2，
# 觸發點改用 nginx -t（swap 後）失敗來進入 ROLLBACK。
reset_sandbox
if run_transaction MOCK_NGINX_LIVE_FAIL_AT=1 MOCK_SYSTEMCTL_DAEMON_RELOAD_FAIL_AT=2; then
  RC_EC=0
else
  RC_EC=$?
fi
assert_eq "$RC_EC" "97" "rollback 自己的 daemon-reload 失敗時 exit=97（distinct code，不是隨便一個非零值）"
assert_grep_log "ROLLBACK-FAILED" "有印 distinct 的 ROLLBACK-FAILED 狀態"
assert_grep_log "rollback：daemon-reload 失敗" "有印出具體是 daemon-reload 這一步失敗"
if ! grep -qF "已回滾到跑前狀態（bootstrap 失敗" "$STATE/last_run.log"; then
  pass "沒有誤報『已回滾到跑前狀態』（daemon-reload 失敗時不能假裝救回來了）"
else
  fail "不該印出『已回滾到跑前狀態』（daemon-reload 已失敗卻謊報成功）"
fi

echo "== 場景 12：rollback 自己的 restart trustforge 失敗 → ROLLBACK-FAILED（exit 97）=="
reset_sandbox
if run_transaction MOCK_NGINX_LIVE_FAIL_AT=1 MOCK_SYSTEMCTL_RESTART_TRUSTFORGE_FAIL_AT=2; then
  RC_EC=0
else
  RC_EC=$?
fi
assert_eq "$RC_EC" "97" "rollback 自己的 restart trustforge 失敗時 exit=97"
assert_grep_log "ROLLBACK-FAILED" "有印 distinct 的 ROLLBACK-FAILED 狀態"
assert_grep_log "rollback：trustforge 重啟失敗" "有印出具體是 restart trustforge 這一步失敗"

echo "== 場景 13：rollback 驗證失敗（回滾後 healthz 對跑前 port 沒回應）→ ROLLBACK-FAILED（exit 97）=="
reset_sandbox
if run_transaction MOCK_NGINX_LIVE_FAIL_AT=1 MOCK_CURL_ROLLBACK_FAIL=1; then
  RC_EC=0
else
  RC_EC=$?
fi
assert_eq "$RC_EC" "97" "rollback 驗證失敗時 exit=97（不能只看每步指令 exit code 就假設服務健康）"
assert_grep_log "ROLLBACK-FAILED" "有印 distinct 的 ROLLBACK-FAILED 狀態"
assert_grep_log "rollback 驗證失敗：healthz" "有印出具體是 healthz 驗證失敗這一步"

echo "== 場景 14：absent 前置狀態（跑前無 live symlink、無 default.conf，模擬全新 nginx 安裝）+ 無失敗注入 → 正常成功 =="
reset_sandbox
rm -f "$SANDBOX/etc/nginx/conf.d/default.conf"
if run_transaction; then
  pass "absent 前置狀態下，無失敗注入時正常結束（exit 0）"
else
  fail "absent 前置狀態下，無失敗注入時不應該非零結束"
  cat "$STATE/last_run.log" >&2
fi
assert_eq "$(active_conf)" "legacy.conf" "symlink 正常指向 candidate legacy.conf"

echo "== 場景 15：跑前 nginx 本來就在跑（重跑 bootstrap 的情境）+ swap 後 nginx -t 失敗 → 回滾要能把 nginx 還原回『原本在跑』而不是誤關掉 =="
reset_sandbox
echo active > "$STATE/svc_nginx_active"
echo enabled > "$STATE/svc_nginx_enabled"
ln -sfn "$SANDBOX/etc/nginx/trustforge-sites/legacy.conf" "$SANDBOX/etc/nginx/conf.d/trustforge.conf" 2>/dev/null || true
mkdir -p "$SANDBOX/etc/nginx/trustforge-sites"
echo "# legacy stub" > "$SANDBOX/etc/nginx/trustforge-sites/legacy.conf"
ln -sfn "$SANDBOX/etc/nginx/trustforge-sites/legacy.conf" "$SANDBOX/etc/nginx/conf.d/trustforge.conf"
if run_transaction MOCK_NGINX_LIVE_FAIL_AT=1; then
  fail "重跑情境下 swap 後 nginx -t 失敗應該非零結束"
else
  pass "重跑情境下 swap 後 nginx -t 失敗時非零結束"
fi
assert_grep_log "已回滾到跑前狀態" "有印回滾完成訊息"
assert_eq "$(active_conf)" "legacy.conf" "回滾後 live symlink 還原回跑前（本來就指向 legacy.conf，不是誤刪）"

echo "== 場景 16（issue #69 冪等性）：live symlink 已是 react.conf（post-cutover，CSP_MODE=react、PORT 已收斂），重跑本腳本（例如單純更新前端 dist）→ 不可把拓樸打回 legacy.conf/CSP_MODE=legacy，也不該做多餘的 daemon-reload/restart trustforge/enable --now nginx =="
reset_sandbox
mkdir -p "$SANDBOX/etc/nginx/trustforge-sites" "$SANDBOX/etc/letsencrypt/live/trustforge.hurricanesoft.com.tw"
echo "# react stub" > "$SANDBOX/etc/nginx/trustforge-sites/react.conf"
touch "$SANDBOX/etc/letsencrypt/live/trustforge.hurricanesoft.com.tw/fullchain.pem"
ln -sfn "$SANDBOX/etc/nginx/trustforge-sites/react.conf" "$SANDBOX/etc/nginx/conf.d/trustforge.conf"
cat > "$SANDBOX/etc/systemd/system/trustforge.service" <<'EOF'
[Service]
Environment=PORT=8080
Environment=TRUSTFORGE_BIND_HOST=127.0.0.1
Environment=TRUSTFORGE_TRUST_PROXY=1
Environment=TRUSTFORGE_CSP_MODE=react
EOF
echo active > "$STATE/svc_nginx_active"
echo enabled > "$STATE/svc_nginx_enabled"
seed_prior_release
PRIOR_TARGET_BEFORE="$(current_target)"
if run_transaction; then
  pass "idempotent redeploy（react 拓樸已 active）正常結束（exit 0）"
else
  fail "idempotent redeploy（react 拓樸已 active）不應該非零結束"
  cat "$STATE/last_run.log" >&2
fi
assert_eq "$(active_conf)" "react.conf" "symlink 仍是 react.conf（沒被打回 legacy.conf，issue #69 核心斷言）"
assert_eq "$(active_port)" "8080" "service file PORT 仍是 8080（沒被動過）"
assert_eq "$(csp_mode)" "react" "CSP_MODE 仍是 react（沒被改回 legacy，issue #69 核心斷言）"
assert_eq "$(call_count systemctl_daemon_reload_count)" "0" "沒有多餘的 daemon-reload（python 綁定/CSP 都沒動，不需要）"
assert_eq "$(call_count systemctl_restart_trustforge_count)" "0" "沒有多餘的 restart trustforge（python 綁定/CSP 都沒動，不需要重啟）"
assert_eq "$(call_count systemctl_enable_now_nginx_count)" "0" "沒有多餘的 enable --now nginx（nginx 本來就已 enabled+active）"
assert_grep_log "視為 post-cutover" "有印偵測到 live symlink 已配置（post-cutover）訊息"
assert_grep_log "現行拓樸：react.conf" "post-switch 完成訊息有標明現行拓樸是 react.conf（不是誤植 legacy）"
# codex 複審 HIGH（dist swap 原子化）：即使是「只是更新前端 dist」的
# idempotent 重跑，也該走 atomic symlink 切新版，且前一版目錄保留不刪
# （供下一次萬一要 rollback 用）。
if [ "$(current_target)" != "$PRIOR_TARGET_BEFORE" ]; then
  pass "重跑成功後 frontend/current atomic 切到全新的 release 目錄（不是繼續指著前一版）"
else
  fail "重跑成功後 frontend/current 應該切到全新的 release 目錄，而不是仍指著 $PRIOR_TARGET_BEFORE"
fi
assert_eq "$(cat "$(PRIOR_RELEASE_DIR)/index.html")" "<html>prior release stub</html>" "前一版 release 目錄保留未刪（供未來 rollback 用）"
assert_eq "$([ -f "$(current_target)/index.html" ] && echo yes || echo no)" "yes" "新版 release 目錄裡有部署的 dist 內容（index.html 存在）"

echo "== 場景 17（Step 5 healthz retry 有效性）：post-switch healthz 前 2 次失敗、第 3 次才成功（模擬 nginx reload 後極短暫的 worker 交接窗口）→ retry 吸收掉，不該誤觸發 rollback =="
reset_sandbox
if run_transaction MOCK_CURL_PUBLIC_TRANSIENT_FAILS=2 TF_BOOTSTRAP_SMOKE_RETRIES=5 TF_BOOTSTRAP_SMOKE_DELAY=0; then
  pass "healthz 前 2 次失敗、第 3 次成功時，retry 吸收掉、正常結束（exit 0，沒有誤判 rollback）"
else
  fail "healthz 前 2 次失敗、第 3 次成功時不應該非零結束（retry 應該要吸收掉暫時性失敗）"
  cat "$STATE/last_run.log" >&2
fi
assert_eq "$(active_conf)" "legacy.conf" "symlink 正常指向 candidate legacy.conf（沒有誤觸發 rollback）"
if grep -qF "已回滾到跑前狀態" "$STATE/last_run.log"; then
  fail "healthz 暫時性失敗（會被 retry 吸收）不應該觸發 rollback"
else
  pass "healthz 暫時性失敗沒有誤觸發 rollback"
fi
# 3 次 = _tf_retry 內部前 2 次失敗 + 第 3 次成功即回傳，剛好用完就結束。
# codex 複審 HIGH：舊寫法在 _tf_retry 成功後還跟一顆無保護的裸重複探測
# （第 4 次），那次瞬斷若還沒過去就會誤觸發 rollback——bug 沒真的解。
# 移除裸重複探測後，成功後不該再多打一次，故意斷言恰好是 3 次而不是 4。
assert_eq "$(call_count curl_public_healthz_call_count)" "3" "healthz 恰好只被打 3 次就結束（retry 用完就是 3 次，成功後不該再多打一次無保護的裸重複探測——codex 複審 HIGH 修復點）"

echo "== 場景 17b（Step 5 healthz retry 真生效，不是裸重複探測在頂）：前 2 次失敗、第 3 次成功，但『第 4 次』若被打到會失敗（模擬瞬斷持續存在）→ 修好後根本不該有第 4 次呼叫 =="
reset_sandbox
if run_transaction MOCK_CURL_PUBLIC_FAIL_CALLS=1,2,4 TF_BOOTSTRAP_SMOKE_RETRIES=5 TF_BOOTSTRAP_SMOKE_DELAY=0; then
  pass "healthz 前 2 次失敗、第 3 次成功後 exit 0（沒有多打第 4 次撞上持續瞬斷而誤 rollback）"
else
  fail "healthz retry 內第 3 次已成功，不該再被『假設中的第 4 次裸重複探測』拖累成非零結束——代表裸重複探測沒清乾淨"
  cat "$STATE/last_run.log" >&2
fi
assert_eq "$(active_conf)" "legacy.conf" "retry 第 3 次成功後應正常指向 candidate legacy.conf（不該因為裸重複探測撞上第 4 次失敗而 rollback）"
if grep -qF "已回滾到跑前狀態" "$STATE/last_run.log"; then
  fail "healthz retry 第 3 次已成功，不該再誤觸發 rollback（代表裸重複探測還在、撞上第 4 次注入的失敗）"
else
  pass "healthz retry 第 3 次成功後沒有誤觸發 rollback（裸重複探測確認已移除）"
fi
assert_eq "$(call_count curl_public_healthz_call_count)" "3" "healthz 恰好只被打 3 次就結束（若還有裸重複探測會是 4 次、且第 4 次會撞上 FAIL_CALLS 而誤觸發 rollback）"

rm -rf "$MOCKDIR" "$SANDBOX" "$STATE" "$CAPTURE"

echo
echo "== 結果：$PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ]
