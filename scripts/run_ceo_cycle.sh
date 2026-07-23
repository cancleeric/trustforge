#!/bin/zsh
set -u
setopt NO_BG_NICE
umask 077

SCRIPT_DIR="${0:A:h}"
DEFAULT_ROOT="${SCRIPT_DIR:h}"
ROOT="${TRUSTFORGE_HOME:-$DEFAULT_ROOT}"
PYTHON_BIN="${TRUSTFORGE_PYTHON:-$ROOT/.venv/bin/python}"
CODEX_BIN="${TRUSTFORGE_CODEX:-codex}"
WORKTREE_ROOT="${TRUSTFORGE_CEO_WORKTREE_ROOT:-/private/tmp/trustforge-ceo-lanes}"
MAX_LANES="${TRUSTFORGE_CEO_MAX_LANES:-1}"
MAX_LOAD_PER_CPU="${TRUSTFORGE_CEO_MAX_LOAD_PER_CPU:-0.85}"
AGENT_TIMEOUT="${TRUSTFORGE_CEO_AGENT_TIMEOUT_SECONDS:-1200}"
LOCK_STALE_SECONDS="${TRUSTFORGE_CEO_LOCK_STALE_SECONDS:-1800}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[ceo_cycle] python unavailable" >&2
  exit 2
fi
ROOT="$($PYTHON_BIN -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' "$ROOT")" || exit 2
if [[ "$ROOT" != "${TRUSTFORGE_HOME:-$DEFAULT_ROOT}" ]]; then
  echo "[ceo_cycle] repository root must be a canonical realpath" >&2
  exit 2
fi

LOG_DIR="$ROOT/out/ceo-cycle"
STATUS_FILE="$LOG_DIR/status.json"
LATEST="$ROOT/out/ceo-sweep-latest.json"
LOCK_DIR="$LOG_DIR/.lock"
PROMPT_FILE="$ROOT/scripts/prompts/ceo-development-loop.md"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
LOG_FILE="$LOG_DIR/$STAMP.log"
EVENT_FILE="$LOG_DIR/.$STAMP.events"
HEARTBEAT_FILE="$LOCK_DIR/active.json"
LOAD_DIAGNOSTICS="{}"
HEARTBEAT_PID=""
LOCK_OWNED=0

if ! "$PYTHON_BIN" "$ROOT/scripts/ceo_runtime_guard.py" prepare --dir "$LOG_DIR" --file "$LOG_FILE" --file "$EVENT_FILE"; then
  echo "[ceo_cycle] unsafe log path" >&2
  exit 2
fi
exec >>"$LOG_FILE" 2>&1

