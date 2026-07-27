#!/usr/bin/env bash
# Provision the activation lock table used by the activation transaction to
# prevent concurrent A/B release promotion on the same target.
# `--print-policy` is intentionally offline-testable.
set -euo pipefail

REGION="${REGION:-ap-southeast-2}"
TABLE="${TRUSTFORGE_ACTIVATION_LOCK_TABLE:-trustforge-activation-locks}"
ROLE="${TRUSTFORGE_EC2_ROLE:-trustforge-ec2}"
PRINT_POLICY_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --print-policy) PRINT_POLICY_ONLY=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if [[ ! "$TABLE" =~ ^[A-Za-z0-9_.-]{3,255}$ ]]; then
  echo "invalid TRUSTFORGE_ACTIVATION_LOCK_TABLE name" >&2
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
    --attribute-definitions AttributeName=activation_target,AttributeType=S \
    --key-schema AttributeName=activation_target,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST >/dev/null
  aws dynamodb wait table-exists --region "$REGION" --table-name "$TABLE"
fi

TTL_STATUS="$(aws dynamodb describe-time-to-live --region "$REGION" --table-name "$TABLE" \
  --query 'TimeToLiveDescription.TimeToLiveStatus' --output text)"
if [[ "$TTL_STATUS" != "ENABLED" && "$TTL_STATUS" != "ENABLING" ]]; then
  aws dynamodb update-time-to-live --region "$REGION" --table-name "$TABLE" \
    --time-to-live-specification Enabled=true,AttributeName=expires_at >/dev/null
fi

aws iam put-role-policy --role-name "$ROLE" --policy-name trustforge-activation-lock \
  --policy-document "$POLICY" >/dev/null

echo "activation lock table and least-privilege instance policy are ready: $TABLE_ARN"
