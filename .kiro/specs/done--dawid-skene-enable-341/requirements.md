# Spec：打開 Dawid-Skene 動態信譽 (#341)

> Issue: #341
> Priority: P0-critical

---

## Requirements

### R1: 預設啟用
- `score()` 的 `dynamic_reputation` 參數預設改為 `True`
- `aggregate()` 呼叫 `score()` 時傳 `dynamic_reputation=True`

### R2: EM 收斂保障
- 設定 max_iterations 上限（現有 clamp）
- 如果 EM 不收斂（超時/發散）→ fallback 到靜態信譽
- 15 分鐘執行窗口內完成

### R3: 可觀測
- 動態信譽結果寫入 report 的 `reputation_trace`
- 不同來源信譽值有差異
