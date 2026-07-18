#!/bin/zsh
set -u

ROOT="${TRUSTFORGE_HOME:-/Users/apple/HurricaneSoft/trustforge}"
LABEL="com.hurricanesoft.trustforge-ceo-sweep"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON_BIN="${TRUSTFORGE_PYTHON:-$ROOT/.venv/bin/python}"

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/out/ceo-cycle"

cat >"$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>$ROOT/scripts/run_ceo_cycle.sh</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>TRUSTFORGE_HOME</key>
    <string>$ROOT</string>
    <key>TRUSTFORGE_PYTHON</key>
    <string>$PYTHON_BIN</string>
    <key>TRUSTFORGE_ENV</key>
    <string>local</string>
    <key>CACHE_BACKEND</key>
    <string>json</string>
    <key>TRUSTFORGE_DISABLE_ADMIN_CONFIG</key>
    <string>1</string>
    <key>TRUSTFORGE_BEDROCK_DAILY_USD_CAP</key>
    <string>0</string>
  </dict>
  <key>StartInterval</key>
  <integer>3600</integer>
  <key>RunAtLoad</key>
  <false/>
  <key>WorkingDirectory</key>
  <string>$ROOT</string>
  <key>StandardOutPath</key>
  <string>$ROOT/out/ceo-cycle/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>$ROOT/out/ceo-cycle/launchd.err.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL every 3600s"
echo "$PLIST"
