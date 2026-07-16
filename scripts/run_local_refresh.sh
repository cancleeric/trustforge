#!/bin/zsh
set -u

ROOT="/Users/apple/HurricaneSoft/trustforge"
export PYTHONPATH="$ROOT/src"
export CACHE_BACKEND="sqlite"
export COST_LEDGER_BACKEND="sqlite"
export TRUSTFORGE_SQLITE_PATH="$ROOT/out/trustforge.sqlite3"
export TRUSTFORGE_DISABLE_ADMIN_CONFIG="1"

cd "$ROOT" || exit 1
"$ROOT/.venv/bin/python" scripts/fetch_scheduler.py \
  --allow-partial --parallelism 4 --total-budget-sec 600
fetch_status=$?

"$ROOT/.venv/bin/python" scripts/fetch_scheduler.py --snapshot

# The persistent analysis-flow launch agent observes this committed cache revision
# and schedules every active question. Refresh stays ingestion-only.
snapshot_status=$?

if (( fetch_status != 0 && snapshot_status != 0 )); then
  exit 1
fi
exit 0
