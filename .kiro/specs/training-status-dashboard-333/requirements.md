# Spec：訓練資料累積狀態儀表板 (#333)

> Issue: #333
> Branch: `feat/issue-333-training-status-dashboard`
> PR: #334 (merged)

---

## Requirements（需求）

### R1: 後端 API
- `GET /api/training-status` 回傳：
  - training_data: total_records, has_direction, direction_ratio, per_coin
  - backfill: mode, is_running, completed, total, progress_pct
  - upgrade_threshold: target(100), current, met(bool), pct

### R2: 前端顯示
- HermesDashboard 右側欄新增「訓練資料」卡片
- 進度條：current/target (距 100 門檻)
- 數字統計：累積筆數、有方向比例
- 回填狀態（mode + running）
- 狀態燈：綠=達標、黃=進行中、紅=停止
- 每 30 秒自動刷新

### R3: 無 admin token（唯讀觀測）

---

## 驗收
- [x] API 回傳正確（448 total, 57 has_direction）
- [x] 前端卡片顯示、自動刷新
- [x] openapi spec 覆蓋測試通過
