#!/bin/zsh
set -u

ROOT="${TRUSTFORGE_HOME:-/Users/apple/HurricaneSoft/trustforge}"
PYTHON_BIN="${TRUSTFORGE_PYTHON:-$ROOT/.venv/bin/python}"
CODEX_BIN="${TRUSTFORGE_CODEX:-codex}"
LOG_DIR="$ROOT/out/ceo-cycle"
STATUS_FILE="$LOG_DIR/status.json"
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
EVENT_FILE="$LOG_DIR/.$STAMP.events"
LOAD_DIAGNOSTICS="{}"
: >"$EVENT_FILE"

record_status() {
  local process_success="$1"
  local status_output severity
  status_output="$("$PYTHON_BIN" "$ROOT/scripts/ceo_cycle_state.py" \
    --status "$STATUS_FILE" \
    --events "$EVENT_FILE" \
    --process-success "$process_success" \
    --load-diagnostics "$LOAD_DIAGNOSTICS")"
  echo "[ceo_cycle] status=$status_output"
  severity="$($PYTHON_BIN -c 'import json,sys; print(json.loads(sys.argv[1]).get("watchdog_severity") or "")' "$status_output")"
  if [[ "$severity" == "warning" ]]; then
    echo "[WARNING] CEO cycle watchdog detected consecutive zero dispatch or no progress"
  elif [[ "$severity" == "critical" ]]; then
    echo "[CRITICAL] CEO cycle watchdog detected sustained zero dispatch or no progress"
  fi
  rm -f "$EVENT_FILE"
}

