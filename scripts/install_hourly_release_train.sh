#!/bin/zsh
set -euo pipefail

ROOT="${TRUSTFORGE_HOME:-${0:A:h:h}}"
PYTHON="${TRUSTFORGE_PYTHON:-$ROOT/.venv/bin/python}"
LABEL="com.hurricanesoft.trustforge-hourly-release-train"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
ENABLE=1
EXECUTE=0

for arg in "$@"; do
  case "$arg" in
    --no-enable) ENABLE=0 ;;
    --execute) EXECUTE=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

ROOT="$("$PYTHON" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' "$ROOT")"
PYTHON="$("$PYTHON" -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' "$PYTHON")"
ARGS=("$PYTHON" "$ROOT/scripts/hourly_release_train.py")
if (( EXECUTE )); then
  : "${TRUSTFORGE_RELEASE_BACKUP_CMD:?required with --execute}"
  : "${TRUSTFORGE_RELEASE_DEPLOY_CMD:?required with --execute}"
  ARGS+=("--execute")
fi

mkdir -p "$ROOT/out/release-train" "$HOME/Library/LaunchAgents"
chmod 700 "$ROOT/out/release-train" "$HOME/Library/LaunchAgents"
"$PYTHON" - "$PLIST" "$ROOT" "$LABEL" "${ARGS[@]}" <<'PY'
import os, plistlib, sys, tempfile
from pathlib import Path

destination, root, label, *arguments = sys.argv[1:]
root = Path(root)
out = root / "out" / "release-train"
payload = {
    "ManagedBy": "TrustForge hourly release train v1",
    "Label": label,
    "ProgramArguments": arguments,
    "EnvironmentVariables": {
        key: os.environ[key]
        for key in ("TRUSTFORGE_RELEASE_BACKUP_CMD", "TRUSTFORGE_RELEASE_DEPLOY_CMD")
        if key in os.environ
    },
    "StartInterval": 3600,
    "RunAtLoad": False,
    "Umask": 0o77,
    "WorkingDirectory": str(root),
    "StandardOutPath": str(out / "launchd.out.log"),
    "StandardErrorPath": str(out / "launchd.err.log"),
}
fd, temporary = tempfile.mkstemp(prefix=".release-train.", dir=Path(destination).parent)
with os.fdopen(fd, "wb") as handle:
    plistlib.dump(payload, handle, sort_keys=False)
os.chmod(temporary, 0o600)
os.replace(temporary, destination)
PY

if (( ENABLE )); then
  launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
fi
echo "installed $LABEL every 3600s (execute=$EXECUTE)"
echo "$PLIST"
