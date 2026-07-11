#!/usr/bin/env bash
# ============================================================================
# put_dedup_alarm.sh  (#104)
#
# 用途：
#   一次性建立 CloudWatch Alarm，監控 `web._record_dedup_prep_failure` 送出的
#   自定義指標 `DedupFailOpenRecentFailures`（滑動視窗內 dedup 準備失敗次數）。
#   指標超過門檻（預設 5，與 `_DEDUP_PREP_FAILURE_ALERT_THRESHOLD` 一致）即
#   觸發 ALARM，讓重複計費/去重失效（#51+#87 fail-open）可被即時看見，不依賴
#   log 解析。
#
# 安全邊界：
#   - 本腳本只建 Alarm，不動應用程式、不讀任何 token。
#   - 純讀取環境變數，不寫死帳號/region（region 預設 us-east-1，可用 REGION 覆寫）。
#   - 僅依賴 aws cli（cloudwatch put-metric-alarm），不引入額外依賴。
#
# 前置（應用端 + 告警鏈）：
#   - web 進程需設定 `TRUSTFORGE_CW_METRICS=1`（或任意 truthy）才會真的送出
#     指標（見 src/trustforge/cloudwatch_metrics.py 的 opt-in 邏輯）。demo 部署
#     清單強制開啟（見 deploy/README.md「#104 dedup fail-open 告警」）。
#   - 本腳本**建議**設 `TRUSTFORGE_DEDUP_ALARM_SNS=<arn:aws:sns:...>`：有 SNS
#     時 Alarm 觸發會真的發通知；未設則 Alarm 仍會建立（純狀態可視、可在
#     CloudWatch 控制台看到），但**不發任何通知**——絕不會再把非法的 Logs ARN
#     塞進 --alarm-actions 導致建表失敗（#104）。
#
# 執行範例：
#   REGION=ap-southeast-2 TRUSTFORGE_CW_NAMESPACE=TrustForge \
#     ./deploy/put_dedup_alarm.sh
# ============================================================================
set -euo pipefail

REGION="${REGION:-us-east-1}"
NAMESPACE="${TRUSTFORGE_CW_NAMESPACE:-TrustForge}"
METRIC="${TRUSTFORGE_CW_METRIC:-DedupFailOpenRecentFailures}"
ALARM_NAME="${TRUSTFORGE_DEDUP_ALARM_NAME:-trustforge-dedup-fail-open}"
THRESHOLD="${TRUSTFORGE_DEDUP_ALARM_THRESHOLD:-5}"
PERIOD="${TRUSTFORGE_DEDUP_ALARM_PERIOD:-300}"
EVAL_PERIODS="${TRUSTFORGE_DEDUP_ALARM_EVAL_PERIODS:-1}"
SNS_TOPIC="${TRUSTFORGE_DEDUP_ALARM_SNS:-}"

if ! command -v aws >/dev/null 2>&1; then
  echo "錯誤：找不到 aws cli，請先安裝並設定憑證。" >&2
  exit 1
fi

echo "[dedup-alarm] 建立 CloudWatch Alarm：${ALARM_NAME}"
echo "  Namespace=${NAMESPACE} Metric=${METRIC} Threshold=${THRESHOLD} Period=${PERIOD}s EvalPeriods=${EVAL_PERIODS}"

if [[ -n "$SNS_TOPIC" ]]; then
  # 先驗證 SNS topic ARN 合法——避免再次把錯的 action（如 Logs ARN）塞進
  # --alarm-actions，那會讓 `set -e` 下的 aws cli 非零結束、Alarm 整個建不出來
  # （#104 codex 必修 1 的根因）。
  if [[ "$SNS_TOPIC" != arn:aws:sns:* ]]; then
    echo "錯誤：TRUSTFORGE_DEDUP_ALARM_SNS 不是合法的 SNS topic ARN（應為 arn:aws:sns:*），中止。" >&2
    exit 1
  fi
  ALARM_ACTIONS=(--alarm-actions "$SNS_TOPIC")
  echo "  AlarmActions=SNS:${SNS_TOPIC}"
else
  # 無 SNS：建「無 action、純狀態可視」的 alarm（**不**帶 --alarm-actions）。
  # 絕不把 Logs ARN 當 alarm action（那是非法 action，會讓 set -e 退出、Alarm
  # 建不出來）。狀態仍可在 CloudWatch 控制台檢視；建議設
  # TRUSTFORGE_DEDUP_ALARM_SNS 以收到實際通知（見 deploy/README.md「#104
  # dedup fail-open 告警」章節）。
  ALARM_ACTIONS=()
  echo "  AlarmActions=無（純狀態可視；Alarm 狀態可在 CloudWatch 控制台檢視；"
  echo "    建議設 TRUSTFORGE_DEDUP_ALARM_SNS 以收到通知）"
fi

if [[ ${#ALARM_ACTIONS[@]} -gt 0 ]]; then
  aws cloudwatch put-metric-alarm \
    --region "$REGION" \
    --alarm-name "$ALARM_NAME" \
    --alarm-description "TrustForge dedup fail-open 頻率過高（#51+#87 防重複計費可能失效）" \
    --namespace "$NAMESPACE" \
    --metric-name "$METRIC" \
    --dimensions "Name=Service,Value=trustforge" \
    --statistic "Maximum" \
    --period "$PERIOD" \
    --evaluation-periods "$EVAL_PERIODS" \
    --threshold "$THRESHOLD" \
    --comparison-operator "GreaterThanOrEqualToThreshold" \
    --treat-missing-data "notBreaching" \
    "${ALARM_ACTIONS[@]}"
else
  aws cloudwatch put-metric-alarm \
    --region "$REGION" \
    --alarm-name "$ALARM_NAME" \
    --alarm-description "TrustForge dedup fail-open 頻率過高（#51+#87 防重複計費可能失效）" \
    --namespace "$NAMESPACE" \
    --metric-name "$METRIC" \
    --dimensions "Name=Service,Value=trustforge" \
    --statistic "Maximum" \
    --period "$PERIOD" \
    --evaluation-periods "$EVAL_PERIODS" \
    --threshold "$THRESHOLD" \
    --comparison-operator "GreaterThanOrEqualToThreshold" \
    --treat-missing-data "notBreaching"
fi

echo "完成：Alarm ${ALARM_NAME} 已建立/更新。指標超過 ${THRESHOLD} 即觸發" \
  "（若未設 SNS，則僅狀態可視、不發通知）。"
