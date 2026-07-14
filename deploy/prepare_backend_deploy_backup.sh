#!/usr/bin/env bash
set -euo pipefail

PRIMARY_UNIT="${TRUSTFORGE_PRIMARY_UNIT:-/etc/systemd/system/trustforge.service}"
CANDIDATE_UNIT="${TRUSTFORGE_CANDIDATE_UNIT:-/run/systemd/system/trustforge-deploy-candidate.service}"
CANDIDATE_PORT="${TRUSTFORGE_CANDIDATE_PORT:-8081}"

if [ ! -f "$PRIMARY_UNIT" ]; then
  echo "candidate backend: primary unit missing: $PRIMARY_UNIT" >&2
  exit 1
fi

systemctl stop trustforge-deploy-candidate.service 2>/dev/null || true
systemctl reset-failed trustforge-deploy-candidate.service 2>/dev/null || true
cp "$PRIMARY_UNIT" "$CANDIDATE_UNIT"
sed -i 's/^Description=.*/Description=TrustForge deploy candidate backend/' "$CANDIDATE_UNIT"
sed -i 's/^Restart=.*/Restart=no/' "$CANDIDATE_UNIT"
sed -i '/^Environment=PORT=/d' "$CANDIDATE_UNIT"
sed -i "/^ExecStart=/i Environment=PORT=$CANDIDATE_PORT\nRuntimeMaxSec=5min" "$CANDIDATE_UNIT"

systemctl daemon-reload
systemctl start trustforge-deploy-candidate.service
for _ in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:$CANDIDATE_PORT/api/health" >/dev/null 2>&1; then
    echo "candidate backend healthy on port $CANDIDATE_PORT"
    exit 0
  fi
  sleep 1
done

systemctl status trustforge-deploy-candidate.service --no-pager -l >&2 || true
journalctl -u trustforge-deploy-candidate.service -n 40 --no-pager >&2 || true
exit 1
