#!/bin/zsh
set -u

LABEL="com.hurricanesoft.trustforge-ceo-sweep"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
rm -f "$PLIST"
echo "uninstalled $LABEL"
