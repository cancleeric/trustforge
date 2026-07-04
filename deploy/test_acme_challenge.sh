#!/usr/bin/env bash
# Let's Encrypt HTTP-01 acme-challenge location 整合測試（codex 複審
# HIGH：`certbot --nginx -d <domain> --non-interactive` 需要 active nginx
# config 有精準匹配該 domain 的 `server_name` block，但
# deploy/nginx-legacy.conf／deploy/nginx-react-http.conf 都用
# `server_name _;`——`--nginx` plugin non-interactive 配對不到，會簽發
# 失敗/留半殘憑證。修法：改用 `certbot certonly --webroot`，只取憑證、不碰
# nginx config，三份 nginx conf（legacy/react-http/react-TLS，TLS 版是續簽
# 用）都新增了 `location ^~ /.well-known/acme-challenge/ { root
# /var/www/certbot; }` 直接從檔案系統回應 challenge。
#
# 這裡真的起本機 nginx（非 mock），對每一份 conf：
#   1. 放一個假 challenge 檔案到 webroot（`/.well-known/acme-challenge/
#      test-token`），curl 該路徑回 200、內容正確——證明 location 真的
#      能從檔案系統服務 challenge。
#   2. **不啟動任何本機 python backend**——若 challenge 路徑被
#      `location /`（proxy_pass 給 python）吃掉而不是被 acme-challenge
#      location 攔截，會因為連不到 python 而變 502/504，不會是 200，藉此
#      證明「不被 proxy 吃」。
#   3. deploy/nginx.conf 額外驗證：/.well-known/acme-challenge/ 不受
#      port-80 的 301 全站 HTTPS redirect 影響（續簽時 nginx.conf 是 live
#      config，若沒有這個例外，certbot renew 會失敗）。
#
# 依賴本機 `nginx`、`curl`、`openssl`（deploy/nginx.conf 需要自簽憑證才能
# nginx -t/啟動）、GNU sed（`gsed`）——任一個沒裝就跳過整份測試。
#
# 用法：bash deploy/test_acme_challenge.sh
set -euo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
PASS=0
FAIL=0

