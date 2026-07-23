#!/bin/zsh
set -u

SCRIPT_DIR="${0:A:h}"
DEFAULT_ROOT="${SCRIPT_DIR:h}"
ROOT="${TRUSTFORGE_HOME:-$DEFAULT_ROOT}"
LABEL="com.hurricanesoft.trustforge-ceo-health-watchdog"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON_BIN="${TRUSTFORGE_PYTHON:-$ROOT/.venv/bin/python}"
ENABLE=1
if [[ "${1:-}" == "--no-enable" ]]; then
  ENABLE=0
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "python3 executable not found: $PYTHON_BIN" >&2
  exit 2
fi

ROOT="$($PYTHON_BIN -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' "$ROOT")" || exit 2
if [[ "$ROOT" != "${TRUSTFORGE_HOME:-$DEFAULT_ROOT}" ]]; then
  echo "repository root must be a canonical realpath" >&2
  exit 2
fi
PYTHON_BIN="$($PYTHON_BIN -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' "$PYTHON_BIN")" || exit 2
"$PYTHON_BIN" "$ROOT/scripts/install_launch_agent.py" \
  --kind watchdog --root "$ROOT" --python "$PYTHON_BIN" --destination "$PLIST" || exit 2

if (( ENABLE )); then
  # launchctl bootout / launchctl bootstrap are deliberately skipped by --no-enable.
  "${TRUSTFORGE_LAUNCHCTL:-launchctl}" bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
  "${TRUSTFORGE_LAUNCHCTL:-launchctl}" bootstrap "gui/$(id -u)" "$PLIST"
fi
echo "installed $LABEL every 300s"
echo "$PLIST"
