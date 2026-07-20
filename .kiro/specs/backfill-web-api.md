# Spec：Backfill Web API

> Issue: #293
> Depends on: #291 (PR #292)
> Branch: `feat/issue-293-backfill-web-api`

## 概述

為歷史回填系統新增 Web API，讓 admin panel 可以查看進度與控制啟停。

---

## 一、需求（Requirements）

### R1: GET /api/backfill-status（唯讀）
- 回傳 `BackfillWorker.status()` 結果
- 不需 admin token
- 回傳 JSON schema 同 CLI `backfill status --json`

### R2: POST /api/admin/backfill-control（寫入）
- 需 admin token（`X-Admin-Token` header）
- Body: `{"action": "start"|"stop"}`
- `start` → `set_backfill_enabled(True, reason="web_admin", actor="admin")`
- `stop` → `set_backfill_enabled(False, reason="web_admin", actor="admin")`
- 無 token → 401

---

## 二、設計（Design）

在 `web.py` 的 `_handle_api_*` 路由體系中新增兩個 handler。
使用既有的 `_admin_auth_check` 機制驗證 admin token。

```python
# GET /api/backfill-status
def _handle_api_backfill_status() -> tuple[int, str]:
    from .backfill import BackfillWorker
    worker = BackfillWorker()
    status = worker.status()
    worker.close()
    return 200, json.dumps({"ok": True, "data": status}, ensure_ascii=False)

# POST /api/admin/backfill-control
def _handle_api_admin_backfill_control(body: dict) -> tuple[int, str]:
    from .backfill import set_backfill_enabled, backfill_enabled
    action = body.get("action")
    if action == "start":
        set_backfill_enabled(True, reason="web_admin", actor="admin")
    elif action == "stop":
        set_backfill_enabled(False, reason="web_admin", actor="admin")
    else:
        return 400, error_json("invalid action")
    ctrl = backfill_enabled()
    return 200, json.dumps({"ok": True, "data": {"enabled": ctrl.enabled, "source": ctrl.source}})
```

---

## 三、實作任務（Tasks）

### Task 1: 在 web.py 加入路由
- GET handler: `/api/backfill-status`
- POST handler: `/api/admin/backfill-control`（驗 admin token）

### Task 2: 測試
- test_backfill.py 追加 API handler 的 unit test（或整合測試）

---

## 四、成功指標

- [ ] `curl /api/backfill-status` 回傳正確 JSON
- [ ] `curl -X POST /api/admin/backfill-control -H "X-Admin-Token: ..." -d '{"action":"start"}'` 啟用
- [ ] 無 token → 401
- [ ] 既有測試不回歸
