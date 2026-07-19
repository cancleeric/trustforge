#!/bin/zsh
set -u

ROOT="${TRUSTFORGE_HOME:-/Users/apple/HurricaneSoft/trustforge}"
LABEL="com.hurricanesoft.trustforge-ceo-sweep"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON_BIN="${TRUSTFORGE_PYTHON:-$ROOT/.venv/bin/python}"
CODEX_BIN="${TRUSTFORGE_CODEX:-$(command -v codex)}"

if [[ ! -x "$CODEX_BIN" ]]; then
  echo "codex executable not found: $CODEX_BIN" >&2
  exit 2
fi

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/out/ceo-cycle"
sed \
  -e "s|__ROOT__|$ROOT|g" \
  -e "s|__PYTHON__|$PYTHON_BIN|g" \
  -e "s|__CODEX__|$CODEX_BIN|g" \
  "$ROOT/scripts/templates/com.hurricanesoft.trustforge-ceo-sweep.plist.in" >"$PLIST"

launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL every 3600s"
echo "$PLIST"
