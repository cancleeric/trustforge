#!/bin/zsh
set -u

ROOT="${TRUSTFORGE_HOME:-/Users/apple/HurricaneSoft/trustforge}"
LABEL="com.hurricanesoft.trustforge-ceo-sweep"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON_BIN="${TRUSTFORGE_PYTHON:-$ROOT/.venv/bin/python}"
CODEX_BIN="${TRUSTFORGE_CODEX:-$(command -v codex)}"
GH_BIN="${TRUSTFORGE_GH:-$(command -v gh)}"

if [[ ! -x "$CODEX_BIN" ]]; then
  echo "codex executable not found: $CODEX_BIN" >&2
  exit 2
fi
if [[ ! -x "$GH_BIN" ]]; then
  echo "gh executable not found: $GH_BIN" >&2
  exit 2
fi
ROOT="$($PYTHON_BIN -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' "$ROOT")" || exit 2
PYTHON_BIN="$($PYTHON_BIN -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' "$PYTHON_BIN")" || exit 2
CODEX_BIN="$($PYTHON_BIN -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' "$CODEX_BIN")" || exit 2
GH_BIN="$($PYTHON_BIN -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' "$GH_BIN")" || exit 2
"$PYTHON_BIN" "$ROOT/scripts/install_launch_agent.py" \
  --kind sweep --root "$ROOT" --python "$PYTHON_BIN" --codex "$CODEX_BIN" --gh "$GH_BIN" --destination "$PLIST" || exit 2

launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL every 1800s"
echo "$PLIST"