cleanup() {
  if [[ -n "$HEARTBEAT_PID" ]]; then
    kill "$HEARTBEAT_PID" >/dev/null 2>&1 || true
    wait "$HEARTBEAT_PID" >/dev/null 2>&1 || true
  fi
  if (( LOCK_OWNED )); then
    "$PYTHON_BIN" "$ROOT/scripts/ceo_runtime_guard.py" release-lock --lock "$LOCK_DIR" --pid "$$" >/dev/null 2>&1 || true
  fi
  "$PYTHON_BIN" "$ROOT/scripts/ceo_runtime_guard.py" redact-file --file "$LOG_FILE" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

LOCK_RESULT="$($PYTHON_BIN "$ROOT/scripts/ceo_runtime_guard.py" acquire-lock --lock "$LOCK_DIR" --pid "$$" --stale-seconds "$LOCK_STALE_SECONDS")"
if [[ $? -ne 0 ]]; then
  echo "[ceo_cycle] lock validation failed: $LOCK_RESULT"
  exit 2
fi
if [[ "$($PYTHON_BIN -c 'import json,sys; print(int(json.loads(sys.argv[1]).get("acquired", False)))' "$LOCK_RESULT")" -ne 1 ]]; then
  echo "[ceo_cycle] active run owns lock: $LOCK_RESULT"
  exit 0
fi
LOCK_OWNED=1

record_status() {
  local process_success="$1" status_output severity
  status_output="$($PYTHON_BIN "$ROOT/scripts/ceo_cycle_state.py" \
    --status "$STATUS_FILE" --events "$EVENT_FILE" --process-success "$process_success" \
    --load-diagnostics "$LOAD_DIAGNOSTICS")"
  if [[ $? -ne 0 || -z "$status_output" ]]; then
    echo "[ceo_cycle] record_status failed"
    return 1
  fi
  echo "[ceo_cycle] status=$status_output"
  severity="$($PYTHON_BIN -c 'import json,sys; print(json.loads(sys.argv[1]).get("watchdog_severity") or "")' "$status_output")" || return 1
  [[ "$severity" == "warning" ]] && echo "[WARNING] CEO cycle watchdog detected consecutive zero dispatch or no progress"
  [[ "$severity" == "critical" ]] && echo "[CRITICAL] CEO cycle watchdog detected sustained zero dispatch or no progress"
  rm -f "$EVENT_FILE"
  return 0
}

fail_cycle() {
  local reason="$1" detail="${2:-}"
  if [[ -n "$detail" && "$detail" == \{* ]]; then
    printf 'blocked\t0\t%s\n' "$detail" >>"$EVENT_FILE"
  else
    printf 'blocked\t0\t%s\n' "$reason" >>"$EVENT_FILE"
  fi
  echo "[ceo_cycle] blocked reason=$reason"
  record_status false || echo "[CRITICAL] unable to persist failed cycle status"
  return 2
}

echo "[ceo_cycle] started_at=$STARTED_AT pid=$$"
echo "[ceo_cycle] policy=no_network_child,no_production,no_push,no_merge,no_release,no_secrets,no_cost_changes"

if ! "$PYTHON_BIN" "$ROOT/scripts/ceo_cycle_state.py" --status "$STATUS_FILE" --mark-active \
  --pid "$$" --started-at "$STARTED_AT" --heartbeat-path "$HEARTBEAT_FILE" >/dev/null; then
  echo "[ceo_cycle] failed to persist active status"
  exit 2
fi
(
  while sleep 300; do
    "$PYTHON_BIN" "$ROOT/scripts/ceo_runtime_guard.py" heartbeat --lock "$LOCK_DIR" --pid "$$" >/dev/null || exit 1
  done
) &
HEARTBEAT_PID="$!"

if [[ ! -f "$PROMPT_FILE" || -L "$PROMPT_FILE" ]]; then
  fail_cycle missing_or_unsafe_prompt
  exit $?
fi
if ! command -v "$CODEX_BIN" >/dev/null 2>&1; then
  fail_cycle codex_unavailable
  exit $?
fi
LOAD_DIAGNOSTICS="$($PYTHON_BIN "$ROOT/scripts/ceo_lane_guard.py" --max-lanes "$MAX_LANES" --max-load-per-cpu "$MAX_LOAD_PER_CPU" --json)"
if [[ $? -ne 0 ]] || ! LANE_COUNT="$($PYTHON_BIN -c 'import json,sys; print(json.loads(sys.argv[1])["capacity"])' "$LOAD_DIAGNOSTICS")"; then
  fail_cycle load_guard_failed
  exit $?
fi
echo "[ceo_cycle] load_diagnostics=$LOAD_DIAGNOSTICS"
if [[ "$LANE_COUNT" -lt 1 ]]; then
  printf 'blocked\t0\tload_guard\n' >>"$EVENT_FILE"
  record_status true || exit 2
  exit 0
fi

if [[ -L "$LATEST" ]]; then
  fail_cycle unsafe_inventory_path
  exit $?
fi
rm -f "$LATEST"
if ! "$PYTHON_BIN" "$ROOT/scripts/ceo_sweep.py" --out "$LATEST" --max-lanes "$LANE_COUNT"; then
  rm -f "$LATEST"
  fail_cycle sweep_failed
  exit $?
fi
if [[ ! -f "$LATEST" ]] || ! INVENTORY_STATUS="$($PYTHON_BIN -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["execution_status"])' "$LATEST")"; then
  rm -f "$LATEST"
  fail_cycle inventory_json_invalid
  exit $?
fi
if [[ "$INVENTORY_STATUS" == "inventory_failed" ]]; then
  INVENTORY_ERROR="$($PYTHON_BIN -c 'import json,sys; print(json.dumps({"reason":"inventory_error","errors":json.load(open(sys.argv[1]))["inventory_errors"]}, separators=(",", ":")))' "$LATEST")" || INVENTORY_ERROR='{"reason":"inventory_error","errors":[{"error":"unreadable summary"}]}'
  fail_cycle inventory_error "$INVENTORY_ERROR"
  exit $?
fi

if ! "$PYTHON_BIN" "$ROOT/scripts/ceo_runtime_guard.py" prepare --dir "$WORKTREE_ROOT" >/dev/null; then
  fail_cycle unsafe_worktree_root
  exit $?
fi
if ! "$PYTHON_BIN" "$ROOT/scripts/ceo_runtime_guard.py" validate-lane --root "$ROOT" --lane "$ROOT" >/dev/null; then
  fail_cycle repository_provenance_failed
  exit $?
fi

pids=()
dispatched_lanes=()
setup_failures=0
while IFS= read -r issue; do
  printf 'selected\t%s\n' "$issue" >>"$EVENT_FILE"
  lane=""
  for (( candidate_lane=1; candidate_lane<=LANE_COUNT; candidate_lane++ )); do
    (( ${dispatched_lanes[(Ie)$candidate_lane]} )) && continue
    candidate_dir="$WORKTREE_ROOT/lane-$candidate_lane"
    if [[ -L "$candidate_dir" ]]; then
      printf 'blocked\t%s\tunsafe_lane_symlink\n' "$issue" >>"$EVENT_FILE"
      continue
    fi
    if [[ -e "$candidate_dir/.git" ]]; then
      if ! lane_validation="$($PYTHON_BIN "$ROOT/scripts/ceo_runtime_guard.py" validate-lane --root "$ROOT" --lane "$candidate_dir")"; then
        printf 'blocked\t%s\t%s\n' "$issue" "$lane_validation" >>"$EVENT_FILE"
        continue
      fi
      cleanliness="$($PYTHON_BIN "$ROOT/scripts/ceo_lane_cleanliness.py" "$candidate_dir")" || continue
      [[ "$($PYTHON_BIN -c 'import json,sys; print(int(json.loads(sys.argv[1])["clean"]))' "$cleanliness")" -eq 1 ]] || continue
    fi
    lane="$candidate_lane"
    break
  done
  if [[ -z "$lane" ]]; then
    printf 'skipped\t%s\tno_verified_clean_lane\n' "$issue" >>"$EVENT_FILE"
    continue
  fi
  lane_dir="$WORKTREE_ROOT/lane-$lane"
  if [[ ! -e "$lane_dir/.git" ]]; then
    if ! git -C "$ROOT" worktree add --detach "$lane_dir" origin/develop; then
      printf 'failed\t%s\t{"reason":"worktree_add_failed"}\n' "$issue" >>"$EVENT_FILE"
      setup_failures=$((setup_failures + 1))
      continue
    fi
  fi
  if ! "$PYTHON_BIN" "$ROOT/scripts/ceo_runtime_guard.py" validate-lane --root "$ROOT" --lane "$lane_dir" >/dev/null; then
    printf 'failed\t%s\t{"reason":"lane_validation_failed"}\n' "$issue" >>"$EVENT_FILE"
    setup_failures=$((setup_failures + 1))
    continue
  fi
  if ! git -C "$lane_dir" fetch origin develop --quiet; then
    printf 'failed\t%s\t{"reason":"fetch_failed"}\n' "$issue" >>"$EVENT_FILE"
    setup_failures=$((setup_failures + 1))
    continue
  fi
  if ! git -C "$lane_dir" checkout --detach origin/develop --quiet; then
    printf 'failed\t%s\t{"reason":"checkout_failed"}\n' "$issue" >>"$EVENT_FILE"
    setup_failures=$((setup_failures + 1))
    continue
  fi
  if ! before_sha="$(git -C "$lane_dir" rev-parse HEAD)"; then
    printf 'failed\t%s\t{"reason":"rev_parse_failed"}\n' "$issue" >>"$EVENT_FILE"
    setup_failures=$((setup_failures + 1))
    continue
  fi
  prompt="$LOG_DIR/$STAMP-lane-$lane-issue-$issue.prompt"
  output="$LOG_DIR/$STAMP-lane-$lane-issue-$issue.last.txt"
  lane_log="$LOG_DIR/$STAMP-lane-$lane.log"
  agent_home="$LOG_DIR/.agent-home-$STAMP-$lane"
  if ! "$PYTHON_BIN" "$ROOT/scripts/ceo_runtime_guard.py" prepare --dir "$agent_home" --file "$output" --file "$lane_log" >/dev/null; then
    printf 'failed\t%s\t{"reason":"output_path_failed"}\n' "$issue" >>"$EVENT_FILE"
    setup_failures=$((setup_failures + 1))
    continue
  fi
  if ! "$PYTHON_BIN" "$ROOT/scripts/ceo_runtime_guard.py" build-prompt --report "$LATEST" --base-prompt "$PROMPT_FILE" --destination "$prompt" --issue "$issue" >/dev/null; then
    printf 'failed\t%s\t{"reason":"snapshot_prompt_failed"}\n' "$issue" >>"$EVENT_FILE"
    setup_failures=$((setup_failures + 1))
    continue
  fi
  dispatched_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'dispatched\t%s\n' "$issue" >>"$EVENT_FILE"
  dispatched_lanes+=("$lane")
  CODEX_HOME="${CODEX_HOME:-$HOME/.codex}" "$PYTHON_BIN" "$ROOT/scripts/ceo_agent_exec.py" \
    --codex "$CODEX_BIN" --cwd "$lane_dir" --prompt "$prompt" --output "$output" \
    --timeout-seconds "$AGENT_TIMEOUT" --lane "$lane" --issue "$issue" --home "$agent_home" \
    >>"$lane_log" 2>&1 &
  pids+=("$!|$issue|$lane|$before_sha|$dispatched_at")
  (( ${#pids[@]} >= LANE_COUNT )) && break
done < <("$PYTHON_BIN" -c 'import json,sys; [print(x["issue"]) for x in json.load(open(sys.argv[1]))["execution_queue"]]' "$LATEST")

failures=0
for dispatch in "${pids[@]}"; do
  IFS='|' read -r pid issue lane before_sha dispatched_at <<<"$dispatch"
  agent_exit=0
  wait "$pid" || agent_exit=$?
  lane_dir="$WORKTREE_ROOT/lane-$lane"
  "$PYTHON_BIN" "$ROOT/scripts/ceo_runtime_guard.py" redact-file --file "$LOG_DIR/$STAMP-lane-$lane.log" >/dev/null 2>&1 || true
  "$PYTHON_BIN" "$ROOT/scripts/ceo_runtime_guard.py" redact-file --file "$LOG_DIR/$STAMP-lane-$lane-issue-$issue.last.txt" >/dev/null 2>&1 || true
  after_sha="$(git -C "$lane_dir" rev-parse HEAD 2>/dev/null || true)"
  descendant=false
  [[ -n "$after_sha" ]] && git -C "$lane_dir" merge-base --is-ancestor "$before_sha" "$after_sha" && descendant=true
  clean=false
  cleanliness="$($PYTHON_BIN "$ROOT/scripts/ceo_lane_cleanliness.py" "$lane_dir" 2>/dev/null || true)"
  [[ -n "$cleanliness" ]] && [[ "$($PYTHON_BIN -c 'import json,sys; print(int(json.loads(sys.argv[1])["clean"]))' "$cleanliness" 2>/dev/null)" -eq 1 ]] && clean=true
  progress_result="$($PYTHON_BIN "$ROOT/scripts/ceo_runtime_guard.py" classify-progress \
    --agent-exit "$agent_exit" --before "$before_sha" --after "$after_sha" --descendant "$descendant" --clean "$clean")"
  if [[ $? -eq 0 && "$($PYTHON_BIN -c 'import json,sys; print(int(json.loads(sys.argv[1])["progress"]))' "$progress_result")" -eq 1 ]]; then
    commit_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'progress\t%s\n' "$issue" >>"$EVENT_FILE"
    printf 'completed\t%s\t{"lane":%s,"commit":"%s","dispatched_at":"%s","commit_at":"%s"}\n' "$issue" "$lane" "$after_sha" "$dispatched_at" "$commit_at" >>"$EVENT_FILE"
    continue
  fi
  result_reason="$($PYTHON_BIN -c 'import json,sys; print(json.loads(sys.argv[1]).get("reason", "progress_validation_failed"))' "$progress_result" 2>/dev/null || echo progress_validation_failed)"
  failures=$((failures + 1))
  printf 'failed\t%s\t{"lane":%s,"reason":"%s","exit_code":%s,"dispatched_at":"%s"}\n' "$issue" "$lane" "$result_reason" "$agent_exit" "$dispatched_at" >>"$EVENT_FILE"
done
failures=$((failures + setup_failures))

if (( ${#pids[@]} == 0 )); then
  echo "[ceo_cycle] no runnable issues or no verified clean lanes"
fi
if (( failures == 0 )); then
  record_status true || exit 2
else
  record_status false || exit 2
fi
exit "$failures"
