# Design

## API Response Schema
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

## 前端組件
- `frontend/src/components/TrainingStatusCard.tsx`
- 掛載於 `HermesDashboard.tsx` 右側欄底部
- 使用 `endpoints.ts` 的 `getTrainingStatus` + `TrainingStatusData` type
