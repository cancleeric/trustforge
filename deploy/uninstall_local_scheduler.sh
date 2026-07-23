#!/usr/bin/env bash
set -euo pipefail

OS="${TRUSTFORGE_SCHEDULER_OS:-$(uname -s)}"
MARKER="TrustForge local scheduler v1"
failures=0
if [[ "$OS" == "Darwin" ]]; then
  TARGET="${TRUSTFORGE_LAUNCH_AGENT_DIR:-$HOME/Library/LaunchAgents}"
  LAUNCHCTL="${TRUSTFORGE_LAUNCHCTL:-launchctl}"
  labels=(
    com.hurricanesoft.trustforge-local-refresh
    com.hurricanesoft.trustforge-analysis-flow
    com.hurricanesoft.trustforge-local-web
    com.hurricanesoft.trustforge-local-frontend
  )
  for label in "${labels[@]}"; do
    plist="$TARGET/$label.plist"
    if [[ -f "$plist" && ! -L "$plist" ]]; then
      PYTHON_BIN="${TRUSTFORGE_PYTHON:-$(command -v python3 || true)}"
      "$PYTHON_BIN" -c 'import plistlib,sys; raise SystemExit(0 if plistlib.load(open(sys.argv[1],"rb")).get("ManagedBy")==sys.argv[2] else 1)' "$plist" "$MARKER" || continue
    else
      continue
    fi
    launch_output=""
    if launch_output="$("$LAUNCHCTL" print "gui/$(id -u)/$label" 2>&1)"; then
      if ! "$LAUNCHCTL" bootout "gui/$(id -u)" "$plist" >/dev/null 2>&1; then
        echo "failed to stop $label; preserving $plist" >&2
        failures=1
        continue
      fi
    elif [[ "$launch_output" != *"Could not find service"* && "$launch_output" != *"could not find service"* ]]; then
      echo "launchctl state query failed for $label: $launch_output; preserving $plist" >&2
      failures=1
      continue
    fi
    rm "$plist"
  done
elif [[ "$OS" == "Linux" ]]; then
  TARGET="${TRUSTFORGE_SYSTEMD_USER_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user}"
  SYSTEMCTL="${TRUSTFORGE_SYSTEMCTL:-systemctl}"
  if ! "$SYSTEMCTL" --user show-environment >/dev/null 2>&1; then
    echo "systemd user manager query failed; preserving all managed units" >&2
    exit 1
  fi
  units=(trustforge-local-refresh.timer trustforge-local-refresh.service trustforge-analysis-flow.service trustforge-local-web.service trustforge-local-frontend.service)
  failures=0
  for unit in "${units[@]}"; do
    file="$TARGET/$unit"
    [[ -f "$file" && ! -L "$file" ]] || continue
    IFS= read -r first_line <"$file" || true
    [[ "$first_line" == "# Managed-By: $MARKER" ]] || continue
    state_failed=0
    if active_state="$("$SYSTEMCTL" --user is-active "$unit" 2>&1)"; then active_rc=0; else active_rc=$?; fi
    case "$active_state" in
      active) [[ "$active_rc" -eq 0 ]] || { echo "inconsistent active state for $unit" >&2; failures=1; continue; }; is_active=1 ;;
      inactive|failed|unknown) is_active=0 ;;
      *) echo "unknown active state for $unit: $active_state" >&2; failures=1; continue ;;
    esac
    if enabled_state="$("$SYSTEMCTL" --user is-enabled "$unit" 2>&1)"; then enabled_rc=0; else enabled_rc=$?; fi
    case "$enabled_state" in
      enabled) [[ "$enabled_rc" -eq 0 ]] || { echo "inconsistent enabled state for $unit" >&2; failures=1; continue; }; is_enabled=1 ;;
      disabled|static|indirect|masked|not-found) is_enabled=0 ;;
      *) echo "unknown enabled state for $unit: $enabled_state" >&2; failures=1; continue ;;
    esac
    if ((is_active)); then
      "$SYSTEMCTL" --user stop "$unit" >/dev/null 2>&1 || state_failed=1
    fi
    if ((is_enabled)); then
      "$SYSTEMCTL" --user disable "$unit" >/dev/null 2>&1 || state_failed=1
    fi
    if ((state_failed)); then
      echo "failed to stop or disable $unit; preserving $file" >&2
      failures=1
    else
      rm "$file"
    fi
  done
  if ! "$SYSTEMCTL" --user daemon-reload; then
    echo "systemd user daemon-reload failed" >&2
    failures=1
  fi
else
  echo "unsupported local scheduler OS: $OS" >&2
  exit 2
fi
echo "removed fixed TrustForge local scheduler units; logs and SQLite data were preserved"
exit "${failures:-0}"
