# P0 Human / AWS Console Audit Checklist (#1206)

Issue #1206 contains compliance items that cannot be closed by source-code inspection alone. This checklist is tracked so the final reviewer can attach evidence before submission.

## Scope

- Competition general rule #2: no prohibited data in AWS-hosted datasets.
- Competition general rule #5: only necessary compute resources remain running.
- Bedrock rule #3: Bedrock model access is periodically reviewed and unused models are revoked.

## Checklist

- [ ] **13 類禁止資料**：抽查 S3、DynamoDB、`demo/hoyabit_data/` 匯入資料與 `data/` 實際資料，確認未上傳 PII、受管制資料、財務資訊、種族／政治／宗教／工會／基因／生物識別／性取向／健康／付款處理資料、惡意程式碼等禁止內容。
- [ ] **EC2**：AWS Console EC2 Dashboard 確認 running instances 僅保留必要競賽服務，閒置或舊 drill instance 已停止或終止。
- [ ] **SageMaker**：SageMaker Console 確認沒有閒置 training job / notebook / endpoint 持續消耗資源；必要資源用途已記錄。
- [ ] **Bedrock model access**：Bedrock Model access 只保留本次實際使用模型；不用的模型存取權已撤銷。
- [ ] **人工確認紀錄**：完成時間、確認者、Console 截圖或文字結論已貼回 GitHub issue #1206。

## Evidence template for issue comment

```markdown
## #1206 human console audit

- Checked at: <ISO timestamp + timezone>
- Reviewer: <name>
- Data discipline: pass/fail + evidence path
- EC2 resources: pass/fail + running instance count and reason
- SageMaker resources: pass/fail + active resource count and reason
- Bedrock model access: pass/fail + retained model list
- Follow-ups: <none or issue links>
```
