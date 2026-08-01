# SageMaker 訓練後端擴充：交付紀錄

**日期**：2026-07-26｜**Issue**：#704｜**分支**：`feat/704-sagemaker-training-backend`

## 1. 一句話現況

SageMaker 訓練後端已實作完成，五幣 dry-run 通過。可安全使用 `TRAINING_BACKEND=sagemaker` 觸發
真實 Training Job。所有候選都是 human-review-only，行為與 ModelHub 路徑一致。

## 2. 已完成範圍

| 模組 | 檔案 | 說明 |
|------|------|------|
| Protocol | `training_backend.py` | TrainingBackend Protocol + resolver |
| SageMaker Client | `sagemaker_client.py` | trigger/poll/download，boto3 延遲匯入 |
| ModelHub Adapter | `modelhub_backend.py` | 包裝既有 client 為 TrainingBackend |
| Training Script | `scripts/sagemaker_train_calibrator.py` | Container entry point（PAV isotonic） |
| Orchestration | `sagemaker_submit.py` | gate→trigger→poll→download→validate→propose |
| CLI | `cli.py` | `sagemaker-train` subcommand |

## 3. AWS 資源

| 資源 | 值 |
|------|-----|
| S3 Bucket | `trustforge-training-ap-southeast-2` |
| IAM Role | `arn:aws:iam::<ACCOUNT_ID>:role/TrustForgeSageMakerTrainingRole` |
| Region | `ap-southeast-2` |
| Inline Policy | S3 讀寫 training bucket + CloudWatch Logs |

## 4. 測試結果

| 測試檔 | passed |
|--------|--------|
| `test_sagemaker_client.py` | 36 |
| `test_sagemaker_train_script.py` | 10 |
| `test_sagemaker_submit.py` | 13 |
| **合計** | **59** |

五幣 CLI dry-run 全部成功（BTC/ETH/SOL/BNB/XRP）。

## 5. 治理約束驗證

- `automatic_apply: false` — 所有 proposal 固定
- `requires_human_approval: true` — 所有回傳固定
- candidate 不等於 activation — 無自動啟用路徑
- artifact 視為 untrusted — SHA256 + load_calibration_model() 雙驗證

## 6. 不在範圍

- ⛔ 不建立 SageMaker Endpoint（不做遠端推論）
- ⛔ 不取代 ModelHub（兩者並行）
- ⛔ 不改動 Bedrock LLM 推論路徑
- ⛔ 不改動本地校準推論（`apply_calibration()`）
- ⛔ 不做 automatic activation
- ⛔ 不做 DB/migration/secret
- ⛔ 不做部署（deployment 獨立流程）

## 7. 使用方式

```bash
# Dry-run（不呼叫 AWS）
PYTHONPATH=src python3 -m trustforge.cli sagemaker-train --all --dry-run

# Live（需設定環境變數）
export TRAINING_BACKEND=sagemaker
export SAGEMAKER_TRAINING_BUCKET=trustforge-training-ap-southeast-2
export SAGEMAKER_ROLE_ARN=arn:aws:iam::<ACCOUNT_ID>:role/TrustForgeSageMakerTrainingRole
export AWS_REGION=ap-southeast-2
PYTHONPATH=src python3 -m trustforge.cli sagemaker-train --all
```

## 8. 接手檢查清單

- [ ] 確認 AWS credentials 可用（SageMaker + S3 權限）
- [ ] 重跑五幣 dry-run 驗證
- [ ] 決定是否執行 live Training Job
- [ ] live 完成後人工檢查 artifact + ECE 改善
- [ ] 人工決定是否啟用新 calibration model
- [ ] pre-push gate 全綠後才 merge
