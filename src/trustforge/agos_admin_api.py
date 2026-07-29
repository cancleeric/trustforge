"""Agent OS Admin Summary API — read-only endpoints for Agent OS lineage.

Provides authorization-gated, paginated, read-only endpoints for memory,
skill manifest, tool invocation, and context manifest queries.

Design principles:
  - Read-only: all endpoints are GET, no mutation
- Authorization-gated by outer web handler using `X-Admin-Token`
  - Content redaction: sensitive memory content redacted by default
  - Typed envelopes: consistent response format
  - Zero third-party dependencies

Issue: #923 | Epic: #914
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from .agos_runtime import AgosRuntime
from .context_builder import ContextManifest, manifest_summary


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
    """GET /api/admin/agos/memories

    Returns: kind, eligibility, lineage (rank/reason), selection reason, provider, timestamps.
    """
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

    # Enrich with governance fields from context manifest
    manifest = runtime.lineage.get_run_context(run_id)
    lineage_map: dict[str, dict[str, Any]] = {}
    if manifest:
        for mref in manifest.included_refs.memory_refs:
            lineage_map[mref["memory_id"]] = {
                "rank": mref.get("rank"),
                "reason": mref.get("reason"),
                "evidence_eligible": mref.get("evidence_eligible", False),
                "inclusion_status": "included",
            }
        for eref in manifest.excluded_refs:
            if eref.ref_type == "memory":
                lineage_map[eref.ref_id] = {
                    "rank": None,
                    "reason": eref.reason,
                    "evidence_eligible": False,
                    "inclusion_status": f"excluded:{eref.reason}",
                }

    for m in memories:
        gov = lineage_map.get(m["memory_id"], {})
        m["lineage_rank"] = gov.get("rank")
        m["selection_reason"] = gov.get("reason", "not_in_manifest")
        m["evidence_eligible_verified"] = gov.get("evidence_eligible", m.get("evidence_eligible", False))
        m["inclusion_status"] = gov.get("inclusion_status", "not_in_manifest")

    # Redact content
    memories = [_redact_memory(m, show_content) for m in memories]

    return admin_response(_paginate(memories, page, page_size))


def handle_admin_skills(
    params: dict[str, str], runtime: AgosRuntime
) -> tuple[int, dict[str, Any]]:
    """GET /api/admin/agos/skills

    Returns: revision, dependencies, risk_class, lifecycle, frozen state.
    """
    run_id = params.get("run_id", "")
    page = _int_param(params, "page", 1)
    page_size = _int_param(params, "page_size", 20)

    if not run_id:
        return admin_error("BAD_REQUEST", "run_id parameter is required", 400)

    manifest = runtime.lineage.get_run_skills(run_id)
    if manifest is None:
        return admin_response(_paginate([], page, page_size))

    items = []
    for e in manifest.entries:
        item: dict[str, Any] = {
            "skill_id": e.skill_id,
            "revision_hash": e.revision_hash,
            "reason": e.reason,
            "frozen_at": manifest.created_at,
        }
        # Enrich from registry if available
        if runtime._skill_registry:
            skill = runtime._skill_registry.get_skill(e.skill_id)
            if skill:
                item["family"] = skill.family
                item["risk_class"] = skill.risk_class
                item["lifecycle"] = skill.lifecycle
                item["side_effect_class"] = skill.side_effect_class
            deps = runtime._skill_registry.get_dependencies(e.skill_id)
            item["dependencies"] = [
                {"to": d.to_skill_id, "relation": d.relation} for d in deps
            ]
        items.append(item)

    return admin_response(_paginate(items, page, page_size))


def handle_admin_tools(
    params: dict[str, str], runtime: AgosRuntime
) -> tuple[int, dict[str, Any]]:
    """GET /api/admin/agos/tools

    Returns: side_effect_class, approval, hashes, evidence_class, status.
    """
    run_id = params.get("run_id", "")
    status_filter = params.get("status", "")
    page = _int_param(params, "page", 1)
    page_size = _int_param(params, "page_size", 20)

    if not run_id:
        return admin_error("BAD_REQUEST", "run_id parameter is required", 400)

    invocations = runtime.lineage.get_run_invocations(run_id)

    # Enrich with tool capability metadata
    for inv in invocations:
        if runtime._tool_registry:
            cap = runtime._tool_registry.get_tool(inv.get("tool_id", ""))
            if cap:
                inv["side_effect_class"] = cap.side_effect_class
                inv["evidence_class"] = cap.evidence_class
                inv["approval_requirement"] = cap.approval_requirement
            else:
                inv["side_effect_class"] = "unknown"
                inv["evidence_class"] = "unknown"
                inv["approval_requirement"] = "unknown"

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

    NOTE: Authorization is handled by the outer web.py _admin_auth_check()
    which validates X-Admin-Token before this function is called. This
    function does NOT perform its own auth check to avoid dual-gate confusion.
    """
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
