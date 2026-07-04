#!/usr/bin/env bash
# cutover_switch.sh **production SSM wrapper**（非 TF_CUTOVER_DRY_RUN 路徑）
# 的整合測試（codex 四次複審，HIGH）。
#
# 背景：`deploy/test_cutover_switch.sh` 全部走 `TF_CUTOVER_DRY_RUN=1`
# 擷取遠端指令內容、在本機沙箱執行——完全不會經過本檔案要測的這段
# wrapper 邏輯（aws ec2 describe-instances / ssm send-command / ssm wait
# command-executed / ssm get-command-invocation，見 cutover_switch.sh
# 尾段）。這段以前有個 bug：只看 `get-command-invocation` 的 `Status`，
# 非 Success 一律 `exit 1`，把遠端腳本實際發出的 distinct exit code
# （97=ROLLBACK-FAILED、98=lock contention）全部塌成同一個訊號，
# 監控/自動化分不出來。
#
# 修法：改讀 `get-command-invocation` 的 `ResponseCode`（遠端指令真正的
# exit code），把 97/98 原樣傳遞成 wrapper 自己的 top-level exit code，
# 其餘（含 TimedOut/Cancelled 等 ResponseCode 可能是 -1/None 的情況）
# 保守 fallback exit 1。
#
# 測法：mock 掉 `aws` 這個二進位本身（不是 TF_CUTOVER_DRY_RUN），讓
# `cutover_switch.sh legacy`（不需要 TRUSTFORGE_CUTOVER_CONFIRMED）走到
# 真正的 wrapper 段落，用環境變數控制 mock aws 回傳的 Status/ResponseCode/
# StandardErrorContent，斷言 wrapper 的 top-level exit code。
#
# 用法：bash deploy/test_cutover_ssm_wrapper.sh
set -euo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

PASS=0
FAIL=0

pass() { echo "  [PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); }

assert_eq() {
  local actual="$1" expected="$2" desc="$3"
  if [ "$actual" = "$expected" ]; then
    pass "$desc"
  else
    fail "$desc — 實際值：${actual}（預期：${expected}）"
  fi
}

MOCKDIR=$(mktemp -d)
LOG=$(mktemp)

# ── mock aws：只認這支腳本會用到的幾種呼叫，回傳可由環境變數控制的
# Status/ResponseCode/StandardErrorContent，藉此在不連真 AWS 的情況下
# 驅動 wrapper 段落走到各種分支 ──────────────────────────────────────────
cat > "$MOCKDIR/aws" <<'AWSEOF'
#!/usr/bin/env bash
svc="${1:-}"; shift || true
sub="${1:-}"; shift || true
case "$svc $sub" in
  "ec2 describe-instances")
    echo "${MOCK_AWS_INSTANCE_ID:-i-mock1234}"
    ;;
  "ssm send-command")
    echo "${MOCK_AWS_COMMAND_ID:-cmd-mock5678}"
    ;;
  "ssm wait")
    exit "${MOCK_AWS_WAIT_EXIT:-0}"
    ;;
  "ssm get-command-invocation")
    query=""
    while [ "$#" -gt 0 ]; do
      if [ "$1" = "--query" ]; then
        query="$2"
        break
      fi
      shift
    done
    case "$query" in
      Status) echo "${MOCK_AWS_STATUS:-Success}" ;;
      ResponseCode) echo "${MOCK_AWS_RESPONSE_CODE:-0}" ;;
      StandardErrorContent) echo "${MOCK_AWS_STDERR_CONTENT:-mock stderr content}" ;;
      *) echo "" ;;
    esac
    ;;
  *)
    echo "mock aws: unhandled invocation: $svc $sub $*" >&2
    exit 1
    ;;
esac
AWSEOF
chmod +x "$MOCKDIR/aws"

