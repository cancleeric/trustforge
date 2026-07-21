# Spec：五年歷史真實分析回填 + 訓練資料累積 (#328)

> Issue: #328
> Priority: P0-critical（競賽帳號 Bedrock 開放中，這兩天必須跑）

## 概述

擴充現有 BackfillWorker，新增 `mode=live` 用真 Bedrock 跑完整 pipeline，
累積有方向預測的訓練資料，觸發外框模組校準升級。

---

## 一、需求

### R1: BackfillWorker 擴充 mode=live
- `_process_day()` 支援 `BedrockClient(offline=False)`
- 每日 snapshot 用 OHLCV + cache 中可用的多源資料
- 抽樣策略：均勻取 200 天/幣（跨 5 年），非全量 8980 天

### R2: 訓練資料持久化
- 結果寫入 `trust_snapshot_history_key`（既有機制）
- 同時 append 到 `out/training-data/{coin}.jsonl`
- 每筆：`{date, coin, direction, trust_score, confidence, evidence_count, sources, model_id}`

### R3: Daemon snapshot 整合
- `run_analysis_flow.py` 每輪 `refresh_once()` 完畢後自動呼叫 snapshot 寫入
- 不再需要手動 `fetch_scheduler.py --snapshot`

### R4: 模型 artifacts 匯出/匯入
- 升級後的參數寫入 `out/model-artifacts/`
- CLI: `trustforge.cli export-model` / `trustforge.cli import-model`
- 新部署環境可恢復

### R5: 環境可攜
- env file 機制：`.env.local` 或環境變數
- 產出可 git commit 或 S3 同步

---

## 二、設計

### 架構變更

```
BackfillWorker
  ├── mode=offline（現有，用 BedrockClient(offline=True)）
  └── mode=live（新增，用 BedrockClient(offline=False)）
        ├── _build_day_snapshot()（同現有）
        ├── replay_snapshot() 改用 run_agent_pipeline(client=BedrockClient(offline=False))
        ├── _persist_to_trust_history()（同現有）
        └── _persist_to_training_data()（新增，append JSONL）

run_analysis_flow.py daemon loop
  └── refresh_once() 後新增：_write_snapshots()
```

### CLI 擴充

```bash
# live 模式回填（抽樣 200 天/幣）
python -m trustforge.cli backfill start --mode live --sample 200 --coin BTC,ETH,SOL,BNB,XRP

# 匯出模型 artifacts
python -m trustforge.cli export-model --out out/model-artifacts/

# 匯入模型 artifacts（新環境）
python -m trustforge.cli import-model --from out/model-artifacts/
```

---

## 三、實作任務

### Task 1: BackfillWorker mode=live
### Task 2: training-data JSONL 持久化
### Task 3: daemon snapshot 自動寫入
### Task 4: model artifacts 匯出/匯入
### Task 5: 測試 + 驗證

---

## 四、成本與時間

| 方案 | 呼叫次數 | 成本 | 時間 |
|------|---------|------|------|
| 全量 8980 天 | 8980 | ~$27 | 62hr |
| 抽樣 200 天/幣 | 1000 | ~$3 | 7hr |
| 抽樣 + 4 worker | 1000 | ~$3 | 2hr |

建議：抽樣 200 + 多 worker 並行。
