#!/usr/bin/env bash
# ============================================================================
# zero_downtime_restart.sh  (issue #280)
#
# Zero-downtime backend restart: starts a new TrustForge process on the backup
# port, verifies health, then stops the old process. nginx upstream failover
# handles the cutover transparently — the backup server absorbs traffic while
# the primary is restarted with the new code.
#
# Usage:
#   bash deploy/zero_downtime_restart.sh [--primary-port 8080] [--backup-port 8081]
#
# Prerequisites:
#   - nginx running with upstream `trustforge_backend` containing both ports
#   - systemd units: trustforge.service (primary) and trustforge-canary.service
#
# Flow:
#   1. Start canary (new code) on backup port
#   2. Health-check canary until it responds (max 30s)
#   3. Reload nginx to route traffic to canary (now healthy)
#   4. Restart primary service (old code → new code)
#   5. Health-check primary until it responds (max 30s)
#   6. Stop canary (primary is back, nginx routes to primary first)
#
# The key insight: nginx `max_fails=1 fail_timeout=1s` on the primary means
# that during step 4 (primary restart), nginx detects the failure within 1s
# and routes to the backup (canary) which is already healthy. Once primary
# comes back (step 5), nginx resumes sending to it as primary.
# ============================================================================
set -euo pipefail

PRIMARY_PORT="${1:-8080}"
BACKUP_PORT="${2:-8081}"
HEALTH_TIMEOUT="${TRUSTFORGE_HEALTH_TIMEOUT:-30}"
HEALTH_INTERVAL="${TRUSTFORGE_HEALTH_INTERVAL:-1}"
CANARY_UNIT="trustforge-canary-$$"

log() { printf '[zero-downtime] %s %s\n' "$(date -u +%H:%M:%S)" "$*"; }

wait_for_health() {
  local port="$1" label="$2" max_wait="$HEALTH_TIMEOUT" waited=0
  while [ "$waited" -lt "$max_wait" ]; do
    if curl -fsS --connect-timeout 2 --max-time 3 "http://127.0.0.1:${port}/healthz" >/dev/null 2>&1; then
      log "$label healthy on port $port (${waited}s)"
      return 0
    fi
    sleep "$HEALTH_INTERVAL"
    waited=$((waited + HEALTH_INTERVAL))
  done
  log "ERROR: $label failed health check after ${max_wait}s on port $port"
  return 1
}

# Step 0: Verify primary is currently healthy (baseline)
if ! curl -fsS --connect-timeout 2 --max-time 3 "http://127.0.0.1:${PRIMARY_PORT}/healthz" >/dev/null 2>&1; then
  log "WARNING: primary port $PRIMARY_PORT not healthy (may already be down); proceeding with direct restart"
  systemctl restart trustforge
  wait_for_health "$PRIMARY_PORT" "primary"
  exit $?
fi

# Step 1: Start canary on backup port
log "Starting canary on port $BACKUP_PORT..."
systemctl stop "$CANARY_UNIT" 2>/dev/null || true
# Create transient canary unit that mirrors trustforge.service but on backup port
systemd-run --unit="$CANARY_UNIT" \
  --property="Environment=PORT=$BACKUP_PORT" \
  --property="Environment=TRUSTFORGE_HOME=/opt/trustforge" \
  --property="Environment=PYTHONPATH=/opt/trustforge" \
  --property="Environment=AWS_REGION=${AWS_REGION:-ap-southeast-2}" \
  --property="Environment=CACHE_BACKEND=${CACHE_BACKEND:-dynamodb}" \
  --property="Environment=TRUSTFORGE_CACHE_TABLE=${TRUSTFORGE_CACHE_TABLE:-trustforge-connector-cache}" \
  --property="Environment=TRUSTFORGE_COST_LEDGER_TABLE=${TRUSTFORGE_COST_LEDGER_TABLE:-trustforge-cost-ledger}" \
  --property="Environment=COST_LEDGER_BACKEND=${COST_LEDGER_BACKEND:-dynamodb}" \
  --property="Environment=BEDROCK_MODEL_ID=${BEDROCK_MODEL_ID-}" \
  --property="Type=exec" \
  /usr/bin/python3.11 -m trustforge.web

# Step 2: Wait for canary to be healthy
if ! wait_for_health "$BACKUP_PORT" "canary"; then
  log "Canary failed to start; aborting (primary still running, no interruption)"
  systemctl stop "$CANARY_UNIT" 2>/dev/null || true
  exit 1
fi

# Step 3: Restart primary — nginx will failover to canary within 1s (max_fails=1)
log "Restarting primary on port $PRIMARY_PORT (nginx failover to canary)..."
systemctl restart trustforge

# Step 4: Wait for primary to become healthy again
if ! wait_for_health "$PRIMARY_PORT" "primary"; then
  log "WARNING: primary failed to restart; canary still serving traffic"
  # Leave canary running as fallback
  exit 1
fi

# Step 5: Stop canary (primary is back, nginx routes to it first)
log "Primary healthy; stopping canary..."
systemctl stop "$CANARY_UNIT" 2>/dev/null || true

log "✅ Zero-downtime restart complete"
