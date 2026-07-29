# 實作任務：Agent OS Admin Summary API

> Issue: #923 | Epic: #914

## Task 1: 實作 Authorization 與 Response Helpers

- [x] Superseded: do not add `_check_admin_auth`; outer `web.py`
  `_admin_auth_check()` is the single `X-Admin-Token` gate
- [x] 實作 `_admin_response(data, status_code=200) -> tuple`
- [x] 實作 `_admin_error(code, message, status_code) -> tuple`
- [x] 實作 `_paginate(items, page, page_size) -> dict`
- [x] 實作 `_redact_memory(entry, show_content=False) -> dict`

## Task 2: 實作 Memories Endpoint

- [x] `GET /api/admin/agos/memories`
- [x] Query params: `run_id`, `kind`, `page`, `page_size`, `show_content`
- [x] 從 AgosLineageQuery 取得 memory data
- [x] 含 lineage info（rank, reason, evidence_eligible）
- [x] Content 預設 redacted

## Task 3: 實作 Skills Endpoint

- [x] `GET /api/admin/agos/skills`
- [x] Query params: `run_id`, `family`, `page`, `page_size`
- [x] 含 revision_hash, risk_class, lifecycle, dependencies, frozen_at

## Task 4: 實作 Tools Endpoint

- [x] `GET /api/admin/agos/tools`
- [x] Query params: `run_id`, `status`, `page`, `page_size`
- [x] 含 side_effect_class, evidence_class, approval, hashes

## Task 5: 實作 Context Endpoint

- [x] `GET /api/admin/agos/context`
- [x] Query params: `run_id`
- [x] 回傳完整 manifest: included/excluded refs, token budget, exclusion reasons

## Task 6: Route Registration

- [x] 在 web.py 路由分發中新增 `/api/admin/agos/*` 路徑
- [x] Authorization check before handler dispatch
- [x] Unknown sub-path → 404

## Task 7: 測試

- [x] 建立 `tests/test_admin_agos_api.py`
- [x] 測試 authorization: no token → 401
- [x] 測試 authorization: wrong token → 401
- [x] 測試 authorization: correct → 200
- [x] 測試 memories endpoint pagination
- [x] 測試 memories endpoint kind filter
- [x] 測試 memories content redaction
- [x] 測試 skills endpoint pagination + family filter
- [x] 測試 tools endpoint pagination + status filter
- [x] 測試 context endpoint returns manifest
- [x] 測試 error envelope format
- [x] 測試 empty results
- [x] 確認 pre-push 通過

### HEAD evidence

Implemented by `src/trustforge/agos_admin_api.py` and the existing outer
`web.py` admin-auth path. `tests/test_agos_admin_api.py` and
`tests/test_agos_http_e2e.py` cover dispatcher contracts plus real-handler 401
and authenticated 200 behavior.
