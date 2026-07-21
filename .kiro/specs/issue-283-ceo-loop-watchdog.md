# Spec：CEO Loop Starvation Watchdog（Issue #283）

## 概述

CEO hourly development loop 排程成功退出（exit 0）但連續零派工，無告警。需加 starvation watchdog、lane artifact exclude、fallback 轉派、結構化狀態輸出。

---

## 一、需求（Requirements）

### R1：結構化 cycle 狀態輸出
- 每輪 `run_ceo_cycle.sh` 結束時輸出 JSON 狀態至 `out/ceo-cycle/<stamp>-status.json`
- 欄位：`selected`, `dispatched`, `completed`, `failed`, `skipped`, `blocked_lanes`, `blocked_reasons`
- 區分 **cycle process success**（腳本本身正常完成）與 **development progress success**（實際產出 code_changed/commit_created/pr_created）

### R2：Starvation watchdog
- 連續 2 輪 `dispatched == 0` → 本機告警（寫 `out/ceo-cycle/starvation_alert.json` + macOS `osascript` 通知）
- 連續 3 輪 → critical（alert level 升級，寫 `starvation_critical` flag）
- watchdog 讀取最近 N 輪 status JSON 判定

### R3：狀態可觀測
- 產出可讀狀態檔 `out/ceo-cycle/dashboard.json`
- 包含：最後掃描時間、最後派工時間、最後 commit 時間、最後 PR 時間、當前阻塞原因列表

### R4：Lane artifact allowlist/exclude
- 判斷 lane worktree 是否「occupied」時，採明確 allowlist/exclude 清單
- 預設 exclude：`.venv/`, `__pycache__/`, `.pytest_cache/`, `node_modules/`, `.DS_Store`, `*.pyc`
- 使用 `git status --porcelain` 搭配 exclude pattern 過濾，不把 runtime artifact 當 dirty

### R5：Lane 轉派（fallback dispatch）
- lane 被占用時，嘗試轉派給其他可用 lane 或選下一個無相依 Issue
- `execution_queue` 產出超過 `max_lanes` 數量的候選（改為 `max_lanes * 2`），供轉派使用

### R6：Load guard 記錄
- `ceo_lane_guard.py` 輸出結構化 JSON（不只 print 數字）
- 包含：`cpu_count`, `load_1m`, `threshold`, `spare`, `lane_capacity`, `next_retry_hint`

### R7：Merged PR ownership check
- `build_execution_queue` 查詢已 merged PR，若 Issue 對應分支已 merged-to-develop → 跳過
- 被 ownership check 判定已完成時，繼續選下一個候選

### R8：Productive vs dispatched 區分
- 新增 `productive` 欄位定義：`code_changed` OR `commit_created` OR `pr_created`
- cycle status 中記錄每個 lane 的 productive 狀態
- starvation watchdog 以 `productive == 0` 為觸發條件（非 dispatched）

### R9：補測試
- 新增/擴充 `tests/test_ceo_sweep_schedule.py` 或獨立 `tests/test_ceo_watchdog.py`
- 覆蓋：starvation detection、artifact exclude、lane fallback、ownership check、load guard JSON

---

## 二、設計（Design）

### 涉及的檔案

| 檔案 | 操作 |
|------|------|
| `scripts/run_ceo_cycle.sh` | 重構主迴圈：加 status 輸出、artifact exclude、fallback dispatch |
| `scripts/ceo_sweep.py` | 增大候選池、加 merged PR ownership check |
| `scripts/ceo_lane_guard.py` | 輸出結構化 JSON |
| `scripts/ceo_watchdog.py` | **新增**：starvation detection + alert + dashboard 產出 |
| `tests/test_ceo_watchdog.py` | **新增**：watchdog 單元測試 |
| `tests/test_ceo_sweep_schedule.py` | 擴充 ownership check 與 fallback 測試 |

### 架構變更

```
run_ceo_cycle.sh
├── ceo_lane_guard.py → JSON output (R6)
├── ceo_sweep.py → larger candidate pool + ownership check (R5, R7)
├── dispatch loop → artifact exclude (R4) + fallback (R5)
├── post-dispatch → collect lane outcomes → write status.json (R1, R8)
└── ceo_watchdog.py → read recent statuses → alert/dashboard (R2, R3)
```

### R4 Artifact Exclude 實作

```python
# 在 run_ceo_cycle.sh 中，取代 git status --porcelain 裸用
EXCLUDE_PATTERNS=('.venv/' '__pycache__/' '.pytest_cache/' 'node_modules/' '.DS_Store' '*.pyc')

is_lane_dirty() {
  local lane_dir="$1"
  local dirty_files
  dirty_files=$(git -C "$lane_dir" status --porcelain | grep -v -E '(\.venv/|__pycache__|\.pytest_cache/|node_modules/|\.DS_Store|\.pyc$)')
  [[ -n "$dirty_files" ]]
}
```

### R7 Merged PR Check 實作

在 `ceo_sweep.py` 的 `build_execution_queue` 前段：
1. 用 `gh pr list --state merged --limit 50 --json number,headRefName,mergedAt` 取最近合併的 PR
2. 解析 branch name 中的 issue number
3. 從候選中移除已有 merged branch 且對應 issue 仍 open 的項目（可能只是未關閉）

### R2 Starvation Watchdog 邏輯

