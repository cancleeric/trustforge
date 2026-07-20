#!/bin/zsh
set -u

ROOT="${TRUSTFORGE_HOME:-/Users/apple/HurricaneSoft/trustforge}"
LABEL="com.hurricanesoft.trustforge-ceo-health-watchdog"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON_BIN="${TRUSTFORGE_PYTHON:-$ROOT/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "python3 executable not found: $PYTHON_BIN" >&2
  exit 2
fi

ROOT="$($PYTHON_BIN -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' "$ROOT")" || exit 2
PYTHON_BIN="$($PYTHON_BIN -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' "$PYTHON_BIN")" || exit 2
"$PYTHON_BIN" "$ROOT/scripts/install_launch_agent.py" \
  --kind watchdog --root "$ROOT" --python "$PYTHON_BIN" --destination "$PLIST" || exit 2

launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL every 300s"
echo "$PLIST"
