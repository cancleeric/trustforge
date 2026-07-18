#!/bin/zsh
set -u

ROOT="${TRUSTFORGE_HOME:-/Users/apple/HurricaneSoft/trustforge}"
PYTHON_BIN="${TRUSTFORGE_PYTHON:-$ROOT/.venv/bin/python}"
LOG_DIR="$ROOT/out/ceo-cycle"
LATEST="$ROOT/out/ceo-sweep-latest.json"
LOCK_DIR="$LOG_DIR/.lock"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT" || exit 1
mkdir -p "$LOG_DIR"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[ceo_cycle] previous run still active; skipped $(date -u +%Y%m%dT%H%M%SZ)" >>"$LOG_DIR/skipped.log"
  exit 0
fi
trap 'rmdir "$LOCK_DIR" >/dev/null 2>&1 || true' EXIT

export TRUSTFORGE_HOME="$ROOT"
export PYTHONPATH="$ROOT/src"
export TRUSTFORGE_ENV="local"
export CACHE_BACKEND="json"
export TRUSTFORGE_DISABLE_ADMIN_CONFIG="1"
export TRUSTFORGE_BEDROCK_DAILY_USD_CAP="0"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/$STAMP.log"

{
  echo "[ceo_cycle] started_at=$STAMP"
  echo "[ceo_cycle] policy=no_auto_merge,no_auto_deploy,no_auto_cost_increase,ceo_gate_required"
  "$PYTHON_BIN" -m trustforge.cli control status
  "$PYTHON_BIN" - <<'PY'
from trustforge.runtime_control import runtime_control
control = runtime_control()
if not control.enabled:
    print(f"[ceo_cycle] runtime disabled ({control.source}); skipped")
    raise SystemExit(75)
print(f"[ceo_cycle] runtime enabled ({control.source}); continuing")
PY
  rc=$?
  if [[ "$rc" == "75" ]]; then
    exit 0
  fi
  if [[ "$rc" != "0" ]]; then
    exit "$rc"
  fi
  "$PYTHON_BIN" scripts/ceo_sweep.py --out "$LATEST"
  echo "[ceo_cycle] wrote=$LATEST"
} >>"$LOG_FILE" 2>&1
