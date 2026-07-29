# 實作任務：Agent OS Admin Summary API

> Issue: #923 | Epic: #914

## Task 1: 實作 Authorization 與 Response Helpers

- [ ] 在 web.py 新增 `_check_admin_auth(headers) -> bool`
- [ ] 實作 `_admin_response(data, status_code=200) -> tuple`
- [ ] 實作 `_admin_error(code, message, status_code) -> tuple`
- [ ] 實作 `_paginate(items, page, page_size) -> dict`
- [ ] 實作 `_redact_memory(entry, show_content=False) -> dict`

## Task 2: 實作 Memories Endpoint

- [ ] `GET /api/admin/agos/memories`
- [ ] Query params: `run_id`, `kind`, `page`, `page_size`, `show_content`
- [ ] 從 AgosLineageQuery 取得 memory data
- [ ] 含 lineage info（rank, reason, evidence_eligible）
- [ ] Content 預設 redacted

## Task 3: 實作 Skills Endpoint

- [ ] `GET /api/admin/agos/skills`
- [ ] Query params: `run_id`, `family`, `page`, `page_size`
- [ ] 含 revision_hash, risk_class, lifecycle, dependencies, frozen_at

## Task 4: 實作 Tools Endpoint

- [ ] `GET /api/admin/agos/tools`
- [ ] Query params: `run_id`, `status`, `page`, `page_size`
- [ ] 含 side_effect_class, evidence_class, approval, hashes

## Task 5: 實作 Context Endpoint

- [ ] `GET /api/admin/agos/context`
- [ ] Query params: `run_id`
- [ ] 回傳完整 manifest: included/excluded refs, token budget, exclusion reasons

## Task 6: Route Registration

- [ ] 在 web.py 路由分發中新增 `/api/admin/agos/*` 路徑
- [ ] Authorization check before handler dispatch
- [ ] Unknown sub-path → 404

## Task 7: 測試

- [ ] 建立 `tests/test_admin_agos_api.py`
- [ ] 測試 authorization: no token → 401
- [ ] 測試 authorization: wrong token → 401
- [ ] 測試 authorization: correct → 200
- [ ] 測試 memories endpoint pagination
- [ ] 測試 memories endpoint kind filter
- [ ] 測試 memories content redaction
- [ ] 測試 skills endpoint pagination + family filter
- [ ] 測試 tools endpoint pagination + status filter
- [ ] 測試 context endpoint returns manifest
- [ ] 測試 error envelope format
- [ ] 測試 empty results
- [ ] 確認 pre-push 通過
