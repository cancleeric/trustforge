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
    PARAMS_RAW=\$(find_after --parameters "\$@")
    # commit 205216b 之後 --parameters 改傳 file://<path>（JSON
    # {"commands":[...]}）而不是內嵌 JSON 字串，這裡要吃 file:// 路徑、
    # 讀檔、還原成原本的多行 CMDS 文字。
    case "\$PARAMS_RAW" in
      file://*)
        # 直接讀原始 JSON 檔案文字（不 json.load 解回原字串）：JSON 編碼
        # 本來就會把每行指令內的 " 轉成 \"，這裡要比對的正是這段
        # JSON escape 後的文字（跟改成 file:// 之前直接比對 inline JSON
        # 字串時的斷言字面值一致，才不用大改既有斷言）。
        PARAMS=\$(cat "\${PARAMS_RAW#file://}") ;;
      *)
        PARAMS="\$PARAMS_RAW" ;;
    esac
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
    PARAMS_RAW=\$(find_after --parameters "\$@")
    # commit 205216b 之後 --parameters 改傳 file://<path>（JSON
    # {"commands":[...]}）而不是內嵌 JSON 字串，這裡要吃 file:// 路徑、
    # 讀檔、還原成原本的多行 CMDS 文字，assert_contains 才找得到內容。
    case "\$PARAMS_RAW" in
      file://*)
        # 直接讀原始 JSON 檔案文字（不 json.load 解回原字串）：JSON 編碼
        # 本來就會把每行指令內的 " 轉成 \"，這裡要比對的正是這段
        # JSON escape 後的文字（跟改成 file:// 之前直接比對 inline JSON
        # 字串時的斷言字面值一致，才不用大改既有斷言）。
        PARAMS=\$(cat "\${PARAMS_RAW#file://}") ;;
      *)
        PARAMS="\$PARAMS_RAW" ;;
    esac
    printf '%s' "\$PARAMS" > "\$CAPTURE_DIR/ssm_params_call\${N}.txt"
    echo "cmd-call\${N}" ;;
  "ssm get-command-invocation"*)
    Q=\$(find_after --query "\$@")
    case "\$Q" in
      ResponseCode) echo "0" ;;
      *) echo "Success" ;;
    esac
    ;;
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
assert_contains "$S3_CALLS" "nginx-react-http.conf" "有上傳 nginx-react-http.conf（bare-IP HTTP-only 版）"
assert_contains "$S3_CALLS" "nginx-legacy-tls.conf" "有上傳 nginx-legacy-tls.conf（codex 複審 HIGH：HSTS-safe legacy 回滾用，443 版）"

SG_CALLS=$(cat "$CAPTURE/sg_authorize_calls.txt" 2>/dev/null || echo "")
assert_contains "$SG_CALLS" "--port 443" "有開 security group 443"

INSTALL_CMD=$(cat "$CAPTURE/ssm_params_call1.txt" 2>/dev/null || echo "")
assert_contains "$INSTALL_CMD" "Environment=PORT=8080" "SSM 安裝指令：PORT 改 8080"
assert_contains "$INSTALL_CMD" "Environment=TRUSTFORGE_BIND_HOST=127.0.0.1" "SSM 安裝指令：加 TRUSTFORGE_BIND_HOST=127.0.0.1"
assert_contains "$INSTALL_CMD" "Environment=TRUSTFORGE_TRUST_PROXY=1" "SSM 安裝指令：加 TRUSTFORGE_TRUST_PROXY=1"
assert_contains "$INSTALL_CMD" "Environment=TRUSTFORGE_CSP_MODE=legacy" "SSM 安裝指令：加 TRUSTFORGE_CSP_MODE=legacy（預設值）"
# 注意：$INSTALL_CMD 擷取自 --parameters 的 JSON 陣列文字（deploy_frontend_nginx.sh
# 用 `python3 -c 'import json...'` 把 CMDS 逐行包成 JSON），JSON 編碼後雙引號會變成
# \" ——比對時要用跳脫後的字面文字，不是原始未跳脫的 shell 語法。
# shellcheck disable=SC2016  # 單引號內的 $CANDIDATE/$LIVE_LINK 刻意留給
# 遠端（deploy_frontend_nginx.sh 的 CMDS heredoc）展開，這裡只比對字面文字。
assert_contains "$INSTALL_CMD" 'ln -sfn \"$CANDIDATE\" \"$LIVE_LINK\"' "SSM 安裝指令：預設 symlink 指向 candidate legacy.conf（不預設切 react）"
# shellcheck disable=SC2016
assert_contains "$INSTALL_CMD" 'nginx-react-http.conf \"$CONF_STAGING_DIR/react-http.conf\"' "SSM 安裝指令：有把 nginx-react-http.conf 放入交易式 staging（bare-IP cutover 候選）"
# shellcheck disable=SC2016
assert_contains "$INSTALL_CMD" 'nginx-legacy-tls.conf \"$CONF_STAGING_DIR/legacy-tls.conf\"' "SSM 安裝指令：有把 nginx-legacy-tls.conf 放入交易式 staging（HSTS-safe legacy 回滾候選）"
# shellcheck disable=SC2016
assert_contains "$INSTALL_CMD" 'rm -f \"$DEFAULT_CONF\"' "SSM 安裝指令：有移除 nginx 預設 conf 避免 port 80 衝突"
# shellcheck disable=SC2016
assert_contains "$INSTALL_CMD" 'nginx -t -c \"$VALIDATE_CONF\"' "SSM 安裝指令：guarded transaction——先驗證候選設定（scratch harness），不動 live conf.d（codex 五次複審 HIGH）"
assert_contains "$INSTALL_CMD" "trap 'ROLLBACK' ERR" "SSM 安裝指令：narrow python + 起 nginx 這段掛 ERR trap，任一步失敗會觸發 ROLLBACK"
assert_contains "$INSTALL_CMD" 'exit 97' "SSM 安裝指令：回滾本身失敗要用 distinct ROLLBACK-FAILED exit code（97），不跟一般失敗（1）混在一起"

