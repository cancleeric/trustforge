#!/usr/bin/env bash
# deploy_frontend_nginx.sh **production SSM wrapper**（非 TF_BOOTSTRAP_DRY_RUN
# 路徑）的整合測試（codex 五次複審，HIGH）。
#
# 背景：`deploy/test_deploy_frontend_nginx_transaction.sh` 全部走
# `TF_BOOTSTRAP_DRY_RUN=1` 擷取遠端 CMDS 內容、在本機沙箱執行——完全不會
# 經過本檔案要測的這段 wrapper 邏輯（`aws ssm send-command` /
# `poll_ssm_terminal_status`（內部呼叫 `aws ssm get-command-invocation`）
# /失敗時再查一次 `ResponseCode`，見 deploy_frontend_nginx.sh 尾段）。
#
# 這段以前的 bug（跟 cutover_switch.sh 修過的同一類）：只看 Status，
# 非 Success 一律 exit 1，把遠端 CMDS 實際發出的 distinct exit code
# （97=ROLLBACK-FAILED）塌成跟一般失敗一樣，監控/自動化分不出來。
#
# 修法：失敗時額外讀一次 ResponseCode，97 原樣傳遞成 wrapper 的
# top-level exit code，其餘（含 TimedOut/Cancelled 等 ResponseCode
# 可能是 -1/None 的情況）保守 fallback exit 1。
#
# 測法：mock 掉 `aws`（涵蓋 sts/ec2/s3api/s3/ssm 全部呼叫）與 `npm`，讓
# `deploy_frontend_nginx.sh` 走到真正的 wrapper 段落，用環境變數控制 mock
# aws 對 `ssm get-command-invocation` 回傳的 Status/ResponseCode/
# StandardErrorContent，斷言 wrapper 的 top-level exit code。
#
# 用法：bash deploy/test_deploy_frontend_nginx_ssm_wrapper.sh
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

cat > "$MOCKDIR/npm" <<'NPMEOF'
#!/usr/bin/env bash
if [ "$1" = "run" ] && [ "$2" = "build" ]; then
  mkdir -p dist
  echo '<html>mock build</html>' > dist/index.html
fi
exit 0
NPMEOF
chmod +x "$MOCKDIR/npm"

# ── mock aws：涵蓋 deploy_frontend_nginx.sh 從頭到尾會用到的所有呼叫，
# `ssm get-command-invocation` 回傳可由環境變數控制的
# Status/ResponseCode/StandardErrorContent，藉此在不連真 AWS 的情況下
# 驅動 wrapper 段落走到各種分支 ──────────────────────────────────────────
cat > "$MOCKDIR/aws" <<'AWSEOF'
#!/usr/bin/env bash
ALL="$*"
case "$ALL" in
  "sts get-caller-identity"*) echo "123456789012" ;;
  "ec2 describe-instances"*) printf 'i-0123456789abcdef0\trunning\n' ;;
  "ec2 describe-vpcs"*) echo "vpc-0123456789abcdef0" ;;
  "ec2 describe-security-groups"*) echo "sg-0123456789abcdef0" ;;
  "ec2 authorize-security-group-ingress"*) exit 0 ;;
  "s3api head-bucket"*) exit 0 ;;
  "s3 cp"*) exit 0 ;;
  "ssm send-command"*) echo "${MOCK_AWS_COMMAND_ID:-cmd-mock5678}" ;;
  "ssm get-command-invocation"*)
    query=""
    prev=""
    for a in "$@"; do
      if [ "$prev" = "--query" ]; then query="$a"; fi
      prev="$a"
    done
    case "$query" in
      Status) echo "${MOCK_AWS_STATUS:-Success}" ;;
      ResponseCode) echo "${MOCK_AWS_RESPONSE_CODE:-0}" ;;
      StandardErrorContent) echo "${MOCK_AWS_STDERR_CONTENT:-mock stderr content}" ;;
      *) echo "" ;;
    esac
    ;;
  *)
    echo "mock aws: unhandled invocation: $ALL" >&2
    exit 1 ;;
esac
AWSEOF
chmod +x "$MOCKDIR/aws"

run_wrapper() {
  # MOCK_AWS_* 透過 "$@" 傳進來
  set +e
  rm -rf "$REPO_ROOT/frontend/dist" "$REPO_ROOT/build/trustforge_frontend_dist.zip"
  env PATH="$MOCKDIR:$PATH" "$@" REGION=ap-southeast-2 \
    bash "$REPO_ROOT/deploy/deploy_frontend_nginx.sh" >"$LOG" 2>&1
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
if grep -qF "nginx 層 + python 內收斂完成" "$LOG"; then
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

echo "== 場景 C：Status=Failed + ResponseCode=1（一般失敗）→ wrapper exit 1（fallback，非 97）=="
if run_wrapper MOCK_AWS_STATUS=Failed MOCK_AWS_RESPONSE_CODE=1; then
  WEC=0
else
  WEC=$?
fi
assert_eq "$WEC" "1" "遠端一般失敗（ResponseCode=1）時 wrapper exit 1"

echo "== 場景 D：Status=TimedOut + ResponseCode=-1（SSM timeout，指令根本沒跑完，不是我們定義的 distinct code）→ wrapper 保守 fallback exit 1，不誤判成 97 =="
if run_wrapper MOCK_AWS_STATUS=TimedOut MOCK_AWS_RESPONSE_CODE=-1; then
  WEC=0
else
  WEC=$?
fi
assert_eq "$WEC" "1" "ResponseCode=-1（未知/非我們定義的值）時 wrapper 保守 fallback exit 1，不誤判成 distinct code"

echo "== 場景 E：Status=Cancelled + ResponseCode=None（SSM cancellation）→ wrapper 保守 fallback exit 1，不誤判成 97 =="
if run_wrapper MOCK_AWS_STATUS=Cancelled MOCK_AWS_RESPONSE_CODE=None; then
  WEC=0
else
  WEC=$?
fi
assert_eq "$WEC" "1" "Cancelled/ResponseCode=None 時 wrapper 保守 fallback exit 1，不誤判成 distinct code"

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

rm -rf "$MOCKDIR" "$REPO_ROOT/frontend/dist" "$REPO_ROOT/build/trustforge_frontend_dist.zip"
rm -f "$LOG"

echo
echo "== 結果：$PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ]
