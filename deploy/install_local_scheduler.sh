#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
DEFAULT_ROOT="$(dirname "$SCRIPT_DIR")"
ROOT="${TRUSTFORGE_HOME:-$DEFAULT_ROOT}"
PYTHON_BIN="${TRUSTFORGE_PYTHON:-$ROOT/.venv/bin/python}"
ENABLE=1
RENDER_ONLY=0
WITH_UI=0
OUTPUT_DIR=""

usage() {
  echo "usage: $0 [--render-only] [--no-enable] [--with-ui] [--output-dir DIR]" >&2
}
while (($#)); do
  case "$1" in
    --render-only) RENDER_ONLY=1; ENABLE=0 ;;
    --no-enable) ENABLE=0 ;;
    --with-ui) WITH_UI=1 ;;
    --output-dir) shift; OUTPUT_DIR="${1:?--output-dir requires a directory}" ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
  shift
done

if [[ ! -x "$PYTHON_BIN" ]]; then PYTHON_BIN="$(command -v python3 || true)"; fi
[[ -x "$PYTHON_BIN" ]] || { echo "python3 unavailable" >&2; exit 2; }
CANONICAL_ROOT="$("$PYTHON_BIN" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' "$ROOT")"
[[ "$CANONICAL_ROOT" == "$ROOT" ]] || { echo "TRUSTFORGE_HOME must be a canonical existing path" >&2; exit 2; }
ROOT="$CANONICAL_ROOT"
PYTHON_BIN="$("$PYTHON_BIN" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' "$PYTHON_BIN")"