pass() { echo "  [PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); }

for bin in nginx curl openssl; do
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
NGINX_RUNNING=0
HARNESS_CONF=""

cleanup() {
  if [ "$NGINX_RUNNING" = "1" ] && [ -n "$HARNESS_CONF" ]; then
    nginx -c "$HARNESS_CONF" -s stop >/dev/null 2>&1 || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

stop_nginx() {
  if [ "$NGINX_RUNNING" = "1" ] && [ -n "$HARNESS_CONF" ]; then
    nginx -c "$HARNESS_CONF" -s stop >/dev/null 2>&1 || true
    NGINX_RUNNING=0
  fi
}

# 放一個假 challenge 檔案到 webroot（供三個場景共用）
CHALLENGE_TOKEN="test-token-abc123"
CHALLENGE_CONTENT="acme-challenge-fixture-content"

run_acme_scenario() {
  local label="$1" src_conf="$2" http_port="$3" needs_tls="$4"
  local case_dir="$WORK/$label"
  mkdir -p "$case_dir/dist/assets" "$case_dir/run" "$case_dir/webroot/.well-known/acme-challenge" "$case_dir/certs"
  echo '<html><body>stub index</body></html>' > "$case_dir/dist/index.html"
  printf '%s' "$CHALLENGE_CONTENT" > "$case_dir/webroot/.well-known/acme-challenge/${CHALLENGE_TOKEN}"

  cp "$REPO_ROOT/deploy/${src_conf}" "$case_dir/patched.conf"
  local sed_args=(
    -e "s#listen 80;#listen ${http_port};#"
    -e "s#listen \[::\]:80;#listen [::]:${http_port};#"
    -e "s#root /opt/trustforge/frontend/dist;#root ${case_dir}/dist;#"
    -e "s#root /var/www/certbot;#root ${case_dir}/webroot;#"
  )
  if [ "$needs_tls" = "1" ]; then
    openssl req -x509 -newkey rsa:2048 -keyout "$case_dir/certs/privkey.pem" \
      -out "$case_dir/certs/fullchain.pem" -days 1 -nodes -subj "/CN=localhost" \
      >/dev/null 2>&1
    sed_args+=(
      -e "s#listen 443 ssl;#listen $((http_port + 1)) ssl;#"
      -e "s#listen \[::\]:443 ssl;#listen [::]:$((http_port + 1)) ssl;#"
      -e "s#/etc/letsencrypt/live/trustforge.hurricanesoft.com.tw/fullchain.pem#${case_dir}/certs/fullchain.pem#"
      -e "s#/etc/letsencrypt/live/trustforge.hurricanesoft.com.tw/privkey.pem#${case_dir}/certs/privkey.pem#"
    )
  fi
  "$GSED_BIN" -i "${sed_args[@]}" "$case_dir/patched.conf"

  HARNESS_CONF="$case_dir/harness.conf"
  cat > "$HARNESS_CONF" <<EOF
worker_processes 1;
error_log $case_dir/run/error.log;
pid $case_dir/run/nginx.pid;
events { worker_connections 64; }
http {
  include $case_dir/patched.conf;
}
EOF

  echo "== [$label] 前置：候選設定驗證（patched deploy/${src_conf}）=="
  if nginx -t -c "$HARNESS_CONF" >"$case_dir/nginx_validate.log" 2>&1; then
    pass "[$label] patched deploy/${src_conf} 通過 nginx -t"
  else
    fail "[$label] patched deploy/${src_conf} 沒通過 nginx -t"
    cat "$case_dir/nginx_validate.log"
    return
  fi

  # 刻意**不啟動任何本機 python**：若 challenge 被 location / proxy_pass
  # 吃掉、打去不存在的 127.0.0.1:8080，會是 502/504，不會是 200。
  nginx -c "$HARNESS_CONF"
  NGINX_RUNNING=1
  sleep 0.3

  local base="http://127.0.0.1:${http_port}"
  local code body
  code=$(curl -sS -o "$case_dir/body.txt" -w '%{http_code}' "$base/.well-known/acme-challenge/${CHALLENGE_TOKEN}")
  body=$(cat "$case_dir/body.txt")
  if [ "$code" = "200" ]; then
    pass "[$label] /.well-known/acme-challenge/${CHALLENGE_TOKEN} 回 200（不被 proxy 吃，因為沒起 python 也沒 502）"
  else
    fail "[$label] /.well-known/acme-challenge/${CHALLENGE_TOKEN} 沒回 200 — status=${code}"
  fi
  if [ "$body" = "$CHALLENGE_CONTENT" ]; then
    pass "[$label] challenge 內容正確（真的從 webroot 檔案系統讀到，不是隨便回 200）"
  else
    fail "[$label] challenge 內容不對 — 實際：${body}"
  fi

  if [ "$needs_tls" = "1" ]; then
    echo "== [$label] 額外驗證：/.well-known/acme-challenge/ 不受 301 全站 HTTPS redirect 影響（續簽用） =="
    local redirect_code
    redirect_code=$(curl -sS -o /dev/null -w '%{http_code}' "$base/")
    if [ "$redirect_code" = "301" ]; then
      pass "[$label] / 本身仍正常回 301（確認 301 catch-all 沒被本次修改破壞）"
    else
      fail "[$label] / 沒有回 301 — status=${redirect_code}（預期仍是 301，這裡只是要確認 acme-challenge 例外沒有連帶弄壞既有 redirect）"
    fi
  fi

  stop_nginx
}

run_acme_scenario "legacy" "nginx-legacy.conf" 18280 0
run_acme_scenario "react-http" "nginx-react-http.conf" 18380 0
run_acme_scenario "react-tls-renewal" "nginx.conf" 18480 1
# codex 複審 HIGH（HSTS-safe legacy 回滾）：legacy-tls 是 react→legacy 回滾
# 時，憑證已存在情況下改用的 443 版本，續簽時（若 live 的剛好是這份）一樣
# 得靠這個 acme-challenge 例外才不會被自己的 301 導去 https，見
# deploy/nginx-legacy-tls.conf、deploy/cutover_switch.sh。
run_acme_scenario "legacy-tls-renewal" "nginx-legacy-tls.conf" 18580 1

echo
echo "== 結果：$PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ]
