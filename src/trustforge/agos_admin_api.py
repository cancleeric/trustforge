"""Agent OS Admin Summary API — read-only endpoints for Agent OS lineage.

Provides authorization-gated, paginated, read-only endpoints for memory,
skill manifest, tool invocation, and context manifest queries.

Design principles:
  - Read-only: all endpoints are GET, no mutation
  - Authorization-gated: TRUSTFORGE_ADMIN_TOKEN as Bearer token
  - Content redaction: sensitive memory content redacted by default
  - Typed envelopes: consistent response format
  - Zero third-party dependencies

Issue: #923 | Epic: #914
"""
from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from .agos_runtime import AgosRuntime
from .context_builder import ContextManifest, manifest_summary


# ─── Authorization ───────────────────────────────────────────────────────────


def check_admin_auth(headers: dict[str, str]) -> bool:
    """Check Admin authorization via Bearer token.

    Fail-closed: no token configured = no access.
    """
    token = os.getenv("TRUSTFORGE_ADMIN_TOKEN", "")
    if not token:
        return False
    auth = headers.get("Authorization", headers.get("authorization", ""))
    return auth == f"Bearer {token}"


# ─── Response Envelope ───────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def admin_response(data: Any, status_code: int = 200) -> tuple[int, dict[str, Any]]:
    """Wrap data in success envelope."""
    return status_code, {
        "status": "ok",
        "data": data,
        "timestamp": _now_iso(),
    }


def admin_error(code: str, message: str, status_code: int = 400) -> tuple[int, dict[str, Any]]:
    """Wrap error in error envelope."""
    return status_code, {
        "status": "error",
        "error": {"code": code, "message": message},
        "timestamp": _now_iso(),
    }


# ─── Pagination ──────────────────────────────────────────────────────────────


def _paginate(items: list[Any], page: int, page_size: int) -> dict[str, Any]:
    """Paginate a list of items."""
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "items": items[start:end],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ─── Content Redaction ───────────────────────────────────────────────────────


def _redact_memory(entry: dict[str, Any], show_content: bool = False) -> dict[str, Any]:
    """Redact sensitive memory content."""
    result = dict(entry)
    if not show_content:
        result["content_ref"] = "[REDACTED]"
    else:
        ref = result.get("content_ref", "")
        if len(ref) > 200:
            result["content_ref"] = ref[:200] + "..."
    return result


# ─── Query Param Helpers ─────────────────────────────────────────────────────


def _parse_params(query_string: str) -> dict[str, str]:
    """Parse URL query string into single-value dict."""
    parsed = parse_qs(query_string, keep_blank_values=False)
    return {k: v[0] for k, v in parsed.items()}


def _int_param(params: dict[str, str], key: str, default: int) -> int:
    """Get integer param with default."""
    try:
        return int(params.get(key, str(default)))
    except (ValueError, TypeError):
        return default


# ─── Endpoint Handlers ───────────────────────────────────────────────────────


def handle_admin_memories(
    params: dict[str, str], runtime: AgosRuntime
) -> tuple[int, dict[str, Any]]:
    """GET /api/admin/agos/memories"""
    run_id = params.get("run_id", "")
    kind = params.get("kind", "")
    show_content = params.get("show_content", "false").lower() == "true"
    page = _int_param(params, "page", 1)
    page_size = _int_param(params, "page_size", 20)

    if not run_id:
        return admin_error("BAD_REQUEST", "run_id parameter is required", 400)

    memories = runtime.lineage.get_run_memories(run_id)

    # Filter by kind
    if kind:
        memories = [m for m in memories if m.get("kind") == kind]

    # Redact content
    memories = [_redact_memory(m, show_content) for m in memories]

    return admin_response(_paginate(memories, page, page_size))


def handle_admin_skills(
    params: dict[str, str], runtime: AgosRuntime
) -> tuple[int, dict[str, Any]]:
    """GET /api/admin/agos/skills"""
    run_id = params.get("run_id", "")
    family = params.get("family", "")
    page = _int_param(params, "page", 1)
    page_size = _int_param(params, "page_size", 20)

    if not run_id:
        return admin_error("BAD_REQUEST", "run_id parameter is required", 400)

    manifest = runtime.lineage.get_run_skills(run_id)
    if manifest is None:
        return admin_response(_paginate([], page, page_size))

    items = [
        {
            "skill_id": e.skill_id,
            "revision_hash": e.revision_hash,
            "reason": e.reason,
        }
        for e in manifest.entries
    ]

    # Filter by family (would need skill lookup, skip in MVP — just pass through)
    return admin_response(_paginate(items, page, page_size))


def handle_admin_tools(
    params: dict[str, str], runtime: AgosRuntime
) -> tuple[int, dict[str, Any]]:
    """GET /api/admin/agos/tools"""
    run_id = params.get("run_id", "")
    status_filter = params.get("status", "")
    page = _int_param(params, "page", 1)
    page_size = _int_param(params, "page_size", 20)

    if not run_id:
        return admin_error("BAD_REQUEST", "run_id parameter is required", 400)

    invocations = runtime.lineage.get_run_invocations(run_id)

    # Filter by status
    if status_filter:
        invocations = [i for i in invocations if i.get("status") == status_filter]

    return admin_response(_paginate(invocations, page, page_size))


def handle_admin_context(
    params: dict[str, str], runtime: AgosRuntime
) -> tuple[int, dict[str, Any]]:
    """GET /api/admin/agos/context"""
    run_id = params.get("run_id", "")

    if not run_id:
        return admin_error("BAD_REQUEST", "run_id parameter is required", 400)

    manifest = runtime.lineage.get_run_context(run_id)
    if manifest is None:
        return admin_error("NOT_FOUND", f"No context manifest for run_id={run_id}", 404)

    summary = manifest_summary(manifest)
    data = {
        "manifest_id": manifest.manifest_id,
        "run_id": manifest.run_id,
        "content_hash": manifest.content_hash,
        "token_budget": manifest.token_budget,
        "token_used": manifest.token_used,
        "created_at": manifest.created_at,
        "included_count": summary["included_count"],
        "excluded_count": summary["excluded_count"],
        "exclusion_reasons": summary["exclusion_reasons"],
        "included_refs": manifest.included_refs.to_dict(),
        "excluded_refs": [e.to_dict() for e in manifest.excluded_refs],
    }
    return admin_response(data)


# ─── Route Dispatcher ────────────────────────────────────────────────────────


def dispatch_admin_agos(
    path: str,
    query_string: str,
    headers: dict[str, str],
    runtime: AgosRuntime,
) -> tuple[int, dict[str, Any]]:
    """Dispatch /api/admin/agos/* requests.

    Returns (status_code, response_body_dict).
    """
    # Authorization check
    if not check_admin_auth(headers):
        return admin_error("UNAUTHORIZED", "Admin token required", 401)

    params = _parse_params(query_string)

    if path == "/api/admin/agos/memories":
        return handle_admin_memories(params, runtime)
    elif path == "/api/admin/agos/skills":
        return handle_admin_skills(params, runtime)
    elif path == "/api/admin/agos/tools":
        return handle_admin_tools(params, runtime)
    elif path == "/api/admin/agos/context":
        return handle_admin_context(params, runtime)
    else:
        return admin_error("NOT_FOUND", f"Unknown endpoint: {path}", 404)
