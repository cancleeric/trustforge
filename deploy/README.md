# 部署 — AWS CLI + pre-push CD（Lambda + Function URL）

> 不走 App Runner 自動化。流程：`git push` → pre-push hook 跑測試 → 綠 → AWS CLI 部署到 Lambda。
> Lambda 在免費方案內可用、每月 100 萬請求免費；App Runner 不在免費內故不採用。

## 一次性前置（🧑 你做，AI 不碰憑證/IAM）

1. **安裝 AWS CLI**
   ```bash
   brew install awscli   # 或：python3 -m pip install --user awscli
   aws --version
   ```
2. **設定憑證**（你在 IAM 建一個有部署權限的使用者，拿 access key 後）：
   ```bash
   aws configure   # 輸入 Access Key / Secret / region=ap-southeast-2
   ```
   建議該 IAM 使用者政策：`lambda:*`、`iam:PassRole`（傳執行角色）、`logs:*`。
3. **建 Lambda 執行角色**（一次）：
   ```bash
   aws iam create-role --role-name trustforge-lambda-exec \
     --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
   aws iam attach-role-policy --role-name trustforge-lambda-exec \
     --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
   # 之後要真實 Bedrock，再加 bedrock:InvokeModel 的 inline policy
   export ROLE_ARN=$(aws iam get-role --role-name trustforge-lambda-exec --query Role.Arn --output text)
   ```
4. **啟用 pre-push CD hook**：
   ```bash
   git config core.hooksPath .githooks
   ```

## 平時：push 即 CD

```bash
git push            # → pre-push 跑 pytest；綠 → deploy_lambda.sh 部署 → 印出 Live Demo URL
TRUSTFORGE_NO_CD=1 git push   # 只跑測試、不部署
```

## 手動部署 / 調參

```bash
ROLE_ARN=<arn> FUNCTION_NAME=trustforge-demo REGION=ap-southeast-2 \
  bash deploy/deploy_lambda.sh
```

## 點亮真實 Bedrock（離線示範→真模型）

1. 給執行角色加 `bedrock:InvokeModel`（+`InvokeModelWithResponseStream`）於選定模型。
2. 提交 Anthropic use case（Bedrock console 橫幅，一次性）。
3. 在 `deploy_lambda.sh` 的 `ENVVARS` 加 `BEDROCK_MODEL_ID=<apac.claude profile>` 與 `AWS_REGION`。
4. 之後請求帶 `?live=1` 走真實 Bedrock；預設仍離線示範。

## 注意
- 互動 demo 單次請求秒級，遠低於 Lambda 15 分鐘上限（上限只在「單請求跑滿全程」才咬）。
- 部署失敗不擋 push（pre-push 設計：測試硬閘、CD 盡力）。
- 成本：Lambda 免費額度通常 cover demo；Bedrock 按 token，用 credits。建議設 AWS Budgets 告警。
