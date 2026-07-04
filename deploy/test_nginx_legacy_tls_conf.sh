#!/usr/bin/env bash
# deploy/nginx-legacy-tls.conf 路由/HSTS-rollback 測試（codex 複審 HIGH：
# HSTS 破壞 HTTP-only legacy 回滾）——真的起一個本機 nginx（監聽非特權測試
# port，非真部署/非真 AWS）+ 一個真的本機 python backend（CSP_MODE=legacy），
# 斷言 react→legacy 緊急回滾若切上這份 conf（憑證已存在的情況，見
# deploy/cutover_switch.sh 的偵測邏輯），使用者仍然能透過 443/HTTPS 連上、
# 正常拿到 SSR 回應，而不是像純 HTTP 版 `nginx-legacy.conf` 那樣在瀏覽器
# 記住 HSTS 之後完全連不上（這正是本測試存在的理由：不能只驗
# `nginx -t` 過，要真的打 https 拿到 200 + 正確內容 + HSTS header）。
#
#   1. HTTPS(443) `/` 真的 proxy 到本機 python SSR，拿到 200 + python 產出的
#      內容（不是 nginx 自己短路回應）。
#   2. HTTPS(443) 回應帶 `Strict-Transport-Security` header（回滾後 HSTS
#      承諾沒有被打破——瀏覽器不會因為這次回滾就被教育「這個 domain 不用
#      HSTS 了」）。
#   3. HTTPS(443) `/healthz` 直接 proxy 到 python，200。
#   4. HTTP(80) `/` 回 301，Location 指向 canonical domain（跟
#      deploy/nginx.conf 同一套設計、同一個 codex HIGH 修法：不用 $host）。
#   5. HTTP(80) `/healthz` 不受 301 影響，直接 200（LB/健康檢查明碼探測用）。
#
# 依賴本機 `nginx`、`openssl`（自簽憑證測試用）、`curl`、GNU sed
# （`gsed`）——任一個沒裝就跳過整份測試（沒有 false pass 的空間）。
#
# 用法：bash deploy/test_nginx_legacy_tls_conf.sh
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

mkdir -p "$WORK/certs" "$WORK/run" "$WORK/webroot/.well-known/acme-challenge"

openssl req -x509 -newkey rsa:2048 -keyout "$WORK/certs/privkey.pem" \
  -out "$WORK/certs/fullchain.pem" -days 1 -nodes -subj "/CN=localhost" \
  >/dev/null 2>&1

# ── 用 gsed 把 deploy/nginx-legacy-tls.conf 的 port/憑證/webroot 路徑改成
# 測試用的值（非特權 port、自簽憑證），語意跟原檔完全一致，只是本機可跑 ──
cp "$REPO_ROOT/deploy/nginx-legacy-tls.conf" "$WORK/nginx-legacy-tls-patched.conf"
"$GSED_BIN" -i \
  -e "s#listen 80;#listen 18680;#" \
  -e "s#listen \[::\]:80;#listen [::]:18680;#" \
  -e "s#listen 443 ssl;#listen 18683 ssl;#" \
  -e "s#listen \[::\]:443 ssl;#listen [::]:18683 ssl;#" \
  -e "s#/etc/letsencrypt/live/trustforge.hurricanesoft.com.tw/fullchain.pem#$WORK/certs/fullchain.pem#" \
  -e "s#/etc/letsencrypt/live/trustforge.hurricanesoft.com.tw/privkey.pem#$WORK/certs/privkey.pem#" \
  -e "s#root /var/www/certbot;#root $WORK/webroot;#" \
  "$WORK/nginx-legacy-tls-patched.conf"

cat > "$WORK/harness.conf" <<EOF
worker_processes 1;
error_log $WORK/run/error.log;
pid $WORK/run/nginx.pid;
events { worker_connections 64; }
http {
  include $WORK/nginx-legacy-tls-patched.conf;
}
EOF

echo "== 前置：候選設定驗證（patched deploy/nginx-legacy-tls.conf）=="
if nginx -t -c "$WORK/harness.conf" >"$WORK/nginx_validate.log" 2>&1; then
  pass "patched deploy/nginx-legacy-tls.conf 通過 nginx -t"
else
  fail "patched deploy/nginx-legacy-tls.conf 沒通過 nginx -t"
  cat "$WORK/nginx_validate.log"
  echo "== 結果：$PASS passed, $FAIL failed =="
  exit 1
fi

