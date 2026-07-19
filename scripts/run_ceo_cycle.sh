#!/bin/zsh
set -u

ROOT="${TRUSTFORGE_HOME:-/Users/apple/HurricaneSoft/trustforge}"
PYTHON_BIN="${TRUSTFORGE_PYTHON:-$ROOT/.venv/bin/python}"
CODEX_BIN="${TRUSTFORGE_CODEX:-codex}"
LOG_DIR="$ROOT/out/ceo-cycle"
LATEST="$ROOT/out/ceo-sweep-latest.json"
LOCK_DIR="$LOG_DIR/.lock"
WORKTREE_ROOT="${TRUSTFORGE_CEO_WORKTREE_ROOT:-/private/tmp/trustforge-ceo-lanes}"
MAX_LANES="${TRUSTFORGE_CEO_MAX_LANES:-4}"
MAX_LOAD_PER_CPU="${TRUSTFORGE_CEO_MAX_LOAD_PER_CPU:-0.85}"
PROMPT_FILE="$ROOT/scripts/prompts/ceo-development-loop.md"

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
  echo "[ceo_cycle] policy=workspace_write,approval_never,no_production,no_main_merge,no_release,no_secrets,no_cost_changes"
  if [[ ! -f "$PROMPT_FILE" ]]; then
    echo "[ceo_cycle] missing prompt: $PROMPT_FILE"
    exit 2
  fi
  if ! command -v "$CODEX_BIN" >/dev/null 2>&1; then
    echo "[ceo_cycle] codex unavailable: $CODEX_BIN"
    exit 2
  fi
  LANE_COUNT="$($PYTHON_BIN "$ROOT/scripts/ceo_lane_guard.py" --max-lanes "$MAX_LANES" --max-load-per-cpu "$MAX_LOAD_PER_CPU")"
  if [[ "$LANE_COUNT" -lt 1 ]]; then
    echo "[ceo_cycle] load guard denied dispatch"
    exit 0
  fi
  echo "[ceo_cycle] lane_capacity=$LANE_COUNT"
  "$PYTHON_BIN" scripts/ceo_sweep.py --out "$LATEST" --max-lanes "$LANE_COUNT"
  echo "[ceo_cycle] wrote=$LATEST"
  mkdir -p "$WORKTREE_ROOT"
  pids=()
  while IFS=$'\t' read -r lane issue; do
    lane_dir="$WORKTREE_ROOT/lane-$lane"
    if [[ ! -e "$lane_dir/.git" ]]; then
      git -C "$ROOT" worktree add --detach "$lane_dir" origin/develop
    fi
    if [[ -n "$(git -C "$lane_dir" status --porcelain)" ]]; then
      echo "[ceo_cycle] lane=$lane occupied by uncommitted work; skipped issue=#$issue"
      continue
    fi
    git -C "$lane_dir" fetch origin develop --quiet
    git -C "$lane_dir" checkout --detach origin/develop --quiet
    output="$LOG_DIR/$STAMP-lane-$lane-issue-$issue.last.txt"
    echo "[ceo_cycle] dispatch lane=$lane issue=#$issue worktree=$lane_dir"
    (
      export TRUSTFORGE_CEO_LANE="$lane" TRUSTFORGE_CEO_ISSUE="$issue"
      "$CODEX_BIN" exec --ephemeral --ignore-user-config \
        -c 'approval_policy="never"' \
        --sandbox workspace-write \
        -C "$lane_dir" \
        -o "$output" \
        - <"$PROMPT_FILE"
    ) >>"$LOG_DIR/$STAMP-lane-$lane.log" 2>&1 &
    pids+=("$!")
  done < <("$PYTHON_BIN" - "$LATEST" <<'PY'
import json, sys
for item in json.load(open(sys.argv[1]))["execution_queue"]:
    print(f'{item["lane"]}\t{item["issue"]}')
PY
)
  if (( ${#pids[@]} == 0 )); then
    echo "[ceo_cycle] no runnable issues or all lanes occupied"
    exit 0
  fi
  failures=0
  for pid in "${pids[@]}"; do
    wait "$pid" || failures=$((failures + 1))
  done
  echo "[ceo_cycle] completed lanes=${#pids[@]} failures=$failures"
  exit "$failures"
} >>"$LOG_FILE" 2>&1