rm -rf "$MOCKDIR" "$CAPTURE" "$REPO_ROOT/frontend/dist" "$REPO_ROOT/build/trustforge_frontend_dist.zip"

echo "== 場景 2b（codex 複審 HIGH：dry-run 完全短路 mutating AWS 呼叫）：TF_BOOTSTRAP_DRY_RUN=1 時，即使沒有 mock npm/沒有 mock 到 s3/SG/start-instances 的處理分支，也不該真的呼叫任何 mutating aws 操作或真的 npm build ——只印出組好的遠端指令內容 =="
DRYDIR=$(mktemp -d)
DRYCAP=$(mktemp -d)
cat > "$DRYDIR/aws" <<MOCKEOF
#!/usr/bin/env bash
echo "\$*" >> "$DRYCAP/aws_calls.log"
case "\$*" in
  "sts get-caller-identity"*) echo "123456789012" ;;
  "ec2 describe-instances"*) printf 'i-0123456789abcdef0\trunning\n' ;;
  "ec2 describe-vpcs"*) echo "vpc-0123456789abcdef0" ;;
  "ec2 describe-security-groups"*) echo "sg-0123456789abcdef0" ;;
  "ec2 start-instances"*|"ec2 wait"*|"s3api head-bucket"*|"s3api create-bucket"*|"s3 cp"*|"ec2 authorize-security-group-ingress"*)
    # 這幾種是 mutating 操作：dry-run 修好之後根本不該打到這裡。萬一還是
    # 打進來，故意讓它非零結束、讓測試明確 fail，而不是默默假裝成功。
    echo "❌ [aws-mock] dry-run 模式不該呼叫到 mutating 操作: \$*" >&2
    exit 1 ;;
  *) echo "[aws-mock] 未預期: \$*" >&2; exit 99 ;;
esac
MOCKEOF
chmod +x "$DRYDIR/aws"
# 故意不放 mock npm、PATH 只留基本系統工具（/usr/bin、/bin，沒有真的
# npm/aws 所在的 nvm/homebrew 路徑）：dry-run 若真的呼叫到 npm ci/build，
# 會直接 exit 127（command not found），同樣能明確暴露「dry-run 沒真的
# 短路本機 build」這個問題，而不是意外跑到真的 npm。
if PATH="$DRYDIR:/usr/bin:/bin" TF_BOOTSTRAP_DRY_RUN=1 \
    bash "$REPO_ROOT/deploy/deploy_frontend_nginx.sh" >"$DRYCAP/stdout.log" 2>"$DRYCAP/stderr.log"; then
  echo "  [PASS] dry-run 模式正常結束（exit 0）"
  PASS=$((PASS + 1))
else
  echo "  [FAIL] dry-run 模式應該正常結束（exit 0），只印指令、不 mutate"
  cat "$DRYCAP/stderr.log"
  FAIL=$((FAIL + 1))
