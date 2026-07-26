# Design

## 架構決策

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

## 資料流

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

## 模組結構

| 檔案 | 職責 |
|------|------|
| `src/trustforge/backfill.py` | BackfillWorker 核心 + 啟停控制 + 進度 DB |
| `scripts/run_analysis_flow.py` | 新增 `--backfill` flag，啟動 BackfillWorker daemon thread |
| `src/trustforge/cli.py` | 新增 `backfill` 子命令 |
| `src/trustforge/web.py` | 新增 `/api/backfill-status` + `/api/admin/backfill-control` |
| `tests/test_backfill.py` | 單元測試 |

## BackfillControl 設計

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

## BackfillProgress 設計

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

## API Response Schema

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
