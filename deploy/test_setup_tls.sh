#!/usr/bin/env bash
# deploy/setup_tls.sh 邏輯測試（禁真 AWS、禁真 certbot）——codex 複審兩個
# HIGH 的回歸測試：
#
#   1. [注入] DOMAIN/ADMIN_EMAIL 以前直接內插進送到遠端的 shell 指令字串
#      （$CMD），若帶 `;`/`$()`/引號/空白等 shell 特殊字元，理論上可能
#      注入任意指令。修法：先嚴格 regex 驗證格式，再以
#      `bash -s -- "$DOMAIN" "$ADMIN_EMAIL"` positional args 傳給
#      single-quoted heredoc 的 remote script（remote script 只用
#      `$1`/`$2` 取值，不再把值到處內插進其他字串位置）。
#
#   2. [fail-open 選錯主機] instance 查詢以前用 `awk '{print $1}'` 靜默挑
#      第一行，0 台時是空字串（有另外擋）、多台時默默只挑其中一台——
#      可能裝錯主機、正牌 prod 沒裝到憑證。修法：比照
#      deploy/deploy_frontend_nginx.sh 已有的做法，算相符實例數，非
#      「剛好 1 台」一律 fail-closed 中止、distinct 訊息 + 非零 exit。
#
# 測法：
#   - 場景 1：hostile DOMAIN（`;`/`$()`/引號/空白）→ 斷言在碰任何 aws 呼叫
#     前就被 regex 擋下、非零結束、訊息含「DOMAIN 格式不合法」。
#   - 場景 2：hostile ADMIN_EMAIL（同上四種特殊字元）→ 斷言被
#     「ADMIN_EMAIL 格式不合法」擋下。
#   - 場景 3：合法參數 + TF_SETUP_TLS_DRY_RUN=1 → 斷言印出的 CMD 改用
#     `bash -s -- "<domain>" "<email>" <<'REMOTE_TLS_EOF'`、remote script
#     body 只用 `$1`/`$2`，而且 `-d`/`-m` 那行不再直接內插原始 DOMAIN/
#     ADMIN_EMAIL 字面值（值只出現在 positional-arg 那一行）。
#   - 場景 4：describe-instances 回 0 筆 → fail-closed 非零結束、訊息含
#     「找到 0 個相符實例」、且完全沒呼叫到 `ssm send-command`（不會裝錯
#     主機，因為根本沒選到主機就中止了）。
#   - 場景 5：describe-instances 回 2 筆（多台 running）→ fail-closed
#     非零結束、訊息含「找到 2 個相符實例」、同樣沒呼叫 `ssm send-command`。
#   - 場景 6：describe-instances 剛好回 1 筆 → 正常往下跑完，
#     `ssm send-command`/`get-command-invocation` 被呼叫、Status=Success
#     時整體 exit 0。
#
# 用法：bash deploy/test_setup_tls.sh
set -euo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

PASS=0
FAIL=0

