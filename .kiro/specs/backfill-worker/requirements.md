# Spec：歷史回填系統（Backfill Worker）

> Issue: #291
> Branch: `feat/issue-291-backfill-worker`

## 概述

為 TrustForge 新增可控、可佈署的歷史回填系統，用 5 年官方 OHLCV 資料逐日產
point-in-time snapshot → 跑 replay，快速累積 ≥100 個校準資料點，讓升級機制
（`diagnose_hermes.py`）能產出有意義的改善提案。

---

## 一、需求（Requirements）

### R1：核心回填邏輯
- 讀取 `data/` 目錄的 5 幣種 5 年 Daily OHLCV
- 逐日截取「到當天為止」的價格窗口（最近 90 天），組成 point-in-time snapshot
- 搭配已實作的歷史來源（alternative-me-fng sentiment、SEC EDGAR、blockchain-com-charts）
- 每日 snapshot 跑一次 `replay_snapshot()`（offline，不用 Bedrock）
- 結果寫入 backfill snapshot key（既有 `source_snapshot_backfill_key` 機制）

### R2：啟停控制（三層 fail-closed）
- Layer 1：環境變數 `TRUSTFORGE_BACKFILL_ENABLED`（最高優先）
- Layer 2：admin config `backfill_enabled` 欄位（DynamoDB 動態設定）
- Layer 3：state file `out/trustforge-backfill-control.json`
- 預設：關閉（需明確啟動）。生產環境更嚴格 fail-closed
- 每個 batch 開始前檢查一次控制狀態

### R3：進度持久化與斷點續跑
- SQLite 資料庫 `out/trustforge-backfill.sqlite3`
- 表 `backfill_tasks`：每個 (coin, date) 對一筆記錄，state = pending/running/completed/failed/skipped
- 中斷後重啟：跳過 completed，從 pending 繼續
- `reset_failed` 指令可重設失敗項目為 pending

### R4：Daemon 整合
- 在 `run_analysis_flow.py --backfill` 下以獨立 daemon thread 執行
- 不阻擋正常 analysis flow（分開的 thread、分開的 SQLite）
- batch_size 控制一輪處理天數（預設 30）
- interval_sec 控制批次間隔（預設 5 秒）

### R5：CLI 入口
```
python -m trustforge.cli backfill start [--coin BTC,ETH] [--start 2021-01-01] [--end 2026-07-01] [--batch-size 30]
python -m trustforge.cli backfill status [--json]
python -m trustforge.cli backfill stop
python -m trustforge.cli backfill reset-failed
python -m trustforge.cli backfill plan  # 顯示預計天數（不執行）
```

### R6：Web API
- `GET /api/backfill-status`：回傳進度、啟停狀態、per-coin breakdown
- `POST /api/admin/backfill-control`：start/stop/pause（需 admin token）

### R7：資料完整性
- 每個 snapshot 標記 `archive_type=backfilled_archive`
- 與 forward-captured snapshot 完全隔離（不同 cache key prefix）
- `retrieved_at` 記錄真實執行時間（不偽造為歷史日期）
- OHLCV 來源標記 `provider=HOYA_BIT_official`

### R8：安全與資源
- 無網路呼叫（純本地 OHLCV + offline replay）
- CPU-bound，不觸發 Bedrock（$0 成本）
- 不寫入 DynamoDB（結果在本地 SQLite）
- batch_size 防止一次佔用過多記憶體

---

## 四、風險與限制

| 風險 | 影響 | 緩解 |
|------|------|------|
| OHLCV 只有 price 一個來源 | snapshot 資訊密度低 | 可搭配 FNG/SEC/blockchain 歷史（後續 task） |
| 5 幣 × 1800 天 = 9000 次 replay | CPU 時間 | batch_size 控制 + interval_sec 間隔 |
| SQLite 同時寫入衝突 | WAL 模式處理 | 與 analysis_flow.sqlite3 分開，無衝突 |
| 生產環境誤啟動 | 浪費 CPU | fail-closed 預設 + admin token 控制 |

---

## 五、成功指標

- [x] `python -m trustforge.cli backfill plan` 顯示正確天數
- [x] `python -m trustforge.cli backfill start --coin BTC` 可啟動並產出 snapshot
- [x] daemon 模式下 backfill 與 analysis flow 並行不衝突
- [x] kill → restart 後從斷點繼續
- [x] `GET /api/backfill-status` 回傳正確進度
- [x] 5 幣完跑後 `available_snapshot_count` ≥ 100
- [x] 測試全過
