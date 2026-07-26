# SageMaker 訓練後端擴充——開發計劃

> 日期：2026-07-26
> 前置文件：`docs/plans/PLAN-sagemaker-training-backend-feasibility.md`
> 狀態：待 CEO 審查後執行
> 估計總工時：10–14 小時（4 個 Phase，可拆成 4–6 個 PR）

---

## 目標

在現有 ModelHub 訓練後端旁，新增 AWS SageMaker 作為同等級訓練後端。
由環境變數選擇走哪個後端（或未來做 failover）。不改動推論路徑。

---

## Phase 0：前置確認（0.5h）

| Task | 內容 | 驗收條件 |
|------|------|----------|
| T0.1 | 確認 AWS 帳號有 SageMaker 權限 | `aws sagemaker list-training-jobs` 不回 403 |
| T0.2 | 確認 S3 bucket 存在或可建立（放訓練資料 + artifact） | bucket policy 允許 SageMaker role 讀寫 |
| T0.3 | 確認 SageMaker execution role 存在 | IAM role 有 `AmazonSageMakerFullAccess` 或等效最小權限 |

**blocker**：T0.1–T0.3 任一未過，後續 Phase 全 blocked。

---

## Phase 1：SageMaker Client 實作（3–4h）

> 目標：一個跟 `modelhub_client.py` 同等介面的 SageMaker training client

| Task | 內容 | 依賴 | 驗收條件 |
|------|------|------|----------|
| T1.1 | 新增 `src/trustforge/sagemaker_client.py` | T0 | 模組存在、可 import |
| T1.2 | 實作 `SageMakerTrainingClient` class | T1.1 | 通過 isinstance(client, TrainingBackend) |
| T1.3 | 實作 `upload_training_data(coin, rows) → s3_uri` | T1.2 | 上傳 JSONL 到 S3，回傳 URI |
| T1.4 | 實作 `trigger_training_job(coin, s3_uri, config) → job_name` | T1.2 | 呼叫 `create_training_job`，回傳 job name |
| T1.5 | 實作 `poll_training_status(job_name, max_wait, interval) → result` | T1.2 | 輪詢至 terminal 狀態，有 timeout |
| T1.6 | 實作 `get_artifact_path(job_name) → s3_model_uri` | T1.2 | 從 describe 回 ModelArtifacts S3 路徑 |
| T1.7 | 實作 `download_artifact(s3_uri, local_path)` | T1.2 | 下載 model.tar.gz 並解壓取 model.json |
| T1.8 | 離線模式支援（`offline=True` 時不呼叫 AWS） | T1.2 | offline 回傳佔位結果，不 raise |
| T1.9 | 單元測試（mock boto3） | T1.2–T1.8 | ≥85% coverage、zero network |

### 介面設計

```python
class SageMakerTrainingClient:
    """AWS SageMaker 訓練後端——與 ModelHubClient 同等級。

    職責：上傳訓練資料 → 觸發 Training Job → 輪詢完成 → 取回 artifact。
    不做推論、不做 endpoint 部署。
    """

    def __init__(
        self,
        bucket: str | None = None,         # S3 bucket，env SAGEMAKER_TRAINING_BUCKET
        role_arn: str | None = None,        # Execution role，env SAGEMAKER_ROLE_ARN
        region: str | None = None,          # env AWS_REGION
        offline: bool = False,
        timeout: float = 300.0,             # poll timeout（秒）
    ): ...

    def upload_training_data(self, coin: str, rows: list[dict]) -> str: ...
    def trigger_training_job(self, coin: str, data_uri: str, *, config: dict | None = None) -> str: ...
    def poll_training_status(self, job_name: str, *, max_wait: float = 300.0, interval: float = 5.0) -> dict: ...
    def get_artifact_path(self, job_name: str) -> str: ...
    def download_artifact(self, s3_uri: str, local_path: Path) -> Path: ...
```

### 設計決定

- boto3 延遲匯入（同 bedrock.py 模式）
- loopback 限制不適用（SageMaker 是 AWS API，非 localhost）
- timeout/retry 策略：`total_max_attempts=2`，connect 10s / read 30s
- API key 不存在（走 IAM role），但 bucket/role_arn 禁止寫入 commit

---

## Phase 2：Training Script（容器內執行的訓練腳本）（2–3h）

> 目標：SageMaker Training Job 容器內實際跑的 Python 腳本

| Task | 內容 | 依賴 | 驗收條件 |
|------|------|------|----------|
| T2.1 | 新增 `scripts/sagemaker_train_calibrator.py` | Phase 1 | 可獨立執行（`python scripts/sagemaker_train_calibrator.py`） |
| T2.2 | 讀取 SageMaker 標準 input channel（`/opt/ml/input/data/training/`） | T2.1 | 正確解析 JSONL |
| T2.3 | 執行 isotonic regression（複用 `calibration_model.train_isotonic()`） | T2.1 | 產出映射表 |
| T2.4 | 輸出 artifact 到 SageMaker 標準 output（`/opt/ml/model/`） | T2.1 | `model.json` 格式與 `save_calibration_model()` 相容 |
| T2.5 | 錯誤處理：資料不足 / 格式錯誤 → 寫 failure 到 `/opt/ml/output/failure` | T2.1 | Job 回報 Failed + 原因 |
| T2.6 | 本地模擬測試（mock `/opt/ml/` 路徑） | T2.1–T2.5 | 無 AWS 依賴可跑 |

### Container 策略

