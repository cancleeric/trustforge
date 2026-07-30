#!/usr/bin/env bash
set -euo pipefail

SERVICE_FILE="${TRUSTFORGE_PREVIEW_SERVICE_FILE:-/etc/systemd/system/trustforge.service}"
APP_ROOT="${TRUSTFORGE_PREVIEW_APP_ROOT:-/opt/trustforge}"
PYTHON_BIN="${TRUSTFORGE_PREVIEW_PYTHON_BIN:-/usr/bin/python3.11}"

if ! grep -qx 'Environment=TRUSTFORGE_PREVIEW_ADMISSION_ENABLED=1' "$SERVICE_FILE"; then
  exit 0
fi

declare -a preview_env=()
while IFS= read -r line; do
  if [[ "$line" =~ ^Environment=(TRUSTFORGE_PREVIEW_[A-Z0-9_]+)=([A-Za-z0-9_./:-]+)$ ]]; then
    preview_env+=("${BASH_REMATCH[1]}=${BASH_REMATCH[2]}")
  fi
done < "$SERVICE_FILE"

result=$(cd "$APP_ROOT" && env "${preview_env[@]}" "$PYTHON_BIN" \
  deploy/preview_admission_smoke.py)
if [ "$result" != "preview_admission_smoke=ready" ]; then
  echo "preview_admission_release_gate=unavailable" >&2
  exit 1
fi
echo "preview_admission_release_gate=ready"
