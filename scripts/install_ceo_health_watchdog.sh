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

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/out/ceo-cycle"
sed \
  -e "s|__ROOT__|$ROOT|g" \
  -e "s|__PYTHON__|$PYTHON_BIN|g" \
  "$ROOT/scripts/templates/com.hurricanesoft.trustforge-ceo-health-watchdog.plist.in" >"$PLIST"

launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL every 300s"
echo "$PLIST"
