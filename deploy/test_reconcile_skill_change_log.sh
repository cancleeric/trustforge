#!/usr/bin/env bash
set -euo pipefail

ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

APP_DIR="$ROOT/app"
UNIT_DIR="$ROOT/systemd"
LOG_PATH="$ROOT/state/skill_changes.jsonl"
LEGACY_LOG="$APP_DIR/out/skill_changes.jsonl"
mkdir -p "$APP_DIR/out" "$UNIT_DIR"
printf '%s\n' '{"action":"approved","skill_id":"outer-source","skill_hash":"baseline"}' > "$LEGACY_LOG"
for unit in trustforge.service hermes-cycle.service trustforge-analysis-flow.service; do
  : > "$UNIT_DIR/$unit"
done

TRUSTFORGE_APP_DIR="$APP_DIR" \
TRUSTFORGE_SKILL_CHANGE_LOG="$LOG_PATH" \
UNIT_DIR="$UNIT_DIR" \
bash deploy/reconcile_skill_change_log.sh

test -L "$LEGACY_LOG"
test "$(readlink "$LEGACY_LOG")" = "$LOG_PATH"
grep -F '"skill_hash":"baseline"' "$LOG_PATH" >/dev/null
printf '%s\n' '{"action":"approved","skill_id":"outer-source","skill_hash":"after-cutover"}' >> "$LEGACY_LOG"
grep -F '"skill_hash":"after-cutover"' "$LOG_PATH" >/dev/null
for unit in trustforge.service hermes-cycle.service trustforge-analysis-flow.service; do
  grep -Fx "Environment=TRUSTFORGE_SKILL_CHANGE_LOG=$LOG_PATH" \
    "$UNIT_DIR/$unit.d/20-skill-change-log.conf"
done

TRUSTFORGE_APP_DIR="$APP_DIR" \
TRUSTFORGE_SKILL_CHANGE_LOG="$LOG_PATH" \
UNIT_DIR="$UNIT_DIR" \
bash deploy/reconcile_skill_change_log.sh

grep -F '"skill_hash":"baseline"' "$LOG_PATH" >/dev/null
grep -F '"skill_hash":"after-cutover"' "$LOG_PATH" >/dev/null
printf '%s\n' '[Service]' 'Environment=TRUSTFORGE_HERMES_AUTONOMY_ENABLED=1' > "$UNIT_DIR/hermes-cycle.service.d/20-skill-change-log.conf"
if TRUSTFORGE_APP_DIR="$APP_DIR" TRUSTFORGE_SKILL_CHANGE_LOG="$LOG_PATH" UNIT_DIR="$UNIT_DIR" bash deploy/reconcile_skill_change_log.sh; then
  echo "expected unexpected drop-in rejection" >&2
  exit 1
fi

# Clean artifacts have no out/ directory; reconciliation creates it before linking.
CLEAN_APP="$ROOT/clean-app"
CLEAN_UNITS="$ROOT/clean-systemd"
CLEAN_LOG="$ROOT/clean-state/skill_changes.jsonl"
mkdir -p "$CLEAN_UNITS"
for unit in trustforge.service hermes-cycle.service trustforge-analysis-flow.service; do : > "$CLEAN_UNITS/$unit"; done
TRUSTFORGE_APP_DIR="$CLEAN_APP" TRUSTFORGE_SKILL_CHANGE_LOG="$CLEAN_LOG" UNIT_DIR="$CLEAN_UNITS" bash deploy/reconcile_skill_change_log.sh
test -d "$CLEAN_APP/out"
test -L "$CLEAN_APP/out/skill_changes.jsonl"

# A symlink, including a dangling one, must never be followed for a drop-in.
rm -f "$UNIT_DIR/hermes-cycle.service.d/20-skill-change-log.conf"
ln -s "$ROOT/missing-drop-in" "$UNIT_DIR/hermes-cycle.service.d/20-skill-change-log.conf"
if TRUSTFORGE_APP_DIR="$APP_DIR" TRUSTFORGE_SKILL_CHANGE_LOG="$LOG_PATH" UNIT_DIR="$UNIT_DIR" bash deploy/reconcile_skill_change_log.sh; then
  echo "expected drop-in symlink rejection" >&2
  exit 1
fi

# The Python lock wrapper must receive the validated shell defaults even when
# the caller did not export either application path variable.
DEFAULT_CAPTURE="$ROOT/default-wrapper-env"
FAKE_BIN="$ROOT/fake-bin"
mkdir -p "$FAKE_BIN"
cat > "$FAKE_BIN/install" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat > "$FAKE_BIN/python3" <<'EOF'
#!/usr/bin/env bash
printf '%s\n%s\n' "$TRUSTFORGE_APP_DIR" "$TRUSTFORGE_SKILL_CHANGE_LOG" > "$DEFAULT_CAPTURE"
EOF
chmod +x "$FAKE_BIN/install" "$FAKE_BIN/python3"
DEFAULT_CAPTURE="$DEFAULT_CAPTURE" PATH="$FAKE_BIN:$PATH" \
  env -u TRUSTFORGE_APP_DIR -u TRUSTFORGE_SKILL_CHANGE_LOG -u UNIT_DIR \
  bash deploy/reconcile_skill_change_log.sh
test "$(sed -n '1p' "$DEFAULT_CAPTURE")" = "/opt/trustforge"
test "$(sed -n '2p' "$DEFAULT_CAPTURE")" = "/var/lib/trustforge/skill_changes.jsonl"

# A symlinked parent directory must not redirect a managed drop-in.
rm -rf "$UNIT_DIR/trustforge.service.d"
mkdir -p "$ROOT/redirected-drop-ins"
ln -s "$ROOT/redirected-drop-ins" "$UNIT_DIR/trustforge.service.d"
if TRUSTFORGE_APP_DIR="$APP_DIR" TRUSTFORGE_SKILL_CHANGE_LOG="$LOG_PATH" UNIT_DIR="$UNIT_DIR" bash deploy/reconcile_skill_change_log.sh; then
  echo "expected drop-in parent symlink rejection" >&2
  exit 1
fi

echo "reconcile_skill_change_log: passed"