pass() { echo "  [PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); }

assert_contains() {
  local haystack="$1" needle="$2" desc="$3"
  if grep -qF -- "$needle" <<<"$haystack"; then
    pass "$desc"
  else
    fail "$desc — 找不到: $needle"
  fi
}

assert_not_contains() {
  local haystack="$1" needle="$2" desc="$3"
  if grep -qF -- "$needle" <<<"$haystack"; then
    fail "$desc — 不應該出現，但找到了: $needle"
  else
    pass "$desc"
  fi
}

MOCKDIR=$(mktemp -d)
CAPTURE=$(mktemp -d)

cleanup() { rm -rf "$MOCKDIR" "$CAPTURE"; }
trap cleanup EXIT

# ── 場景 1：hostile DOMAIN ──────────────────────────────────────────────
echo "== 場景 1：hostile DOMAIN（;/\$()/引號/空白）在碰任何 aws 呼叫前就被擋 =="
POISON_MARKER="$CAPTURE/domain_injection_marker"
rm -f "$POISON_MARKER"

run_with_domain() {
  local domain="$1"
  ADMIN_EMAIL="ops@hurricanesoft.com.tw" TRUSTFORGE_RUN_CERTBOT=yes \
    TF_SETUP_TLS_DRY_RUN=1 DOMAIN="$domain" \
    bash "$REPO_ROOT/deploy/setup_tls.sh" >"$CAPTURE/out.log" 2>"$CAPTURE/err.log"
}

HOSTILE_DOMAINS=(
  "trustforge.hurricanesoft.com.tw; touch $POISON_MARKER"
  "\$(touch $POISON_MARKER)"
  "trustforge.hurricanesoft.com.tw\""
  "trustforge.hurricanesoft.com.tw evil"
  "trustforge.hurricanesoft.com.tw\`touch $POISON_MARKER\`"
)
for d in "${HOSTILE_DOMAINS[@]}"; do
  if run_with_domain "$d"; then
    fail "hostile DOMAIN 應該非零結束卻成功了：${d}"
  else
    pass "hostile DOMAIN 被拒絕（非零結束）：${d}"
  fi
  assert_contains "$(cat "$CAPTURE/err.log")" "DOMAIN 格式不合法" "hostile DOMAIN 錯誤訊息含「DOMAIN 格式不合法」：${d}"
done
if [ -f "$POISON_MARKER" ]; then
  fail "hostile DOMAIN 竟然真的執行了注入內容（$POISON_MARKER 被建立）"
else
  pass "hostile DOMAIN 沒有任何一個真的被執行（無 side-effect 檔案）"
fi

# ── 場景 2：hostile ADMIN_EMAIL ─────────────────────────────────────────
echo "== 場景 2：hostile ADMIN_EMAIL（;/\`/引號/空白）在碰任何 aws 呼叫前就被擋 =="
rm -f "$POISON_MARKER"

run_with_email() {
  local email="$1"
  DOMAIN="trustforge.hurricanesoft.com.tw" TRUSTFORGE_RUN_CERTBOT=yes \
    TF_SETUP_TLS_DRY_RUN=1 ADMIN_EMAIL="$email" \
    bash "$REPO_ROOT/deploy/setup_tls.sh" >"$CAPTURE/out.log" 2>"$CAPTURE/err.log"
}

HOSTILE_EMAILS=(
  "ops@hurricanesoft.com.tw; touch $POISON_MARKER"
  "ops@hurricanesoft.com.tw\`touch $POISON_MARKER\`"
  "ops@hurricanesoft.com.tw\" evil"
  "ops@hurricanesoft.com.tw evil"
)
for e in "${HOSTILE_EMAILS[@]}"; do
  if run_with_email "$e"; then
    fail "hostile ADMIN_EMAIL 應該非零結束卻成功了：${e}"
  else
    pass "hostile ADMIN_EMAIL 被拒絕（非零結束）：${e}"
  fi
  assert_contains "$(cat "$CAPTURE/err.log")" "ADMIN_EMAIL 格式不合法" "hostile ADMIN_EMAIL 錯誤訊息含「ADMIN_EMAIL 格式不合法」：${e}"
done
if [ -f "$POISON_MARKER" ]; then
  fail "hostile ADMIN_EMAIL 竟然真的執行了注入內容（$POISON_MARKER 被建立）"
else
  pass "hostile ADMIN_EMAIL 沒有任何一個真的被執行（無 side-effect 檔案）"
fi

# ── 場景 3：合法參數 → CMD 改用 positional args，不再直接內插進 certbot 行 ──
echo "== 場景 3：合法參數時，CMD 改用 bash -s -- positional args，certbot 那行不再直接內插原始值 =="
CMD_OUT=$(DOMAIN="trustforge.hurricanesoft.com.tw" ADMIN_EMAIL="ops@hurricanesoft.com.tw" \
  TRUSTFORGE_RUN_CERTBOT=yes TF_SETUP_TLS_DRY_RUN=1 bash "$REPO_ROOT/deploy/setup_tls.sh" 2>/dev/null)
assert_contains "$CMD_OUT" 'bash -s -- "trustforge.hurricanesoft.com.tw" "ops@hurricanesoft.com.tw"' \
  "CMD 有用 bash -s -- 把 DOMAIN/ADMIN_EMAIL 當 positional args 傳給 remote script"
assert_contains "$CMD_OUT" '<<'"'"'REMOTE_TLS_EOF'"'"'' \
  "CMD 用單引號 heredoc 界定字（本機組字串時不會展開 remote script 裡的 \$1/\$2）"
# shellcheck disable=SC2016  # 單引號內的 $1 刻意留給遠端（remote heredoc
# 裡的 positional parameter）展開，這裡只比對字面文字。
assert_contains "$CMD_OUT" 'TF_DOMAIN="$1"' "remote script 用 \$1 取得 domain（不是直接內插字面值）"
# shellcheck disable=SC2016
assert_contains "$CMD_OUT" 'TF_ADMIN_EMAIL="$2"' "remote script 用 \$2 取得 email（不是直接內插字面值）"
assert_not_contains "$CMD_OUT" '-d trustforge.hurricanesoft.com.tw' \
  "certbot -d 那行不再直接內插 DOMAIN 字面值（只透過 \$TF_DOMAIN 引用）"
assert_not_contains "$CMD_OUT" '-m ops@hurricanesoft.com.tw' \
  "certbot -m 那行不再直接內插 ADMIN_EMAIL 字面值（只透過 \$TF_ADMIN_EMAIL 引用）"
if bash -n <(printf '%s\n' "$CMD_OUT"); then
  pass "TF_SETUP_TLS_DRY_RUN 印出的 CMD 本身是合法 bash 語法（bash -n 過）"
else
  fail "TF_SETUP_TLS_DRY_RUN 印出的 CMD 不是合法 bash 語法"
fi

# ── mock aws：describe-instances 依 SCENARIO 決定回幾筆，其餘呼叫記錄下來 ──
write_mock_aws() {
  local scenario="$1"
  cat > "$MOCKDIR/aws" <<MOCKEOF
#!/usr/bin/env bash
ALL="\$*"
find_after() {
  local flag="\$1"; shift
  local prev=""
  for arg in "\$@"; do
    if [ "\$prev" = "\$flag" ]; then printf '%s' "\$arg"; return 0; fi
    prev="\$arg"
  done
}
case "\$ALL" in
  "ec2 describe-instances"*)
    case "$scenario" in
      zero) printf '' ;;
      multi) printf 'i-0aaaaaaaaaaaaaaaa\ni-0bbbbbbbbbbbbbbbb\n' ;;
      one) printf 'i-0123456789abcdef0\n' ;;
    esac
    ;;
  "ssm send-command"*)
    echo "call" >> "$CAPTURE/ssm_send_command_calls.txt"
    echo "cmd-mock1234"
    ;;
  "ssm wait command-executed"*) exit 0 ;;
  "ssm get-command-invocation"*)
    Q=\$(find_after --query "\$@")
    case "\$Q" in
      Status) echo "Success" ;;
      *) echo "" ;;
    esac
    ;;
  *) echo "[aws-mock] 未預期: \$ALL" >&2; exit 99 ;;
