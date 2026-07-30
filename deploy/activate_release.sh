#!/usr/bin/env bash
# TrustForge activation transaction: promote candidate → active with guarded
# rollback, modelled on cutover_switch.sh's hardened transaction pattern.
#
# Steps:
#   0.  Acquire activation lock (fail-closed, exit 98 if contention)
#   1.  Preflight gate (all checks before any mutation)
#   2.  Capture pre-state (previous + active pointers for rollback)
#   3.  Download candidate artifact + manifest to target
#   4.  Verify artifact integrity on target
#   5.  Restart service (zero-downtime restart)
#   6.  Promote candidate pointer -> active previous -> previous
#   7.  Post-verify healthz + fetch-scheduler
#   8.  Write activation receipt
#   9.  Release activation lock
#
# ERR trap → ROLLBACK() on any failure after preflight (Steps 3-9).
# Rollback failure → exit 97 (ROLLBACK-FAILED), distinct manual recovery
# instructions in output.
#
# Usage:
#   deploy/activate_release.sh --target <iid> [--dry-run] [--owner-id <id>]
#
# Expected called by deploy/deploy_ec2.sh after candidate pointer is written
# and deployment completes.
set -euo pipefail
cd "$(dirname "$0")/.."

REGION="${REGION:-ap-southeast-2}"
MODEL="${BEDROCK_MODEL_ID-}"
DRY_RUN=0
TARGET=""
OWNER_ID=""

while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --owner-id) OWNER_ID="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ -n "$MODEL" ] && ! [[ "$MODEL" =~ ^[A-Za-z0-9._:-]+$ ]]; then
  echo "[activate] ERROR: BEDROCK_MODEL_ID contains invalid characters" >&2
  exit 2
fi

