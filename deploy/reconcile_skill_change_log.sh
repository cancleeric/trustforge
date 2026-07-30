#!/usr/bin/env bash
# Preserve Hermes' append-only skill approval history outside replaceable releases.
set -euo pipefail

APP_DIR="${TRUSTFORGE_APP_DIR:-/opt/trustforge}"
SKILL_LOG_PATH="${TRUSTFORGE_SKILL_CHANGE_LOG:-/var/lib/trustforge/skill_changes.jsonl}"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"

is_safe_absolute_path() {
  [[ "$1" =~ ^/[A-Za-z0-9._/-]+$ ]] && [[ "$1" != *"/../"* ]] && [[ "$1" != */.. ]]
}

for path in "$APP_DIR" "$SKILL_LOG_PATH" "$UNIT_DIR"; do
  if ! is_safe_absolute_path "$path"; then
    echo "[skill-change-log] ERROR: unsafe path" >&2
    exit 2
  fi
done

export TRUSTFORGE_APP_DIR="$APP_DIR"
export TRUSTFORGE_SKILL_CHANGE_LOG="$SKILL_LOG_PATH"

state_dir="$(dirname "$SKILL_LOG_PATH")"
legacy_log="$APP_DIR/out/skill_changes.jsonl"
install -d -m 0700 "$state_dir"
# A clean release artifact need not contain out/. Create it before linking.
install -d -m 0755 "$(dirname "$legacy_log")"

# Python writers lock the resolved log path. Hold both possible lock names
# through mv+symlink so a governed writer cannot create a split legacy log.
if [ "${TRUSTFORGE_SKILL_CHANGE_LOG_LOCK_HELD:-0}" != "1" ]; then
  exec python3 - "$0" <<'PY'
import fcntl
import os
import subprocess
import sys
from pathlib import Path

script = sys.argv[1]
legacy = Path(os.environ["TRUSTFORGE_APP_DIR"]) / "out" / "skill_changes.jsonl"
persistent = Path(os.environ["TRUSTFORGE_SKILL_CHANGE_LOG"])
locks = sorted({Path(f"{legacy}.lock"), Path(f"{persistent}.lock")}, key=str)
handles = []
try:
    for lock_path in locks:
        handle = lock_path.open("a+", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handles.append(handle)
    environment = os.environ.copy()
    environment["TRUSTFORGE_SKILL_CHANGE_LOG_LOCK_HELD"] = "1"
    raise SystemExit(subprocess.run(["bash", script], env=environment, pass_fds=tuple(handle.fileno() for handle in handles)).returncode)
finally:
    for handle in reversed(handles):
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
PY
fi

if [ -L "$SKILL_LOG_PATH" ] || { [ -e "$SKILL_LOG_PATH" ] && [ ! -f "$SKILL_LOG_PATH" ]; }; then
  echo "[skill-change-log] ERROR: persistent log path is not a regular file" >&2
  exit 1
fi

if [ -L "$legacy_log" ]; then
  if [ "$(readlink "$legacy_log")" != "$SKILL_LOG_PATH" ]; then
    echo "[skill-change-log] ERROR: legacy log symlink has an unexpected target" >&2
    exit 1
  fi
elif [ -e "$legacy_log" ] && [ -e "$SKILL_LOG_PATH" ]; then
  echo "[skill-change-log] ERROR: refusing to reconcile independent legacy and persistent logs" >&2
  exit 1
elif [ -e "$legacy_log" ]; then
  legacy_fs="$(df -P "$legacy_log" | awk 'NR == 2 {print $1}')"
  state_fs="$(df -P "$state_dir" | awk 'NR == 2 {print $1}')"
  if [ -z "$legacy_fs" ] || [ "$legacy_fs" != "$state_fs" ]; then
    echo "[skill-change-log] ERROR: legacy and persistent paths must share a filesystem" >&2
    exit 1
  fi
  mv "$legacy_log" "$SKILL_LOG_PATH"
  chmod 0600 "$SKILL_LOG_PATH"
  ln -s "$SKILL_LOG_PATH" "$legacy_log"
  echo "[skill-change-log] migrated approval history and linked the legacy path"
elif [ -e "$SKILL_LOG_PATH" ]; then
  ln -s "$SKILL_LOG_PATH" "$legacy_log"
  echo "[skill-change-log] linked the legacy path to existing persistent approval history"
else
  ln -s "$SKILL_LOG_PATH" "$legacy_log"
  echo "[skill-change-log] initialized a persistent approval-log path"
fi

for unit in trustforge.service hermes-cycle.service trustforge-analysis-flow.service; do
  if [ ! -f "$UNIT_DIR/$unit" ]; then
    continue
  fi
  drop_in_dir="$UNIT_DIR/$unit.d"
  drop_in="$drop_in_dir/20-skill-change-log.conf"
  expected="[Service]\nEnvironment=TRUSTFORGE_SKILL_CHANGE_LOG=$SKILL_LOG_PATH\n"
  if [ -L "$drop_in_dir" ] || [ -L "$drop_in" ]; then
    echo "[skill-change-log] ERROR: refusing to follow a systemd drop-in symlink" >&2
    exit 1
  fi
  if [ -e "$drop_in" ] && { [ ! -f "$drop_in" ] || ! printf '%b' "$expected" | cmp -s - "$drop_in"; }; then
    echo "[skill-change-log] ERROR: refusing to overwrite an unexpected systemd drop-in" >&2
    exit 1
  fi
  install -d -m 0755 "$drop_in_dir"
  printf '%b' "$expected" > "$drop_in"
  chmod 0644 "$drop_in"
done
