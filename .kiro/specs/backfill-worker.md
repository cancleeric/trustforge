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

## 二、設計（Design）

### 架構決策

```
┌─────────────────────────────────────────────────────────────┐
│  run_analysis_flow.py --daemon --backfill                    │
│  ┌─────────────────────┐  ┌──────────────────────────────┐ │
│  │ Hermes Analysis Flow │  │ BackfillWorker (daemon thread)│ │
│  │ (正常即時分析)         │  │ (歷史回填，獨立 SQLite)       │ │
│  └─────────────────────┘  └──────────────────────────────┘ │
│        ↕ shared nothing         ↕                           │
│  trustforge.sqlite3          trustforge-backfill.sqlite3    │
└─────────────────────────────────────────────────────────────┘
            │                          │
            ▼                          ▼
    forward-captured snapshots   backfilled_archive snapshots
    (source_snapshot_history_key)  (source_snapshot_backfill_key)
```

### 資料流

```
  data/{COIN}_daily_ohlcv.csv
          │
          ▼  load_ohlcv()
  [全部 Bar 列表]
          │
          ▼  截取到目標日期 → 最近 90 天窗口
  [eligible_bars[-90:]]
          │
          ▼  _build_day_snapshot()
  { coin, snapshot_epoch, archive_type="backfilled_archive",
    sources: [{source: "ohlcv-official", documents: [...]}] }
          │
          ▼  replay_snapshot() (offline, 不用 Bedrock)
  { report, evidence, execution_log }
          │
          ▼  記錄進度 → backfill_tasks.state = "completed"
  SQLite backfill DB
```

### 模組結構

| 檔案 | 職責 |
|------|------|
| `src/trustforge/backfill.py` | BackfillWorker 核心 + 啟停控制 + 進度 DB |
| `scripts/run_analysis_flow.py` | 新增 `--backfill` flag，啟動 BackfillWorker daemon thread |
| `src/trustforge/cli.py` | 新增 `backfill` 子命令 |
| `src/trustforge/web.py` | 新增 `/api/backfill-status` + `/api/admin/backfill-control` |
| `tests/test_backfill.py` | 單元測試 |

### BackfillControl 設計

```python
@dataclass(frozen=True)
class BackfillControl:
    enabled: bool
    source: str        # "env" | "config" | "state_file" | "default"
    reason: str = ""

def backfill_enabled() -> BackfillControl:
    # Layer 1: env TRUSTFORGE_BACKFILL_ENABLED
    # Layer 2: admin_config.backfill_enabled
    # Layer 3: state file
    # Default: False (needs explicit activation)
```

### BackfillProgress 設計

```python
@dataclass
class BackfillProgress:
    coin: str
    start_date: str
    end_date: str
    last_completed_date: str | None
    total_days: int
    completed_days: int
    failed_days: int
    skipped_days: int
    state: str  # idle / running / paused / completed
```

### API Response Schema

```json
{
  "enabled": true,
  "source": "state_file",
  "is_running": true,
  "coins": ["BTC", "ETH", "SOL", "BNB", "XRP"],
  "date_range": {"start": "2021-07-01", "end": "2026-07-01"},
  "total_days": 9125,
  "total_completed": 1500,
  "total_remaining": 7625,
  "progress_pct": 16.4,
  "per_coin": { "BTC": {...}, "ETH": {...} }
}
```

---

## 三、實作任務（Tasks）

### Task 1：BackfillWorker 核心模組
- 檔案：`src/trustforge/backfill.py`
- 實作 `backfill_enabled()` 三層控制
- 實作 `set_backfill_enabled()` state file 寫入
- 實作 `BackfillWorker` class：
  - `__init__`：參數解析 + SQLite schema 初始化
  - `plan()`：計算預計天數
  - `seed_tasks()`：將待回填日期寫入 DB
  - `run_batch()`：執行一個 batch
  - `_process_day()`：單日處理
  - `_build_day_snapshot()`：OHLCV 截取 → snapshot 建構
  - `start_daemon()` / `stop_daemon()`：daemon thread 管理
  - `progress()` / `status()`：狀態查詢
  - `reset_failed()`：重設失敗項目
  - `close()`：清理資源

### Task 2：Daemon 整合
- 修改 `scripts/run_analysis_flow.py`
- 新增 `--backfill` flag
- daemon 模式下同時啟動 BackfillWorker daemon thread
- backfill worker 獨立於 analysis flow，不共用 queue

### Task 3：CLI 入口
- 修改 `src/trustforge/cli.py`
- 新增 `backfill` 子命令群（start/status/stop/reset-failed/plan）
- `start`：seed tasks + 啟動前台或 daemon 執行
- `status`：查詢進度
- `stop`：寫入 state file 關閉
- `plan`：乾跑顯示預計天數

### Task 4：Web API
- 修改 `src/trustforge/web.py`
- `GET /api/backfill-status`：回傳 `status()` 結果
- `POST /api/admin/backfill-control`：需 admin token，body `{"action": "start"|"stop"}`

### Task 5：測試
- 檔案：`tests/test_backfill.py`
- 測試案例：
  - 三層啟停控制優先序
  - SQLite 進度持久化（seed → partial run → restart → continue）
  - 斷點續跑（completed 不重跑）
  - 日期邊界（start > end、無資料日）
  - batch_size 控制
  - reset_failed 重設邏輯

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

- [ ] `python -m trustforge.cli backfill plan` 顯示正確天數
- [ ] `python -m trustforge.cli backfill start --coin BTC` 可啟動並產出 snapshot
- [ ] daemon 模式下 backfill 與 analysis flow 並行不衝突
- [ ] kill → restart 後從斷點繼續
- [ ] `GET /api/backfill-status` 回傳正確進度
- [ ] 5 幣完跑後 `available_snapshot_count` ≥ 100
- [ ] 測試全過
