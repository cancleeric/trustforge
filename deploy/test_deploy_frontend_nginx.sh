#!/usr/bin/env bash
# deploy_frontend_nginx.sh 邏輯測試（禁真 AWS、禁真 npm install）：完全 mock
# `aws` 與 `npm`，斷言：
#   1. 找不到既有實例（tag Name=trustforge-demo）時非零結束、印出清楚訊息
#      （叫人先跑 deploy_ec2.sh）。
#   2. 找到剛好一個 running 實例時：
#      - 有把 frontend dist 打包上傳（zip 檔存在）
#      - 有上傳兩份 nginx conf（legacy/react）
#      - SSM 安裝指令裡有把 trustforge.service 的 PORT 改 8080、加
#        TRUSTFORGE_BIND_HOST=127.0.0.1、TRUSTFORGE_TRUST_PROXY=1、
#        TRUSTFORGE_CSP_MODE=legacy
#      - SSM 安裝指令裡有把 conf.d/trustforge.conf symlink 指向 legacy.conf
#        （預設不切 react，cutover 前不破現況）
#      - 有開 security group 443
#
# 用法：bash deploy/test_deploy_frontend_nginx.sh
set -euo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
PASS=0
FAIL=0

assert_contains() {
  local haystack="$1" needle="$2" desc="$3"
  if grep -qF -- "$needle" <<<"$haystack"; then
    echo "  [PASS] $desc"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] $desc — 找不到: $needle"
    FAIL=$((FAIL + 1))
  fi
}

assert_file_exists() {
  local file="$1" desc="$2"
  if [ -f "$file" ]; then
    echo "  [PASS] $desc"
    PASS=$((PASS + 1))
  else
    echo "  [FAIL] $desc — 檔案不存在: $file"
    FAIL=$((FAIL + 1))
  fi
}

MOCKDIR=$(mktemp -d)
CAPTURE=$(mktemp -d)

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

# ── mock aws：依 scenario 決定 describe-instances 回什麼 ────────────────────
SCENARIO="${1:-happy}"
cat > "$MOCKDIR/aws" <<MOCKEOF
#!/usr/bin/env bash
ALL="\$*"
CAPTURE_DIR="$CAPTURE"
SCENARIO="$SCENARIO"

find_after() {
  local flag="\$1"; shift
  local prev=""
  for arg in "\$@"; do
    if [ "\$prev" = "\$flag" ]; then printf '%s' "\$arg"; return 0; fi
    prev="\$arg"
  done
}

case "\$ALL" in
  "sts get-caller-identity"*)
    echo "123456789012" ;;
  "ec2 describe-instances"*)
    if [ "\$SCENARIO" = "no-instance" ]; then
      printf ''
    else
      printf 'i-0123456789abcdef0\trunning\n'
    fi ;;
  "ec2 describe-vpcs"*)
    echo "vpc-0123456789abcdef0" ;;
  "ec2 describe-security-groups"*)
    echo "sg-0123456789abcdef0" ;;
  "ec2 authorize-security-group-ingress"*)
    printf '%s\n' "\$ALL" >> "\$CAPTURE_DIR/sg_authorize_calls.txt"
    ;;
  "s3api head-bucket"*)
    exit 0 ;;
  "s3 cp"*)
    printf '%s\n' "\$ALL" >> "\$CAPTURE_DIR/s3_cp_calls.txt"
    ;;
  "ssm send-command"*)
    N=1
    if [ -f "\$CAPTURE_DIR/ssm_call_count" ]; then
      N=\$((\$(cat "\$CAPTURE_DIR/ssm_call_count") + 1))
    fi
    echo "\$N" > "\$CAPTURE_DIR/ssm_call_count"
    PARAMS=\$(find_after --parameters "\$@")
    printf '%s' "\$PARAMS" > "\$CAPTURE_DIR/ssm_params_call\${N}.txt"
    echo "cmd-call\${N}" ;;
  "ssm get-command-invocation"*)
    echo "Success" ;;
  *)
    echo "[aws-mock] 未預期的呼叫，測試沒 mock 到，中止: \$ALL" >&2
    exit 99 ;;
esac
MOCKEOF
chmod +x "$MOCKDIR/aws"

