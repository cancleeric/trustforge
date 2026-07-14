#!/usr/bin/env bash
set -euo pipefail

URL="${1:?usage: monitor_deploy_health.sh URL -- command [args...]}"
shift
if [ "${1:-}" != "--" ]; then
  echo "expected -- before deploy command" >&2
  exit 2
fi
shift
if [ "$#" -eq 0 ]; then
  echo "missing deploy command" >&2
  exit 2
fi

INTERVAL="${TRUSTFORGE_DEPLOY_MONITOR_INTERVAL:-0.25}"
CONNECT_TIMEOUT="${TRUSTFORGE_DEPLOY_MONITOR_CONNECT_TIMEOUT:-2}"
REQUEST_TIMEOUT="${TRUSTFORGE_DEPLOY_MONITOR_REQUEST_TIMEOUT:-4}"
EVIDENCE="${TRUSTFORGE_DEPLOY_MONITOR_EVIDENCE:-out/release/deploy-health-canary.jsonl}"
mkdir -p "$(dirname "$EVIDENCE")"
: >"$EVIDENCE"

probe() {
  local phase="$1" started code rc=0 latency
  started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  code=$(curl -sS -o /dev/null -w '%{http_code} %{time_total}' \
    --connect-timeout "$CONNECT_TIMEOUT" --max-time "$REQUEST_TIMEOUT" \
    "$URL") || rc=$?
  latency="${code#* }"
  code="${code%% *}"
  [ -n "$code" ] || code="000"
  printf '{"timestamp":"%s","phase":"%s","http_code":%s,"latency_seconds":%s,"curl_exit":%s}\n' \
    "$started" "$phase" "$((10#$code))" "${latency:-0}" "$rc" >>"$EVIDENCE"
  [ "$rc" -eq 0 ] && [ "$code" -ge 200 ] && [ "$code" -lt 300 ]
}

if ! probe before; then
  echo "deploy health monitor: baseline probe failed: $URL" >&2
  exit 1
fi

"$@" &
deploy_pid=$!
probe_failed=0
while kill -0 "$deploy_pid" 2>/dev/null; do
  if ! probe during; then
    probe_failed=1
  fi
  sleep "$INTERVAL"
done

deploy_status=0
wait "$deploy_pid" || deploy_status=$?
if ! probe after; then
  probe_failed=1
fi

if [ "$deploy_status" -ne 0 ]; then
  echo "deploy health monitor: deploy command failed with status $deploy_status" >&2
  exit "$deploy_status"
fi
if [ "$probe_failed" -ne 0 ]; then
  echo "deploy health monitor: public health interruption detected; evidence: $EVIDENCE" >&2
  exit 1
fi
echo "deploy health monitor passed; evidence: $EVIDENCE"