fi
AWS_CALLS=$(cat "$DRYCAP/aws_calls.log" 2>/dev/null || echo "")
if printf '%s\n' "$AWS_CALLS" | grep -qE '^(ec2 start-instances|ec2 wait|s3api head-bucket|s3api create-bucket|s3 cp|ec2 authorize-security-group-ingress)'; then
  echo "  [FAIL] dry-run 模式不該呼叫任何 mutating aws 操作，但實際呼叫記錄裡有"
  printf '%s\n' "$AWS_CALLS"
  FAIL=$((FAIL + 1))
else
  echo "  [PASS] dry-run 模式完全沒有呼叫 mutating aws 操作（start-instances/s3api create-bucket/s3 cp/authorize-security-group-ingress 呼叫次數皆為 0）——codex 複審 HIGH 修復點"
  PASS=$((PASS + 1))
fi
assert_contains "$AWS_CALLS" "sts get-caller-identity" "dry-run 模式仍會做唯讀查詢（sts get-caller-identity，純讀取不算 mutation）"
assert_contains "$(cat "$DRYCAP/stdout.log")" "dnf install -y nginx unzip" "dry-run 模式的 stdout 仍有印出組好的遠端指令內容（非空、有實質內容，不是提早 exit 的空字串）"
rm -rf "$DRYDIR" "$DRYCAP"

echo "== 場景 3：X-Real-IP 覆蓋整合測試（harper CISO 建議）=="
# 目的：deploy/nginx-legacy.conf 裡 `proxy_set_header X-Real-IP $remote_addr;`
# 是限流繞過防護的關鍵一行——這裡真的起一支本機 nginx（用
# deploy/nginx-legacy.conf 本尊，非 mock/非 stub）+ 真的本機 python
# （`TRUSTFORGE_TRUST_PROXY=1`），送帶偽造 X-Real-IP 的請求打 `/api/status`，
# 斷言：
#   1. 經過 nginx 的請求，不管客戶端帶什麼偽造 X-Real-IP，nginx 都會覆蓋成
#      真實來源 IP，所以同一來源短時間內連續打會共用同一個限流 bucket，在
#      `_STATUS_RATE_MAX=10`／`_STATUS_RATE_WINDOW=30`（見 web.py）內第 11
#      次觸發 429。
#   2. 繞過 nginx、直接打 python（`TRUSTFORGE_TRUST_PROXY=1` 沒有 nginx 擋在
#      前面覆蓋 header 時）：每次用不同偽造 X-Real-IP，各自佔一個獨立
#      bucket，不會觸發 429——這正是「python 只監聽 127.0.0.1、對外只能經過
#      nginx」這個安全設計的理由，未來若不小心刪掉 nginx 那行
#      `proxy_set_header X-Real-IP $remote_addr;`，這裡的斷言 1 會先紅掉。
#
# 依賴本機 `nginx`、GNU sed（`gsed`，純測試用途改監聽 port，跟腳本本身無關）。
# 任一沒裝就跳過本場景（不影響場景 1/2 已經跑完的結果）。
XRIP_NGINX_PORT=19080
XRIP_PYTHON_PORT=19081
XRIP_SKIP=0
for bin in nginx curl; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "找不到 ${bin}，跳過場景 3（純本機驗證環境依賴，不影響前面場景結果）。" >&2
    XRIP_SKIP=1
  fi
done
XRIP_GSED_BIN="$(command -v gsed || true)"
if [ -z "$XRIP_GSED_BIN" ]; then
  echo "找不到 gsed（GNU sed），跳過場景 3。macOS 可用: brew install gnu-sed" >&2
  XRIP_SKIP=1
fi

if [ "$XRIP_SKIP" -eq 0 ]; then
  XRIP_WORK=$(mktemp -d)
  XRIP_NGINX_STARTED=""
  XRIP_PYTHON_PID=""

  xrip_cleanup() {
    if [ -n "$XRIP_NGINX_STARTED" ]; then
      nginx -c "$XRIP_WORK/harness.conf" -s stop >/dev/null 2>&1 || true
    fi
    if [ -n "$XRIP_PYTHON_PID" ]; then
      kill "$XRIP_PYTHON_PID" >/dev/null 2>&1 || true
      wait "$XRIP_PYTHON_PID" 2>/dev/null || true
    fi
    rm -rf "$XRIP_WORK"
  }
  trap xrip_cleanup EXIT

  mkdir -p "$XRIP_WORK/run"
  cp "$REPO_ROOT/deploy/nginx-legacy.conf" "$XRIP_WORK/nginx-legacy-patched.conf"
  "$XRIP_GSED_BIN" -i \
    -e "s#listen 80;#listen ${XRIP_NGINX_PORT};#" \
    -e "s#listen \[::\]:80;#listen [::]:${XRIP_NGINX_PORT};#" \
    -e "s#proxy_pass http://127.0.0.1:8080;#proxy_pass http://127.0.0.1:${XRIP_PYTHON_PORT};#" \
    "$XRIP_WORK/nginx-legacy-patched.conf"

  cat > "$XRIP_WORK/harness.conf" <<EOF
