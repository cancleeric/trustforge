# Design

## 涉及的檔案

| 檔案 | 操作 |
|------|------|
| `scripts/run_ceo_cycle.sh` | 重構主迴圈：加 status 輸出、artifact exclude、fallback dispatch |
| `scripts/ceo_sweep.py` | 增大候選池、加 merged PR ownership check |
| `scripts/ceo_lane_guard.py` | 輸出結構化 JSON |
| `scripts/ceo_watchdog.py` | **新增**：starvation detection + alert + dashboard 產出 |
| `tests/test_ceo_watchdog.py` | **新增**：watchdog 單元測試 |
| `tests/test_ceo_sweep_schedule.py` | 擴充 ownership check 與 fallback 測試 |

## 架構變更

```
run_ceo_cycle.sh
├── ceo_lane_guard.py → JSON output (R6)
├── ceo_sweep.py → larger candidate pool + ownership check (R5, R7)
├── dispatch loop → artifact exclude (R4) + fallback (R5)
├── post-dispatch → collect lane outcomes → write status.json (R1, R8)
└── ceo_watchdog.py → read recent statuses → alert/dashboard (R2, R3)
```

## R4 Artifact Exclude 實作

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

## R7 Merged PR Check 實作

在 `ceo_sweep.py` 的 `build_execution_queue` 前段：
1. 用 `gh pr list --state merged --limit 50 --json number,headRefName,mergedAt` 取最近合併的 PR
2. 解析 branch name 中的 issue number
3. 從候選中移除已有 merged branch 且對應 issue 仍 open 的項目（可能只是未關閉）

## R2 Starvation Watchdog 邏輯

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
