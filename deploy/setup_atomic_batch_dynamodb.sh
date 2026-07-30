#!/usr/bin/env bash
# Provision the production atomic multi-angle authority and its least-privilege
# EC2 policy. `--print-policy` keeps the IAM contract offline-testable.
set -euo pipefail

REGION="${REGION:-ap-southeast-2}"
TABLE="${TRUSTFORGE_ATOMIC_BATCH_TABLE:-trustforge-multi-angle-batches}"
ROLE="${TRUSTFORGE_EC2_ROLE:-trustforge-ec2}"
PRINT_POLICY_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --print-policy) PRINT_POLICY_ONLY=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if [[ ! "$TABLE" =~ ^[A-Za-z0-9_.-]{3,255}$ ]]; then
  echo "invalid TRUSTFORGE_ATOMIC_BATCH_TABLE name" >&2
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
  {"Effect":"Allow","Action":[
    "dynamodb:GetItem","dynamodb:BatchGetItem","dynamodb:PutItem",
    "dynamodb:UpdateItem","dynamodb:ConditionCheckItem",
    "dynamodb:TransactWriteItems","dynamodb:Query","dynamodb:DescribeTable"
  ],"Resource":"${TABLE_ARN}"}
]}
JSON
)

if [[ "$PRINT_POLICY_ONLY" -eq 1 ]]; then
  printf '%s\n' "$POLICY"
  exit 0
fi

if ! aws dynamodb describe-table --region "$REGION" --table-name "$TABLE" >/dev/null 2>&1; then
  aws dynamodb create-table --region "$REGION" --table-name "$TABLE" \
    --attribute-definitions AttributeName=pk,AttributeType=S AttributeName=sk,AttributeType=S \
    --key-schema AttributeName=pk,KeyType=HASH AttributeName=sk,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST \
    --tags Key=Project,Value=TrustForge Key=Environment,Value=production \
      Key=ManagedBy,Value=release-train \
      Key=Purpose,Value=multi-angle-atomic-authority >/dev/null
  aws dynamodb wait table-exists --region "$REGION" --table-name "$TABLE"
fi

HASH_KEY="$(aws dynamodb describe-table --region "$REGION" --table-name "$TABLE" \
  --query "Table.KeySchema[?KeyType=='HASH'].AttributeName | [0]" --output text)"
RANGE_KEY="$(aws dynamodb describe-table --region "$REGION" --table-name "$TABLE" \
  --query "Table.KeySchema[?KeyType=='RANGE'].AttributeName | [0]" --output text)"
PK_TYPE="$(aws dynamodb describe-table --region "$REGION" --table-name "$TABLE" \
  --query "Table.AttributeDefinitions[?AttributeName=='pk'].AttributeType | [0]" --output text)"
SK_TYPE="$(aws dynamodb describe-table --region "$REGION" --table-name "$TABLE" \
  --query "Table.AttributeDefinitions[?AttributeName=='sk'].AttributeType | [0]" --output text)"
if [[ "$HASH_KEY" != "pk" || "$RANGE_KEY" != "sk" || "$PK_TYPE" != "S" || "$SK_TYPE" != "S" ]]; then
  echo "existing atomic authority table has incompatible key schema: expected pk(HASH,S)+sk(RANGE,S)" >&2
  exit 1
fi

PITR_STATUS="$(aws dynamodb describe-continuous-backups --region "$REGION" \
  --table-name "$TABLE" \
  --query 'ContinuousBackupsDescription.PointInTimeRecoveryDescription.PointInTimeRecoveryStatus' \
  --output text)"
if [[ "$PITR_STATUS" != "ENABLED" ]]; then
  aws dynamodb update-continuous-backups --region "$REGION" --table-name "$TABLE" \
    --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true >/dev/null
fi

aws iam put-role-policy --role-name "$ROLE" \
  --policy-name trustforge-multi-angle-authority \
  --policy-document "$POLICY" >/dev/null
echo "atomic multi-angle table and least-privilege instance policy are ready: $TABLE_ARN"
