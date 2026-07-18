#!/usr/bin/env bash
# Install the AWS-owned clock for the bounded Hermes cycle.
set -euo pipefail

REGION="${REGION:-ap-southeast-2}"
NAME="${TRUSTFORGE_SCHEDULE_NAME:-trustforge-hermes-cycle-30m}"
ROLE="${TRUSTFORGE_SCHEDULER_ROLE:-trustforge-eventbridge-scheduler}"
QUEUE="${TRUSTFORGE_SCHEDULER_DLQ:-trustforge-scheduler-dlq}"
INSTANCE_ID="${TRUSTFORGE_INSTANCE_ID:-}"
APPLY="${1:-}"

if [ -z "$INSTANCE_ID" ]; then
  INSTANCE_ID=$(aws ec2 describe-instances --region "$REGION" \
    --filters Name=tag:Name,Values=trustforge-demo Name=instance-state-name,Values=running \
    --query 'Reservations[].Instances[0].InstanceId' --output text)
fi
[ -n "$INSTANCE_ID" ] && [ "$INSTANCE_ID" != "None" ] || { echo "No running trustforge-demo instance" >&2; exit 1; }

echo "Scheduler target: $INSTANCE_ID, every 30 minutes (UTC), SSM RunCommand"
[ "$APPLY" = "--apply" ] || { echo "Dry run only. Re-run with --apply to create/update AWS resources."; exit 0; }

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
QUEUE_URL=$(aws sqs create-queue --region "$REGION" --queue-name "$QUEUE" --query QueueUrl --output text)
QUEUE_ARN=$(aws sqs get-queue-attributes --region "$REGION" --queue-url "$QUEUE_URL" --attribute-names QueueArn --query 'Attributes.QueueArn' --output text)

if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"scheduler.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
fi
aws iam put-role-policy --role-name "$ROLE" --policy-name trustforge-scheduler-ssm --policy-document \
  "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"ssm:SendCommand\",\"Resource\":[\"arn:aws:ssm:$REGION:$ACCOUNT:document/AWS-RunShellScript\",\"arn:aws:ec2:$REGION:$ACCOUNT:instance/$INSTANCE_ID\"]},{\"Effect\":\"Allow\",\"Action\":\"sqs:SendMessage\",\"Resource\":\"$QUEUE_ARN\"}]}" >/dev/null

INPUT=$(printf '{"DocumentName":"AWS-RunShellScript","InstanceIds":["%s"],"Parameters":{"commands":["systemctl start hermes-cycle.service"]}}' "$INSTANCE_ID")
TARGET="{\"Arn\":\"arn:aws:scheduler:::aws-sdk:ssm:sendCommand\",\"RoleArn\":\"arn:aws:iam::$ACCOUNT:role/$ROLE\",\"Input\":$(printf '%s' "$INPUT" | jq -Rs .),\"DeadLetterConfig\":{\"Arn\":\"$QUEUE_ARN\"}}"
if aws scheduler get-schedule --region "$REGION" --name "$NAME" >/dev/null 2>&1; then
  aws scheduler update-schedule --region "$REGION" --name "$NAME" \
  --schedule-expression 'cron(0/30 * * * ? *)' --schedule-expression-timezone UTC \
  --flexible-time-window Mode=OFF --state ENABLED \
  --target "$TARGET" >/dev/null
else
  aws scheduler create-schedule --region "$REGION" --name "$NAME" \
    --schedule-expression 'cron(0/30 * * * ? *)' --schedule-expression-timezone UTC \
    --flexible-time-window Mode=OFF --state ENABLED --target "$TARGET" >/dev/null
fi

echo "Created $NAME. Disable the EC2 hermes-cycle.timer only after the first scheduled SSM run is observed."
