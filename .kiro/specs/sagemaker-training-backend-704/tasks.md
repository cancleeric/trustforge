# Tasks：SageMaker 訓練後端擴充（#704）

## Phase 0：前置確認

- [ ] T0.1: 確認 AWS 帳號有 SageMaker 權限（`aws sagemaker list-training-jobs` 不回 403）
  - Dependencies: none
  - Acceptance: CLI 回傳成功或空列表
- [ ] T0.2: 確認或建立 S3 bucket（`SAGEMAKER_TRAINING_BUCKET`）
  - Dependencies: T0.1
  - Acceptance: `aws s3 ls s3://{bucket}` 成功
- [ ] T0.3: 確認 SageMaker execution role 存在且有正確權限
  - Dependencies: T0.1
  - Acceptance: `aws iam get-role --role-name <role>` 回傳含 SageMaker trust policy

## Phase 1：TrainingBackend Protocol + SageMaker Client

- [ ] T1.1: 建立 `src/trustforge/training_backend.py`——定義 `TrainingBackend` Protocol 和 `resolve_training_backend()` resolver
  - Dependencies: none
  - Acceptance: Protocol 可 isinstance 驗證；resolver 根據 env 回傳正確後端；unsupported 值 raise RuntimeError
- [ ] T1.2: 建立 `src/trustforge/sagemaker_client.py`——`SageMakerBackend` class 骨架（`__init__`、offline 模式、boto3 延遲匯入）
  - Dependencies: T1.1
  - Acceptance: `SageMakerBackend(offline=True)` 不 raise、不匯入 boto3
- [ ] T1.3: 實作 `SageMakerBackend.trigger_training()`——S3 upload + create_training_job
  - Dependencies: T1.2
  - Acceptance: mock 測試驗證 S3 put_object + create_training_job 被正確呼叫；offline 回佔位 job_name
- [ ] T1.4: 實作 `SageMakerBackend.poll_result()`——describe_training_job 輪詢至 terminal
  - Dependencies: T1.2
  - Acceptance: mock 測試驗證 Completed/Failed/timeout 三種路徑
- [ ] T1.5: 實作 `SageMakerBackend.download_artifact()`——S3 下載 model.tar.gz + 解壓 + 驗證 model.json
  - Dependencies: T1.2
  - Acceptance: mock 測試驗證下載、解壓、格式驗證；無效 artifact raise 錯誤
- [ ] T1.6: 建立 `tests/test_sagemaker_client.py`——完整單元測試
  - Dependencies: T1.3, T1.4, T1.5
  - Acceptance: coverage ≥ 85%；所有成功/失敗/offline 路徑覆蓋；零 AWS 呼叫

## Phase 2：Training Script

- [ ] T2.1: 建立 `scripts/sagemaker_train_calibrator.py`——SageMaker container entry point
  - Dependencies: none
  - Acceptance: 讀取 `/opt/ml/input/data/training/data.jsonl`，呼叫 `train_isotonic()`，產出 `/opt/ml/model/model.json`
- [ ] T2.2: 實作 failure 路徑——資料不足或格式錯寫入 `/opt/ml/output/failure` 並 exit(1)
  - Dependencies: T2.1
  - Acceptance: 空資料或壞 JSON 正確觸發 failure 寫入
- [ ] T2.3: 建立 `tests/test_sagemaker_train_script.py`——本地模擬 /opt/ml/ 路徑的整合測試
  - Dependencies: T2.1, T2.2
  - Acceptance: 用 tmp_path 模擬 SageMaker 目錄結構；成功產出可被 `load_calibration_model()` 讀取的 model.json

## Phase 3：Orchestration 整合

- [ ] T3.1: 建立 `src/trustforge/sagemaker_submit.py`——比照 modelhub_submit.py 的完整編排流程
  - Dependencies: T1.6, T2.3
  - Acceptance: gate → trigger → poll → download → checksum → ECE → proposal → log → current
- [ ] T3.2: 整合 ExecutionLog 時間預算——各階段檢查 `log.remaining()`
  - Dependencies: T3.1
  - Acceptance: 剩餘時間不足時回傳 timeout 狀態
- [ ] T3.3: 實作 artifact SHA256 驗證
  - Dependencies: T3.1
  - Acceptance: checksum 不符時回傳 error 狀態且不產 proposal
- [ ] T3.4: CLI 擴充——`trustforge sagemaker-train` subcommand（`--all`, `--coin`, `--dry-run`, `--training-dir`, `--out-dir`）
  - Dependencies: T3.1
  - Acceptance: `--dry-run` 模式五幣成功產出 execution log；無 AWS 呼叫
- [ ] T3.5: 包裝 `ModelHubBackend` adapter——將既有 `modelhub_client.py` 包裝成 `TrainingBackend` Protocol 實作
  - Dependencies: T1.1
  - Acceptance: isinstance(ModelHubBackend(), TrainingBackend) 為 True；既有 modelhub-train CLI 行為不變
- [ ] T3.6: 建立 `tests/test_sagemaker_submit.py`——mock 整合測試
  - Dependencies: T3.1, T3.2, T3.3, T3.4
  - Acceptance: 五幣 dry-run e2e 通過；覆蓋 blocked/timeout/error/no_improvement/candidate 全部狀態

## Phase 4：治理與文件

- [ ] T4.1: 更新 `docs/architecture/ARCHITECTURE.md`——新增 SageMaker 訓練後端段落
  - Dependencies: T3.6
  - Acceptance: 文件描述兩個同等級訓練後端 + 環境變數切換方式
- [ ] T4.2: 建立 `docs/handoff/2026-07-xx-sagemaker-training-backend.md`——交接文件
  - Dependencies: T3.6
  - Acceptance: 含已完成範圍、接手檢查清單、live 執行前置條件
- [ ] T4.3: 驗證治理約束——所有 proposal 帶 `automatic_apply: false` + `requires_human_approval: true`
  - Dependencies: T3.6
  - Acceptance: grep 新模組確認無 automatic activation 路徑
- [ ] T4.4: pre-push gate 全綠
  - Dependencies: T4.1, T4.2, T4.3
  - Acceptance: `.githooks/pre-push` 通過（tests + lint + build + diff-check）
