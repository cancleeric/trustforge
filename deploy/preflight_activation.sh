#!/usr/bin/env bash
# TrustForge activation preflight gate.
# Validates that every artifact required for the activation transaction is
# present and correctly formed before any mutation is attempted.
#
# Exit codes:
#   0   all preflight checks pass
#   1   one or more checks failed
#   98  lock contention (another activator holds the target lock)
#
# Usage:
#   deploy/preflight_activation.sh --target <iid> [--dry-run] [--skip-lock]
#   deploy/preflight_activation.sh --print-checklist
set -euo pipefail
cd "$(dirname "$0")/.."

REGION="${REGION:-ap-southeast-2}"
DRY_RUN=0
SKIP_LOCK=0
TARGET=""
PRINT_CHECKLIST=0
LOCK_RESULT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --skip-lock) SKIP_LOCK=1; shift ;;
    --print-checklist) PRINT_CHECKLIST=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ "$PRINT_CHECKLIST" = "1" ]; then
  echo "preflight checklist:"
  echo "  [1] S3 bucket reachable"
  echo "  [2] pointers/candidate.json exists and has valid digest"
  echo "  [3] pointers/active.json exists and has valid digest"
  echo "  [4] artifacts/<candidate-digest>/artifact.zip exists"
  echo "  [5] artifacts/<candidate-digest>/manifest.json exists"
  echo "  [6] config snapshot identity matches"
  echo "  [7] EC2 target instance exists and is running"
  echo "  [8] SSM agent online on target"
  if [ "$SKIP_LOCK" = "0" ]; then
    echo "  [9] activation lock acquirable (no contention)"
  fi
  exit 0
fi

if [ "$DRY_RUN" = "1" ]; then
  echo "[preflight] dry-run mode: checks will use exit codes but no side-effects"
fi

ACCT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="trustforge-deploy-${ACCT}"

FAILED=0

check_pass() { echo "  [PASS] $1"; }
check_fail() { echo "  [FAIL] $1"; FAILED=$((FAILED + 1)); }

do_cmd() {
  local desc="$1"; shift
  if "$@"; then
    check_pass "$desc"
    return 0
  else
    check_fail "$desc"
    return 1
  fi
}

echo "[preflight] target=${TARGET:-<none>} region=${REGION} bucket=${BUCKET}"

# ---- [1] S3 bucket reachable -------------------------------------------------
if aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null 2>&1; then
  check_pass "[1] S3 bucket reachable: $BUCKET"
else
  check_fail "[1] S3 bucket $BUCKET not reachable"
fi

# Preset pointer digests so the later `if [ -n "$VAR" ]` guards are safe under
# `set -u` even when the JSON branch that assigns them is skipped (empty/missing
# pointer). Mirrors deploy/activate_release.sh top-level defaults.
CANDIDATE_DIGEST=""
ACTIVE_DIGEST=""

# ---- [2] candidate pointer ---------------------------------------------------
CANDIDATE_JSON=$(aws s3 cp "s3://${BUCKET}/pointers/candidate.json" - --region "$REGION" 2>/dev/null || echo "")
if [ -z "$CANDIDATE_JSON" ]; then
  check_fail "[2] pointers/candidate.json missing or empty"
else
  CANDIDATE_DIGEST=$(echo "$CANDIDATE_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('digest',''))" 2>/dev/null || echo "")
  if [ -z "$CANDIDATE_DIGEST" ]; then
    check_fail "[2] pointers/candidate.json has no digest field"
  else
    TEMP_CANDIDATE_VER=$(echo "$CANDIDATE_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('version',''))" 2>/dev/null || echo "")
    check_pass "[2] pointers/candidate.json: digest=${CANDIDATE_DIGEST} version=${TEMP_CANDIDATE_VER}"
  fi
fi

# ---- [3] active pointer ------------------------------------------------------
ACTIVE_JSON=$(aws s3 cp "s3://${BUCKET}/pointers/active.json" - --region "$REGION" 2>/dev/null || echo "")
if [ -z "$ACTIVE_JSON" ]; then
  check_fail "[3] pointers/active.json missing or empty"
else
  ACTIVE_DIGEST=$(echo "$ACTIVE_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('digest',''))" 2>/dev/null || echo "")
  if [ -z "$ACTIVE_DIGEST" ]; then
    check_fail "[3] pointers/active.json has no digest field"
  else
    check_pass "[3] pointers/active.json: digest=${ACTIVE_DIGEST}"
  fi
fi

# ---- [4] candidate artifact zip -----------------------------------------------
if [ -n "$CANDIDATE_DIGEST" ]; then
  CANDIDATE_PREFIX="artifacts/${CANDIDATE_DIGEST}/"
  if aws s3api head-object --bucket "$BUCKET" --key "${CANDIDATE_PREFIX}artifact.zip" --region "$REGION" >/dev/null 2>&1; then
    check_pass "[4] artifacts/${CANDIDATE_DIGEST}/artifact.zip exists"
  else
    check_fail "[4] artifacts/${CANDIDATE_DIGEST}/artifact.zip not found"
  fi
