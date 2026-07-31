#!/usr/bin/env bash
# Provision exact-parameter IAM for the Admin-managed Etherscan SecureString.
# This script never receives, prints, or writes the secret value.
set -euo pipefail

REGION="${REGION:-ap-southeast-2}"
ROLE="${TRUSTFORGE_EC2_ROLE:-trustforge-ec2}"
PARAMETER="${TRUSTFORGE_ETHERSCAN_SSM_PARAMETER:-/trustforge/production/etherscan-api-key}"
PRINT_POLICY_ONLY=0

for arg in "$@"; do
  case "$arg" in
    --print-policy) PRINT_POLICY_ONLY=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

# Reject any `..` path segment (not just the `/../` substring) — must match
# etherscan_secret.py::_parameter_name(), which rejects `..` in ANY segment
# (`".." in name.split("/")`). The old substring check `*"/../"*` misses a
# trailing `..` (e.g. /trustforge/production/..): IAM would provision a
# parameter that Python then rejects at runtime → silent unavailable, no fetch.
# (Same hardening as deploy/setup_cmc_ssm.sh.)
_has_dotdot_segment=0
IFS='/' read -ra _segments <<< "$PARAMETER"
for _seg in "${_segments[@]}"; do
  if [[ "$_seg" == ".." ]]; then
    _has_dotdot_segment=1
    break
  fi
done
if ! [[ "$PARAMETER" =~ ^/[A-Za-z0-9_./-]{1,255}$ ]] || [[ "$_has_dotdot_segment" -eq 1 ]]; then
  echo "invalid TRUSTFORGE_ETHERSCAN_SSM_PARAMETER" >&2
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
{"Version":"2012-10-17","Statement":[{"Sid":"EtherscanExactSecureString","Effect":"Allow","Action":["ssm:GetParameter","ssm:PutParameter","ssm:DeleteParameter","ssm:AddTagsToResource","ssm:RemoveTagsFromResource","ssm:ListTagsForResource"],"Resource":"${PARAMETER_ARN}"}]}
JSON
)

if [[ "$PRINT_POLICY_ONLY" -eq 1 ]]; then
  printf '%s\n' "$POLICY"
  exit 0
fi

aws iam put-role-policy \
  --role-name "$ROLE" \
  --policy-name trustforge-etherscan-secret \
  --policy-document "$POLICY" >/dev/null
echo "Etherscan exact-parameter IAM policy ready: $PARAMETER_ARN"