{
  echo "[ceo_cycle] started_at=$STAMP"
  echo "[ceo_cycle] policy=workspace_write,approval_never,no_production,no_main_merge,no_release,no_secrets,no_cost_changes"
  LOAD_DIAGNOSTICS="$($PYTHON_BIN "$ROOT/scripts/ceo_lane_guard.py" --max-lanes "$MAX_LANES" --max-load-per-cpu "$MAX_LOAD_PER_CPU" --json)"
  LANE_COUNT="$($PYTHON_BIN -c 'import json,sys; print(json.loads(sys.argv[1])["capacity"])' "$LOAD_DIAGNOSTICS")"
  echo "[ceo_cycle] load_diagnostics=$LOAD_DIAGNOSTICS"
  if [[ ! -f "$PROMPT_FILE" ]]; then
    echo "[ceo_cycle] missing prompt: $PROMPT_FILE"
    printf 'blocked\t0\tmissing_prompt\n' >>"$EVENT_FILE"
    record_status false
    exit 2
  fi
  if ! command -v "$CODEX_BIN" >/dev/null 2>&1; then
    echo "[ceo_cycle] codex unavailable: $CODEX_BIN"
    printf 'blocked\t0\tcodex_unavailable\n' >>"$EVENT_FILE"
    record_status false
    exit 2
  fi
  if [[ "$LANE_COUNT" -lt 1 ]]; then
    echo "[ceo_cycle] load guard denied dispatch"
    printf 'blocked\t0\tload_guard\n' >>"$EVENT_FILE"
    record_status true
    exit 0
  fi
  echo "[ceo_cycle] lane_capacity=$LANE_COUNT"
  "$PYTHON_BIN" scripts/ceo_sweep.py --out "$LATEST" --max-lanes "$LANE_COUNT"
  echo "[ceo_cycle] wrote=$LATEST"
  INVENTORY_STATUS="$($PYTHON_BIN -c 'import json,sys; print(json.load(open(sys.argv[1]))["execution_status"])' "$LATEST")"
  if [[ "$INVENTORY_STATUS" == "inventory_failed" ]]; then
    INVENTORY_ERROR="$($PYTHON_BIN -c 'import json,sys; print(json.dumps({"reason":"inventory_error","errors":json.load(open(sys.argv[1]))["inventory_errors"]}, separators=(",", ":")))' "$LATEST")"
    echo "[ceo_cycle] inventory failed diagnostics=$INVENTORY_ERROR"
    printf 'blocked\t0\t%s\n' "$INVENTORY_ERROR" >>"$EVENT_FILE"
    record_status false
    exit 2
  fi
  mkdir -p "$WORKTREE_ROOT"
  pids=()
  dispatched_lanes=()
  while IFS= read -r issue; do
    printf 'selected\t%s\n' "$issue" >>"$EVENT_FILE"
    lane=""
    for (( candidate_lane=1; candidate_lane<=LANE_COUNT; candidate_lane++ )); do
      if (( ${dispatched_lanes[(Ie)$candidate_lane]} )); then
        continue
      fi
      candidate_dir="$WORKTREE_ROOT/lane-$candidate_lane"
      if [[ -e "$candidate_dir/.git" ]]; then
        cleanliness="$($PYTHON_BIN "$ROOT/scripts/ceo_lane_cleanliness.py" "$candidate_dir")"
        if [[ "$($PYTHON_BIN -c 'import json,sys; print(int(json.loads(sys.argv[1])["clean"]))' "$cleanliness")" -ne 1 ]]; then
          echo "[ceo_cycle] lane=$candidate_lane blocked diagnostics=$cleanliness"
          printf 'blocked\t%s\t%s\n' "$issue" "$cleanliness" >>"$EVENT_FILE"
          continue
        fi
      fi
      lane="$candidate_lane"
      break
    done
    if [[ -z "$lane" ]]; then
      printf 'skipped\t%s\tno_clean_lane\n' "$issue" >>"$EVENT_FILE"
      continue
    fi
    lane_dir="$WORKTREE_ROOT/lane-$lane"
    if [[ ! -e "$lane_dir/.git" ]]; then
      git -C "$ROOT" worktree add --detach "$lane_dir" origin/develop
    fi
    git -C "$lane_dir" fetch origin develop --quiet
    git -C "$lane_dir" checkout --detach origin/develop --quiet
    before_sha="$(git -C "$lane_dir" rev-parse HEAD)"
    output="$LOG_DIR/$STAMP-lane-$lane-issue-$issue.last.txt"
    echo "[ceo_cycle] dispatch lane=$lane issue=#$issue worktree=$lane_dir"
    printf 'dispatched\t%s\n' "$issue" >>"$EVENT_FILE"
    dispatched_lanes+=("$lane")
    (
      export TRUSTFORGE_CEO_LANE="$lane" TRUSTFORGE_CEO_ISSUE="$issue"
      "$CODEX_BIN" exec --ephemeral --ignore-user-config \
        -c 'approval_policy="never"' \
        -c 'sandbox_workspace_write.network_access=true' \
        --sandbox workspace-write \
        -C "$lane_dir" \
        -o "$output" \
        - <"$PROMPT_FILE"
    ) >>"$LOG_DIR/$STAMP-lane-$lane.log" 2>&1 &
    pids+=("$!:$issue:$lane:$before_sha")
    if (( ${#pids[@]} >= LANE_COUNT )); then
      break
    fi
  done < <("$PYTHON_BIN" - "$LATEST" <<'PY'
import json, sys
for item in json.load(open(sys.argv[1]))["execution_queue"]:
    print(item["issue"])
PY
)
  if (( ${#pids[@]} == 0 )); then
    echo "[ceo_cycle] no runnable issues or all lanes occupied"
    record_status true
    exit 0
  fi
  failures=0
  for dispatch in "${pids[@]}"; do
    IFS=: read -r pid issue lane before_sha <<<"$dispatch"
    wait "$pid" || failures=$((failures + 1))
    after_sha="$(git -C "$WORKTREE_ROOT/lane-$lane" rev-parse HEAD)"
    after_cleanliness="$($PYTHON_BIN "$ROOT/scripts/ceo_lane_cleanliness.py" "$WORKTREE_ROOT/lane-$lane")"
    if [[ "$after_sha" != "$before_sha" ]] || [[ "$($PYTHON_BIN -c 'import json,sys; print(int(json.loads(sys.argv[1])["clean"]))' "$after_cleanliness")" -ne 1 ]]; then
      printf 'progress\t%s\n' "$issue" >>"$EVENT_FILE"
    fi
  done
  echo "[ceo_cycle] completed lanes=${#pids[@]} failures=$failures"
  if (( failures == 0 )); then
    record_status true
  else
    record_status false
  fi
  exit "$failures"
} >>"$LOG_FILE" 2>&1
