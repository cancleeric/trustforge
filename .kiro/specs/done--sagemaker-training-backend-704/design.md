# Design：SageMaker 訓練後端擴充（#704）

## 架構總覽

```
┌──────────────────────────────────────────────────────────┐
│              TrustForge Training Orchestration             │
│                                                           │
│  training_backend.py: TrainingBackend Protocol + resolver  │
└─────────────────────┬────────────────────┬────────────────┘
                      │                    │
           ┌──────────▼────────┐  ┌────────▼──────────────┐
           │  ModelHubBackend  │  │  SageMakerBackend     │
           │  (既有，包裝      │  │  (新增)                │
           │   modelhub_client)│  │                        │
           └───────────────────┘  └────────────────────────┘
                      │                    │
               ModelHub API          boto3 sagemaker +
               (loopback HTTP)       boto3 s3
                                           │
                                    ┌──────▼──────────────┐
                                    │  SageMaker Training  │
                                    │  Job Container       │
                                    │                      │
                                    │  /opt/ml/input/...   │
                                    │  → train_isotonic()  │
                                    │  → /opt/ml/model/    │
                                    └──────────────────────┘
```

推論路徑完全不動：
- LLM 推論 → Bedrock（`bedrock.py`）
- 校準推論 → 本地（`calibration_model.apply_calibration()`）

## 元件設計

### 1. TrainingBackend Protocol（`src/trustforge/training_backend.py`）

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class TrainingBackend(Protocol):
    """訓練後端抽象——ModelHub 和 SageMaker 的統一介面。"""

    backend_id: str

    def trigger_training(
        self, coin: str, rows: list[dict], *, config: dict | None = None
    ) -> str:
        """提交訓練。回傳 job identifier（req_no 或 job_name）。"""
        ...

    def poll_result(
        self, job_id: str, *, max_wait: float = 300.0, interval: float = 5.0
    ) -> dict:
        """輪詢至 terminal 狀態。回傳含 status + artifact info 的 dict。"""
        ...

    def download_artifact(self, job_id: str, local_path: "Path") -> "Path":
        """取回 model artifact 到本地路徑。回傳實際檔案 Path。"""
        ...
```

Resolver：

```python
def resolve_training_backend(*, offline: bool = False) -> TrainingBackend:
    """根據 TRAINING_BACKEND 環境變數選擇後端。"""
    backend = os.getenv("TRAINING_BACKEND", "modelhub").lower()
    if backend == "sagemaker":
        return SageMakerBackend(offline=offline)
    elif backend == "modelhub":
        return ModelHubBackend(offline=offline)
    else:
        raise RuntimeError(f"Unsupported TRAINING_BACKEND: {backend}")
```

### 2. SageMakerBackend（`src/trustforge/sagemaker_client.py`）

#### 初始化

| 參數 | 來源 | 預設 |
|------|------|------|
| `bucket` | env `SAGEMAKER_TRAINING_BUCKET` | 必填，無預設 |
| `role_arn` | env `SAGEMAKER_ROLE_ARN` | 必填，無預設 |
| `region` | env `AWS_REGION` | `ap-southeast-2` |
| `instance_type` | env `SAGEMAKER_INSTANCE_TYPE` | `ml.m5.large` |
| `use_spot` | env `SAGEMAKER_USE_SPOT` | `false` |
| `offline` | 呼叫端傳入 | `False` |

#### boto3 使用策略

- 延遲匯入（`import boto3` 在方法內），離線模式不需 AWS 憑證
- 兩個 client：`sagemaker`（create/describe job）+ `s3`（upload/download）
- Config：`connect_timeout=10s`, `read_timeout=30s`, `total_max_attempts=2`

#### S3 路徑慣例

```
s3://{bucket}/trustforge/training/{coin}/{timestamp}/input/data.jsonl
s3://{bucket}/trustforge/training/{coin}/{timestamp}/output/    ← SageMaker 自動寫入
```

#### Job 命名

```
trustforge-calibrator-{coin}-{YYYYMMDD-HHmmss}
```

### 3. Training Script（`scripts/sagemaker_train_calibrator.py`）

SageMaker container 內執行的進入點：

```python
#!/usr/bin/env python3
"""SageMaker Training Job entry point for TrustForge calibrator."""

import json
import sys
from pathlib import Path

# SageMaker 標準路徑
INPUT_DIR = Path("/opt/ml/input/data/training")
OUTPUT_DIR = Path("/opt/ml/model")
FAILURE_PATH = Path("/opt/ml/output/failure")

