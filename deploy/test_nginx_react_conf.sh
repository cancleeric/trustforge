#!/usr/bin/env bash
# deploy/nginx.conf（cutover 後 React 拓樸）路由/安全 header 測試：真的起一個
# 本機 nginx（監聽非特權測試 port，非真部署/非真 AWS）+ 一份 stub
# `frontend/dist`，斷言：
#
#   1. `/`、`/analyze`（SPA client-side route）、`/index.html` 三者都
#      fallback／命中同一份 index.html（200，內容一致）。
#   2. 上述三者的回應都真的帶有 CSP / X-Frame-Options / Referrer-Policy /
#      X-Content-Type-Options 安全 header（不是只看 conf 檔裡「寫了」
#      add_header，而是真的打過去量得到——nginx 的 `try_files ...
#      /index.html` fallback 是內部重導向，會拿 `/index.html` 重新跑一次
#      location 比對，真正命中的是 `location = /index.html`，不是
#      `location /`，兩邊 add_header 沒同步的話很容易看起來「有寫」但
#      實際生效的 header 是空的——這正是本測試存在的理由）。
#   3. `/assets/<hash>.css` 走長快取 Cache-Control，不帶 CSP（靜態資源，
#      非 SPA 頁面本身）。
#
# 依賴本機 `nginx`、`openssl`（自簽憑證測試用）、GNU sed（`gsed`，macOS
# 內建 BSD sed 的 `-i` 語法跟這份 conf 沒關係，這裡單純拿來改測試用的 port/
# root/憑證路徑，跟 cutover_switch.sh 的遠端 sed 無關）——任一個沒裝就跳過
# 整份測試（沒有 false pass 的空間）。
#
# 用法：bash deploy/test_nginx_react_conf.sh
set -euo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
PASS=0
FAIL=0