fi

# ---- [5] candidate manifest --------------------------------------------------
if [ -n "$CANDIDATE_DIGEST" ]; then
  if aws s3api head-object --bucket "$BUCKET" --key "${CANDIDATE_PREFIX}manifest.json" --region "$REGION" >/dev/null 2>&1; then
    check_pass "[5] artifacts/${CANDIDATE_DIGEST}/manifest.json exists"
  else
    check_fail "[5] artifacts/${CANDIDATE_DIGEST}/manifest.json not found"
  fi
fi

# ---- [6] config snapshot identity match ---------------------------------------
if [ -n "$CANDIDATE_DIGEST" ]; then
  MANIFEST_JSON=$(aws s3 cp "s3://${BUCKET}/${CANDIDATE_PREFIX}manifest.json" - --region "$REGION" 2>/dev/null || echo "")
  if [ -n "$MANIFEST_JSON" ]; then
    MANIFEST_CONFIG_ID=$(echo "$MANIFEST_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('config_snapshot_identity',''))" 2>/dev/null || echo "")
    if [ -x .venv/bin/python ]; then PYTHON=.venv/bin/python; else PYTHON=python3; fi
    CURRENT_CONFIG_ID=$($PYTHON -c "
import sys
sys.path.insert(0, 'src')
from trustforge.config_snapshot import ConfigSnapshot
print(ConfigSnapshot.capture().identity)
" 2>/dev/null || echo "unavailable")
    if [ "$MANIFEST_CONFIG_ID" = "$CURRENT_CONFIG_ID" ]; then
      check_pass "[6] config snapshot identity matches: $CURRENT_CONFIG_ID"
    else
      check_fail "[6] config snapshot mismatch: manifest=${MANIFEST_CONFIG_ID} current=${CURRENT_CONFIG_ID}"
    fi
  else
    check_fail "[6] cannot read manifest to check config identity"
  fi
fi

# ---- [7] EC2 target instance -------------------------------------------------
if [ -n "$TARGET" ]; then
  IID_STATE=$(aws ec2 describe-instances --region "$REGION" \
    --instance-ids "$TARGET" \
    --query 'Reservations[0].Instances[0].State.Name' --output text 2>/dev/null || echo "")
  if [ "$IID_STATE" = "running" ]; then
    check_pass "[7] EC2 target $TARGET is running"
  else
    check_fail "[7] EC2 target $TARGET state=$IID_STATE (expected running)"
  fi
else
  check_fail "[7] no target instance specified (use --target)"
fi

# ---- [8] SSM agent online ----------------------------------------------------
if [ -n "$TARGET" ] && [ "$IID_STATE" = "running" ]; then
  SSM_PING=$(aws ssm describe-instance-information --region "$REGION" \
    --filters Key=InstanceIds,Values="$TARGET" \
    --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || echo "")
  if [ "$SSM_PING" = "Online" ]; then
    check_pass "[8] SSM agent online on $TARGET"
  else
    check_fail "[8] SSM agent status=$SSM_PING on $TARGET (expected Online)"
  fi
fi

# ---- [9] activation lock -----------------------------------------------------
if [ "$SKIP_LOCK" = "0" ]; then
  if [ -x .venv/bin/python ]; then PYTHON=.venv/bin/python; else PYTHON=python3; fi
  LOCK_RESULT=$($PYTHON -c "
import sys
sys.path.insert(0, 'src')
from trustforge.activation_lock import get_activation_lock
lock = get_activation_lock('${TARGET:-trustforge-demo}')
if lock is None:
    print('free')
    sys.exit(0)
import time
now = time.time()
if lock.expires_at < now:
    print('expired')
    sys.exit(0)
print('contention:' + str(lock.expires_at))
sys.exit(1)
" 2>/dev/null || echo "error")
  case "$LOCK_RESULT" in
    free|expired)
      check_pass "[9] activation lock is free for $TARGET" ;;
    contention:*)
      check_fail "[9] activation lock contention: expires_at=${LOCK_RESULT#contention:}" ;;
    *)
      check_fail "[9] activation lock check failed: backend unreachable" ;;
  esac
fi

# ---- summary ------------------------------------------------------------------
echo ""
if [ "$FAILED" -eq 0 ]; then
  echo "[preflight] ALL CHECKS PASSED"
  exit 0
fi

if echo "$LOCK_RESULT" | grep -q '^contention:'; then
  echo "[preflight] ${FAILED} check(s) failed including lock contention"
  exit 98
fi

echo "[preflight] ${FAILED} check(s) failed"
exit 1
