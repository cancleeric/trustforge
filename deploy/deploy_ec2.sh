#!/usr/bin/env bash
# TrustForge -> EC2 deploy with immutable content-addressed artifacts + manifest.
# Phase 4 of #728: content-addressed artifact + A/B pointers + fail-closed gates.
set -euo pipefail
cd "$(dirname "$0")/.."

REGION="${REGION:-ap-southeast-2}"
NAME=trustforge
MODEL="${BEDROCK_MODEL_ID-}"
TOKEN_SSM_PREFIX="${TRUSTFORGE_TOKEN_SSM_PREFIX-}"
DAILY_CAP="${TRUSTFORGE_BEDROCK_DAILY_USD_CAP-}"
BUDGET_BACKEND="${TRUSTFORGE_BUDGET_GUARD_BACKEND:-dynamodb}"
CW_METRICS="${TRUSTFORGE_CW_METRICS:-1}"
COUNTER_TABLE="${TRUSTFORGE_BUDGET_COUNTER_TABLE:-trustforge-budget-guard}"
LEASE_BACKEND="${TRUSTFORGE_IDEMPOTENCY_LEASE_BACKEND:-dynamodb}"
LEASE_TABLE="${TRUSTFORGE_LEASE_TABLE:-trustforge-analyze-leases}"
TRAINING_DATA_DIR="${TRUSTFORGE_TRAINING_DATA_DIR:-/opt/trustforge/data/training}"
ATOMIC_TABLE="${TRUSTFORGE_ATOMIC_BATCH_TABLE:-trustforge-multi-angle-batches}"
ATOMIC_CONFIG_VERSION="${TRUSTFORGE_ATOMIC_BATCH_CONFIG_VERSION:-dynamodb-v1}"
ATOMIC_EXCLUSIVE="${TRUSTFORGE_ATOMIC_BATCH_EXCLUSIVE:-1}"
SHARED_ANALYSIS_DB="${TRUSTFORGE_SHARED_ANALYSIS_DB_PATH:-/opt/out/trustforge.sqlite3}"
PREVIEW_ADMISSION_ENABLED="${TRUSTFORGE_PREVIEW_ADMISSION_ENABLED:-0}"
PREVIEW_ENV_KEYS=(
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

if [ -n "$TOKEN_SSM_PREFIX" ] && ! [[ "$TOKEN_SSM_PREFIX" =~ ^[A-Za-z0-9._/~-]+$ ]]; then
  echo "[ec2] ERROR: TRUSTFORGE_TOKEN_SSM_PREFIX contains invalid characters" >&2
  exit 1
fi
if [ -n "$DAILY_CAP" ] && ! [[ "$DAILY_CAP" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
  echo "[ec2] ERROR: TRUSTFORGE_BEDROCK_DAILY_USD_CAP must be a decimal number" >&2
  exit 1
fi
if [ -n "$MODEL" ] && ! [[ "$MODEL" =~ ^[A-Za-z0-9._:-]+$ ]]; then
  echo "[ec2] ERROR: BEDROCK_MODEL_ID contains invalid characters" >&2
  exit 1
fi
if ! [[ "$TRAINING_DATA_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
  echo "[ec2] ERROR: TRUSTFORGE_TRAINING_DATA_DIR must be an absolute safe path" >&2
  exit 1
fi
if [[ ! "$ATOMIC_TABLE" =~ ^[A-Za-z0-9_.-]{3,255}$ ]] \
  || [[ ! "$ATOMIC_CONFIG_VERSION" =~ ^[A-Za-z0-9._-]+$ ]] \
  || [[ "$ATOMIC_EXCLUSIVE" != "1" ]] \
  || [[ ! "$SHARED_ANALYSIS_DB" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
  echo "[ec2] ERROR: invalid atomic authority runtime contract" >&2
  exit 1
fi
if [ "$PREVIEW_ADMISSION_ENABLED" != "0" ] && [ "$PREVIEW_ADMISSION_ENABLED" != "1" ]; then
  echo "[ec2] ERROR: preview admission flag must be exactly 0 or 1" >&2
  exit 1
fi
if [ "$PREVIEW_ADMISSION_ENABLED" = "1" ] && {
  [ "${TRUSTFORGE_PREVIEW_MAX_MINUTE_TOKENS-}" != "8000" ] ||
  [ "${TRUSTFORGE_PREVIEW_MAX_DAY_TOKENS-}" != "51200" ] ||
  [ "${TRUSTFORGE_PREVIEW_MAX_MINUTE_MICRO_USD-}" != "50000" ] ||
  [ "${TRUSTFORGE_PREVIEW_MAX_DAY_MICRO_USD-}" != "500000" ];
}; then
  echo "[ec2] ERROR: preview cost cap contract missing or invalid" >&2
  exit 1
fi
for key in "${PREVIEW_ENV_KEYS[@]}"; do
  value="${!key-}"
  if [ -n "$value" ] && ! [[ "$value" =~ ^[A-Za-z0-9_./:-]+$ ]]; then
    echo "[ec2] ERROR: invalid preview deployment value" >&2
    exit 1
  fi
done

EXTRA_UNIT_ENV=""
EXTRA_UNIT_ENV="${EXTRA_UNIT_ENV}Environment=TRUSTFORGE_TRAINING_DATA_DIR=${TRAINING_DATA_DIR}\n"
EXTRA_UNIT_ENV="${EXTRA_UNIT_ENV}Environment=TRUSTFORGE_ATOMIC_BATCH_TABLE=${ATOMIC_TABLE}\n"
EXTRA_UNIT_ENV="${EXTRA_UNIT_ENV}Environment=TRUSTFORGE_ATOMIC_BATCH_CONFIG_VERSION=${ATOMIC_CONFIG_VERSION}\n"
EXTRA_UNIT_ENV="${EXTRA_UNIT_ENV}Environment=TRUSTFORGE_ATOMIC_BATCH_EXCLUSIVE=${ATOMIC_EXCLUSIVE}\n"
EXTRA_UNIT_ENV="${EXTRA_UNIT_ENV}Environment=TRUSTFORGE_SHARED_ANALYSIS_DB_PATH=${SHARED_ANALYSIS_DB}\n"
[ -n "$DAILY_CAP" ] && EXTRA_UNIT_ENV="${EXTRA_UNIT_ENV}Environment=TRUSTFORGE_BEDROCK_DAILY_USD_CAP=${DAILY_CAP}\n"
[ -n "$TOKEN_SSM_PREFIX" ] && EXTRA_UNIT_ENV="${EXTRA_UNIT_ENV}Environment=TRUSTFORGE_TOKEN_SSM_PREFIX=${TOKEN_SSM_PREFIX}\n"
EXTRA_UNIT_ENV="${EXTRA_UNIT_ENV}Environment=TRUSTFORGE_BUDGET_GUARD_BACKEND=${BUDGET_BACKEND}\n"
EXTRA_UNIT_ENV="${EXTRA_UNIT_ENV}Environment=TRUSTFORGE_BUDGET_COUNTER_TABLE=${COUNTER_TABLE}\n"
EXTRA_UNIT_ENV="${EXTRA_UNIT_ENV}Environment=TRUSTFORGE_CW_METRICS=${CW_METRICS}\n"
EXTRA_UNIT_ENV="${EXTRA_UNIT_ENV}Environment=TRUSTFORGE_IDEMPOTENCY_LEASE_BACKEND=${LEASE_BACKEND}\n"
EXTRA_UNIT_ENV="${EXTRA_UNIT_ENV}Environment=TRUSTFORGE_LEASE_TABLE=${LEASE_TABLE}\n"
EXTRA_UNIT_ENV="${EXTRA_UNIT_ENV}Environment=TRUSTFORGE_PREVIEW_ADMISSION_ENABLED=${PREVIEW_ADMISSION_ENABLED}\n"
for key in "${PREVIEW_ENV_KEYS[@]}"; do
  value="${!key-}"
  [ -n "$value" ] && EXTRA_UNIT_ENV="${EXTRA_UNIT_ENV}Environment=${key}=${value}\n"
done

ssm_env_cmd() {
  local key="$1" val="$2"
  if [ -n "$val" ]; then
    printf ',"if grep -q \\"^Environment=%s=\\" /etc/systemd/system/trustforge.service; then sed -i \\"s|^Environment=%s=.*|Environment=%s=%s|\\" /etc/systemd/system/trustforge.service; else sed -i \\"/^Environment=PYTHONPATH=/a Environment=%s=%s\\" /etc/systemd/system/trustforge.service; fi"' "$key" "$key" "$key" "$val" "$key" "$val"
  else
    printf ',"sed -i \\"/^Environment=%s=/d\\" /etc/systemd/system/trustforge.service"' "$key"
  fi
}

UNIT_ENV_RECONCILE_CMDS="$(ssm_env_cmd TRUSTFORGE_ADMIN_TOKEN "")$(ssm_env_cmd TRUSTFORGE_LIVE_TOKEN "")$(ssm_env_cmd TRUSTFORGE_BEDROCK_DAILY_USD_CAP "$DAILY_CAP")$(ssm_env_cmd TRUSTFORGE_TOKEN_SSM_PREFIX "$TOKEN_SSM_PREFIX")$(ssm_env_cmd TRUSTFORGE_BUDGET_GUARD_BACKEND "$BUDGET_BACKEND")$(ssm_env_cmd TRUSTFORGE_BUDGET_COUNTER_TABLE "$COUNTER_TABLE")$(ssm_env_cmd TRUSTFORGE_CW_METRICS "$CW_METRICS")$(ssm_env_cmd TRUSTFORGE_IDEMPOTENCY_LEASE_BACKEND "$LEASE_BACKEND")$(ssm_env_cmd TRUSTFORGE_LEASE_TABLE "$LEASE_TABLE")$(ssm_env_cmd TRUSTFORGE_ATOMIC_BATCH_TABLE "$ATOMIC_TABLE")$(ssm_env_cmd TRUSTFORGE_ATOMIC_BATCH_CONFIG_VERSION "$ATOMIC_CONFIG_VERSION")$(ssm_env_cmd TRUSTFORGE_ATOMIC_BATCH_EXCLUSIVE "$ATOMIC_EXCLUSIVE")$(ssm_env_cmd TRUSTFORGE_SHARED_ANALYSIS_DB_PATH "$SHARED_ANALYSIS_DB")"
UNIT_ENV_RECONCILE_CMDS="${UNIT_ENV_RECONCILE_CMDS}$(ssm_env_cmd TRUSTFORGE_PREVIEW_ADMISSION_ENABLED "$PREVIEW_ADMISSION_ENABLED")"
for key in "${PREVIEW_ENV_KEYS[@]}"; do
  UNIT_ENV_RECONCILE_CMDS="${UNIT_ENV_RECONCILE_CMDS}$(ssm_env_cmd "$key" "${!key-}")"
done

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

verify_fetch_scheduler() {
  local iid="$1"
  local seed_cmdid
  seed_cmdid=$(aws ssm send-command --region "$REGION" --instance-ids "$iid" \
    --document-name AWS-RunShellScript --parameters commands='["cd /opt/trustforge","systemctl daemon-reload","if systemctl start fetch-scheduler.service; then echo \"[ec2] fetch-scheduler seed success (best-effort)\"; else echo \"[ec2] WARNING: fetch-scheduler seed non-zero (best-effort)\" >&2; journalctl -u fetch-scheduler -n 40 --no-pager >&2 || true; fi"]' \
    --query 'Command.CommandId' --output text 2>/dev/null || echo "")
  if [ -n "$seed_cmdid" ] && [ "$seed_cmdid" != "None" ]; then
    echo "[ec2] fetch-scheduler seed sent (CommandId=${seed_cmdid}, fire-and-forget)"
  fi

  local vcmdid
  vcmdid=$(aws ssm send-command --region "$REGION" --instance-ids "$iid" \
    --document-name AWS-RunShellScript --parameters commands='["set -e","cd /opt/trustforge","if ! ( AWS_REGION='"$REGION"' PYTHONPATH=/opt/trustforge CACHE_BACKEND=dynamodb TRUSTFORGE_CACHE_TABLE=trustforge-connector-cache TRUSTFORGE_COST_LEDGER_TABLE=trustforge-cost-ledger COST_LEDGER_BACKEND=dynamodb /usr/bin/python3.11 scripts/fetch_scheduler.py --probe ); then echo \"[ec2] fetch-scheduler --probe failed\" >&2; exit 1; fi","echo \"[ec2] fetch-scheduler probe passed\""]' \
    --query 'Command.CommandId' --output text)
  if [ -z "$vcmdid" ] || [ "$vcmdid" = "None" ]; then
    echo "[ec2] ERROR: fetch-scheduler probe send-command failed" >&2
    exit 1
  fi
  local vstatus
  vstatus=$(poll_ssm_terminal_status "$vcmdid" "$iid" 90 5) || true
  if [ "$vstatus" != "Success" ]; then
    echo "[ec2] ERROR: fetch-scheduler probe failed (Status=${vstatus})" >&2
    aws ssm get-command-invocation --region "$REGION" --command-id "$vcmdid" --instance-id "$iid" \
      --query 'StandardErrorContent' --output text >&2 2>/dev/null || true
    exit 1
  fi
  echo "[ec2] fetch-scheduler probe passed"
}

verify_web_healthz() {
  local iid="$1"
  local hcmdid
  hcmdid=$(aws ssm send-command --region "$REGION" --instance-ids "$iid" \
    --document-name AWS-RunShellScript --parameters commands='["for i in $(seq 1 12); do systemctl is-active --quiet trustforge && curl -fsS http://localhost/healthz >/dev/null 2>&1 && exit 0; sleep 3; done; echo \"[ec2] healthz check failed\" >&2; journalctl -u trustforge -n 40 --no-pager >&2; exit 1"]' \
    --query 'Command.CommandId' --output text)
  if [ -z "$hcmdid" ] || [ "$hcmdid" = "None" ]; then
    echo "[ec2] ERROR: web healthz send-command failed" >&2
    exit 1
  fi
  local hstatus
  hstatus=$(poll_ssm_terminal_status "$hcmdid" "$iid" 120 5) || true
  if [ "$hstatus" != "Success" ]; then
    echo "[ec2] ERROR: web healthz failed (Status=${hstatus})" >&2
    aws ssm get-command-invocation --region "$REGION" --command-id "$hcmdid" --instance-id "$iid" \
      --query 'StandardErrorContent' --output text >&2 2>/dev/null || true
    return 1
  fi
  echo "[ec2] web healthz passed"
}

verify_preview_admission() {
  local iid="$1" cmdid status
  cmdid=$(aws ssm send-command --region "$REGION" --instance-ids "$iid" \
    --document-name AWS-RunShellScript \
    --parameters commands='["set -e","cd /opt/trustforge","bash deploy/preview_admission_release_gate.sh"]' \
    --query 'Command.CommandId' --output text)
  status=$(poll_ssm_terminal_status "$cmdid" "$iid" 120 5) || true
  if [ "$status" != "Success" ]; then
    echo "[ec2] preview admission readiness failed" >&2
    return 1
  fi
}

# 0) Discover existing instances (same as before) -------------------------------------
if ! MATCHES=$(aws ec2 describe-instances --region "$REGION" \
  --filters Name=tag:Name,Values=trustforge-demo \
    Name=instance-state-name,Values=pending,running,shutting-down,stopping,stopped \
  --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output text); then
  echo "[ec2] ERROR: describe-instances failed" >&2
  exit 1
fi
MATCH_COUNT=$(printf '%s\n' "$MATCHES" | grep -c . || true)

ACCT=$(aws sts get-caller-identity --query Account --output text)
PREVIEW_CURRENT_PARAMETER="${TRUSTFORGE_PREVIEW_QUOTA_KEY_PARAMETER:-/trustforge/preview-admission/quota-hmac}"
PREVIEW_PREVIOUS_PARAMETER="${TRUSTFORGE_PREVIEW_PREVIOUS_QUOTA_KEY_PARAMETER:-/trustforge/preview-admission/__none__}"
PREVIEW_CURRENT_PARAMETER_ARN="arn:aws:ssm:$REGION:$ACCT:parameter${PREVIEW_CURRENT_PARAMETER}"
PREVIEW_PREVIOUS_PARAMETER_ARN="arn:aws:ssm:$REGION:$ACCT:parameter${PREVIEW_PREVIOUS_PARAMETER}"
BUCKET="trustforge-deploy-${ACCT}"
ROLE=trustforge-ec2
SG=trustforge-ec2-sg
INSTANCE_TYPE="${INSTANCE_TYPE:-t3.micro}"
BOOTSTRAP="${TRUSTFORGE_BOOTSTRAP:-1}"

echo "[ec2] account=${ACCT} region=${REGION} model=${MODEL:-<offline>}"

# 1) Bootstrap IAM + DynamoDB --------------------------------------------------------
if [ "$BOOTSTRAP" = "1" ]; then
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  echo "[ec2] creating IAM role ${ROLE}..."
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  aws iam attach-role-policy --role-name "$ROLE" \
    --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore >/dev/null
  aws iam create-instance-profile --instance-profile-name "$ROLE" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$ROLE" --role-name "$ROLE" >/dev/null
  echo "[ec2] waiting for instance profile propagation..."
  for _i in $(seq 1 30); do
    if aws iam get-instance-profile --instance-profile-name "$ROLE" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
fi

aws iam put-role-policy --role-name "$ROLE" --policy-name trustforge-inline \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[
    {\"Effect\":\"Allow\",\"Action\":\"bedrock:InvokeModel\",\"Resource\":[
      \"arn:aws:bedrock:ap-southeast-2::foundation-model/anthropic.*\",
      \"arn:aws:bedrock:ap-southeast-4::foundation-model/anthropic.*\",
      \"arn:aws:bedrock:ap-southeast-6::foundation-model/anthropic.*\",
      \"arn:aws:bedrock:$REGION:$ACCT:inference-profile/*anthropic*\"]},
    {\"Effect\":\"Allow\",\"Action\":\"s3:GetObject\",\"Resource\":\"arn:aws:s3:::$BUCKET/*\"},
    {\"Effect\":\"Allow\",\"Action\":[\"ssm:GetParameter\",\"ssm:GetParametersByPath\",\"ssm:DeleteParameter\"],\"Resource\":[\"arn:aws:ssm:$REGION:$ACCT:parameter/trustforge/deploy\",\"arn:aws:ssm:$REGION:$ACCT:parameter/trustforge/deploy/*\"]},
    {\"Effect\":\"Allow\",\"Action\":\"ssm:GetParameter\",\"Resource\":\"arn:aws:ssm:$REGION:$ACCT:parameter/trustforge/runtime/*\"},
    {\"Effect\":\"Allow\",\"Action\":\"kms:Decrypt\",\"Resource\":\"*\",\"Condition\":{\"StringEquals\":{\"kms:ViaService\":\"ssm.$REGION.amazonaws.com\"}}},
    {\"Effect\":\"Deny\",\"Action\":\"kms:Decrypt\",\"Resource\":\"*\",\"Condition\":{\"ArnLike\":{\"kms:EncryptionContext:PARAMETER_ARN\":\"arn:aws:ssm:$REGION:$ACCT:parameter/trustforge/preview-admission/*\"},\"ArnNotEquals\":{\"kms:EncryptionContext:PARAMETER_ARN\":[\"$PREVIEW_CURRENT_PARAMETER_ARN\",\"$PREVIEW_PREVIOUS_PARAMETER_ARN\"]}}}
  ]}" >/dev/null

aws iam put-role-policy --role-name "$ROLE" --policy-name trustforge-cloudwatch \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[\"Effect\":\"Allow\",\"Action\":\"cloudwatch:PutMetricData\",\"Resource\":\"*\"]}" >/dev/null

aws iam put-role-policy --role-name "$ROLE" --policy-name trustforge-dynamodb \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[
    {\"Effect\":\"Allow\",\"Action\":[\"dynamodb:GetItem\",\"dynamodb:PutItem\",\"dynamodb:Scan\",\"dynamodb:Query\"],\"Resource\":\"arn:aws:dynamodb:$REGION:$ACCT:table/trustforge-connector-cache\"},
    {\"Effect\":\"Allow\",\"Action\":[\"dynamodb:GetItem\",\"dynamodb:PutItem\",\"dynamodb:Scan\"],\"Resource\":\"arn:aws:dynamodb:$REGION:$ACCT:table/trustforge-cost-ledger\"}
  ]}" >/dev/null

for T in trustforge-connector-cache trustforge-cost-ledger; do
  if ! aws dynamodb describe-table --region "$REGION" --table-name "$T" >/dev/null 2>&1; then
    echo "[ec2] ERROR: DynamoDB table $T not found" >&2
    exit 1
  fi
done

if ! "$(dirname "$0")/setup_budget_guard_dynamodb.sh"; then
  echo "[ec2] ERROR: budget guard setup failed" >&2
  exit 1
fi
if ! "$(dirname "$0")/setup_idempotency_lease_dynamodb.sh"; then
  echo "[ec2] ERROR: idempotency lease setup failed" >&2
  exit 1
fi
if ! aws dynamodb describe-table --region "$REGION" --table-name "$LEASE_TABLE" >/dev/null 2>&1; then
  echo "[ec2] ERROR: lease table $LEASE_TABLE not found" >&2
  exit 1
fi
BG_TABLE="${TRUSTFORGE_BUDGET_COUNTER_TABLE:-trustforge-budget-guard}"
if ! aws dynamodb describe-table --region "$REGION" --table-name "$BG_TABLE" >/dev/null 2>&1; then
  echo "[ec2] ERROR: budget guard table $BG_TABLE not found" >&2
  exit 1
fi
else
  echo "[ec2] skip bootstrap (TRUSTFORGE_BOOTSTRAP=${BOOTSTRAP})"
fi

# 2) Build content-addressed artifact + manifest --------------------------------------
echo "[ec2] building artifact..."
B=$(mktemp -d); ZIP="$(pwd)/build/trustforge_app.zip"; mkdir -p build
cp -r src/trustforge "$B/trustforge"; cp -r src/trustforge_core "$B/trustforge_core"
cp -r data "$B/data"; cp -r demo "$B/demo"
cp -r scripts "$B/scripts"
cp -r skills "$B/skills"
cp -r deploy "$B/deploy"
chmod +x "$B/scripts/"*.sh "$B/deploy/"*.sh
mkdir -p "$B/docs"; cp -r docs/api "$B/docs/api"; cp llms.txt "$B/llms.txt"
GIT_SHA=$(git rev-parse HEAD 2>/dev/null || echo unknown)
[[ "$GIT_SHA" =~ ^[0-9a-f]{40}$ ]] || {
  echo "[ec2] ERROR: refusing release without an exact git SHA" >&2
  exit 1
}
PACKAGE_VER=$(python3 -c 'import runpy; print(runpy.run_path("scripts/release_version.py")["package_version"]())')
[[ "$PACKAGE_VER" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
  echo "[ec2] ERROR: refusing release without a strict package SemVer" >&2
  exit 1
}
GIT_VER="v${PACKAGE_VER}"
printf 'VERSION = "%s"\n' "$GIT_VER" > "$B/trustforge/_version.py"
echo "[ec2] version=${GIT_VER}"

# Compute config snapshot before building
if [ -x .venv/bin/python ]; then PYTHON="${PWD}/.venv/bin/python"; else PYTHON=python3; fi
CONFIG_SNAPSHOT=$("$PYTHON" -c "
import sys
sys.path.insert(0, 'src')
from trustforge.config_snapshot import ConfigSnapshot
import json
snapshot = ConfigSnapshot.capture()
print(json.dumps({'identity': snapshot.identity, 'payload': snapshot.payload}))
" 2>/dev/null || echo '{"identity":"sha256:unknown","payload":"{}"}')
CONFIG_IDENTITY=$(echo "$CONFIG_SNAPSHOT" | python3 -c "import sys,json; print(json.load(sys.stdin)['identity'])")
CONFIG_SNAPSHOT_JSON=$(echo "$CONFIG_SNAPSHOT" | python3 -c "import sys,json; print(json.load(sys.stdin)['payload'])")
export CONFIG_SNAPSHOT_JSON

( cd "$B" && zip -qr "$ZIP" trustforge trustforge_core data demo scripts skills deploy docs llms.txt -x '*/__pycache__/*' )
ARTIFACT_DIGEST=$(sha256sum "$ZIP" | awk '{print $1}')
ARTIFACT_PREFIX="artifacts/${ARTIFACT_DIGEST}/"
MANIFEST_JSON=$(cd "$B" && "$PYTHON" -c "
import os, sys, json
sys.path.insert(0, '.')
from trustforge.release_manifest import compute_manifest, manifest_to_json
manifest = compute_manifest('${ZIP}', os.environ['CONFIG_SNAPSHOT_JSON'].encode('utf-8'), git_sha='${GIT_SHA}')
print(manifest_to_json(manifest))
" 2>/dev/null || echo '{}')
rm -rf "$B"

if [ "$MANIFEST_JSON" = "{}" ]; then
  echo "[ec2] ERROR: failed to compute manifest" >&2
  exit 1
fi

echo "[ec2] artifact digest=sha256:${ARTIFACT_DIGEST}"
echo "[ec2] config identity=${CONFIG_IDENTITY}"

# Upload content-addressed artifact + manifest
aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" 2>/dev/null || \
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION" >/dev/null

echo "[ec2] uploading to s3://${BUCKET}/${ARTIFACT_PREFIX}..."
aws s3 cp "$ZIP" "s3://${BUCKET}/${ARTIFACT_PREFIX}artifact.zip" --region "$REGION" >/dev/null
aws s3 cp - "s3://${BUCKET}/${ARTIFACT_PREFIX}manifest.json" --region "$REGION" <<<"$MANIFEST_JSON" >/dev/null

# Append to index
INDEX_LINE=$(python3 -c "
import sys, json
entry = {'digest': '${ARTIFACT_DIGEST}', 'timestamp': __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(), 'git_sha': '${GIT_SHA}', 'version': '${GIT_VER}'}
print(json.dumps(entry, sort_keys=True))
")
echo "$INDEX_LINE" | aws s3 cp - "s3://${BUCKET}/artifacts/index.jsonl" --region "$REGION" >/dev/null || true
aws s3api put-object-acl --bucket "$BUCKET" --key "artifacts/index.jsonl" --acl bucket-owner-full-control --region "$REGION" 2>/dev/null || true

# 3) Write candidate pointer
CANDIDATE_JSON="{\"digest\":\"${ARTIFACT_DIGEST}\",\"uploaded_at\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"version\":\"${GIT_VER}\"}"
aws s3 cp - "s3://${BUCKET}/pointers/candidate.json" --region "$REGION" <<<"$CANDIDATE_JSON" >/dev/null
echo "[ec2] candidate pointer written"

# 4) Update-in-place or first-time build ----------------------------------------------
if [ "$MATCH_COUNT" -gt 1 ]; then
  echo "[ec2] ERROR: found ${MATCH_COUNT} matching instances" >&2
  printf '%s\n' "$MATCHES" >&2
  exit 1
elif [ "$MATCH_COUNT" -eq 1 ]; then
  IID=$(printf '%s\n' "$MATCHES" | awk '{print $1}')
  STATE=$(printf '%s\n' "$MATCHES" | awk '{print $2}')
  case "$STATE" in
    running) echo "[ec2] existing instance ${IID} (running) -> update-in-place" ;;
    stopped)
      echo "[ec2] existing instance ${IID} (stopped) -> start then update"
      aws ec2 start-instances --region "$REGION" --instance-ids "$IID" >/dev/null
      aws ec2 wait instance-running --region "$REGION" --instance-ids "$IID"
      echo "[ec2] waiting for SSM agent..."
      SSM_READY=""
      for _try in $(seq 1 30); do
        PING=$(aws ssm describe-instance-information --region "$REGION" \
          --filters Key=InstanceIds,Values="$IID" \
          --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || echo "")
        if [ "$PING" = "Online" ]; then SSM_READY=1; break; fi
        sleep 5
      done
      if [ -z "$SSM_READY" ]; then
        echo "[ec2] ERROR: SSM agent not online after start" >&2
        exit 1
      fi
      echo "[ec2] instance started and SSM ready"
      ;;
    *)
      echo "[ec2] ERROR: instance $IID state=${STATE} is transitional" >&2
      exit 1
      ;;
  esac

  # Deploy candidate: delegate full activation transaction to
  # deploy/activate_release.sh (lock → preflight → download → verify →
  # restart → promote → post-verify → receipt → release).
  echo "[ec2] activating candidate artifact on ${IID} via activate_release.sh..."
  deploy/activate_release.sh --target "$IID"

  IP=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$IID" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
  echo "[ec2] updated instance: $IID  public IP: ${IP} (model=${MODEL:-<offline>})"
  echo "[ec2] Live Demo: http://${IP}/"
  echo "[ec2]   healthz: http://${IP}/healthz"
  exit 0
fi

# 5) First-time build (no existing instances) -----------------------------------------
echo "[ec2] no existing instance -> first-time build"

VPC=$(aws ec2 describe-vpcs --region "$REGION" --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
SGID=$(aws ec2 describe-security-groups --region "$REGION" --filters Name=group-name,Values=$SG Name=vpc-id,Values=$VPC --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo None)
if [ "$SGID" = "None" ] || [ -z "$SGID" ]; then
  echo "[ec2] creating security group..."
  SGID=$(aws ec2 create-security-group --region "$REGION" --group-name "$SG" \
    --description "TrustForge demo 80" --vpc-id "$VPC" --query GroupId --output text)
  aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SGID" \
    --protocol tcp --port 80 --cidr 0.0.0.0/0 >/dev/null
fi

echo "[ec2] SG=$SGID VPC=$VPC"

UD=$(mktemp)
cat > "$UD" <<EOF
#!/bin/bash
set -x
dnf install -y python3.11 python3.11-pip unzip >/var/log/tf-setup.log 2>&1
python3.11 -m pip install 'boto3>=1.34' 'certifi>=2024.2.2' 'cryptography>=44,<50' 'jsonschema>=4.23,<5' 'portalocker>=3,<4' 'pypdf>=5,<7' >>/var/log/tf-setup.log 2>&1
mkdir -p /opt/trustforge && cd /opt/trustforge
aws s3 cp s3://${BUCKET}/${ARTIFACT_PREFIX}artifact.zip ./app.zip --region ${REGION} >>/var/log/tf-setup.log 2>&1
aws s3 cp s3://${BUCKET}/${ARTIFACT_PREFIX}manifest.json ./manifest.json --region ${REGION} >>/var/log/tf-setup.log 2>&1
unzip -o app.zip >>/var/log/tf-setup.log 2>&1
cat > /etc/systemd/system/trustforge.service <<UNIT
[Unit]
Description=TrustForge web
After=network.target
[Service]
Environment=PORT=80
Environment=TRUSTFORGE_HOME=/opt/trustforge
Environment=AWS_REGION=${REGION}
Environment=BEDROCK_MODEL_ID=${MODEL}
Environment=PYTHONPATH=/opt/trustforge
Environment=CACHE_BACKEND=dynamodb
Environment=TRUSTFORGE_CACHE_TABLE=trustforge-connector-cache
Environment=TRUSTFORGE_COST_LEDGER_TABLE=trustforge-cost-ledger
Environment=COST_LEDGER_BACKEND=dynamodb
Environment=TRUSTFORGE_RUNTIME_RELEASE_MANIFEST_PATH=/opt/trustforge/manifest.json
Environment=TRUSTFORGE_RUNTIME_RELEASE_ARTIFACT_PATH=/opt/trustforge/app.zip
ExecStartPre=/opt/trustforge/scripts/sweep_deploy_parameters.sh
${EXTRA_UNIT_ENV}ExecStart=/usr/bin/python3.11 -m trustforge.web
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
chmod 600 /etc/systemd/system/trustforge.service
cat > /etc/systemd/system/fetch-scheduler.service <<UNIT2
[Unit]
Description=TrustForge connector cache fetch scheduler
[Service]
Type=oneshot
WorkingDirectory=/opt/trustforge
Environment=AWS_REGION=${REGION}
ExecStart=/usr/bin/python3.11 scripts/fetch_scheduler.py
UNIT2
cat > /etc/systemd/system/fetch-scheduler.timer <<UNIT3
[Unit]
Description=Run TrustForge fetch scheduler periodically
[Timer]
OnBootSec=1min
OnUnitActiveSec=10min
[Install]
WantedBy=timers.target
UNIT3
systemctl daemon-reload
systemctl enable --now trustforge.service
systemctl enable --now fetch-scheduler.timer
EOF

IID=$(aws ec2 run-instances --region "$REGION" --instance-type "$INSTANCE_TYPE" \
  --image-id resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-x86_64 \
  --iam-instance-profile Name="$ROLE" --security-group-ids "$SGID" \
  --user-data "file://$UD" --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=trustforge-demo}]" \
  --query 'Instances[0].InstanceId' --output text)
rm "$UD"

echo "[ec2] launched new instance: $IID"
aws ec2 wait instance-running --region "$REGION" --instance-ids "$IID"

for _i in $(seq 1 60); do
  PING=$(aws ssm describe-instance-information --region "$REGION" \
    --filters Key=InstanceIds,Values="$IID" \
    --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || echo "")
  if [ "$PING" = "Online" ]; then break; fi
  sleep 5
done

verify_web_healthz "$IID"
verify_preview_admission "$IID"
verify_fetch_scheduler "$IID"

IP=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

# First-time: candidate == active (no previous yet)
aws s3 cp - "s3://${BUCKET}/pointers/active.json" --region "$REGION" <<<"$CANDIDATE_JSON" >/dev/null
aws s3 cp - "s3://${BUCKET}/pointers/previous.json" --region "$REGION" <<<"$CANDIDATE_JSON" >/dev/null

# First-time receipt
if [ -x .venv/bin/python ]; then PYTHON="${PWD}/.venv/bin/python"; else PYTHON=python3; fi
"$PYTHON" -c "
import sys
sys.path.insert(0, 'src')
from trustforge.activation_receipt import ActivationReceipt, write_receipt_to_s3
ts = '$(date -u +%Y-%m-%dT%H:%M:%SZ)'
receipt = ActivationReceipt(
    activation_target='$IID',
    owner_id='first-time',
    candidate_digest='$ARTIFACT_DIGEST',
    previous_active_digest='',
    status='completed',
    build_timestamp=ts,
    started_at=ts,
    finished_at=ts,
    error='',
    rollback_triggered=False,
    rollback_succeeded=False,
)
write_receipt_to_s3(receipt, region='$REGION')
" 2>/dev/null || echo "[ec2] first-time receipt write failed (non-fatal)" >&2

echo "[ec2] first-time build complete: $IID  public IP: ${IP} (model=${MODEL:-<offline>})"
echo "[ec2] Live Demo: http://${IP}/"
echo "[ec2]   healthz: http://${IP}/healthz"
