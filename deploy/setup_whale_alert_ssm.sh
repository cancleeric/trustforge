#!/usr/bin/env bash
# Provision exact-parameter IAM for the Admin-managed Whale Alert SecureString.
# This script never receives, prints, or writes the secret value.
set -euo pipefail

REGION="${REGION:-ap-southeast-2}"
ROLE="${TRUSTFORGE_EC2_ROLE:-trustforge-ec2}"
PARAMETER="${TRUSTFORGE_WHALE_ALERT_SSM_PARAMETER:-/trustforge/production/whale-alert-api-key}"
PRINT_POLICY_ONLY=0

for arg in "$@"; do
  case "$arg" in
    --print-policy) PRINT_POLICY_ONLY=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if ! [[ "$PARAMETER" =~ ^/[A-Za-z0-9_./-]{1,255}$ ]] || [[ "$PARAMETER" == *"/../"* ]]; then
  echo "invalid TRUSTFORGE_WHALE_ALERT_SSM_PARAMETER" >&2
  exit 2
fi

if [[ "$PRINT_POLICY_ONLY" -eq 1 ]]; then
  ACCOUNT=123456789012
else
  ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
fi
PARAMETER_PATH="${PARAMETER#/}"
PARAMETER_ARN="arn:aws:ssm:${REGION}:${ACCOUNT}:parameter/${PARAMETER_PATH}"
POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[{"Sid":"WhaleAlertExactSecureString","Effect":"Allow","Action":["ssm:GetParameter","ssm:PutParameter","ssm:DeleteParameter","ssm:AddTagsToResource","ssm:RemoveTagsFromResource","ssm:ListTagsForResource"],"Resource":"${PARAMETER_ARN}"}]}
JSON
)

if [[ "$PRINT_POLICY_ONLY" -eq 1 ]]; then
  printf '%s\n' "$POLICY"
  exit 0
fi

aws iam put-role-policy \
  --role-name "$ROLE" \
  --policy-name trustforge-whale-alert-secret \
  --policy-document "$POLICY" >/dev/null
echo "Whale Alert exact-parameter IAM policy ready: $PARAMETER_ARN"