- 使用 SageMaker 內建 `sklearn` container 或自帶輕量 Dockerfile
- 推薦：用 AWS 提供的 `framework: sklearn, version: 1.2-1` 預置容器
- 訓練腳本只用 stdlib + 複用 `calibration_model.py`（零第三方依賴原則）
- 不使用 sklearn library（PAV 已有純 Python 實作），只借用 container runtime

---

## Phase 3：Orchestration 整合（3–4h）

> 目標：將 SageMaker client 接入現有訓練編排流程

| Task | 內容 | 依賴 | 驗收條件 |
|------|------|------|----------|
| T3.1 | 定義 `TrainingBackend` Protocol（抽象層） | — | `modelhub_client` 和 `sagemaker_client` 都能通過 isinstance |
| T3.2 | 新增 `src/trustforge/training_backend.py`（Protocol + resolver） | T3.1 | env `TRAINING_BACKEND=modelhub|sagemaker` 選擇後端 |
| T3.3 | 擴充 `modelhub_training.py` model_route_gate_status | T3.2 | 新增 `"sagemaker-training"` candidate route |
| T3.4 | 新增 `src/trustforge/sagemaker_submit.py`（比照 modelhub_submit.py） | Phase 1, T3.2 | gate → upload → trigger → poll → download → proposal |
| T3.5 | CLI 擴充：`trustforge sagemaker-train --all [--dry-run]` | T3.4 | 五幣 dry-run 成功 |
| T3.6 | 整合 `ExecutionLog` 時間預算 | T3.4 | 訓練不超過 15 分鐘 budget |
| T3.7 | artifact checksum 驗證 | T3.4 | 下載後 SHA256 比對 |
| T3.8 | 整合測試 | T3.1–T3.7 | mock SageMaker API，五幣 e2e |

### 環境變數設計

```bash
# 選擇訓練後端
TRAINING_BACKEND=sagemaker          # "modelhub" (default) | "sagemaker"

# SageMaker 專用
SAGEMAKER_TRAINING_BUCKET=trustforge-training-data
SAGEMAKER_ROLE_ARN=arn:aws:iam::123456789:role/TrustForgeSageMakerRole
SAGEMAKER_INSTANCE_TYPE=ml.m5.large  # 預設
SAGEMAKER_USE_SPOT=true              # 省錢
AWS_REGION=ap-southeast-2            # 沿用 Bedrock 同 region
```

---

## Phase 4：治理與文件（1–2h）

| Task | 內容 | 依賴 | 驗收條件 |
|------|------|------|----------|
| T4.1 | 更新 `docs/architecture/ARCHITECTURE.md` ModelHub 段落 | Phase 3 | 包含 SageMaker 並行說明 |
| T4.2 | 新增 handoff 文件 | Phase 3 | `docs/handoff/2026-07-xx-sagemaker-training-backend.md` |
| T4.3 | 確保 `automatic_apply: false` + `requires_human_approval: true` | Phase 3 | 與 ModelHub 一致的安全治理 |
| T4.4 | pre-push gate 通過 | Phase 3 | tests + lint + build + diff-check |

---

## 依賴圖

```
Phase 0（AWS 權限確認）
    │
    ▼
Phase 1（SageMaker Client）──→ Phase 2（Training Script）
    │                                    │
    ▼                                    ▼
Phase 3（Orchestration 整合）◄───────────┘
    │
    ▼
Phase 4（治理與文件）
```

Phase 1 和 Phase 2 可平行開發（client 和 training script 互不依賴）。
Phase 3 需要 Phase 1 + Phase 2 都完成。

---

## PR 拆分建議

| PR | 內容 | Size |
|----|------|------|
| PR-1 | `training_backend.py` Protocol 定義 + `sagemaker_client.py` + 單元測試 | M |
| PR-2 | `scripts/sagemaker_train_calibrator.py` + 本地模擬測試 | S |
| PR-3 | `sagemaker_submit.py` + CLI 擴充 + 整合測試 | L |
| PR-4 | 文件更新 + handoff | S |

---

## 風險與緩解

| 風險 | 機率 | 影響 | 緩解 |
|------|------|------|------|
| AWS 帳號無 SageMaker 權限 | 中 | Phase 0 blocker | 先確認，不假設有 |
| Training Job 啟動延遲 > 5 分鐘 | 低 | poll timeout | 訓練是離線批次，不佔 pipeline budget |
| S3 bucket 跨 region | 低 | 延遲/成本 | 與 Bedrock 同 region (ap-southeast-2) |
| artifact 格式不相容 | 極低 | 載入失敗 | 訓練腳本直接用 `save_calibration_model()` |
| 競賽評審對 SageMaker 的看法 | — | — | 訓練不是 LLM，不違規；展示 AWS 整合深度 |

---

## 驗收標準（Done Definition）

1. `TRAINING_BACKEND=sagemaker` + 五幣 dry-run 成功
2. 與 ModelHub 後端產出的 artifact 格式一致（`load_calibration_model()` 可讀）
3. `automatic_apply: false` + `requires_human_approval: true` 維持不變
4. 所有新模組 coverage ≥ 85%
5. pre-push gate 全綠
6. handoff 文件完成
7. 不引入任何新第三方依賴（僅 boto3 sagemaker client）

---

## 不做的事（明確排除）

- 不建立 SageMaker Endpoint（推論端點）
- 不取代 ModelHub（兩者並行）
- 不改動 Bedrock LLM 推論路徑
- 不改動校準模型本地推論邏輯
- 不做 automatic activation
- 不做 DB/migration
- 不做部署（deployment 獨立流程）