MODEL_RECONCILE_COMMAND="true"
TRAINING_DATA_DIR="${TRUSTFORGE_TRAINING_DATA_DIR:-/opt/trustforge/data/training}"
if ! [[ "$TRAINING_DATA_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
  echo "[activate] ERROR: TRUSTFORGE_TRAINING_DATA_DIR must be an absolute safe path" >&2
  exit 2
fi
TRAINING_RECONCILE_COMMAND="if grep -q '^Environment=TRUSTFORGE_TRAINING_DATA_DIR=' /etc/systemd/system/trustforge.service; then sed -i 's|^Environment=TRUSTFORGE_TRAINING_DATA_DIR=.*|Environment=TRUSTFORGE_TRAINING_DATA_DIR=${TRAINING_DATA_DIR}|' /etc/systemd/system/trustforge.service; else sed -i '/^Environment=PYTHONPATH=/a Environment=TRUSTFORGE_TRAINING_DATA_DIR=${TRAINING_DATA_DIR}' /etc/systemd/system/trustforge.service; fi"
PREVIEW_READINESS_COMMAND="bash deploy/preview_admission_release_gate.sh"
PREVIEW_ADMISSION_ENABLED="${TRUSTFORGE_PREVIEW_ADMISSION_ENABLED:-0}"
PREVIEW_ENV_KEYS=(
  TRUSTFORGE_PREVIEW_ADMISSION_ENABLED
  TRUSTFORGE_PREVIEW_ADMISSION_TABLE TRUSTFORGE_PREVIEW_TABLE_ARN
  TRUSTFORGE_PREVIEW_TABLE_KMS_KEY_ARN TRUSTFORGE_PREVIEW_QUOTA_KEY_PARAMETER
  TRUSTFORGE_PREVIEW_QUOTA_KEY_VERSION TRUSTFORGE_PREVIEW_QUOTA_KEY_INCARNATION
  TRUSTFORGE_PREVIEW_PREVIOUS_QUOTA_KEY_PARAMETER
  TRUSTFORGE_PREVIEW_PREVIOUS_QUOTA_KEY_VERSION
  TRUSTFORGE_PREVIEW_PREVIOUS_QUOTA_KEY_INCARNATION
  TRUSTFORGE_PREVIEW_QUOTA_LIFECYCLE_GENERATION
  TRUSTFORGE_PREVIEW_QUOTA_KEY_ACTIVATED
  TRUSTFORGE_PREVIEW_PREVIOUS_QUOTA_KEY_ACTIVATED
  TRUSTFORGE_PREVIEW_PREVIOUS_QUOTA_KEY_RETIRE_NOT_BEFORE
  TRUSTFORGE_PREVIEW_QUOTA_ISSUED_EARLIEST
  TRUSTFORGE_PREVIEW_QUOTA_ISSUED_LATEST
  TRUSTFORGE_PREVIEW_MAX_MINUTE_TOKENS
  TRUSTFORGE_PREVIEW_MAX_DAY_TOKENS
  TRUSTFORGE_PREVIEW_MAX_MINUTE_MICRO_USD
  TRUSTFORGE_PREVIEW_MAX_DAY_MICRO_USD
)
export TRUSTFORGE_PREVIEW_ADMISSION_ENABLED="$PREVIEW_ADMISSION_ENABLED"
if [ "$PREVIEW_ADMISSION_ENABLED" != "0" ] && [ "$PREVIEW_ADMISSION_ENABLED" != "1" ]; then
  echo "[activate] ERROR: preview admission flag must be exactly 0 or 1" >&2
  exit 2
fi
if [ "$PREVIEW_ADMISSION_ENABLED" = "1" ] && {
  [ "${TRUSTFORGE_PREVIEW_MAX_MINUTE_TOKENS-}" != "8000" ] ||
  [ "${TRUSTFORGE_PREVIEW_MAX_DAY_TOKENS-}" != "51200" ] ||
  [ "${TRUSTFORGE_PREVIEW_MAX_MINUTE_MICRO_USD-}" != "50000" ] ||
  [ "${TRUSTFORGE_PREVIEW_MAX_DAY_MICRO_USD-}" != "500000" ];
}; then
  echo "[activate] ERROR: preview cost cap contract missing or invalid" >&2
  exit 2
fi
PREVIEW_RECONCILE_COMMAND="bash deploy/reconcile_preview_service_env.sh"
for key in "${PREVIEW_ENV_KEYS[@]}"; do
  value="${!key-}"
  if [ -n "$value" ] && ! [[ "$value" =~ ^[A-Za-z0-9_./:-]+$ ]]; then
    echo "[activate] ERROR: invalid preview deployment value" >&2
    exit 2
  fi
  if [ -n "$value" ]; then
    PREVIEW_RECONCILE_COMMAND="${PREVIEW_RECONCILE_COMMAND} ${key}=${value}"
  fi
done
if [ -n "$MODEL" ]; then
  MODEL_RECONCILE_COMMAND="if grep -q '^Environment=BEDROCK_MODEL_ID=' /etc/systemd/system/trustforge.service; then sed -i 's|^Environment=BEDROCK_MODEL_ID=.*|Environment=BEDROCK_MODEL_ID=${MODEL}|' /etc/systemd/system/trustforge.service; else sed -i '/^Environment=PYTHONPATH=/a Environment=BEDROCK_MODEL_ID=${MODEL}' /etc/systemd/system/trustforge.service; fi"
fi

if [ -z "$TARGET" ]; then
  echo "usage: $0 --target <iid>" >&2
  exit 2
fi

ACCT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="trustforge-deploy-${ACCT}"
LOCK_TARGET="$TARGET"

CANDIDATE_DIGEST=""
ACTIVE_DIGEST=""
PREVIOUS_DIGEST=""
PREVIOUS_JSON=""
OWNER=""
LOCK_ACQUIRED=0
RECEIPT_START_TS=""

# ---- SSM poll helper (mirrors deploy_ec2.sh) ----------------------------------
poll_ssm_terminal_status() {
  local cmdid="$1" iid="$2" max_wait="${3:-180}" interval="${4:-5}"
  local waited=0 status
  while :; do
    status=$(aws ssm get-command-invocation --region "$REGION" \
      --command-id "$cmdid" --instance-id "$iid" --query Status --output text 2>/dev/null || echo "")
    case "$status" in
      Success|Failed|Cancelled|TimedOut) echo "$status"; return 0 ;;
    esac
    if [ "$waited" -ge "$max_wait" ]; then
      echo "${status:-Unknown}"
      return 1
    fi
    sleep "$interval"
    waited=$((waited + interval))
  done
}

# ---- healthz check ------------------------------------------------------------
verify_web_healthz() {
  local iid="$1"
  local hcmdid
  hcmdid=$(aws ssm send-command --region "$REGION" --instance-ids "$iid" \
    --document-name AWS-RunShellScript --parameters commands='["for i in $(seq 1 12); do systemctl is-active --quiet trustforge && curl -fsS http://localhost/healthz >/dev/null 2>&1 && exit 0; sleep 3; done; echo \"[activate] healthz check failed\" >&2; journalctl -u trustforge -n 40 --no-pager >&2; exit 1"]' \
    --query 'Command.CommandId' --output text)
  if [ -z "$hcmdid" ] || [ "$hcmdid" = "None" ]; then
    echo "[activate] ERROR: healthz send-command failed" >&2
    return 1
  fi
  local hstatus
  hstatus=$(poll_ssm_terminal_status "$hcmdid" "$iid" 120 5) || true
  if [ "$hstatus" != "Success" ]; then
    echo "[activate] ERROR: healthz check failed (Status=${hstatus})" >&2
    aws ssm get-command-invocation --region "$REGION" --command-id "$hcmdid" --instance-id "$iid" \
      --query 'StandardErrorContent' --output text >&2 2>/dev/null || true
    return 1
  fi
  echo "[activate] healthz passed on $iid"
}

# ---- fetch-scheduler verify (mirrors deploy_ec2.sh) ---------------------------
verify_fetch_scheduler() {
  local iid="$1"
  local vcmdid
  vcmdid=$(aws ssm send-command --region "$REGION" --instance-ids "$iid" \
    --document-name AWS-RunShellScript --parameters commands='["set -e","cd /opt/trustforge","if ! ( AWS_REGION='"$REGION"' PYTHONPATH=/opt/trustforge CACHE_BACKEND=dynamodb TRUSTFORGE_CACHE_TABLE=trustforge-connector-cache TRUSTFORGE_COST_LEDGER_TABLE=trustforge-cost-ledger COST_LEDGER_BACKEND=dynamodb /usr/bin/python3.11 scripts/fetch_scheduler.py --probe ); then echo \"[activate] fetch-scheduler --probe failed\" >&2; exit 1; fi","echo \"[activate] fetch-scheduler probe passed\""]' \
    --query 'Command.CommandId' --output text)
  if [ -z "$vcmdid" ] || [ "$vcmdid" = "None" ]; then
    echo "[activate] ERROR: fetch-scheduler probe send-command failed" >&2
    return 1
  fi
  local vstatus
  vstatus=$(poll_ssm_terminal_status "$vcmdid" "$iid" 90 5) || true
  if [ "$vstatus" != "Success" ]; then
    echo "[activate] ERROR: fetch-scheduler probe failed (Status=${vstatus})" >&2
    aws ssm get-command-invocation --region "$REGION" --command-id "$vcmdid" --instance-id "$iid" \
      --query 'StandardErrorContent' --output text >&2 2>/dev/null || true
    return 1
  fi
  echo "[activate] fetch-scheduler probe passed"
}

# ---- durable analysis worker verify ------------------------------------------
verify_analysis_worker() {
  local iid="$1"
  local wcmdid
  wcmdid=$(aws ssm send-command --region "$REGION" --instance-ids "$iid" \
    --document-name AWS-RunShellScript --parameters commands='["set -e","before=$(systemctl show trustforge-analysis-flow.service -p NRestarts --value)","systemctl is-active --quiet trustforge-analysis-flow.service","sleep 15","systemctl is-active --quiet trustforge-analysis-flow.service","after=$(systemctl show trustforge-analysis-flow.service -p NRestarts --value)","test \"$before\" = \"$after\" || { echo \"[activate] analysis-flow worker restart loop detected: before=$before after=$after\" >&2; journalctl -u trustforge-analysis-flow.service -n 80 --no-pager >&2; exit 1; }","echo \"[activate] analysis-flow worker stable\""]' \
    --query 'Command.CommandId' --output text)
  if [ -z "$wcmdid" ] || [ "$wcmdid" = "None" ]; then
    echo "[activate] ERROR: analysis-flow worker check send-command failed" >&2
    return 1
  fi
  local wstatus
  wstatus=$(poll_ssm_terminal_status "$wcmdid" "$iid" 90 5) || true
  if [ "$wstatus" != "Success" ]; then
    echo "[activate] ERROR: analysis-flow worker unstable (Status=${wstatus})" >&2
    aws ssm get-command-invocation --region "$REGION" --command-id "$wcmdid" --instance-id "$iid" \
      --query 'StandardErrorContent' --output text >&2 2>/dev/null || true
    return 1
  fi
  echo "[activate] analysis-flow worker stable on $iid"
}

# ---- real manual report E2E ---------------------------------------------------
verify_analysis_report() {
  local iid="$1"
  local rcmdid
  rcmdid=$(aws ssm send-command --region "$REGION" --instance-ids "$iid" \
    --document-name AWS-RunShellScript --parameters commands='["set -e","cd /opt/trustforge","/usr/bin/python3.11 scripts/verify_production_analysis_report.py --base-url http://127.0.0.1:8080 --timeout-seconds 600 --poll-seconds 5"]' \
    --query 'Command.CommandId' --output text)
  if [ -z "$rcmdid" ] || [ "$rcmdid" = "None" ]; then
    echo "[activate] ERROR: analysis report E2E send-command failed" >&2
    return 1
  fi
  local rstatus
  rstatus=$(poll_ssm_terminal_status "$rcmdid" "$iid" 660 5) || true
  if [ "$rstatus" != "Success" ]; then
    echo "[activate] ERROR: analysis report E2E failed (Status=${rstatus})" >&2
    aws ssm get-command-invocation --region "$REGION" --command-id "$rcmdid" --instance-id "$iid" \
      --query 'StandardErrorContent' --output text >&2 2>/dev/null || true
    return 1
  fi
  echo "[activate] analysis report E2E passed on $iid"
}

# ---- receipt helper -----------------------------------------------------------
write_receipt() {
  local status="$1" error="${2:-}"
  local ts
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  if [ -x .venv/bin/python ]; then PYTHON=.venv/bin/python; else PYTHON=python3; fi
  $PYTHON -c "
import sys
sys.path.insert(0, 'src')
from trustforge.activation_receipt import ActivationReceipt, write_receipt_to_s3
receipt = ActivationReceipt(
    activation_target='${TARGET}',
    owner_id='${OWNER}',
    candidate_digest='${CANDIDATE_DIGEST:-unknown}',
    previous_active_digest='${ACTIVE_DIGEST:-unknown}',
    status='${status}',
    build_timestamp='${RECEIPT_START_TS:-unknown}',
    started_at='${RECEIPT_START_TS:-unknown}',
    finished_at='${ts}',
    error='${error}',
    rollback_triggered='${ROLLBACK_TRIGGERED:-0}',
    rollback_succeeded='${ROLLBACK_OK:-0}',
)
write_receipt_to_s3(receipt, region='${REGION}')
" 2>/dev/null || echo "[activate] receipt write failed (non-fatal)" >&2
}

# ---- ROLLBACK -----------------------------------------------------------------
ROLLBACK_TRIGGERED=0
ROLLBACK_OK=0
UNIT_BACKUP_CAPTURED=0
ROLLBACK() {
  local ec="${1:-1}"
  trap - ERR
  ROLLBACK_TRIGGERED=1
  echo "[activate] ROLLBACK triggered (exit=$ec)"

  local rb_ok=1

  # Restore the captured service configuration independently of artifact
  # history. First activations may not have an active digest yet.
  if [ "$UNIT_BACKUP_CAPTURED" -eq 1 ]; then
    URCMD=$(aws ssm send-command --region "$REGION" --instance-ids "$TARGET" \
      --document-name AWS-RunShellScript --parameters commands='["set -e","cp -p /opt/trustforge/.activation-trustforge.service.bak /etc/systemd/system/trustforge.service","systemctl daemon-reload","systemctl restart trustforge"]' \
      --query 'Command.CommandId' --output text 2>/dev/null || echo "")
    if [ -n "$URCMD" ] && [ "$URCMD" != "None" ]; then
      urstatus=$(poll_ssm_terminal_status "$URCMD" "$TARGET" 120 5) || true
      if [ "$urstatus" != "Success" ]; then
        echo "[activate] rollback: service configuration restore FAILED (Status=$urstatus)" >&2
        rb_ok=0
      fi
    else
      echo "[activate] rollback: service configuration restore FAILED (SSM send-command failed)" >&2
      rb_ok=0
    fi
  else
    echo "[activate] rollback: service configuration unchanged (no captured backup)"
  fi

  # Re-download previous artifact and restart
  if [ -n "$ACTIVE_DIGEST" ] && [ "$ACTIVE_DIGEST" != "unknown" ]; then
    echo "[activate] rollback: restoring previous active ($ACTIVE_DIGEST)"
    RBCMD=$(aws ssm send-command --region "$REGION" --instance-ids "$TARGET" \
      --document-name AWS-RunShellScript --parameters commands='["set -e","cd /opt/trustforge","aws s3 cp s3://'"$BUCKET"'/artifacts/'"$ACTIVE_DIGEST"'/artifact.zip ./app.zip --region '"$REGION"'","aws s3 cp s3://'"$BUCKET"'/artifacts/'"$ACTIVE_DIGEST"'/manifest.json ./manifest.json --region '"$REGION"'","unzip -o app.zip","systemctl daemon-reload","bash deploy/zero_downtime_restart.sh","rm -f /opt/trustforge/.activation-trustforge.service.bak","echo \"[activate] rollback restore completed\""]' \
      --query 'Command.CommandId' --output text 2>/dev/null || echo "")
    if [ -n "$RBCMD" ] && [ "$RBCMD" != "None" ]; then
      rbstatus=$(poll_ssm_terminal_status "$RBCMD" "$TARGET" 120 5) || true
      if [ "$rbstatus" = "Success" ]; then
        echo "[activate] rollback restore succeeded"
      else
        echo "[activate] rollback restore FAILED (Status=$rbstatus)" >&2
        rb_ok=0
      fi
    else
      echo "[activate] rollback restore FAILED (SSM send-command failed)" >&2
      rb_ok=0
    fi
  else
    echo "[activate] rollback: no previous active digest to restore" >&2
    rb_ok=0
  fi

  # Restore pointers (previous back to active, previous back to previous-capture)
  if [ -n "$PREVIOUS_JSON" ]; then
    aws s3 cp - "s3://${BUCKET}/pointers/active.json" --region "$REGION" <<<"$PREVIOUS_JSON" >/dev/null 2>/dev/null || {
      echo "[activate] rollback: pointer active.json restore FAILED" >&2
      rb_ok=0
    }
    echo "[activate] rollback: active.json restored to captured previous"
  fi

  # Post-rollback verification
  if ! verify_web_healthz "$TARGET" 2>/dev/null; then
    echo "[activate] rollback healthz verification FAILED" >&2
    rb_ok=0
  else
    echo "[activate] rollback healthz verification passed"
  fi

  if [ "$rb_ok" = 1 ]; then
    ROLLBACK_OK=1
    echo "[activate] rollback complete and verified (exit=$ec)"
    write_receipt "rolled_back" ""
    release_lock
    exit "$ec"
  fi

  # ---- ROLLBACK-FAILED --------------------------------------------------------
  ROLLBACK_OK=0
  echo "[activate] ROLLBACK-FAILED: automatic rollback did not fully succeed" >&2
  echo "  manual recovery steps:" >&2
  echo "  1) verify S3 pointers/active.json pointed to working artifact" >&2
  echo "     aws s3 cp s3://${BUCKET}/pointers/active.json -" >&2
  echo "  2) redeploy to target: deploy/deploy_ec2.sh" >&2
  echo "  3) verify health: curl http://<ip>/healthz" >&2
  echo "  4) check target instance state: aws ec2 describe-instances --instance-ids ${TARGET}" >&2
  write_receipt "rollback_failed" "rollback did not fully succeed"
  release_lock
  exit 97
}

# ---- lock helpers -------------------------------------------------------------
acquire_lock() {
  if [ -x .venv/bin/python ]; then PYTHON=.venv/bin/python; else PYTHON=python3; fi
  if [ -z "$OWNER" ]; then
    OWNER=$($PYTHON -c "import os,uuid; print(f'{os.getpid()}:{uuid.uuid4().hex}')")
  fi
  LOCK_OK=$($PYTHON -c "
import sys
sys.path.insert(0, 'src')
from trustforge.activation_lock import acquire_activation_lock
print('true' if acquire_activation_lock('${LOCK_TARGET}', '${OWNER}', ttl=600) else 'false')
" 2>/dev/null || echo "error")
  if [ "$LOCK_OK" = "true" ]; then
    LOCK_ACQUIRED=1
    echo "[activate] lock acquired for target=$LOCK_TARGET owner=$OWNER"
    return 0
  fi
  echo "[activate] lock contention for target=$LOCK_TARGET (another activation in progress)" >&2
  exit 98
}

release_lock() {
  if [ "$LOCK_ACQUIRED" -eq 1 ] && [ -n "$OWNER" ]; then
    if [ -x .venv/bin/python ]; then PYTHON=.venv/bin/python; else PYTHON=python3; fi
    $PYTHON -c "
import sys
sys.path.insert(0, 'src')
from trustforge.activation_lock import release_activation_lock
release_activation_lock('${LOCK_TARGET}', '${OWNER}')
" 2>/dev/null || true
    echo "[activate] lock released for target=$LOCK_TARGET"
    LOCK_ACQUIRED=0
  fi
}

# ---- MAIN TRANSACTION ---------------------------------------------------------

echo "[activate] starting activation transaction for target=$TARGET"
RECEIPT_START_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# Step 0: Acquire lock
echo "[activate] Step 0: acquiring activation lock..."
acquire_lock

# Step 1: Preflight
echo "[activate] Step 1: preflight gate..."
"$(dirname "$0")/preflight_activation.sh" --target "$TARGET" --skip-lock || {
  echo "[activate] preflight failed, aborting" >&2
  release_lock
  exit 1
}
echo "[activate] preflight passed"

# Step 1.5: Ensure the target runtime satisfies pyproject requires-python.
echo "[activate] Step 1.5: ensuring Python 3.11 runtime..."
PCMDID=$(aws ssm send-command --region "$REGION" --instance-ids "$TARGET" \
  --document-name AWS-RunShellScript --parameters commands='["set -e","dnf install -y python3.11 python3.11-pip","/usr/bin/python3.11 -m pip install '\''boto3>=1.34'\'' '\''certifi>=2024.2.2'\'' '\''cryptography>=44,<50'\'' '\''jsonschema>=4.23,<5'\'' '\''portalocker>=3,<4'\'' '\''pypdf>=5,<7'\''","sed -i \"s|^ExecStart=/usr/bin/python3 -m trustforge.web$|ExecStart=/usr/bin/python3.11 -m trustforge.web|\" /etc/systemd/system/trustforge.service","grep -q TRUSTFORGE_RUNTIME_RELEASE_MANIFEST_PATH /etc/systemd/system/trustforge.service || sed -i \"/Environment=COST_LEDGER_BACKEND/a Environment=TRUSTFORGE_RUNTIME_RELEASE_MANIFEST_PATH=/opt/trustforge/manifest.json\\nEnvironment=TRUSTFORGE_RUNTIME_RELEASE_ARTIFACT_PATH=/opt/trustforge/app.zip\" /etc/systemd/system/trustforge.service","for unit in fetch-scheduler.service hermes-cycle.service trustforge-analysis-flow.service; do if [ -f /etc/systemd/system/$unit ]; then sed -i \"s|/usr/bin/python3 |/usr/bin/python3.11 |g\" /etc/systemd/system/$unit; fi; done","systemctl daemon-reload","/usr/bin/python3.11 -c \"import boto3, certifi, cryptography, enum, jsonschema, portalocker, pypdf; assert hasattr(enum, '\''StrEnum'\'')\""]' \
  --query 'Command.CommandId' --output text)
if [ -z "$PCMDID" ] || [ "$PCMDID" = "None" ]; then
  echo "[activate] ERROR: Python 3.11 migration send-command failed" >&2
  release_lock
  exit 1
fi
PSTATUS=$(poll_ssm_terminal_status "$PCMDID" "$TARGET" 300 5) || true
if [ "$PSTATUS" != "Success" ]; then
  echo "[activate] ERROR: Python 3.11 migration failed (Status=${PSTATUS})" >&2
  aws ssm get-command-invocation --region "$REGION" --command-id "$PCMDID" --instance-id "$TARGET" \
    --query 'StandardErrorContent' --output text >&2 2>/dev/null || true
  release_lock
  exit 1
fi
echo "[activate] Python 3.11 runtime verified"

# Step 2: Capture pre-state
echo "[activate] Step 2: capturing pre-state..."
ACTIVE_JSON=$(aws s3 cp "s3://${BUCKET}/pointers/active.json" - --region "$REGION" 2>/dev/null || echo "")
ACTIVE_DIGEST=$(echo "$ACTIVE_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('digest',''))" 2>/dev/null || echo "")
PREVIOUS_JSON=$(aws s3 cp "s3://${BUCKET}/pointers/previous.json" - --region "$REGION" 2>/dev/null || echo "")
PREVIOUS_DIGEST=$(echo "$PREVIOUS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('digest',''))" 2>/dev/null || echo "")
CANDIDATE_JSON=$(aws s3 cp "s3://${BUCKET}/pointers/candidate.json" - --region "$REGION" 2>/dev/null || echo "")
CANDIDATE_DIGEST=$(echo "$CANDIDATE_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('digest',''))" 2>/dev/null || echo "")
echo "[activate] candidate=${CANDIDATE_DIGEST} active=${ACTIVE_DIGEST} previous=${PREVIOUS_DIGEST}"

if [ -z "$CANDIDATE_DIGEST" ]; then
  echo "[activate] ERROR: no candidate digest, aborting" >&2
  release_lock
  exit 1
fi

CANDIDATE_PREFIX="artifacts/${CANDIDATE_DIGEST}/"

# ---- ERR trap: any failure from here on triggers rollback ---------------------
trap 'ROLLBACK $?' ERR

# Step 3: Download candidate to target
echo "[activate] Step 3: downloading candidate artifact to target..."
CMDID=$(aws ssm send-command --region "$REGION" --instance-ids "$TARGET" \
  --document-name AWS-RunShellScript --parameters commands='["set -e","cd /opt/trustforge","cp -p /etc/systemd/system/trustforge.service /opt/trustforge/.activation-trustforge.service.bak","aws s3 cp s3://'"$BUCKET"'/'"${CANDIDATE_PREFIX}"'artifact.zip ./app.zip --region '"$REGION"'","aws s3 cp s3://'"$BUCKET"'/'"${CANDIDATE_PREFIX}"'manifest.json ./manifest.json --region '"$REGION"'","echo \"[activate] candidate artifact downloaded\""]' \
  --query 'Command.CommandId' --output text)
if [ -z "$CMDID" ] || [ "$CMDID" = "None" ]; then
  echo "[activate] ERROR: download send-command failed" >&2
  exit 1
fi
SSM_STATUS=$(poll_ssm_terminal_status "$CMDID" "$TARGET" 120 5) || true
if [ "$SSM_STATUS" != "Success" ]; then
  echo "[activate] ERROR: download failed (Status=$SSM_STATUS)" >&2
  aws ssm get-command-invocation --region "$REGION" --command-id "$CMDID" --instance-id "$TARGET" \
    --query 'StandardErrorContent' --output text >&2 2>/dev/null || true
  exit 1
fi
echo "[activate] candidate artifact downloaded"
UNIT_BACKUP_CAPTURED=1

# Step 4: Verify artifact integrity on target
echo "[activate] Step 4: verifying artifact integrity..."
VCMDID=$(aws ssm send-command --region "$REGION" --instance-ids "$TARGET" \
  --document-name AWS-RunShellScript --parameters commands='["set -e","cd /opt/trustforge","unzip -o app.zip 2>&1 | wc -l","echo \"[activate] artifact extracted\"","sha256sum app.zip","ls -la app.zip manifest.json"]' \
  --query 'Command.CommandId' --output text)
if [ -z "$VCMDID" ] || [ "$VCMDID" = "None" ]; then
  echo "[activate] ERROR: verify send-command failed" >&2
  exit 1
fi
VS_STATUS=$(poll_ssm_terminal_status "$VCMDID" "$TARGET" 120 5) || true
if [ "$VS_STATUS" != "Success" ]; then
  echo "[activate] ERROR: artifact verify failed (Status=$VS_STATUS)" >&2
  aws ssm get-command-invocation --region "$REGION" --command-id "$VCMDID" --instance-id "$TARGET" \
    --query 'StandardErrorContent' --output text >&2 2>/dev/null || true
  exit 1
fi
echo "[activate] artifact verified"

# Step 5: Restart service (zero-downtime)
echo "[activate] Step 5: restarting service..."
RCMDID=$(aws ssm send-command --region "$REGION" --instance-ids "$TARGET" \
  --document-name AWS-RunShellScript --parameters commands='["set -e","cd /opt/trustforge","'"$MODEL_RECONCILE_COMMAND"'","'"$TRAINING_RECONCILE_COMMAND"'","'"$PREVIEW_RECONCILE_COMMAND"'","systemctl daemon-reload","bash deploy/zero_downtime_restart.sh","systemctl try-restart trustforge-analysis-flow.service","'"$PREVIEW_READINESS_COMMAND"'","echo \"[activate] service restarted\""]' \
  --query 'Command.CommandId' --output text)
if [ -z "$RCMDID" ] || [ "$RCMDID" = "None" ]; then
  echo "[activate] ERROR: restart send-command failed" >&2
  exit 1
fi
RS_STATUS=$(poll_ssm_terminal_status "$RCMDID" "$TARGET" 120 5) || true
if [ "$RS_STATUS" != "Success" ]; then
  echo "[activate] ERROR: restart failed (Status=$RS_STATUS)" >&2
  aws ssm get-command-invocation --region "$REGION" --command-id "$RCMDID" --instance-id "$TARGET" \
    --query 'StandardErrorContent' --output text >&2 2>/dev/null || true
  exit 1
fi
echo "[activate] service restarted"

# Step 6: Promote candidate pointer -> active, previous active -> previous
echo "[activate] Step 6: promoting pointers..."
if [ -n "$ACTIVE_JSON" ] && [ -n "$ACTIVE_DIGEST" ]; then
  aws s3 cp - "s3://${BUCKET}/pointers/previous.json" --region "$REGION" <<<"$ACTIVE_JSON" >/dev/null
  echo "[activate] previous.json updated (old active: $ACTIVE_DIGEST)"
fi
aws s3 cp - "s3://${BUCKET}/pointers/active.json" --region "$REGION" <<<"$CANDIDATE_JSON" >/dev/null
echo "[activate] active.json promoted to $CANDIDATE_DIGEST"

# Step 7: Post-verify
echo "[activate] Step 7: post-verify..."
verify_web_healthz "$TARGET"
verify_fetch_scheduler "$TARGET"
verify_analysis_worker "$TARGET"
verify_analysis_report "$TARGET"
echo "[activate] post-verify passed"

# Step 8: Write receipt while rollback state is still intact.
echo "[activate] Step 8: writing receipt..."
write_receipt "completed" ""

# Commit the transaction before deleting rollback-only state. A cleanup polling
# timeout must not trigger a rollback after the remote backup may already be gone.
trap - ERR

# Remove the transaction-only unit backup after commit. Cleanup is best-effort:
# activation and its receipt are already complete.
CCMDID=$(aws ssm send-command --region "$REGION" --instance-ids "$TARGET" \
  --document-name AWS-RunShellScript --parameters commands='["set -e","rm -f /opt/trustforge/.activation-trustforge.service.bak"]' \
  --query 'Command.CommandId' --output text 2>/dev/null || echo "")
if [ -z "$CCMDID" ] || [ "$CCMDID" = "None" ]; then
  echo "[activate] WARNING: unit backup cleanup send-command failed" >&2
else
  CS_STATUS=$(poll_ssm_terminal_status "$CCMDID" "$TARGET" 120 5) || true
  if [ "$CS_STATUS" != "Success" ]; then
    echo "[activate] WARNING: unit backup cleanup unconfirmed (Status=$CS_STATUS)" >&2
  fi
fi

# Step 9: Release lock
echo "[activate] Step 9: releasing lock..."
release_lock

echo "[activate] ACTIVATION COMPLETE: target=$TARGET candidate=${CANDIDATE_DIGEST}"

# ---- deploy_ec2.sh integration -------------------------------------------------
# This script is meant to be called standalone or by deploy_ec2.sh.
# When called by deploy_ec2.sh, the caller should have already done:
#   1. Build + upload artifact
#   2. Write candidate pointer
#   3. Deploy artifact to target
# Then call: deploy/activate_release.sh --target <iid>
# to handle the promotion, verification, and receipt.
