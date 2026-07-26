# Tasks

## Phase 1：基礎結構化輸出（低風險，其他 phase 依賴）

### Task 1.1：`ceo_lane_guard.py` 輸出結構化 JSON
- 加 `--json` flag，輸出 `{"cpu_count", "load_1m", "threshold", "spare", "lane_capacity", "next_retry_hint"}`
- 保持向後相容：無 `--json` 時仍 print 數字
- 測試：`test_ceo_watchdog.py::test_lane_guard_json_output`

### Task 1.2：`run_ceo_cycle.sh` 收集 cycle status
- dispatch loop 結束後寫 `$LOG_DIR/$STAMP-status.json`
- 收集 selected/dispatched/skipped/blocked_lanes/blocked_reasons
- 區分 exit code：0 = process ok（即使 dispatched=0），非零 = process error

## Phase 2：Artifact exclude + Fallback dispatch

### Task 2.1：Lane dirty 判定加 exclude pattern
- 在 shell 中實作 `is_lane_dirty()` 函式
- 預設 exclude `.venv/`, `__pycache__/`, `.pytest_cache/`, `node_modules/`, `.DS_Store`, `*.pyc`
- 環境變數 `TRUSTFORGE_CEO_LANE_EXCLUDE` 可覆蓋

### Task 2.2：Fallback dispatch 邏輯
- `ceo_sweep.py` 候選池改為 `max_lanes * 2`
- `run_ceo_cycle.sh` 中，若 lane 被 occupied，嘗試下一個 candidate
- 維持 lane 數量上限不變

### Task 2.3：測試 artifact exclude 與 fallback
- `test_ceo_watchdog.py::test_artifact_exclude_patterns`
- `test_ceo_sweep_schedule.py::test_execution_queue_provides_overflow_candidates`

## Phase 3：Merged PR ownership check

### Task 3.1：`ceo_sweep.py` 加 merged PR 查詢
- `build_execution_queue` 中 call `gh pr list --state merged`
- 從候選移除已完成 issue，繼續選下一個
- 測試：`test_ceo_sweep_schedule.py::test_merged_pr_ownership_excludes_completed`

## Phase 4：Starvation watchdog + Dashboard

### Task 4.1：新增 `scripts/ceo_watchdog.py`
- `check_starvation()`：讀最近 N 輪 status，判定連續零 productive
- `generate_dashboard()`：產出 `dashboard.json`
- `alert()`：warning 層級寫 JSON + osascript 通知；critical 加 flag 檔案

### Task 4.2：`run_ceo_cycle.sh` 結尾呼叫 watchdog
- 在 dispatch 完成後 invoke `ceo_watchdog.py --status-dir "$LOG_DIR"`
- watchdog 非阻塞、失敗不影響主流程

### Task 4.3：Productive 判定
- 每個 lane dispatch 完後，檢查 worktree 的 `git log --oneline HEAD...origin/develop` 與 `gh pr list`
- 寫入 status.json 的 `lanes[].productive` 欄位

### Task 4.4：測試 starvation watchdog
- `test_ceo_watchdog.py::test_starvation_detection_levels`
- `test_ceo_watchdog.py::test_dashboard_generation`
- `test_ceo_watchdog.py::test_alert_file_creation`
