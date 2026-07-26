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