worker_processes 1;
error_log $XRIP_WORK/run/error.log;
pid $XRIP_WORK/run/nginx.pid;
events { worker_connections 64; }
http {
  include $XRIP_WORK/nginx-legacy-patched.conf;
}
EOF

  if nginx -t -c "$XRIP_WORK/harness.conf" >"$XRIP_WORK/nginx_validate.log" 2>&1; then
    pass_xrip=1
  else
    echo "  [FAIL] patched deploy/nginx-legacy.conf 沒通過 nginx -t"
    cat "$XRIP_WORK/nginx_validate.log"
    FAIL=$((FAIL + 1))
    pass_xrip=0
  fi

  if [ "$pass_xrip" -eq 1 ]; then
    (
      cd "$REPO_ROOT"
      PORT="$XRIP_PYTHON_PORT" TRUSTFORGE_BIND_HOST=127.0.0.1 TRUSTFORGE_TRUST_PROXY=1 \
        CACHE_BACKEND=json PYTHONPATH=src \
        exec python3 -m trustforge.web
    ) >"$XRIP_WORK/python.log" 2>&1 &
    XRIP_PYTHON_PID=$!

    XRIP_READY=0
    for _ in $(seq 1 20); do
      if curl -fsS -o /dev/null "http://127.0.0.1:${XRIP_PYTHON_PORT}/healthz" 2>/dev/null; then
        XRIP_READY=1
        break
      fi
      sleep 0.2
    done
    if [ "$XRIP_READY" -eq 1 ]; then
      echo "  [PASS] 本機 python /healthz 已就緒"
      PASS=$((PASS + 1))
    else
      echo "  [FAIL] 本機 python /healthz 逾時未就緒"
      cat "$XRIP_WORK/python.log"
      FAIL=$((FAIL + 1))
    fi

    if [ "$XRIP_READY" -eq 1 ]; then
      nginx -c "$XRIP_WORK/harness.conf"
      XRIP_NGINX_STARTED="1"
      sleep 0.5

      echo "  -- 經過 nginx：連續 11 次帶不同偽造 X-Real-IP 打 /api/status --"
      NGINX_429_SEEN=0
      for i in $(seq 1 11); do
        code=$(curl -sS -o /dev/null -w '%{http_code}' \
          -H "X-Real-IP: 10.99.0.${i}" \
          "http://127.0.0.1:${XRIP_NGINX_PORT}/api/status")
        if [ "$i" -eq 11 ] && [ "$code" = "429" ]; then
          NGINX_429_SEEN=1
        fi
      done
      if [ "$NGINX_429_SEEN" -eq 1 ]; then
        echo "  [PASS] 經 nginx：第 11 次觸發 429（nginx 覆蓋偽造 X-Real-IP，全部共用真實 IP 的限流 bucket）"
        PASS=$((PASS + 1))
      else
        echo "  [FAIL] 經 nginx：第 11 次沒有觸發 429（預期 nginx 應該覆蓋偽造 X-Real-IP）"
        FAIL=$((FAIL + 1))
      fi

      echo "  -- 繞過 nginx：直接打 python，11 次都用不同偽造 X-Real-IP --"
      DIRECT_429_SEEN=0
      for i in $(seq 1 11); do
        code=$(curl -sS -o /dev/null -w '%{http_code}' \
          -H "X-Real-IP: 10.88.0.${i}" \
          "http://127.0.0.1:${XRIP_PYTHON_PORT}/api/status")
        if [ "$code" = "429" ]; then
          DIRECT_429_SEEN=1
        fi
      done
      if [ "$DIRECT_429_SEEN" -eq 0 ]; then
        echo "  [PASS] 繞過 nginx：11 個不同偽造 X-Real-IP 各自獨立 bucket，沒有觸發 429（符合預期：python 若對外直接聽會被繞過限流，這正是為何只監聽 127.0.0.1）"
        PASS=$((PASS + 1))
      else
        echo "  [FAIL] 繞過 nginx：不預期地觸發了 429"
        FAIL=$((FAIL + 1))
      fi
    fi
  fi

  xrip_cleanup
  trap - EXIT
fi

echo
echo "== 結果：$PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ]
