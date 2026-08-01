#!/usr/bin/env bash
# Reconcile the immutable account-wide Bedrock request-gate identity into the
# primary web unit. Hermes/analysis-flow units receive the same values from
# install_hermes_scheduler.sh.
set -euo pipefail

SERVICE_FILE="${TRUSTFORGE_SERVICE_FILE:-/etc/systemd/system/trustforge.service}"
BACKEND="${TRUSTFORGE_BEDROCK_RPS_BACKEND:-dynamodb}"
REGION="${TRUSTFORGE_BEDROCK_RPS_REGION:-us-east-1}"
TABLE="${TRUSTFORGE_BEDROCK_RPS_TABLE:-competition-trustforge-team11-budget}"

if [[ "$BACKEND" != "dynamodb" ]] ||
   ! [[ "$REGION" =~ ^[a-z]{2}(-gov)?-[a-z]+-[0-9]+$ ]] ||
   ! [[ "$TABLE" =~ ^[A-Za-z0-9_.-]{3,255}$ ]]; then
  echo "invalid canonical Bedrock RPS gate configuration" >&2
  exit 2
fi
if [[ ! -f "$SERVICE_FILE" ]]; then
  echo "primary service file missing" >&2
  exit 2
fi

reconcile() {
  local key="$1" value="$2"
  local temporary
  temporary="$(mktemp "${SERVICE_FILE}.XXXXXX")"
  awk -v prefix="Environment=${key}=" -v line="Environment=${key}=${value}" '
    BEGIN { found = 0 }
    index($0, prefix) == 1 { if (!found) print line; found = 1; next }
    { print; if ($0 ~ /^Environment=PYTHONPATH=/ && !found) { print line; found = 1 } }
    END { if (!found) print line }
  ' "$SERVICE_FILE" >"$temporary"
  chmod --reference="$SERVICE_FILE" "$temporary" 2>/dev/null || true
  mv "$temporary" "$SERVICE_FILE"
}

reconcile TRUSTFORGE_BEDROCK_RPS_BACKEND "$BACKEND"
reconcile TRUSTFORGE_BEDROCK_RPS_REGION "$REGION"
reconcile TRUSTFORGE_BEDROCK_RPS_TABLE "$TABLE"
