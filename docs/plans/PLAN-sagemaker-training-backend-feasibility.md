# SageMaker 訓練後端擴充——可行性分析

> 日期：2026-07-26
> 狀態：分析完成，待 CEO 審查
> 範圍：評估在現有 ModelHub 訓練後端旁，新增 AWS SageMaker 作為同等級訓練後端的可行性
> 不涉及：推論路徑變更、Bedrock LLM 路徑、AgentCore runtime

---

## 1. 現況盤點

### 1.1 TrustForge 模型使用全景

```
推論端（inference）                訓練端（training）
────────────────────              ────────────────────
Bedrock                           ModelHub（自主平台，有 GPU）
 • LLM 行文（報告撰寫）
 • Claim 抽取
 • Stance 分類

校準模型推論（本地）               ↓ 產出 artifact
 • load_calibration_model()       model.json（isotonic 映射表）
 • apply_calibration()            ↓ 下載回本地
 • 純 Python 線性插值              pipeline 進程內推論
 • 無遠端 endpoint 呼叫
```

關鍵事實：
- **推論**走 Bedrock（LLM）或本地純 Python（校準模型），不走 ModelHub/SageMaker
- **訓練**目前只走 ModelHub（自主平台，有 GPU，能跑 isotonic/logreg fit）
- 校準模型極輕量（JSON 映射表，幾十個點），不需要 GPU 推論

### 1.2 ModelHub 現有整合

| 模組 | 職責 |
|------|------|
| `modelhub_client.py` | Loopback HTTP client：`trigger_retrain()`、`poll_training_result()`、`get_model_path()`、`list_models()`、`health_check()` |
| `modelhub_training.py` | 訓練資料打包（flat JSONL）、gate 審查、route gating |
| `modelhub_submit.py` | Immutable proposal 發布、atomic current manifest、dirfd/fsync |
| `modelhub_readonly_probe.py` | 唯讀健康複驗 evaluator |
| `modelhub_probe_collector.py` | Probe → client 橋接 |
| `calibration_model.py` | 純 Python isotonic regression（PAV）、model JSON 讀寫 |
| CLI `modelhub-train` | 五幣 dry-run / live orchestration |

### 1.3 ModelHub 現況限制

- #503：唯讀複驗仍 BLOCKED（外部 API 合約 blocker）
- Live retrain/activation 尚未執行（需具名人工授權）
- `automatic_apply: false`，所有候選都是 human-review-only
- API key / tenant scope / 跨租戶隔離尚待 ModelHub owner 提供唯讀合約

---

## 2. 架構定位：ModelHub 與 SageMaker 的關係

```
                    ┌──────────────────────────────────┐
                    │     TrustForge Training Layer      │
                    │     （訓練後端抽象）                │
                    └─────────┬───────────┬─────────────┘
                              │           │
                   ┌──────────▼────┐  ┌───▼──────────────┐
                   │  ModelHub     │  │  SageMaker        │
                   │  （自主平台） │  │  （AWS 託管）     │
                   │  • 有 GPU     │  │  • 有 GPU         │
                   │  • 跑 fit     │  │  • 跑 fit         │
                   │  • 產 artifact│  │  • 產 artifact    │
                   │  • 版本管理   │  │  • 版本管理       │
                   └──────────────┘  └───────────────────┘
                              │           │
                              ▼           ▼
                        model artifact（JSON 映射表）
                              │
                              ▼
                    本地 apply_calibration() 推論
```

**兩者是同等級、可並行或互為備援的訓練平台。**

兩者都能：
- 接收訓練資料
- 跑 training job（isotonic / logistic regression / 未來更複雜模型）
- 產出 model artifact
- 做版本管理與 artifact registry

兩者都**不做**（在此 use case 下）：
- 推論 endpoint 服務（校準模型太小，本地推論即可）

---

## 3. 為什麼要擴充 SageMaker

