# 歷史回填系統（Backfill Worker）

> 版本：v0.16.16+
> Issue: #291 | Spec: `.kiro/specs/backfill-worker.md`

## 概述

用 5 年官方 OHLCV 資料逐日產 point-in-time snapshot → 跑 replay，
快速累積校準資料（≥100 個），讓升級機制（`diagnose_hermes.py`）能產出改善提案。

**三個分析流程對照**：

| 流程 | 觸發 | Priority | 控制 |
|------|------|----------|------|
| 手動立即分析 | Web UI / CLI | 0（最高）| 永遠可用 |
| 自動排程分析 | Daemon refresh_once | 100 | `autonomy_enabled()` |
| 歷史回填 | CLI / Daemon --backfill | 獨立 | `backfill_enabled()` |

## 用法

### CLI

```bash
# 查看回填計畫（不執行）
python -m trustforge.cli backfill plan --coin BTC

# 啟動（跑一個 batch 後退出）
python -m trustforge.cli backfill start --coin BTC --start 2021-07-01 --end 2026-07-01

# 啟動（前台持續執行，Ctrl+C 停止）
python -m trustforge.cli backfill start --coin BTC,ETH,SOL,BNB,XRP --daemon

# 查看進度
python -m trustforge.cli backfill status --json

# 停止（寫入 state file）
python -m trustforge.cli backfill stop

# 重設失敗的任務
python -m trustforge.cli backfill reset-failed
```

### Daemon 模式（與 analysis flow 並行）

```bash
python scripts/run_analysis_flow.py --daemon --backfill \
  --backfill-batch-size 50 --backfill-interval 2
```

### Web API

```bash
# 查看進度
curl http://127.0.0.1:8799/api/backfill-status

# 啟動（需 admin token）
curl -X POST http://127.0.0.1:8799/api/admin/backfill-control \
  -H "X-Admin-Token: $TOKEN" -d '{"action":"start"}'

# 停止
curl -X POST http://127.0.0.1:8799/api/admin/backfill-control \
  -H "X-Admin-Token: $TOKEN" -d '{"action":"stop"}'
```

## 啟停控制（三層 fail-closed）

| 層級 | 來源 | 說明 |
|------|------|------|
| 1 | `TRUSTFORGE_BACKFILL_ENABLED` env | 最高優先 |
| 2 | admin config `backfill_enabled` | DynamoDB 動態設定 |
| 3 | state file `out/trustforge-backfill-control.json` | CLI 寫入 |
| default | — | **關閉**（需明確啟動）|

## 資料完整性

- 每個 snapshot 標記 `archive_type=backfilled_archive`
- 與 forward-captured snapshot 完全隔離（不同 key prefix）
- `retrieved_at` 記錄真實執行時間（不偽造）
- 進度持久化在 `out/trustforge-backfill.sqlite3`

## 參數

| 參數 | 預設 | 說明 |
|------|------|------|
| `--coin` | 全部 5 幣 | 逗號分隔 |
| `--start` | 2021-07-01 | YYYY-MM-DD |
| `--end` | 今天 | YYYY-MM-DD |
| `--batch-size` | 30 | 每輪處理天數 |
| `--interval` | 5.0 | 批次間隔秒數 |

## 校準目標

5 幣 × 5 年 ≈ 9000 天 snapshot。升級門檻：
- ≥100 個 → `diagnose_hermes.py` 開始產出校準提案
- ≥100 eligible predictions + hit_rate → confidence calibrator 提案
