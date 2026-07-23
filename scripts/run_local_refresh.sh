#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
DEFAULT_ROOT="$(dirname "$SCRIPT_DIR")"
ROOT="${TRUSTFORGE_HOME:-$DEFAULT_ROOT}"
PYTHON_BIN="${TRUSTFORGE_PYTHON:-$ROOT/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[local_refresh] python unavailable" >&2
  exit 2
fi
ROOT="$("$PYTHON_BIN" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' "$ROOT")" || exit 2
if [[ "$ROOT" != "${TRUSTFORGE_HOME:-$DEFAULT_ROOT}" ]]; then
  echo "[local_refresh] repository root must be a canonical realpath" >&2
  exit 2
fi
export PYTHONPATH="$ROOT/src"
export CACHE_BACKEND="sqlite"
export COST_LEDGER_BACKEND="sqlite"
export TRUSTFORGE_SQLITE_PATH="$ROOT/out/trustforge.sqlite3"
export TRUSTFORGE_DISABLE_ADMIN_CONFIG="1"

cd "$ROOT" || exit 1
"$PYTHON_BIN" scripts/fetch_scheduler.py \
  --allow-partial --parallelism 4 --total-budget-sec 600
fetch_status=$?

"$PYTHON_BIN" scripts/fetch_scheduler.py --snapshot

# The persistent analysis-flow launch agent observes this committed cache revision
# and schedules every active question. Refresh stays ingestion-only.
snapshot_status=$?

if (( fetch_status != 0 && snapshot_status != 0 )); then
  exit 1
fi
exit 0
