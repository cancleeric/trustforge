#!/usr/bin/env bash
# Verify or enable DynamoDB point-in-time recovery for the cost ledger.
set -euo pipefail

REGION="${REGION:-ap-southeast-2}"
TABLE="${TRUSTFORGE_COST_LEDGER_TABLE:-trustforge-cost-ledger}"
MODE="${1:---verify}"
case "$MODE" in
  --verify|--enable) ;;
  *) echo "usage: $0 [--verify|--enable]" >&2; exit 2 ;;
esac

if [[ "$MODE" == "--enable" ]]; then
  aws dynamodb update-continuous-backups --region "$REGION" --table-name "$TABLE" \
    --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true >/dev/null
fi

status="$(aws dynamodb describe-continuous-backups --region "$REGION" --table-name "$TABLE" \
  --query 'ContinuousBackupsDescription.PointInTimeRecoveryDescription.PointInTimeRecoveryStatus' \
  --output text)"
if [[ "$status" != "ENABLED" ]]; then
  echo "cost ledger PITR is not enabled (status=${status:-UNKNOWN})" >&2
  exit 1
fi
echo "cost ledger PITR enabled: table=$TABLE region=$REGION"
