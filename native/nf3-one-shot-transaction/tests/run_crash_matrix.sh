#!/usr/bin/env bash
set -euo pipefail

helper="${1:?usage: run_crash_matrix.sh /path/to/nf3-test-helper [secure-parent]}"
parent="${2:-/root}"
if [ ! -d "$parent" ] || [ -L "$parent" ]; then
  echo "secure parent must be a real directory: $parent" >&2
  exit 1
fi
parent_uid="$(stat -c %u -- "$parent")"
parent_mode="$(stat -c %a -- "$parent")"
if [ "$parent_uid" != 0 ] || [ "$parent_mode" != 700 ]; then
  echo "secure parent must be root-owned mode 0700: $parent" >&2
  exit 1
fi

deadline=0
set_deadline() {
  local uptime uptime_seconds deadline_seconds
  IFS=' ' read -r uptime _ </proc/uptime
  uptime_seconds="${uptime%%.*}"
  case "$uptime_seconds" in
    ''|*[!0-9]*) echo "invalid /proc/uptime" >&2; exit 1 ;;
  esac
  if [ "$uptime_seconds" -gt 9223371796 ]; then
    echo "uptime cannot be converted safely to nanoseconds" >&2
    exit 1
  fi
  deadline_seconds=$((uptime_seconds + 240))
  deadline=$((deadline_seconds * 1000000000))
}

artifacts=(BURN PREPARED CLAIMED COMMIT TOMBSTONE)
stages=(AFTER_CREATE AFTER_WRITE AFTER_FDATASYNC AFTER_DIR_FSYNC)

assert_no_retry() {
  local root="$1"
  if "$helper" probe-blocked "$root"; then
    return
  fi
  if "$helper" commit "$root" \
    2222222222222222222222222222222222222222222222222222222222222222 \
    logical-request "$deadline"; then
    echo "logical request retried after recovery: $root" >&2
    exit 1
  fi
}

new_case() {
  local label="$1"
  mktemp -d -p "$parent" "trustforge-nf3-${label}-XXXXXXXX"
}

run_pause_case() {
  local artifact="$1" stage="$2"
  local root log command pid
  set_deadline
  root="$(new_case "sigkill-${artifact,,}-${stage,,}")"
  log="${root}.log"
  "$helper" provision "$root"
  command=pause-commit
  if [ "$artifact" = TOMBSTONE ]; then command=pause-abandon; fi
  env TRUSTFORGE_NF3_HOOK_ARTIFACT="$artifact" \
    TRUSTFORGE_NF3_HOOK_STAGE="$stage" \
    "$helper" "$command" "$root" \
    1111111111111111111111111111111111111111111111111111111111111111 \
    logical-request "$deadline" >"$log" 2>&1 &
  pid=$!
  for _ in $(seq 1 200); do
    grep -q "PAUSED artifact=${artifact} stage=${stage}" "$log" && break
    kill -0 "$pid" 2>/dev/null || { cat "$log"; exit 1; }
    sleep 0.05
  done
  grep -q "PAUSED artifact=${artifact} stage=${stage}" "$log"
  kill -KILL "$pid"
  wait "$pid" 2>/dev/null || true
  assert_no_retry "$root"
  echo "PASS SIGKILL $artifact $stage root=$root log=$log"
}

run_error_case() {
  local error_kind="$1" artifact="$2" stage="$3"
  local root log command
  set_deadline
  root="$(new_case "${error_kind,,}-${artifact,,}-${stage,,}")"
  log="${root}.log"
  "$helper" provision "$root"
  command=commit
  if [ "$artifact" = TOMBSTONE ]; then command=abandon; fi
  if env TRUSTFORGE_NF3_HOOK_ARTIFACT="$artifact" \
    TRUSTFORGE_NF3_HOOK_STAGE="$stage" \
    TRUSTFORGE_NF3_HOOK_ERROR="$error_kind" \
    "$helper" "$command" "$root" \
    1111111111111111111111111111111111111111111111111111111111111111 \
    logical-request "$deadline" >"$log" 2>&1; then
    # Abandon terminalizes from Drop, whose public contract is best-effort and
    # therefore may return success while latching/persisting poison.
    if [ "$artifact" != TOMBSTONE ]; then
      echo "$error_kind injection unexpectedly succeeded: $artifact/$stage" >&2
      exit 1
    fi
  fi
  assert_no_retry "$root"
  echo "PASS $error_kind $artifact $stage root=$root log=$log"
}

for artifact in "${artifacts[@]}"; do
  for stage in "${stages[@]}"; do
    run_pause_case "$artifact" "$stage"
    run_error_case EIO "$artifact" "$stage"
    run_error_case ENOSPC "$artifact" "$stage"
  done
done
