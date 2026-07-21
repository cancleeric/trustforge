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

## Design（設計）

### API Response Schema
```json
{
  "training_data": {
    "total_records": 344,
    "has_direction": 36,
    "direction_ratio": 0.10,
    "per_coin": {"BTC": {"total": 138, "has_direction": 20}, ...}
  },
  "backfill": {
    "mode": "live",
    "is_running": true,
    "completed": 29,
    "total": 1000,
    "progress_pct": 2.9
  },
  "upgrade_threshold": {
    "target": 100,
    "current": 36,
    "met": false,
    "pct": 36.0
  }
}
```

### 前端組件
- `frontend/src/components/TrainingStatusCard.tsx`
- 掛載於 `HermesDashboard.tsx` 右側欄底部
- 使用 `endpoints.ts` 的 `getTrainingStatus` + `TrainingStatusData` type

---

## Tasks（任務）

- [x] Task 1: `_handle_api_training_status()` handler + route
- [x] Task 2: `TrainingStatusCard.tsx` React 組件
- [x] Task 3: `endpoints.ts` + type
- [x] Task 4: `openapi.yaml` 同步
- [x] Task 5: HermesDashboard 整合

---

## 驗收
- [x] API 回傳正確（448 total, 57 has_direction）
- [x] 前端卡片顯示、自動刷新
- [x] openapi spec 覆蓋測試通過
