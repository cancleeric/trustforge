#!/usr/bin/env bash
# Reconcile formal-run production identity into the primary web unit.
# Hermes/analysis-flow units are rendered by install_hermes_scheduler.sh.
set -euo pipefail

SERVICE_FILE="${TRUSTFORGE_SERVICE_FILE:-/etc/systemd/system/trustforge.service}"
FORMAL_RUN_TABLE="${TRUSTFORGE_FORMAL_RUN_DYNAMODB_TABLE:-trustforge-formal-run}"
TOKEN_SSM_PREFIX="${TRUSTFORGE_TOKEN_SSM_PREFIX:-/trustforge/runtime}"
SHARED_ANALYSIS_DB_PATH="${TRUSTFORGE_SHARED_ANALYSIS_DB_PATH:-/var/lib/trustforge/analysis.sqlite3}"

if ! [[ "$FORMAL_RUN_TABLE" =~ ^[A-Za-z0-9_.-]{3,255}$ ]] ||
   ! [[ "$TOKEN_SSM_PREFIX" =~ ^/[A-Za-z0-9_./-]+$ ]] ||
   ! [[ "$SHARED_ANALYSIS_DB_PATH" =~ ^/[A-Za-z0-9_./-]+$ ]]; then
  echo "invalid formal-run production configuration" >&2
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
    index($0, prefix) == 1 {
      if (!found) {
        print line
        found = 1
      }
      next
    }
    {
      print
      if ($0 ~ /^Environment=PYTHONPATH=/ && !found) {
        print line
        found = 1
      }
    }
    END {
      if (!found) {
        print line
      }
    }
  ' "$SERVICE_FILE" >"$temporary"
  chmod --reference="$SERVICE_FILE" "$temporary" 2>/dev/null || true
  mv "$temporary" "$SERVICE_FILE"
}

reconcile TRUSTFORGE_FORMAL_RUN_DYNAMODB_TABLE "$FORMAL_RUN_TABLE"
reconcile TRUSTFORGE_TOKEN_SSM_PREFIX "$TOKEN_SSM_PREFIX"
reconcile TRUSTFORGE_SHARED_ANALYSIS_DB_PATH "$SHARED_ANALYSIS_DB_PATH"