esac
MOCKEOF
  chmod +x "$MOCKDIR/aws"
}

run_instance_scenario() {
  local scenario="$1"
  rm -f "$CAPTURE/ssm_send_command_calls.txt"
  write_mock_aws "$scenario"
  PATH="$MOCKDIR:$PATH" DOMAIN="trustforge.hurricanesoft.com.tw" \
    ADMIN_EMAIL="ops@hurricanesoft.com.tw" TRUSTFORGE_RUN_CERTBOT=yes \
    bash "$REPO_ROOT/deploy/setup_tls.sh" >"$CAPTURE/out.log" 2>"$CAPTURE/err.log"
}

# ── 場景 4：0 台相符實例 → fail-closed ──────────────────────────────────
echo "== 場景 4：describe-instances 回 0 筆 → fail-closed 中止，不呼叫 ssm send-command =="
if run_instance_scenario "zero"; then
  fail "0 台相符實例應該非零結束卻成功了"
else
  pass "0 台相符實例時非零結束"
fi
assert_contains "$(cat "$CAPTURE/err.log")" "找到 0 個相符實例" "0 台相符實例錯誤訊息含「找到 0 個相符實例」"
if [ -f "$CAPTURE/ssm_send_command_calls.txt" ]; then
  fail "0 台相符實例時竟然呼叫了 ssm send-command（不應該裝到任何主機）"
else
  pass "0 台相符實例時沒有呼叫 ssm send-command"
fi

# ── 場景 5：多台相符實例 → fail-closed ──────────────────────────────────
echo "== 場景 5：describe-instances 回 2 筆（多台 running）→ fail-closed 中止，不亂選 =="
if run_instance_scenario "multi"; then
  fail "多台相符實例應該非零結束卻成功了"
else
  pass "多台相符實例時非零結束"
fi
assert_contains "$(cat "$CAPTURE/err.log")" "找到 2 個相符實例" "多台相符實例錯誤訊息含「找到 2 個相符實例」"
if [ -f "$CAPTURE/ssm_send_command_calls.txt" ]; then
  fail "多台相符實例時竟然呼叫了 ssm send-command（不應該亂選其中一台裝）"
else
  pass "多台相符實例時沒有呼叫 ssm send-command（沒有靜默選第一台）"
fi

# ── 場景 6：剛好 1 台相符實例 → 正常往下跑完 ────────────────────────────
echo "== 場景 6：describe-instances 剛好回 1 筆 → 正常往下跑完，Status=Success 時 exit 0 =="
if run_instance_scenario "one"; then
  pass "剛好 1 台相符實例時成功結束（exit 0）"
else
  fail "剛好 1 台相符實例時應該成功結束卻非零 — stderr: $(cat "$CAPTURE/err.log")"
fi
assert_contains "$(cat "$CAPTURE/err.log")" "目標實例 i-0123456789abcdef0" "剛好 1 台時有印出選中的目標實例 ID"
if [ -f "$CAPTURE/ssm_send_command_calls.txt" ]; then
  pass "剛好 1 台相符實例時有呼叫 ssm send-command"
else
  fail "剛好 1 台相符實例時應該呼叫 ssm send-command 卻沒有"
fi

echo ""
echo "== 結果：$PASS 通過，$FAIL 失敗 =="
if [ "$FAIL" -ne 0 ]; then
  exit 1
fi
