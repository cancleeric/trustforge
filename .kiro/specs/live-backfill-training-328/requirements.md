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

## 四、成本與時間

| 方案 | 呼叫次數 | 成本 | 時間 |
|------|---------|------|------|
| 全量 8980 天 | 8980 | ~$27 | 62hr |
| 抽樣 200 天/幣 | 1000 | ~$3 | 7hr |
| 抽樣 + 4 worker | 1000 | ~$3 | 2hr |

建議：抽樣 200 + 多 worker 並行。
