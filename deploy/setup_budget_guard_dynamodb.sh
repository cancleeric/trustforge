#!/usr/bin/env bash
# ============================================================================
# setup_budget_guard_dynamodb.sh  (#75)
#
# 用途：
#   一次性（或部署期由 deploy_ec2.sh 呼叫）建立 #75 多實例 budget 預留所需的
#   DynamoDB 表 `trustforge-budget-guard`（可用 TRUSTFORGE_BUDGET_COUNTER_TABLE
#   覆寫），並把**最小權限** IAM policy `trustforge-budget-guard` 掛到
#   `trustforge-ec2` instance role 上——只包含對該表 ARN 的
#   `dynamodb:UpdateItem` / `dynamodb:GetItem`（**絕不** dynamodb:*，也不含
#   PutItem/DeleteItem/Scan 等），讓多實例預留的原子 conditional write 能跑、
#   又最小化 blast radius。
#
# 安全邊界：
#   - IAM policy Resource 鎖死該表 ARN，不放行其他表 / 其他 action。
#   - `--print-policy`：只印出 policy JSON（供測試 / 審查解析斷言，不實際
#     建表 / 掛 IAM、不需 AWS 憑證）。
#   - 若表不存在 / 建表失敗，脚本以非零結束（fail-closed），不讓「多實例保護
#     靜默失效」的部署被誤判成功。
#
# 前置：
#   - 需要在「部署主機」用有 DynamoDB/ IAM 權限的 aws cli 執行（與
#     deploy_ec2.sh 同一套憑證）。
#   - 角色 `trustforge-ec2` 必須已存在（deploy_ec2.sh 會先建）。
#
# 執行：
#   # 印 policy（審查 / 測試）
#   ./deploy/setup_budget_guard_dynamodb.sh --print-policy
#   # 實際建表 + 掛 IAM
#   REGION=ap-southeast-2 TRUSTFORGE_BUDGET_COUNTER_TABLE=trustforge-budget-guard \
#     ./deploy/setup_budget_guard_dynamodb.sh
# ============================================================================
set -euo pipefail

REGION="${REGION:-ap-southeast-2}"
TABLE="${TRUSTFORGE_BUDGET_COUNTER_TABLE:-trustforge-budget-guard}"
ROLE="${TRUSTFORGE_EC2_ROLE:-trustforge-ec2}"
# 帳號：print-policy 模式不需要真 AWS，用佔位帳號讓 ARN 可被解析斷言。
PRINT_POLICY_ONLY=0
for _a in "$@"; do
  case "$_a" in
    --print-policy) PRINT_POLICY_ONLY=1 ;;
  esac
done

ACCT=""
if [[ "$PRINT_POLICY_ONLY" -eq 0 ]]; then
  if ! ACCT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)"; then
    echo "錯誤：無法取得 AWS 帳號（aws sts get-caller-identity 失敗），中止。" >&2
    exit 1
  fi
else
  ACCT="123456789012"
fi

TABLE_ARN="arn:aws:dynamodb:${REGION}:${ACCT}:table/${TABLE}"

# 最小權限 policy：僅該表 ARN 的 UpdateItem / GetItem（原子 conditional write
# + 狀態查詢所需；不含 PutItem/DeleteItem/Scan，更不含 dynamodb:*）。
read -r -d '' POLICY <<JSON || true
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["dynamodb:UpdateItem","dynamodb:GetItem"],
   "Resource":"${TABLE_ARN}"}
]}
JSON

if [[ "$PRINT_POLICY_ONLY" -eq 1 ]]; then
  echo "$POLICY"
  exit 0
fi

echo "[budget-guard] 建 DynamoDB 表 ${TABLE}（region=${REGION}）…"
if ! aws dynamodb describe-table --region "$REGION" --table-name "$TABLE" >/dev/null 2>&1; then
  aws dynamodb create-table --region "$REGION" \
    --table-name "$TABLE" \
    --attribute-definitions \
      AttributeName=source_id,AttributeType=S \
      AttributeName=coin,AttributeType=S \
    --key-schema \
      AttributeName=source_id,KeyType=HASH \
      AttributeName=coin,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST \
    >/dev/null
  # 啟用 TTL（屬性名 ttl）→ 過期日期的 item 自動回收（見 budget_counter.py
  # _TTL_BUFFER_SECONDS）。
  aws dynamodb update-time-to-live --region "$REGION" \
    --table-name "$TABLE" \
    --time-to-live-specification Enabled=true,AttributeName=ttl \
    >/dev/null
  echo "[budget-guard] 表 ${TABLE} 已建立（PAY_PER_REQUEST + TTL on ttl）"
else
  echo "[budget-guard] 表 ${TABLE} 已存在，略過建表"
fi

echo "[budget-guard] 掛最小權限 IAM policy trustforge-budget-guard 到角色 ${ROLE}…"
aws iam put-role-policy --role-name "$ROLE" --policy-name trustforge-budget-guard \
  --policy-document "$POLICY" >/dev/null

echo "完成：budget guard 表 + IAM 就緒（多實例預留保護啟用）。"
echo "  表 ARN=${TABLE_ARN}"
echo "  IAM policy 動作：dynamodb:UpdateItem, dynamodb:GetItem（僅鎖該表 ARN）"