run_wrapper() {
  # MOCK_AWS_* 透過 "$@" 傳進來
  set +e
  env PATH="$MOCKDIR:$PATH" "$@" REGION=ap-southeast-2 \
    bash "$REPO_ROOT/deploy/cutover_switch.sh" legacy >"$LOG" 2>&1
  local ec=$?
  set -e
  return $ec
}

echo "== 場景 A：Status=Success + ResponseCode=0 → wrapper exit 0（happy path）=="
if run_wrapper MOCK_AWS_STATUS=Success MOCK_AWS_RESPONSE_CODE=0; then
  WEC=0
else
  WEC=$?
fi
assert_eq "$WEC" "0" "Status=Success + ResponseCode=0 時 wrapper exit 0"
if grep -qF "✅ 已切換到" "$LOG"; then
  pass "有印切換成功訊息"
else
  fail "沒印切換成功訊息 — log: $(cat "$LOG")"
fi

echo "== 場景 B：Status=Failed + ResponseCode=97（遠端 ROLLBACK-FAILED）→ wrapper 原樣傳遞 exit 97 =="
if run_wrapper MOCK_AWS_STATUS=Failed MOCK_AWS_RESPONSE_CODE=97; then
  WEC=0
else
  WEC=$?
fi
assert_eq "$WEC" "97" "遠端 ResponseCode=97 時 wrapper top-level exit code 也是 97（不是全塌成 1）"
if grep -qF "ResponseCode=97" "$LOG"; then
  pass "有印出 ResponseCode=97 供人工判讀"
else
  fail "沒印出 ResponseCode=97 — log: $(cat "$LOG")"
fi

echo "== 場景 C：Status=Failed + ResponseCode=98（遠端 lock contention）→ wrapper 原樣傳遞 exit 98 =="
if run_wrapper MOCK_AWS_STATUS=Failed MOCK_AWS_RESPONSE_CODE=98; then
  WEC=0
else
  WEC=$?
fi
assert_eq "$WEC" "98" "遠端 ResponseCode=98 時 wrapper top-level exit code 也是 98（不是全塌成 1，可跟 97 區分）"
if grep -qF "ResponseCode=98" "$LOG"; then
  pass "有印出 ResponseCode=98 供人工判讀"
else
  fail "沒印出 ResponseCode=98 — log: $(cat "$LOG")"
fi

echo "== 場景 D：Status=Failed + ResponseCode=1（一般失敗）→ wrapper exit 1（fallback，非 97/98）=="
if run_wrapper MOCK_AWS_STATUS=Failed MOCK_AWS_RESPONSE_CODE=1; then
  WEC=0
else
  WEC=$?
fi
assert_eq "$WEC" "1" "遠端一般失敗（ResponseCode=1）時 wrapper exit 1"

echo "== 場景 E：Status=TimedOut + ResponseCode=-1（指令根本沒跑完，不是我們定義的 distinct code）→ wrapper 保守 fallback exit 1，不誤判成 97/98 =="
if run_wrapper MOCK_AWS_STATUS=TimedOut MOCK_AWS_RESPONSE_CODE=-1; then
  WEC=0
else
  WEC=$?
fi
assert_eq "$WEC" "1" "ResponseCode=-1（未知/非我們定義的值）時 wrapper 保守 fallback exit 1，不誤判成 distinct code"

echo "== 場景 F：Status=Success 但 ResponseCode 非 0（不一致的邊界情況）→ 不當作成功，wrapper 非零結束 =="
if run_wrapper MOCK_AWS_STATUS=Success MOCK_AWS_RESPONSE_CODE=2; then
  WEC=0
else
  WEC=$?
fi
if [ "$WEC" != "0" ]; then
  pass "Status=Success 但 ResponseCode≠0 時不會被誤判成功（wrapper 非零結束，exit=${WEC}）"
else
  fail "Status=Success 但 ResponseCode=2 卻被當成功（exit 0），不應該"
fi

rm -rf "$MOCKDIR"
rm -f "$LOG"

echo
echo "== 結果：$PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ]