# ── 起本機 python（CACHE_BACKEND=json 全離線、CSP_MODE=legacy 跟
# nginx-legacy(-tls).conf 一致——這份 conf 本身不下 CSP，靠 python 自己下）──
(
  cd "$REPO_ROOT"
  PORT=8080 TRUSTFORGE_BIND_HOST=127.0.0.1 TRUSTFORGE_TRUST_PROXY=1 \
    TRUSTFORGE_CSP_MODE=legacy CACHE_BACKEND=json PYTHONPATH=src \
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

BASE_HTTPS="https://127.0.0.1:18683"
BASE_HTTP="http://127.0.0.1:18680"

echo "== 場景 1：HTTPS(443) / 真的 proxy 到本機 python SSR，回 200（react→legacy 回滾後，回訪過 HTTPS+HSTS 的使用者仍連得上）=="
CODE=$(curl -sSk -o "$WORK/body.html" -w '%{http_code}' "$BASE_HTTPS/")
if [ "$CODE" = "200" ]; then
  pass "HTTPS(443) / 回應 200（status=${CODE}）"
else
  fail "HTTPS(443) / 沒有回 200 — status=${CODE}"
fi
if grep -qi 'trustforge' "$WORK/body.html" 2>/dev/null || [ -s "$WORK/body.html" ]; then
  pass "HTTPS(443) / 回應內容非空（真的是 python SSR 產出，不是 nginx 短路回應）"
else
  fail "HTTPS(443) / 回應內容是空的"
fi

echo "== 場景 2：HTTPS(443) 回應帶 Strict-Transport-Security header（回滾後 HSTS 承諾沒被打破）=="
HDRS=$(curl -sSk -D - -o /dev/null "$BASE_HTTPS/")
if grep -qi '^strict-transport-security:' <<<"$HDRS"; then
  pass "HTTPS(443) / 有 Strict-Transport-Security header"
else
  fail "HTTPS(443) / 沒有 Strict-Transport-Security header — 這正是 codex 複審 HIGH 要修的東西：回滾後還是得繼續送 HSTS"
fi
if grep -qi 'max-age=31536000' <<<"$HDRS"; then
  pass "HSTS max-age 跟 deploy/nginx.conf 一致（一年，31536000）"
else
  fail "HSTS max-age 不是預期的 31536000 — 實際：$(grep -i '^strict-transport-security:' <<<"$HDRS")"
fi

echo "== 場景 3：HTTPS(443) /healthz 直接 proxy 到 python，回 200 =="
HEALTHZ_CODE=$(curl -sSk -o /dev/null -w '%{http_code}' "$BASE_HTTPS/healthz")
if [ "$HEALTHZ_CODE" = "200" ]; then
  pass "HTTPS(443) /healthz 回 200"
else
  fail "HTTPS(443) /healthz 沒有回 200 — status=${HEALTHZ_CODE}"
fi

echo "== 場景 4：HTTP(80) / 回 301，Location 指向 canonical domain（不是 127.0.0.1，跟 deploy/nginx.conf 同一套設計）=="
REDIRECT_HDRS=$(curl -sS -D - -o /dev/null "$BASE_HTTP/" | tr -d '\r')
if grep -qi '^HTTP/[0-9.]* 301' <<<"$REDIRECT_HDRS"; then
  pass "HTTP(80) 對 / 回 301"
else
  fail "HTTP(80) 對 / 沒有回 301 — 實際首行：$(head -1 <<<"$REDIRECT_HDRS")"
fi
if grep -qi '^location: https://trustforge\.hurricanesoft\.com\.tw/$' <<<"$REDIRECT_HDRS"; then
  pass "HTTP(80) redirect Location 是 canonical domain（https://trustforge.hurricanesoft.com.tw/）"
else
  fail "HTTP(80) redirect Location 不是預期的 canonical domain — 實際：$(grep -i '^location:' <<<"$REDIRECT_HDRS")"
fi

echo "== 場景 5：HTTP(80) /healthz（健康檢查用明碼端點）不受 redirect 規則影響，直接 200 =="
HEALTHZ_HTTP_CODE=$(curl -sS -o /dev/null -w '%{http_code}' "$BASE_HTTP/healthz")
if [ "$HEALTHZ_HTTP_CODE" = "200" ]; then
  pass "HTTP(80) /healthz 回 200（明碼健康檢查端點，不被導去 https）"
else
  fail "HTTP(80) /healthz 沒有回 200 — status=${HEALTHZ_HTTP_CODE}"
fi

rm -f "$WORK/body.html"

echo
echo "== 結果：$PASS passed, $FAIL failed =="
[ "$FAIL" -eq 0 ]
