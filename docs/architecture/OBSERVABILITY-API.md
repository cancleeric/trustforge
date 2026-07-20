# 觀測層 API 端點文件

> 版本：v0.16.16+
> Issues: #279, #278, #274

## 新增端點

### GET /api/budget-governance

預算治理狀態。唯讀，不需 admin token。

**回應範例：**
```json
{
  "ok": true,
  "data": {
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
}
```

### GET /api/improvement-diagnostics

最新改善診斷報告。唯讀。

**回應範例（無報告時）：**
```json
{
  "ok": true,
  "data": {
    "status": "no_diagnostic_available",
    "proposals": [],
    "message": "尚無診斷報告。"
  }
}
```

### GET /api/alerts-operations

告警狀態與操作流程 runbook。唯讀。

**回應範例：**
```json
{
  "ok": true,
  "data": {
    "alerts": {
      "dedup_fail_open": {
        "incident_active": false,
        "recent_failures": 0,
        "threshold": 5,
        "window_sec": 300.0,
        "cooldown_sec": 300.0
      },
      "budget_cap_exceeded": { "active": false }
    },
    "observability": {
      "cloudwatch_metrics_enabled": false,
      "log_alert_prefix": "ALERT: TrustForge"
    },
    "runbooks": {
      "dedup_fail_open": "deploy/put_dedup_alarm.sh",
      "budget_exceeded": "設 TRUSTFORGE_BEDROCK_DAILY_USD_CAP=0 緊急全關",
      "bedrock_offline": "確認 BEDROCK_MODEL_ID + AWS 憑證"
    }
  }
}
```

### GET /api/backfill-status

歷史回填進度。唯讀。

### POST /api/admin/backfill-control

啟停回填。需 admin token。Body: `{"action": "start"|"stop"}`
