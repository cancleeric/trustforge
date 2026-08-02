#!/usr/bin/env bash
set -euo pipefail

UNIT="${TRUSTFORGE_PRIMARY_UNIT:-/etc/systemd/system/trustforge.service}"
TABLE="${TRUSTFORGE_FORMAL_RUN_DYNAMODB_TABLE:-trustforge-formal-run}"
PREFIX="${TRUSTFORGE_TOKEN_SSM_PREFIX:-/trustforge/runtime}"
PROJECTION="${TRUSTFORGE_SHARED_ANALYSIS_DB_PATH:-/var/lib/trustforge/analysis.sqlite3}"
[[ -f "$UNIT" && ! -L "$UNIT" ]] || { echo "formal-run primary unit is unsafe" >&2; exit 1; }
[[ "$TABLE" =~ ^[A-Za-z0-9_.-]{3,255}$ ]] || exit 2
[[ "$PREFIX" =~ ^/[A-Za-z0-9_./-]+$ ]] || exit 2
[[ "$PROJECTION" =~ ^/[A-Za-z0-9_./-]+$ ]] || exit 2

set_env() {
  local key="$1" value="$2"
  sed -i "/^Environment=${key}=/d" "$UNIT"
  sed -i "/^Environment=PYTHONPATH=/a Environment=${key}=${value}" "$UNIT"
}
set_env TRUSTFORGE_ENV production
set_env TRUSTFORGE_FORMAL_RUN_DYNAMODB_TABLE "$TABLE"
set_env TRUSTFORGE_TOKEN_SSM_PREFIX "$PREFIX"
set_env TRUSTFORGE_SHARED_ANALYSIS_DB_PATH "$PROJECTION"
install -d -m 0750 -o root -g root "$(dirname "$PROJECTION")"

