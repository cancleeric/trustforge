#!/usr/bin/env bash
# deploy/cutover_switch.sh 的「找目標 EC2 實例」邏輯測試（禁真 AWS）——
# codex 複審 HIGH：cutover instance fail-open。
#
# 問題：以前用 `describe-instances ... --query
# 'Reservations[].Instances[].InstanceId' --output text | awk '{print $1}'`
# 找目標實例，0 台時是空字串（有另外擋掉），但**多台 running 相符時會靜默
# 挑第一個**——可能對到 stale/非 prod 的實例卻回報 cutover 成功，正牌 prod
# 完全沒切，事故排查極難察覺。比照 deploy/setup_tls.sh 已修的做法：
# `--query` 多包一層 `[InstanceId]`，讓 `--output text` 每個相符實例各自
# 一行，用 `grep -c .` 算出真正的相符數，非「剛好 1 台」一律 fail-closed
# 中止、不猜、不亂選，用獨立 exit code（99）跟其他失敗類型區分。
#
# 這段邏輯是本機 wrapper 腳本自己執行的（不是組進 `$CMD` 送去遠端 SSM 執行
# 的那段），所以 deploy/test_cutover_switch.sh 用的
# `TF_CUTOVER_DRY_RUN=1` 擷取＋沙箱重放手法碰不到這裡（DRY_RUN 會在碰到
# describe-instances 前就先印出 CMD 並 exit 0）——這份測試改成不設
# TF_CUTOVER_DRY_RUN，讓腳本真的跑到 describe-instances/ssm 那段，但用
# mock `aws` 二進位擋掉，禁真 AWS。用 MODE=legacy（不需要
# TRUSTFORGE_CUTOVER_CONFIRMED/TF_ALLOW_INSECURE_HTTP_CUTOVER 這些額外
# confirm gate，最單純能走到 instance 選擇這段的路徑）。
#
# 測法：
#   - 場景 1：describe-instances 回 0 筆 → fail-closed 非零結束（exit=99）、
#     訊息含「找到 0 個相符實例」，且完全沒呼叫到 ssm send-command。
#   - 場景 2：describe-instances 回 2 筆（多台 running）→ fail-closed
#     非零結束（exit=99）、訊息含「找到 2 個相符實例」，同樣沒呼叫
#     ssm send-command（不亂選其中一台）。
#   - 場景 3：describe-instances 剛好回 1 筆 → 正常往下跑完，
#     ssm send-command/get-command-invocation 被呼叫、Status=Success +
#     ResponseCode=0 時整體 exit 0。
#
# 用法：bash deploy/test_cutover_switch_instance_selection.sh
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

MOCKDIR=$(mktemp -d)
CAPTURE=$(mktemp -d)
cleanup() { rm -rf "$MOCKDIR" "$CAPTURE"; }
trap cleanup EXIT

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
      ResponseCode) echo "0" ;;
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
  PATH="$MOCKDIR:$PATH" \
    bash "$REPO_ROOT/deploy/cutover_switch.sh" legacy >"$CAPTURE/out.log" 2>"$CAPTURE/err.log"
}

# ── 場景 1：0 台相符實例 → fail-closed ──────────────────────────────────
echo "== 場景 1：describe-instances 回 0 筆 → fail-closed 中止（exit=99），不呼叫 ssm send-command =="
if run_instance_scenario "zero"; then
  RC=0
else
  RC=$?
fi
if [ "$RC" -eq 0 ]; then
  fail "0 台相符實例應該非零結束卻成功了"
else
  pass "0 台相符實例時非零結束"
fi
if [ "$RC" -eq 99 ]; then
  pass "0 台相符實例時 exit code 是獨立的 99（不是含糊的一般失敗 exit=1，方便監控分辨是哪一種問題）"
else
  fail "0 台相符實例時 exit code 應該是 99，實際是 ${RC}"
fi
assert_contains "$(cat "$CAPTURE/err.log")" "找到 0 個相符實例" "0 台相符實例錯誤訊息含「找到 0 個相符實例」"
if [ -f "$CAPTURE/ssm_send_command_calls.txt" ]; then
  fail "0 台相符實例時竟然呼叫了 ssm send-command（不應該裝到任何主機）"
else
  pass "0 台相符實例時沒有呼叫 ssm send-command"
fi

# ── 場景 2：多台相符實例 → fail-closed ──────────────────────────────────
echo "== 場景 2：describe-instances 回 2 筆（多台 running）→ fail-closed 中止（exit=99），不亂選 =="
if run_instance_scenario "multi"; then
  RC=0
else
  RC=$?
fi
if [ "$RC" -eq 0 ]; then
  fail "多台相符實例應該非零結束卻成功了"
else
  pass "多台相符實例時非零結束"
fi
if [ "$RC" -eq 99 ]; then
  pass "多台相符實例時 exit code 是獨立的 99"
else
  fail "多台相符實例時 exit code 應該是 99，實際是 ${RC}"
fi
assert_contains "$(cat "$CAPTURE/err.log")" "找到 2 個相符實例" "多台相符實例錯誤訊息含「找到 2 個相符實例」"
if [ -f "$CAPTURE/ssm_send_command_calls.txt" ]; then
  fail "多台相符實例時竟然呼叫了 ssm send-command（不應該亂選其中一台裝——這正是 codex 複審 HIGH 要修的 fail-open）"
else
  pass "多台相符實例時沒有呼叫 ssm send-command（沒有靜默選第一台）"
fi

# ── 場景 3：剛好 1 台相符實例 → 正常往下跑完 ────────────────────────────
echo "== 場景 3：describe-instances 剛好回 1 筆 → 正常往下跑完，Status=Success + ResponseCode=0 時 exit 0 =="
if run_instance_scenario "one"; then
  pass "剛好 1 台相符實例時成功結束（exit 0）"
else
  fail "剛好 1 台相符實例時應該成功結束卻非零 — stderr: $(cat "$CAPTURE/err.log")"
fi
if [ -f "$CAPTURE/ssm_send_command_calls.txt" ]; then
  pass "剛好 1 台相符實例時有呼叫 ssm send-command"
else
  fail "剛好 1 台相符實例時應該呼叫 ssm send-command 卻沒有"
fi

echo
echo "== 結果：$PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ]
