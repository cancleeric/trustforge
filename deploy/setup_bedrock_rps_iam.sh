#!/usr/bin/env bash
# Grant the EC2 role only GetItem/UpdateItem on the existing canonical gate
# table. This script never creates or mutates a table/schema.
set -euo pipefail

REGION="${TRUSTFORGE_BEDROCK_RPS_REGION:-us-east-1}"
TABLE="${TRUSTFORGE_BEDROCK_RPS_TABLE:-competition-trustforge-team11-budget}"
ROLE="${TRUSTFORGE_EC2_ROLE:-trustforge-ec2}"
PRINT_POLICY_ONLY=0
[[ "${1:-}" == "--print-policy" ]] && PRINT_POLICY_ONLY=1

if ! [[ "$REGION" =~ ^[a-z]{2}(-gov)?-[a-z]+-[0-9]+$ ]] ||
   ! [[ "$TABLE" =~ ^[A-Za-z0-9_.-]{3,255}$ ]]; then
  echo "invalid canonical Bedrock RPS gate identity" >&2
  exit 2
fi

if [[ "$PRINT_POLICY_ONLY" -eq 1 ]]; then
  ACCOUNT=123456789012
else
  ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
fi
ARN="arn:aws:dynamodb:${REGION}:${ACCOUNT}:table/${TABLE}"
POLICY="{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"dynamodb:GetItem\",\"dynamodb:UpdateItem\"],\"Resource\":\"${ARN}\"}]}"

if [[ "$PRINT_POLICY_ONLY" -eq 1 ]]; then
  echo "$POLICY"
  exit 0
fi

aws dynamodb describe-table --region "$REGION" --table-name "$TABLE" >/dev/null
aws iam put-role-policy --role-name "$ROLE" \
  --policy-name trustforge-bedrock-rps-gate --policy-document "$POLICY" >/dev/null