```python
# scripts/ceo_watchdog.py
def check_starvation(status_dir: Path, window: int = 3) -> dict:
    recent = sorted(status_dir.glob("*-status.json"))[-window:]
    productive_counts = [json.loads(f.read_text()).get("productive", 0) for f in recent]
    consecutive_zero = 0
    for count in reversed(productive_counts):
        if count == 0:
            consecutive_zero += 1
        else:
            break
    return {
        "consecutive_zero_productive": consecutive_zero,
        "alert_level": "critical" if consecutive_zero >= 3 else "warning" if consecutive_zero >= 2 else "ok",
    }
```

---

## 三、任務（Tasks）

### Phase 1：基礎結構化輸出（低風險，其他 phase 依賴）

#### Task 1.1：`ceo_lane_guard.py` 輸出結構化 JSON
- 加 `--json` flag，輸出 `{"cpu_count", "load_1m", "threshold", "spare", "lane_capacity", "next_retry_hint"}`
- 保持向後相容：無 `--json` 時仍 print 數字
- 測試：`test_ceo_watchdog.py::test_lane_guard_json_output`

#### Task 1.2：`run_ceo_cycle.sh` 收集 cycle status
- dispatch loop 結束後寫 `$LOG_DIR/$STAMP-status.json`
- 收集 selected/dispatched/skipped/blocked_lanes/blocked_reasons
- 區分 exit code：0 = process ok（即使 dispatched=0），非零 = process error

### Phase 2：Artifact exclude + Fallback dispatch

#### Task 2.1：Lane dirty 判定加 exclude pattern
- 在 shell 中實作 `is_lane_dirty()` 函式
- 預設 exclude `.venv/`, `__pycache__/`, `.pytest_cache/`, `node_modules/`, `.DS_Store`, `*.pyc`
- 環境變數 `TRUSTFORGE_CEO_LANE_EXCLUDE` 可覆蓋

#### Task 2.2：Fallback dispatch 邏輯
- `ceo_sweep.py` 候選池改為 `max_lanes * 2`
- `run_ceo_cycle.sh` 中，若 lane 被 occupied，嘗試下一個 candidate
- 維持 lane 數量上限不變

#### Task 2.3：測試 artifact exclude 與 fallback
- `test_ceo_watchdog.py::test_artifact_exclude_patterns`
- `test_ceo_sweep_schedule.py::test_execution_queue_provides_overflow_candidates`

### Phase 3：Merged PR ownership check

#### Task 3.1：`ceo_sweep.py` 加 merged PR 查詢
- `build_execution_queue` 中 call `gh pr list --state merged`
- 從候選移除已完成 issue，繼續選下一個
- 測試：`test_ceo_sweep_schedule.py::test_merged_pr_ownership_excludes_completed`

### Phase 4：Starvation watchdog + Dashboard

#### Task 4.1：新增 `scripts/ceo_watchdog.py`
- `check_starvation()`：讀最近 N 輪 status，判定連續零 productive
- `generate_dashboard()`：產出 `dashboard.json`
- `alert()`：warning 層級寫 JSON + osascript 通知；critical 加 flag 檔案

#### Task 4.2：`run_ceo_cycle.sh` 結尾呼叫 watchdog
- 在 dispatch 完成後 invoke `ceo_watchdog.py --status-dir "$LOG_DIR"`
- watchdog 非阻塞、失敗不影響主流程

#### Task 4.3：Productive 判定
- 每個 lane dispatch 完後，檢查 worktree 的 `git log --oneline HEAD...origin/develop` 與 `gh pr list`
- 寫入 status.json 的 `lanes[].productive` 欄位

#### Task 4.4：測試 starvation watchdog
- `test_ceo_watchdog.py::test_starvation_detection_levels`
- `test_ceo_watchdog.py::test_dashboard_generation`
- `test_ceo_watchdog.py::test_alert_file_creation`

---

## 四、風險

| 風險 | 影響 | 緩解 |
|------|------|------|
| `gh pr list --state merged` API rate limit | 排程頻率 1hr 遠低於限制 | 加 `--limit 50` + 快取 |
| artifact exclude pattern 漏掉某些環境 | lane 永遠 dirty → 零派工 | 環境變數覆蓋 + 日誌揭示被過濾檔案 |
| osascript 在 headless/SSH 環境失敗 | 通知靜默失敗 | watchdog 捕捉 exception、不影響主流程 |
| lane outcome 判定假陽性 | productive=true 但實際無意義 commit | 以 diff stat > 0 作為額外條件 |
| merged check 時 issue 未被 gh 關閉 | issue 仍 open 但 branch 已合併 | 只 skip 有 merged branch 的 issue，不假設狀態 |

---

## 五、驗收準則

1. `run_ceo_cycle.sh` 每次產出 `*-status.json`，欄位齊全
2. 手動測試：連續執行 3 次（mock 零 productive），dashboard 顯示 critical
3. `.venv` 等 artifact 不觸發 lane occupied
4. 候選池 > max_lanes 時，occupied lane 的 issue 被後補 candidate 取代
5. 已 merged PR 對應的 issue 不出現在 execution_queue
6. `pytest tests/test_ceo_watchdog.py tests/test_ceo_sweep_schedule.py` 全通過
7. pre-push hook 通過（不引入新 lint/type 錯誤）
