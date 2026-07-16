#!/usr/bin/env bash
# Provision the shared analysis lease table used to prevent cross-instance
# duplicate Bedrock work.  `--print-policy` is intentionally offline-testable.
set -euo pipefail

REGION="${REGION:-ap-southeast-2}"
TABLE="${TRUSTFORGE_LEASE_TABLE:-trustforge-analyze-leases}"
ROLE="${TRUSTFORGE_EC2_ROLE:-trustforge-ec2}"
PRINT_POLICY_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --print-policy) PRINT_POLICY_ONLY=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if [[ ! "$TABLE" =~ ^[A-Za-z0-9_.-]{3,255}$ ]]; then
  echo "invalid TRUSTFORGE_LEASE_TABLE name" >&2
  exit 2
fi

if [[ "$PRINT_POLICY_ONLY" -eq 1 ]]; then
  ACCOUNT=123456789012
else
  ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
fi
TABLE_ARN="arn:aws:dynamodb:${REGION}:${ACCOUNT}:table/${TABLE}"
POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["dynamodb:GetItem","dynamodb:PutItem","dynamodb:DeleteItem"],"Resource":"${TABLE_ARN}"}
]}
JSON
)

if [[ "$PRINT_POLICY_ONLY" -eq 1 ]]; then
  printf '%s\n' "$POLICY"
  exit 0
fi

if ! aws dynamodb describe-table --region "$REGION" --table-name "$TABLE" >/dev/null 2>&1; then
  aws dynamodb create-table --region "$REGION" --table-name "$TABLE" \
    --attribute-definitions AttributeName=lease_key,AttributeType=S \
    --key-schema AttributeName=lease_key,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST >/dev/null
  aws dynamodb wait table-exists --region "$REGION" --table-name "$TABLE"
fi

# TTL is a crash-recovery aid.  Correctness comes from the conditional lease
# expression, because DynamoDB's TTL deletion is deliberately asynchronous.
# `update-time-to-live` rejects an already-enabled attribute, so explicitly
# treat ENABLED/ENABLING as the desired idempotent state for repeat deploys.
TTL_STATUS="$(aws dynamodb describe-time-to-live --region "$REGION" --table-name "$TABLE" \
  --query 'TimeToLiveDescription.TimeToLiveStatus' --output text)"
if [[ "$TTL_STATUS" != "ENABLED" && "$TTL_STATUS" != "ENABLING" ]]; then
  aws dynamodb update-time-to-live --region "$REGION" --table-name "$TABLE" \
    --time-to-live-specification Enabled=true,AttributeName=ttl >/dev/null
fi
aws iam put-role-policy --role-name "$ROLE" --policy-name trustforge-idempotency-lease \
  --policy-document "$POLICY" >/dev/null
echo "idempotency lease table and least-privilege instance policy are ready: $TABLE_ARN"