def main():
    # 1. 讀取訓練資料
    data_file = INPUT_DIR / "data.jsonl"
    rows = [json.loads(line) for line in data_file.read_text().splitlines() if line.strip()]

    # 2. 抽取 confidence + hit
    confidences = [r["calibrated_confidence"] for r in rows]
    hits = [bool(r["hit"]) for r in rows]

    # 3. 跑 PAV isotonic regression
    from trustforge.calibration_model import train_isotonic, save_calibration_model
    points = train_isotonic(confidences, hits)

    # 4. 存到 output
    if not points:
        FAILURE_PATH.write_text("Isotonic regression produced empty model")
        sys.exit(1)

    save_calibration_model(points, OUTPUT_DIR / "model.json", sample_count=len(rows))

if __name__ == "__main__":
    main()
```

#### Container 選擇

- 使用 AWS SKLearn 預置容器（`framework: sklearn, version: 1.2-1`）
- `source_dir` 指向含 `sagemaker_train_calibrator.py` + `src/trustforge/calibration_model.py` 的打包
- 或用自建輕量 Dockerfile（只需 Python 3.11 + 純 stdlib）

### 4. SageMaker Submit 編排（`src/trustforge/sagemaker_submit.py`）

比照 `modelhub_submit.py` 的流程：

```
load_flat_training_rows(path, coin=coin)
  → gate check (≥100 unique labelled outcomes)
  → chronological train/holdout split
  → backend.trigger_training(coin, train_rows)
  → backend.poll_result(job_id)
  → backend.download_artifact(job_id, local_path)
  → SHA256 驗證
  → weighted ECE 比對（baseline - candidate ≥ 0.02）
  → immutable proposal + execution log
  → per-coin current manifest（atomic）
```

### 5. CLI 擴充

在 `cli.py` 新增 subcommand：

```bash
trustforge sagemaker-train --all [--dry-run] [--training-dir DIR] [--out-dir DIR]
trustforge sagemaker-train --coin BTC [--dry-run]
```

## 資料流

```
data/training/BTC.jsonl
    │
    ▼ load_flat_training_rows()
    │
    ▼ gate (≥100 outcomes)
    │
    ▼ split (chronological 80/20)
    │
    ├─── TRAINING_BACKEND=modelhub ───→ ModelHub API (既有)
    │
    └─── TRAINING_BACKEND=sagemaker ──→ S3 upload
                                            │
                                            ▼ create_training_job()
                                            │
                                            ▼ poll describe_training_job()
                                            │
                                            ▼ download model.tar.gz from S3
                                            │
                                            ▼ unpack → model.json
    │
    ▼ SHA256 驗證
    │
    ▼ weighted ECE 比對
    │
    ▼ proposal / execution log / current manifest
```

## 錯誤處理

| 場景 | 處理 |
|------|------|
| S3 上傳失敗 | status=`unavailable`，不 retry POST |
| Job 建立失敗（IAM/quota） | status=`error`，記錄 failure reason |
| Job 執行中逾時 | status=`timeout`，不取消 Job（讓它自然結束或手動清理） |
| Artifact 不存在或格式錯 | status=`error`，不產 proposal |
| ECE 改善不足 | status=`no_improvement` |
| ExecutionLog budget 不足 | status=`timeout`，中止並記錄 |
| offline=True | 全路徑回佔位結果，零 AWS 呼叫 |

## 安全考量

- `SAGEMAKER_ROLE_ARN` 和 bucket name 只從環境變數讀取，禁止 commit
- S3 路徑不接受使用者可控輸入（只用 coin name enum + timestamp）
- 下載的 artifact 視為 untrusted：先 checksum 再 parse
- Training Job 在 SageMaker managed infra 內，有 VPC 隔離
- 不做 automatic_apply：artifact 必須經人工審查才能啟用

## 測試策略

| 層級 | 方式 |
|------|------|
| Unit | mock boto3 (sagemaker + s3 client)，覆蓋每個方法的成功/失敗路徑 |
| Integration | 本地模擬 `/opt/ml/` 路徑跑 training script |
| E2E dry-run | `--dry-run` 模式五幣全跑，驗證 execution log 產出 |
| Live（手動） | 需 AWS 權限，不在自動化 CI 範圍 |

目標覆蓋率：所有新模組 ≥ 85%

## 不做的事

- 不建立 SageMaker Endpoint（推論端點）
- 不做 SageMaker Model Registry（直接下載 artifact 到本地）
- 不改動 Bedrock LLM 推論路徑
- 不改動本地 `apply_calibration()` 推論邏輯
- 不做 automatic activation
- 不做 ModelHub 到 SageMaker 的遷移（兩者並行）
