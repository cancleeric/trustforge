#!/usr/bin/env bash
# TrustForge → AWS Lambda + Function URL 部署（純 AWS CLI，冪等：建立或更新）。
# 由 pre-push hook 於測試綠後呼叫（CD），也可手動執行。
#
# 一次性前置（🧑 你做，見 deploy/README.md）：
#   1. 安裝 aws cli 並 `aws configure`（輸入你建的 IAM access key）
#   2. 建 Lambda 執行角色，匯出其 ARN 到環境變數 ROLE_ARN
#
# 可調環境變數：
#   FUNCTION_NAME(預設 trustforge-demo) REGION(預設 ap-southeast-2)
#   ROLE_ARN(必填) MEMORY(512) TIMEOUT(120)
set -euo pipefail
cd "$(dirname "$0")/.."

FUNCTION_NAME="${FUNCTION_NAME:-trustforge-demo}"
REGION="${REGION:-ap-southeast-2}"
MEMORY="${MEMORY:-512}"
TIMEOUT="${TIMEOUT:-120}"          # 秒；互動 demo 單次請求遠低於此
RUNTIME="python3.12"
HANDLER="trustforge.lambda_handler.handler"

command -v aws >/dev/null || { echo "✗ 找不到 aws cli，請先安裝（見 deploy/README.md）"; exit 2; }
: "${ROLE_ARN:?✗ 請先 export ROLE_ARN=<Lambda 執行角色 ARN>（一次性，見 deploy/README.md）}"

echo "[deploy] 打包 zip…"
BUILD="$(mktemp -d)"; ZIP="$(pwd)/build/trustforge_lambda.zip"; mkdir -p build
cp -r src/trustforge "$BUILD/trustforge"
cp -r data "$BUILD/data"
cp -r demo "$BUILD/demo"
GIT_VER=$(git describe --tags --always --dirty 2>/dev/null || echo dev)
printf 'VERSION = "%s"\n' "$GIT_VER" > "$BUILD/trustforge/_version.py"
echo "[lambda] 版號 = $GIT_VER"
( cd "$BUILD" && zip -qr "$ZIP" trustforge data demo -x '*/__pycache__/*' )
rm -rf "$BUILD"
echo "[deploy] zip = $ZIP ($(du -h "$ZIP" | cut -f1))"

ENVVARS="Variables={TRUSTFORGE_HOME=/var/task}"
# 要真實 Bedrock / live 模式時，在呼叫前 export 以下變數再執行本腳本：
#   BEDROCK_MODEL_ID=au.anthropic.claude-sonnet-4-6（或 apac.* inference profile）
#   TRUSTFORGE_LIVE_TOKEN=<自選秘密字串>（live 請求須帶相符的 ?token= 參數）
#   AWS_REGION=ap-southeast-2
# 並確保 Lambda 執行角色只有以下最小 bedrock 權限（不需 InvokeModelWithResponseStream）：
# CISO hardening R2（#2b）：region 收斂——bedrock.py 預設 stance_model_id 用
# `au.` 跨區 inference profile（只能從 ap-southeast-2/4/6 呼叫，AWS 會在這 3
# 個 region 之間路由底層 foundation-model 呼叫），foundation-model Resource
# 明確列舉這 3 個白名單 region，不留 region 萬用字元 `*`；inference-profile
# 用實際部署的 <REGION>/<ACCOUNT_ID>（單次部署固定打單一 region）。
#   {
#     "Effect": "Allow",
#     "Action": "bedrock:InvokeModel",
#     "Resource": [
#       "arn:aws:bedrock:ap-southeast-2::foundation-model/anthropic.*",
#       "arn:aws:bedrock:ap-southeast-4::foundation-model/anthropic.*",
#       "arn:aws:bedrock:ap-southeast-6::foundation-model/anthropic.*",
#       "arn:aws:bedrock:<REGION>:<ACCOUNT_ID>:inference-profile/*anthropic*"
#     ]
#   }

if aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "[deploy] 更新既有函數程式碼…"
  aws lambda update-function-code --function-name "$FUNCTION_NAME" \
    --zip-file "fileb://$ZIP" --region "$REGION" >/dev/null
  aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$REGION"
  aws lambda update-function-configuration --function-name "$FUNCTION_NAME" \
    --memory-size "$MEMORY" --timeout "$TIMEOUT" --environment "$ENVVARS" \
    --region "$REGION" >/dev/null
else
  echo "[deploy] 建立新函數…"
  aws lambda create-function --function-name "$FUNCTION_NAME" \
    --runtime "$RUNTIME" --handler "$HANDLER" --role "$ROLE_ARN" \
    --zip-file "fileb://$ZIP" --memory-size "$MEMORY" --timeout "$TIMEOUT" \
    --environment "$ENVVARS" --region "$REGION" >/dev/null
  aws lambda wait function-active --function-name "$FUNCTION_NAME" --region "$REGION"
fi

# Function URL（公開 HTTPS）+ 公開叫用權限
if ! aws lambda get-function-url-config --function-name "$FUNCTION_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "[deploy] 建立 Function URL（auth=NONE，公開 demo）…"
  aws lambda create-function-url-config --function-name "$FUNCTION_NAME" \
    --auth-type NONE --region "$REGION" >/dev/null
  aws lambda add-permission --function-name "$FUNCTION_NAME" \
    --statement-id FunctionURLAllowPublicAccess \
    --action lambda:InvokeFunctionUrl --principal '*' \
    --function-url-auth-type NONE --region "$REGION" >/dev/null || true
fi

URL="$(aws lambda get-function-url-config --function-name "$FUNCTION_NAME" --region "$REGION" --query FunctionUrl --output text)"
echo "[deploy] ✅ Live Demo URL: ${URL}"
echo "[deploy]   健康檢查: ${URL}healthz"
