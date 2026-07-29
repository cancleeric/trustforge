# 設計：Agent OS Admin Summary API

> Issue: #923 | Epic: #914

## 架構決策

### AD-1: 整合進既有 web.py

在 `src/trustforge/web.py` 新增 `/api/admin/agos/*` route handlers。
遵循既有 pattern（手動路由分發 + JSON response）。

### AD-2: Authorization Middleware

```python
def _check_admin_auth(headers: dict) -> bool:
    """Check Admin authorization via Bearer token."""
    token = os.getenv("TRUSTFORGE_ADMIN_TOKEN", "")
    if not token:
        return False  # fail-closed: no token configured = no access
    auth = headers.get("Authorization", "")
    return auth == f"Bearer {token}"
```

### AD-3: Response Envelope

```python
def _admin_response(data: dict, status_code: int = 200) -> tuple[int, dict]:
    return status_code, {
        "status": "ok",
        "data": data,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

def _admin_error(code: str, message: str, status_code: int = 400) -> tuple[int, dict]:
    return status_code, {
        "status": "error",
        "error": {"code": code, "message": message},
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
```

### AD-4: Pagination Helper

```python
def _paginate(items: list, page: int, page_size: int) -> dict:
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "items": items[start:end],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
```

### AD-5: Content Redaction

```python
def _redact_memory(entry: dict, show_content: bool = False) -> dict:
    if not show_content:
        entry["content_preview"] = "[REDACTED]"
        entry.pop("content_ref", None)
    else:
        # Truncate to 200 chars max
        if entry.get("content_preview") and len(entry["content_preview"]) > 200:
            entry["content_preview"] = entry["content_preview"][:200] + "..."
    return entry
```

### AD-6: Route Registration

```python
# In web.py route dispatch:
elif path.startswith("/api/admin/agos/"):
    if not _check_admin_auth(headers):
        return _admin_error("UNAUTHORIZED", "Admin token required", 401)
    if path == "/api/admin/agos/memories":
        return _handle_admin_memories(params)
    elif path == "/api/admin/agos/skills":
        return _handle_admin_skills(params)
    elif path == "/api/admin/agos/tools":
        return _handle_admin_tools(params)
    elif path == "/api/admin/agos/context":
        return _handle_admin_context(params)
    else:
        return _admin_error("NOT_FOUND", "Unknown endpoint", 404)
```

## 測試策略

`tests/test_admin_agos_api.py`：
- Authorization: no token → 401
- Authorization: wrong token → 401
- Authorization: correct token → 200
- Memories endpoint: pagination, kind filter, run_id filter
- Skills endpoint: pagination, family filter
- Tools endpoint: pagination, status filter
- Context endpoint: returns manifest for run_id
- Content redaction: default redacted, show_content=true shows preview
- Typed envelope format correct
- Error responses follow envelope format
- Empty results → empty items array, total=0