OS="${TRUSTFORGE_SCHEDULER_OS:-$(uname -s)}"
if [[ "$OS" == "Darwin" ]]; then
  TARGET="${OUTPUT_DIR:-$HOME/Library/LaunchAgents}"
  [[ "$RENDER_ONLY" -eq 0 || -n "$OUTPUT_DIR" ]] || TARGET="$ROOT/out/scheduler-render/launchd"
  mkdir -p "$TARGET"
  TARGET="$("$PYTHON_BIN" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' "$TARGET")"
  kinds=(refresh analysis web)
  ((WITH_UI)) && kinds+=(frontend)
  LAUNCHCTL="${TRUSTFORGE_LAUNCHCTL:-launchctl}"
  loaded_labels=()
  STAGING="$(mktemp -d "$TARGET/.trustforge-install.XXXXXX")"
  trap 'rm -rf "$STAGING"' EXIT
  labels=()
  for kind in "${kinds[@]}"; do
    label="com.hurricanesoft.trustforge-$kind"
    [[ "$kind" == "refresh" ]] && label="com.hurricanesoft.trustforge-local-refresh"
    [[ "$kind" == "analysis" ]] && label="com.hurricanesoft.trustforge-analysis-flow"
    [[ "$kind" == "web" ]] && label="com.hurricanesoft.trustforge-local-web"
    [[ "$kind" == "frontend" ]] && label="com.hurricanesoft.trustforge-local-frontend"
    labels+=("$label")
    existing="$TARGET/$label.plist"
    label_loaded=0
    launch_output=""
    if ((RENDER_ONLY)); then
      label_loaded=0
    elif launch_output="$("$LAUNCHCTL" print "gui/$(id -u)/$label" 2>&1)"; then
      label_loaded=1
    elif [[ "$launch_output" == *"Could not find service"* || "$launch_output" == *"could not find service"* ]]; then
      label_loaded=0
    else
      echo "launchctl state query failed for $label: $launch_output" >&2
      exit 2
    fi
    if ((label_loaded)) && [[ ! -f "$existing" || -L "$existing" ]]; then
      echo "refusing to replace loaded label without managed plist backup: $label" >&2
      exit 2
    fi
    if [[ -e "$existing" || -L "$existing" ]]; then
      [[ -f "$existing" && ! -L "$existing" ]] || { echo "unsafe existing scheduler file: $existing" >&2; exit 2; }
      "$PYTHON_BIN" -c 'import plistlib,sys; raise SystemExit(0 if plistlib.load(open(sys.argv[1],"rb")).get("ManagedBy")=="TrustForge local scheduler v1" else 1)' "$existing" \
        || { echo "refusing to replace unmanaged plist: $existing" >&2; exit 2; }
      cp "$existing" "$STAGING/backup-$label.plist"
      if ((ENABLE && label_loaded)); then
        loaded_labels+=("$label")
      fi
    fi
    args=(--kind "$kind" --root "$ROOT" --python "$PYTHON_BIN" --destination "$STAGING/$label.plist" --skip-log-prepare)
    if [[ "$kind" == "frontend" ]]; then
      NODE_BIN="${TRUSTFORGE_NODE:-$(command -v node || true)}"
      [[ -x "$NODE_BIN" ]] || { echo "node unavailable; cannot render opt-in UI unit" >&2; exit 2; }
      args+=(--node "$NODE_BIN")
    fi
    "$PYTHON_BIN" "$ROOT/scripts/install_launch_agent.py" "${args[@]}"
    PLUTIL="${TRUSTFORGE_PLUTIL:-plutil}"
    "$PLUTIL" -lint "$STAGING/$label.plist" >/dev/null
  done
  for label in "${labels[@]}"; do mv "$STAGING/$label.plist" "$TARGET/$label.plist"; done
  if ((ENABLE)); then
    mkdir -p "$ROOT/out/logs"
    installed=()
    for label in "${labels[@]}"; do
      plist="$TARGET/$label.plist"
      "$LAUNCHCTL" bootout "gui/$(id -u)" "$plist" >/dev/null 2>&1 || true
      if ! "$LAUNCHCTL" bootstrap "gui/$(id -u)" "$plist"; then
        rollback_failures=()
        for rollback in "${labels[@]}"; do
          rollback_plist="$TARGET/$rollback.plist"
          "$LAUNCHCTL" bootout "gui/$(id -u)" "$rollback_plist" >/dev/null 2>&1 || true
          if [[ -f "$STAGING/backup-$rollback.plist" ]]; then
            mv "$STAGING/backup-$rollback.plist" "$rollback_plist"
            if ((${#loaded_labels[@]})); then
              for loaded in "${loaded_labels[@]}"; do
                if [[ "$loaded" == "$rollback" ]]; then
                  if ! "$LAUNCHCTL" bootstrap "gui/$(id -u)" "$rollback_plist" >/dev/null 2>&1; then
                    rollback_failures+=("$rollback")
                  fi
                  break
                fi
              done
            fi
          else
            rm -f "$rollback_plist"
          fi
        done
        if ((${#rollback_failures[@]})); then
          echo "rollback incomplete: ${rollback_failures[*]}" >&2
        else
          echo "failed to bootstrap $label; transaction rolled back" >&2
        fi
        exit 1
      fi
      installed+=("$label")
    done
  fi
elif [[ "$OS" == "Linux" ]]; then
  TARGET="${OUTPUT_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user}"
  [[ "$RENDER_ONLY" -eq 0 || -n "$OUTPUT_DIR" ]] || TARGET="$ROOT/out/scheduler-render/systemd-user"
  mkdir -p "$TARGET"
  TARGET="$("$PYTHON_BIN" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' "$TARGET")"
  STAGING="$(mktemp -d "$TARGET/.trustforge-install.XXXXXX")"
  trap 'rm -rf "$STAGING"' EXIT
  for kind in refresh analysis web; do
    "$PYTHON_BIN" "$ROOT/scripts/install_launch_agent.py" --format systemd --kind "$kind" \
      --root "$ROOT" --python "$PYTHON_BIN" --destination "$STAGING"
  done
  if ((WITH_UI)); then
    NODE_BIN="${TRUSTFORGE_NODE:-$(command -v node || true)}"
    [[ -x "$NODE_BIN" ]] || { echo "node unavailable; cannot render opt-in UI unit" >&2; exit 2; }
    "$PYTHON_BIN" "$ROOT/scripts/install_launch_agent.py" --format systemd --kind frontend \
      --root "$ROOT" --python "$PYTHON_BIN" --node "$NODE_BIN" --destination "$STAGING"
  fi
  units=(trustforge-local-refresh.service trustforge-local-refresh.timer trustforge-analysis-flow.service trustforge-local-web.service)
  ((WITH_UI)) && units+=(trustforge-local-frontend.service)
  SYSTEMCTL="${TRUSTFORGE_SYSTEMCTL:-systemctl}"
  enable_units=(trustforge-local-refresh.timer trustforge-analysis-flow.service trustforge-local-web.service)
  ((WITH_UI)) && enable_units+=(trustforge-local-frontend.service)
  previously_enabled=()
  previously_active=()
  if ((ENABLE)); then
    for enable_unit in "${enable_units[@]}"; do
      if enabled_state="$("$SYSTEMCTL" --user is-enabled "$enable_unit" 2>&1)"; then enabled_rc=0; else enabled_rc=$?; fi
      case "$enabled_state" in
        enabled) [[ "$enabled_rc" -eq 0 ]] || { echo "inconsistent enabled state for $enable_unit" >&2; exit 2; }; previously_enabled+=("$enable_unit") ;;
        disabled|static|indirect|masked|not-found) ;;
        *) echo "unknown enabled state for $enable_unit: $enabled_state" >&2; exit 2 ;;
      esac
      if active_state="$("$SYSTEMCTL" --user is-active "$enable_unit" 2>&1)"; then active_rc=0; else active_rc=$?; fi
      case "$active_state" in
        active) [[ "$active_rc" -eq 0 ]] || { echo "inconsistent active state for $enable_unit" >&2; exit 2; }; previously_active+=("$enable_unit") ;;
        inactive|failed|dead|not-found) ;;
        *) echo "unknown active state for $enable_unit: $active_state" >&2; exit 2 ;;
      esac
    done
  fi
  for unit in "${units[@]}"; do
    existing="$TARGET/$unit"
    if [[ -e "$existing" || -L "$existing" ]]; then
      [[ -f "$existing" && ! -L "$existing" ]] || { echo "unsafe existing scheduler file: $existing" >&2; exit 2; }
      IFS= read -r first_line <"$existing" || true
      [[ "$first_line" == "# Managed-By: TrustForge local scheduler v1" ]] \
        || { echo "refusing to replace unmanaged unit: $existing" >&2; exit 2; }
      cp "$existing" "$STAGING/backup-$unit"
    fi
  done
  for unit in "${units[@]}"; do mv "$STAGING/$unit" "$TARGET/$unit"; done
  if ((ENABLE)); then
    if ! "$SYSTEMCTL" --user daemon-reload || ! "$SYSTEMCTL" --user enable --now "${enable_units[@]}"; then
      rollback_failures=()
      "$SYSTEMCTL" --user disable --now "${enable_units[@]}" >/dev/null 2>&1 || rollback_failures+=("disable-new")
      for unit in "${units[@]}"; do
        if [[ -f "$STAGING/backup-$unit" ]]; then
          mv "$STAGING/backup-$unit" "$TARGET/$unit"
        else
          rm -f "$TARGET/$unit"
        fi
      done
      "$SYSTEMCTL" --user daemon-reload >/dev/null 2>&1 || rollback_failures+=("daemon-reload")
      if ((${#previously_enabled[@]})); then
        "$SYSTEMCTL" --user enable "${previously_enabled[@]}" >/dev/null 2>&1 || rollback_failures+=("enable:${previously_enabled[*]}")
      fi
      if ((${#previously_active[@]})); then
        "$SYSTEMCTL" --user start "${previously_active[@]}" >/dev/null 2>&1 || rollback_failures+=("start:${previously_active[*]}")
      fi
      if ((${#rollback_failures[@]})); then
        echo "rollback incomplete: ${rollback_failures[*]}" >&2
      else
        echo "systemd enable failed; transaction rolled back" >&2
      fi
      exit 1
    fi
  fi
else
  echo "unsupported local scheduler OS: $OS" >&2
  exit 2
fi
echo "rendered local scheduler files in $TARGET (enabled=$ENABLE ui=$WITH_UI)"
