# Design

新增 `_handle_api_budget_governance()` handler，呼叫既有：
- `daily_cap_usd_resolved()` → cap + source
- `daily_cap_exceeded()` → bool
- `online_stance_requested()` → bool
- `_env_cap()` → env 層值
- ledger 今日 spent
