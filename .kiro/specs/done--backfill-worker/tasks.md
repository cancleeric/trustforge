# Tasks

## Task 1：BackfillWorker 核心模組
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

## Task 2：Daemon 整合
- 修改 `scripts/run_analysis_flow.py`
- 新增 `--backfill` flag
- daemon 模式下同時啟動 BackfillWorker daemon thread
- backfill worker 獨立於 analysis flow，不共用 queue

## Task 3：CLI 入口
- 修改 `src/trustforge/cli.py`
- 新增 `backfill` 子命令群（start/status/stop/reset-failed/plan）
- `start`：seed tasks + 啟動前台或 daemon 執行
- `status`：查詢進度
- `stop`：寫入 state file 關閉
- `plan`：乾跑顯示預計天數

## Task 4：Web API
- 修改 `src/trustforge/web.py`
- `GET /api/backfill-status`：回傳 `status()` 結果
- `POST /api/admin/backfill-control`：需 admin token，body `{"action": "start"|"stop"}`

## Task 5：測試
- 檔案：`tests/test_backfill.py`
- 測試案例：
  - 三層啟停控制優先序
  - SQLite 進度持久化（seed → partial run → restart → continue）
  - 斷點續跑（completed 不重跑）
  - 日期邊界（start > end、無資料日）
  - batch_size 控制
  - reset_failed 重設邏輯