pass() { echo "  [PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); }

for bin in nginx openssl curl; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "找不到 ${bin}，跳過本測試（純本機驗證環境依賴，不影響腳本本身邏輯）。" >&2
    exit 0
  fi
done
GSED_BIN="$(command -v gsed || true)"
if [ -z "$GSED_BIN" ]; then
  echo "找不到 gsed（GNU sed），跳過本測試。macOS 可用: brew install gnu-sed" >&2
  exit 0
fi

WORK=$(mktemp -d)
NGINX_PID=""
PYTHON_PID=""

cleanup() {
  if [ -n "$NGINX_PID" ]; then
    nginx -c "$WORK/harness.conf" -s stop >/dev/null 2>&1 || true
  fi
  if [ -n "$PYTHON_PID" ]; then
    kill "$PYTHON_PID" >/dev/null 2>&1 || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

mkdir -p "$WORK/dist/assets" "$WORK/certs" "$WORK/run"
echo '<html><body>react stub index</body></html>' > "$WORK/dist/index.html"
echo 'body{color:#000}' > "$WORK/dist/assets/app-teststub.css"

openssl req -x509 -newkey rsa:2048 -keyout "$WORK/certs/privkey.pem" \
  -out "$WORK/certs/fullchain.pem" -days 1 -nodes -subj "/CN=localhost" \
  >/dev/null 2>&1

# ── 用 gsed 把 deploy/nginx.conf 的 port/root/憑證路徑改成測試用的值
# （非特權 port、stub dist、自簽憑證），語意跟原檔完全一致，只是本機可跑──
cp "$REPO_ROOT/deploy/nginx.conf" "$WORK/nginx-react-patched.conf"
"$GSED_BIN" -i \
  -e "s#listen 80;#listen 18080;#" \
  -e "s#listen \[::\]:80;#listen [::]:18080;#" \
  -e "s#listen 443 ssl;#listen 18443 ssl;#" \
  -e "s#listen \[::\]:443 ssl;#listen [::]:18443 ssl;#" \
  -e "s#/etc/letsencrypt/live/trustforge.example.com/fullchain.pem#$WORK/certs/fullchain.pem#" \
  -e "s#/etc/letsencrypt/live/trustforge.example.com/privkey.pem#$WORK/certs/privkey.pem#" \
  -e "s#root /opt/trustforge/frontend/dist;#root $WORK/dist;#" \
  "$WORK/nginx-react-patched.conf"

cat > "$WORK/harness.conf" <<EOF
worker_processes 1;
error_log $WORK/run/error.log;
pid $WORK/run/nginx.pid;
events { worker_connections 64; }
http {
  include $WORK/nginx-react-patched.conf;
}
EOF

echo "== 前置：候選設定驗證（patched deploy/nginx.conf）=="
if nginx -t -c "$WORK/harness.conf" >"$WORK/nginx_validate.log" 2>&1; then
  pass "patched deploy/nginx.conf 通過 nginx -t"
else
  fail "patched deploy/nginx.conf 沒通過 nginx -t"
  cat "$WORK/nginx_validate.log"
  echo "== 結果：$PASS passed, $FAIL failed =="
  exit 1
fi

# ── 起本機 python（CACHE_BACKEND=json 全離線、CSP_MODE=react 跟 nginx 同步）──
(
  cd "$REPO_ROOT"
  PORT=8080 TRUSTFORGE_BIND_HOST=127.0.0.1 TRUSTFORGE_TRUST_PROXY=1 \
    TRUSTFORGE_CSP_MODE=react CACHE_BACKEND=json PYTHONPATH=src \
    exec python3 -m trustforge.web
) >"$WORK/python.log" 2>&1 &
PYTHON_PID=$!

for _ in $(seq 1 20); do
  if curl -fsS -o /dev/null http://127.0.0.1:8080/healthz 2>/dev/null; then
    break
  fi
  sleep 0.2
done
if curl -fsS -o /dev/null http://127.0.0.1:8080/healthz 2>/dev/null; then
  pass "本機 python /healthz 已就緒"
else
  fail "本機 python /healthz 逾時未就緒"
  cat "$WORK/python.log"
  echo "== 結果：$PASS passed, $FAIL failed =="
  exit 1
fi

nginx -c "$WORK/harness.conf"
NGINX_PID="started"
sleep 0.5

BASE="https://127.0.0.1:18443"

fetch_headers() {
  # $1 = path，回傳 header 內容（-k：本機自簽憑證，測試用途）
  curl -sSk -D - -o "$WORK/last_body.html" "$BASE$1"
}

assert_status_200() {
  local path="$1" desc="$2"
  local code
  code=$(curl -sSk -o /dev/null -w '%{http_code}' "$BASE$path")
  if [ "$code" = "200" ]; then
    pass "${desc}（status=${code}）"
  else
    fail "${desc} — status=${code}（預期 200）"
  fi
}

assert_header_present() {
  local headers="$1" name="$2" desc="$3"
  if grep -qi "^${name}:" <<<"$headers"; then
    pass "$desc"
  else
    fail "$desc — 回應裡沒有 $name header"
  fi
}

assert_header_absent() {
  local headers="$1" name="$2" desc="$3"
  if grep -qi "^${name}:" <<<"$headers"; then
    fail "$desc — 不預期出現 $name header，但有"
  else
    pass "$desc"
  fi
}

echo "== 場景 1：/ （SPA 根路由）=="
assert_status_200 "/" "/ 回應 200"
H=$(fetch_headers "/")
BODY_ROOT=$(cat "$WORK/last_body.html")
assert_header_present "$H" "content-security-policy" "/ 有 CSP header"
assert_header_present "$H" "x-frame-options" "/ 有 X-Frame-Options header"
assert_header_present "$H" "referrer-policy" "/ 有 Referrer-Policy header"
assert_header_present "$H" "x-content-type-options" "/ 有 X-Content-Type-Options header"

echo "== 場景 2：/analyze （SPA client-side route，非真實檔案，應 fallback 回 index.html）=="
assert_status_200 "/analyze" "/analyze 回應 200（fallback 成功，不是 404）"
H=$(fetch_headers "/analyze")
BODY_ANALYZE=$(cat "$WORK/last_body.html")
if [ "$BODY_ANALYZE" = "$BODY_ROOT" ]; then
  pass "/analyze fallback 內容跟 / 一致（同一份 index.html）"
else
  fail "/analyze fallback 內容跟 / 不一致"
fi
assert_header_present "$H" "content-security-policy" "/analyze 有 CSP header（fallback 後仍要有，這是本測試主要斷言）"
assert_header_present "$H" "x-frame-options" "/analyze 有 X-Frame-Options header"
assert_header_present "$H" "referrer-policy" "/analyze 有 Referrer-Policy header"
assert_header_present "$H" "x-content-type-options" "/analyze 有 X-Content-Type-Options header"

echo "== 場景 3：/index.html（直接命中，非經 try_files 重導向）=="
assert_status_200 "/index.html" "/index.html 回應 200"
H=$(fetch_headers "/index.html")
assert_header_present "$H" "content-security-policy" "/index.html 有 CSP header"
assert_header_present "$H" "x-frame-options" "/index.html 有 X-Frame-Options header"

echo "== 場景 4：/assets/<hash>.css（靜態資源，長快取，不帶 CSP）=="
assert_status_200 "/assets/app-teststub.css" "/assets/app-teststub.css 回應 200"
H=$(fetch_headers "/assets/app-teststub.css")
assert_header_present "$H" "cache-control" "/assets/*.css 有 Cache-Control（長快取）header"
assert_header_absent "$H" "content-security-policy" "/assets/*.css 不帶 CSP header（靜態資源非 SPA 頁面本身）"

rm -f "$WORK/last_body.html"

echo
echo "== 結果：$PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ]
