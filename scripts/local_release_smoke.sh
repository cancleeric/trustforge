#!/usr/bin/env bash
# Release preflight: start the real local HTTP server before any production CD.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if [ -x "$ROOT/.venv/bin/python" ]; then
    PYTHON="$ROOT/.venv/bin/python"
  else
    PYTHON="$(command -v python3)"
  fi
fi
PORT="${TRUSTFORGE_SMOKE_PORT:-18788}"
BASE="http://127.0.0.1:${PORT}"
LOG="$(mktemp)"
ANALYZE_RESPONSE="$(mktemp)"

cleanup() {
  [ -n "${PID:-}" ] && kill "$PID" 2>/dev/null || true
  rm -f "$LOG" "$ANALYZE_RESPONSE"
}
trap cleanup EXIT

cd "$ROOT"
PORT="$PORT" TRUSTFORGE_BIND_HOST=127.0.0.1 CACHE_BACKEND=json \
  "$PYTHON" -m trustforge.web >"$LOG" 2>&1 &
PID=$!
READY=0
for _ in $(seq 1 20); do
  if curl -fsS --max-time 2 "$BASE/api/health" >/dev/null 2>&1; then
    READY=1
    break
  fi
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "local release smoke server failed to start:" >&2
    cat "$LOG" >&2
    exit 1
  fi
  sleep 0.5
done
[ "$READY" -eq 1 ] || {
  echo "local release smoke server did not become healthy:" >&2
  cat "$LOG" >&2
  exit 1
}
curl -fsS "$BASE/api/health" | grep -q '"ok": true'
curl -fsS "$BASE/api/overview" | grep -q '"coins"'
curl -fsS "$BASE/api/costs?offset=0&limit=50" | grep -q '"run_count"'
curl -fsS "$BASE/api/costs?offset=50&limit=50" | grep -q '"offset": 50'
curl -fsS --max-time 60 \
  "$BASE/api/analyze?coin=BTC&type=multi_source&q=local-release-smoke" >"$ANALYZE_RESPONSE"
"$PYTHON" - "$ANALYZE_RESPONSE" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
data = payload.get("data") or {}
expected_nodes = [
    "source_ingestion", "claim_extraction", "trust_reasoning",
    "evidence_assembly", "report_delivery",
]
assert payload.get("ok") is True
assert data.get("report", {}).get("coin") == "BTC"
assert data.get("evidence"), "analysis returned no Evidence"
assert [node.get("id") for node in data.get("execution", {}).get("nodes", [])] == expected_nodes
assert data.get("execution_log"), "analysis returned no Execution Log"
PY
echo "local release smoke passed: $BASE"
