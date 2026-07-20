# Spec：成本與預算治理狀態暴露

> Issue: #279
> Branch: `feat/issue-279-budget-governance-api`

## 概述

將 budget_guard 的預算治理狀態暴露到觀測層 API，讓前端 admin panel 與監控可以
即時看到：cap 是多少、來源為何、今日已花費、是否已超限、online-stance 是否啟用。

目前 `/api/costs` 只提供帳本明細（花了多少），但不知道「上限是多少」和「還剩多少」。

---

## 一、需求（Requirements）

### R1: GET /api/budget-governance
唯讀。回傳預算治理完整狀態：

```json
{
  "daily_cap_usd": 3.0,
  "daily_cap_source": "default",
  "spent_today_usd": 0.42,
  "remaining_today_usd": 2.58,
  "cap_exceeded": false,
  "online_stance_enabled": false,
  "bedrock_model_configured": false,
  "kill_switch_active": false,
  "governance_layers": {
    "config": null,
    "env": null,
    "default": 3.0
  }
}
```

### R2: 不需 admin token（唯讀觀測）

### R3: OpenAPI spec 同步更新

---

## 二、設計（Design）

新增 `_handle_api_budget_governance()` handler，呼叫既有：
- `daily_cap_usd_resolved()` → cap + source
- `daily_cap_exceeded()` → bool
- `online_stance_requested()` → bool
- `_env_cap()` → env 層值
- ledger 今日 spent

---

## 三、實作任務（Tasks）

### Task 1: handler + route
### Task 2: OpenAPI spec
### Task 3: 驗證測試通過