| 動機 | 說明 |
|------|------|
| **備援** | ModelHub 外部 API 合約仍 BLOCKED（#503），SageMaker 全由我方控制，無外部 blocker |
| **AWS 生態整合** | 競賽加分（AWS 架構合理性 25%）；SageMaker 是 AWS 原生 ML 服務 |
| **彈性** | SageMaker Training Job 支援多種 instance type、spot instance、distributed training |
| **未來擴展** | 若模型變複雜（如 teacher/student、fine-tune），SageMaker 的 infra 更成熟 |
| **可審計** | SageMaker Experiments / Model Registry 提供原生 lineage tracking |

---

## 4. 技術可行性評估

### 4.1 可行——理由

| 面向 | 評估 |
|------|------|
| **依賴** | 只需 boto3（已在允許範圍），零第三方新依賴 |
| **架構準備度** | `modelhub_training.py` 已有「訓練資料打包 → gate → 提交 → poll → artifact」的完整流程；SageMaker 版只需對齊同一介面 |
| **artifact 格式** | 校準模型是 JSON 映射表，SageMaker output 也是 S3 上的 model.tar.gz，解壓後取 JSON 即可 |
| **競賽約束** | 訓練後端不是 LLM 入口，不觸犯「僅限 Bedrock」的硬約束 |
| **安全** | SageMaker Training Job 在 VPC 內執行，IAM role 控制存取，比 loopback HTTP 更有明確隔離 |

### 4.2 注意事項

| 面向 | 風險 | 緩解 |
|------|------|------|
| **成本** | SageMaker Training Job 按 instance-second 計費；但校準模型極小（<1000 rows），訓練秒級完成 | 用 `ml.m5.large` 或 spot instance |
| **cold start** | Training Job 啟動需 2-5 分鐘（container provision） | 接受；訓練是離線批次，不在 pipeline 即時路徑上 |
| **15 分鐘預算** | 如果納入 pipeline execution budget，需注意 SageMaker Job 啟動延遲 | 訓練獨立於 pipeline 執行；不佔 pipeline 15 分鐘預算 |
| **artifact 信任** | SageMaker 產出的 artifact 視為 untrusted（同 ModelHub），需驗 SHA256 | 沿用 `modelhub_submit.py` 既有的 checksum 驗證模式 |
| **human gate** | 必須維持 `automatic_apply: false` + `requires_human_approval: true` | 沿用既有治理流程 |

### 4.3 不可行的部分（明確排除）

- 不做 SageMaker Endpoint（推論端點）——校準模型本地推論即可
- 不取代 ModelHub——兩者並行，由環境變數或 gate 選擇
- 不改動 Bedrock LLM 推論路徑
- 不改動 AgentCore stub

---

## 5. 與現有程式碼的接合點

| 現有模組 | SageMaker 對應 | 變更方式 |
|----------|---------------|----------|
| `modelhub_client.py` trigger_retrain | `sagemaker.create_training_job()` | 新增 `sagemaker_client.py`，同介面 |
| `modelhub_client.py` poll_training_result | `sagemaker.describe_training_job()` polling | 同上 |
| `modelhub_client.py` get_model_path | S3 model artifact path | 同上 |
| `modelhub_training.py` build_flat_training_package | 不變（訓練資料格式通用） | 無變更 |
| `modelhub_training.py` model_route_gate_status | 新增 `"sagemaker-training"` route | 小幅擴充 |
| `modelhub_submit.py` | 新增 SageMaker 版 submit（S3 upload + job trigger） | 新檔案 |
| `calibration_model.py` | 不變（artifact 格式一致） | 無變更 |

---

## 6. 結論

**可行性：高（8/10）。**

- 架構已有明確的訓練後端抽象（data → gate → submit → poll → artifact）
- SageMaker 只是這個抽象的第二個實作，跟 ModelHub 同介面同治理
- 零新第三方依賴（只多用 boto3 的 sagemaker client）
- 不影響推論路徑、不影響 LLM 路徑、不觸犯競賽約束
- 主要風險是 cold start 延遲，但訓練是離線批次，可接受

**唯一前置：確認 AWS 帳號有 SageMaker 權限（IAM role / service-linked role）。**