run_script() {
  rm -f "$CAPTURE"/ssm_call_count "$CAPTURE"/*.txt
  rm -rf "$REPO_ROOT/build/trustforge_frontend_dist.zip" "$REPO_ROOT/frontend/dist"
  PATH="$MOCKDIR:$PATH" bash "$REPO_ROOT/deploy/deploy_frontend_nginx.sh" \
    >"$CAPTURE/stdout.log" 2>&1
}

echo "== 場景 1：找不到既有實例 → 應該非零結束、提示先跑 deploy_ec2.sh =="
SCENARIO="no-instance"
cat > "$MOCKDIR/aws" <<MOCKEOF
#!/usr/bin/env bash
ALL="\$*"
case "\$ALL" in
  "sts get-caller-identity"*) echo "123456789012" ;;
  "ec2 describe-instances"*) printf '' ;;
  *) echo "[aws-mock] 未預期: \$ALL" >&2; exit 99 ;;
esac
MOCKEOF
chmod +x "$MOCKDIR/aws"
if run_script; then
  echo "  [FAIL] 應該非零結束（沒有既有實例時）"
  FAIL=$((FAIL + 1))
else
  echo "  [PASS] 沒有既有實例時非零結束"
  PASS=$((PASS + 1))
fi
assert_contains "$(cat "$CAPTURE/stdout.log")" "deploy_ec2.sh" "錯誤訊息有提示先跑 deploy_ec2.sh"

echo "== 場景 2：剛好一個既有實例 → 應該成功跑完 =="
cat > "$MOCKDIR/aws" <<MOCKEOF
#!/usr/bin/env bash
ALL="\$*"
CAPTURE_DIR="$CAPTURE"
find_after() {
  local flag="\$1"; shift
  local prev=""
  for arg in "\$@"; do
    if [ "\$prev" = "\$flag" ]; then printf '%s' "\$arg"; return 0; fi
    prev="\$arg"
  done
}
case "\$ALL" in
  "sts get-caller-identity"*) echo "123456789012" ;;
  "ec2 describe-instances"*) printf 'i-0123456789abcdef0\trunning\n' ;;
  "ec2 describe-vpcs"*) echo "vpc-0123456789abcdef0" ;;
  "ec2 describe-security-groups"*) echo "sg-0123456789abcdef0" ;;
  "ec2 authorize-security-group-ingress"*)
    printf '%s\n' "\$ALL" >> "\$CAPTURE_DIR/sg_authorize_calls.txt" ;;
  "s3api head-bucket"*) exit 0 ;;
  "s3 cp"*) printf '%s\n' "\$ALL" >> "\$CAPTURE_DIR/s3_cp_calls.txt" ;;
  "ssm send-command"*)
    N=1
    if [ -f "\$CAPTURE_DIR/ssm_call_count" ]; then
      N=\$((\$(cat "\$CAPTURE_DIR/ssm_call_count") + 1))
    fi
    echo "\$N" > "\$CAPTURE_DIR/ssm_call_count"
    PARAMS=\$(find_after --parameters "\$@")
    printf '%s' "\$PARAMS" > "\$CAPTURE_DIR/ssm_params_call\${N}.txt"
    echo "cmd-call\${N}" ;;
  "ssm get-command-invocation"*) echo "Success" ;;
  *) echo "[aws-mock] 未預期: \$ALL" >&2; exit 99 ;;
esac
MOCKEOF
chmod +x "$MOCKDIR/aws"
if run_script; then
  echo "  [PASS] deploy_frontend_nginx.sh 執行成功（exit 0）"
  PASS=$((PASS + 1))
else
  echo "  [FAIL] deploy_frontend_nginx.sh 非零結束"
  cat "$CAPTURE/stdout.log"
  FAIL=$((FAIL + 1))
fi

assert_file_exists "$REPO_ROOT/build/trustforge_frontend_dist.zip" "前端 dist zip 已產生"

S3_CALLS=$(cat "$CAPTURE/s3_cp_calls.txt" 2>/dev/null || echo "")
assert_contains "$S3_CALLS" "trustforge_frontend_dist.zip" "有上傳前端 dist zip"
assert_contains "$S3_CALLS" "nginx-legacy.conf" "有上傳 nginx-legacy.conf"
assert_contains "$S3_CALLS" "nginx-react.conf" "有上傳 nginx-react.conf（react.conf 命名）"

SG_CALLS=$(cat "$CAPTURE/sg_authorize_calls.txt" 2>/dev/null || echo "")
assert_contains "$SG_CALLS" "--port 443" "有開 security group 443"

INSTALL_CMD=$(cat "$CAPTURE/ssm_params_call1.txt" 2>/dev/null || echo "")
assert_contains "$INSTALL_CMD" "Environment=PORT=8080" "SSM 安裝指令：PORT 改 8080"
assert_contains "$INSTALL_CMD" "Environment=TRUSTFORGE_BIND_HOST=127.0.0.1" "SSM 安裝指令：加 TRUSTFORGE_BIND_HOST=127.0.0.1"
assert_contains "$INSTALL_CMD" "Environment=TRUSTFORGE_TRUST_PROXY=1" "SSM 安裝指令：加 TRUSTFORGE_TRUST_PROXY=1"
assert_contains "$INSTALL_CMD" "Environment=TRUSTFORGE_CSP_MODE=legacy" "SSM 安裝指令：加 TRUSTFORGE_CSP_MODE=legacy（預設值）"
assert_contains "$INSTALL_CMD" "ln -sfn /etc/nginx/trustforge-sites/legacy.conf /etc/nginx/conf.d/trustforge.conf" "SSM 安裝指令：預設 symlink 指向 legacy.conf（不預設切 react）"
assert_contains "$INSTALL_CMD" "rm -f /etc/nginx/conf.d/default.conf" "SSM 安裝指令：有移除 nginx 預設 conf 避免 port 80 衝突"

rm -rf "$MOCKDIR" "$CAPTURE" "$REPO_ROOT/frontend/dist" "$REPO_ROOT/build/trustforge_frontend_dist.zip"

echo
echo "== 結果：$PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ]
