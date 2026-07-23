#!/usr/bin/env bash
# ============================================================================
# put_dedup_alarm.sh (#104)
#
# Creates/updates the two CloudWatch alarms required for dedup fail-open
# monitoring:
#   1. DedupFailOpenRecentFailures: app-emitted numeric gauge for the current
#      sliding-window failure count.
#   2. DedupFailOpenAlertLogCount: CloudWatch Logs metric filter for the fixed
#      "ALERT: TrustForge dedup" prefix, used as the first-notification path.
#
# Safety boundary:
# - Only creates CloudWatch Logs metric filters and CloudWatch alarms.
# - Does not modify application state and does not read tokens.
# - Reads configuration from environment variables; REGION defaults to
#   us-east-1 and can be overridden.
#
# Important:
# - Set TRUSTFORGE_CW_METRICS=1 in the web process or the numeric metric alarm
#   will never receive datapoints.
# - Set TRUSTFORGE_DEDUP_ALARM_SNS=arn:aws:sns:... to send notifications.
#   Without SNS the alarms are still created for status visibility, but no
#   --alarm-actions value is passed.
# ============================================================================
set -euo pipefail

REGION="${REGION:-us-east-1}"
NAMESPACE="${TRUSTFORGE_CW_NAMESPACE:-TrustForge}"

RECENT_FAILURES_METRIC="${TRUSTFORGE_CW_METRIC:-DedupFailOpenRecentFailures}"
RECENT_FAILURES_ALARM_NAME="${TRUSTFORGE_DEDUP_ALARM_NAME:-trustforge-dedup-fail-open}"
RECENT_FAILURES_THRESHOLD="${TRUSTFORGE_DEDUP_ALARM_THRESHOLD:-5}"

LOG_GROUP="${TRUSTFORGE_DEDUP_LOG_GROUP:-/aws/apprunner/trustforge/application}"
LOG_FILTER_NAME="${TRUSTFORGE_DEDUP_LOG_FILTER_NAME:-trustforge-dedup-alert-prefix}"
LOG_FILTER_PATTERN="${TRUSTFORGE_DEDUP_LOG_FILTER_PATTERN:-\"ALERT: TrustForge dedup\"}"
LOG_METRIC="${TRUSTFORGE_DEDUP_LOG_METRIC:-DedupFailOpenAlertLogCount}"
LOG_ALARM_NAME="${TRUSTFORGE_DEDUP_LOG_ALARM_NAME:-trustforge-dedup-alert-log}"
LOG_ALARM_THRESHOLD="${TRUSTFORGE_DEDUP_LOG_ALARM_THRESHOLD:-1}"

PERIOD="${TRUSTFORGE_DEDUP_ALARM_PERIOD:-300}"
EVAL_PERIODS="${TRUSTFORGE_DEDUP_ALARM_EVAL_PERIODS:-1}"
SNS_TOPIC="${TRUSTFORGE_DEDUP_ALARM_SNS:-}"

if ! command -v aws >/dev/null 2>&1; then
  echo "error: aws cli not found; install/configure aws before running this script." >&2
  exit 1
fi

ALARM_ACTIONS=()
if [[ -n "$SNS_TOPIC" ]]; then
  if [[ "$SNS_TOPIC" != arn:aws:sns:* ]]; then
    echo "error: TRUSTFORGE_DEDUP_ALARM_SNS is not a valid SNS topic ARN: $SNS_TOPIC" >&2
    exit 1
  fi
  ALARM_ACTIONS=(--alarm-actions "$SNS_TOPIC" --ok-actions "$SNS_TOPIC")
fi

echo "[dedup-alarm] region=$REGION namespace=$NAMESPACE"
if [[ -z "$SNS_TOPIC" ]]; then
  echo "[dedup-alarm] SNS not set; alarms will be status-visible only."
fi

aws logs put-metric-filter \
  --region "$REGION" \
  --log-group-name "$LOG_GROUP" \
  --filter-name "$LOG_FILTER_NAME" \
  --filter-pattern "$LOG_FILTER_PATTERN" \
  --metric-transformations \
    "metricName=$LOG_METRIC,metricNamespace=$NAMESPACE,metricValue=1,defaultValue=0"

aws cloudwatch put-metric-alarm \
  --region "$REGION" \
  --alarm-name "$RECENT_FAILURES_ALARM_NAME" \
  --alarm-description "TrustForge dedup fail-open recent_failures exceeded threshold (#104)." \
  --namespace "$NAMESPACE" \
  --metric-name "$RECENT_FAILURES_METRIC" \
  --dimensions "Name=Service,Value=trustforge" \
  --statistic "Maximum" \
  --period "$PERIOD" \
  --evaluation-periods "$EVAL_PERIODS" \
  --threshold "$RECENT_FAILURES_THRESHOLD" \
  --comparison-operator "GreaterThanOrEqualToThreshold" \
  --treat-missing-data "notBreaching" \
  ${ALARM_ACTIONS[@]+"${ALARM_ACTIONS[@]}"}

aws cloudwatch put-metric-alarm \
  --region "$REGION" \
  --alarm-name "$LOG_ALARM_NAME" \
  --alarm-description "TrustForge dedup ALERT log prefix observed (#104)." \
  --namespace "$NAMESPACE" \
  --metric-name "$LOG_METRIC" \
  --statistic "Sum" \
  --period "$PERIOD" \
  --evaluation-periods "$EVAL_PERIODS" \
  --threshold "$LOG_ALARM_THRESHOLD" \
  --comparison-operator "GreaterThanOrEqualToThreshold" \
  --treat-missing-data "notBreaching" \
  ${ALARM_ACTIONS[@]+"${ALARM_ACTIONS[@]}"}

echo "done: alarms updated: ${RECENT_FAILURES_ALARM_NAME}, ${LOG_ALARM_NAME}; log filter updated: ${LOG_FILTER_NAME}."
