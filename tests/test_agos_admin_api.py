"""Tests for Agent OS Admin Summary API.

Issue: #923 | Epic: #914
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from trustforge.agos_admin_api import (
    admin_error,
    admin_response,
    check_admin_auth,
    dispatch_admin_agos,
    handle_admin_context,
    handle_admin_memories,
    handle_admin_skills,
    handle_admin_tools,
)
from trustforge.agos_runtime import AgosRuntime
from trustforge.memory_os import MemoryEntry
from trustforge.memory_retrieval import MemoryRef
from trustforge.tool_registry import ToolCapability


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "admin_api_data"
    d.mkdir()
    return d


@pytest.fixture
def runtime(data_dir: Path) -> AgosRuntime:
    with patch.dict(os.environ, {"TRUSTFORGE_AGOS_ENABLED": "1"}):
        r = AgosRuntime(data_dir=data_dir)
        r._ensure_init()
        yield r
        r.close()


@pytest.fixture
def admin_headers():
    return {"Authorization": "Bearer test-admin-token"}


@pytest.fixture
def _set_admin_token():
    with patch.dict(os.environ, {"TRUSTFORGE_ADMIN_TOKEN": "test-admin-token"}):
        yield


# ─── Authorization Tests ─────────────────────────────────────────────────────


class TestAuthorization:
    """Auth is handled by web.py outer gate (_admin_auth_check + X-Admin-Token).
    dispatch_admin_agos does NOT do its own auth — test that it proceeds directly."""

    def test_dispatch_proceeds_without_internal_auth(self, runtime, _set_admin_token):
        """dispatch_admin_agos trusts the caller (web.py) already authenticated."""
        status, body = dispatch_admin_agos(
            "/api/admin/agos/memories", "run_id=x", {}, runtime
        )
        # Should NOT return 401 — auth is not this module's job
        assert status == 200


# ─── Response Envelope Tests ─────────────────────────────────────────────────


class TestEnvelope:
    def test_success_envelope(self):
        status, body = admin_response({"key": "value"})
        assert status == 200
        assert body["status"] == "ok"
        assert body["data"] == {"key": "value"}
        assert "timestamp" in body

    def test_error_envelope(self):
        status, body = admin_error("NOT_FOUND", "not found", 404)
        assert status == 404
        assert body["status"] == "error"
        assert body["error"]["code"] == "NOT_FOUND"
        assert body["error"]["message"] == "not found"
        assert "timestamp" in body


# ─── Memories Endpoint Tests ─────────────────────────────────────────────────


class TestMemoriesEndpoint:
    def test_memories_requires_run_id(self, runtime, admin_headers, _set_admin_token):
        status, body = dispatch_admin_agos(
            "/api/admin/agos/memories", "", admin_headers, runtime
        )
        assert status == 400
        assert body["error"]["code"] == "BAD_REQUEST"

    def test_memories_empty_run(self, runtime, admin_headers, _set_admin_token):
        status, body = dispatch_admin_agos(
            "/api/admin/agos/memories", "run_id=no-run", admin_headers, runtime
        )
        assert status == 200
        assert body["data"]["items"] == []
        assert body["data"]["total"] == 0

    def test_memories_with_data(self, runtime, admin_headers, _set_admin_token):
        # Add memory entry
        runtime._memory_repo.save(MemoryEntry(
            memory_id="mem-1", kind="episodic", provider="test",
            content_hash="a" * 64, content_ref="sensitive content here",
            published_at="2026-07-01T00:00:00Z",
            retrieved_at="2026-07-01T00:00:00Z",
            evidence_eligible=True, run_id="run-mem-api",
        ))

        status, body = dispatch_admin_agos(
            "/api/admin/agos/memories", "run_id=run-mem-api", admin_headers, runtime
        )
        assert status == 200
        assert body["data"]["total"] == 1
        # Content should be redacted by default
        assert body["data"]["items"][0]["content_ref"] == "[REDACTED]"

    def test_memories_show_content(self, runtime, admin_headers, _set_admin_token):
        runtime._memory_repo.save(MemoryEntry(
            memory_id="mem-2", kind="episodic", provider="test",
            content_hash="b" * 64, content_ref="visible content",
            published_at="2026-07-01T00:00:00Z",
            retrieved_at="2026-07-01T00:00:00Z",
            evidence_eligible=False, run_id="run-show",
        ))

        status, body = dispatch_admin_agos(
            "/api/admin/agos/memories", "run_id=run-show&show_content=true",
            admin_headers, runtime
        )
        assert status == 200
        assert body["data"]["items"][0]["content_ref"] == "visible content"

    def test_memories_filter_kind(self, runtime, admin_headers, _set_admin_token):
        runtime._memory_repo.save(MemoryEntry(
            memory_id="mem-3", kind="episodic", provider="t1",
            content_hash="c" * 64, content_ref="r1",
            published_at="2026-07-01T00:00:00Z",
            retrieved_at="2026-07-01T00:00:00Z",
            run_id="run-kind",
        ))
        runtime._memory_repo.save(MemoryEntry(
            memory_id="mem-4", kind="semantic", provider="t2",
            content_hash="d" * 64, content_ref="r2",
            published_at="2026-07-01T00:00:00Z",
            retrieved_at="2026-07-01T00:00:00Z",
            run_id="run-kind",
        ))

        status, body = dispatch_admin_agos(
            "/api/admin/agos/memories", "run_id=run-kind&kind=episodic",
            admin_headers, runtime
        )
        assert status == 200
        assert body["data"]["total"] == 1

    def test_memories_pagination(self, runtime, admin_headers, _set_admin_token):
        for i in range(5):
            runtime._memory_repo.save(MemoryEntry(
                memory_id=f"mem-pg-{i}", kind="episodic", provider=f"p{i}",
                content_hash=f"{i}" * 64, content_ref=f"ref{i}",
                published_at="2026-07-01T00:00:00Z",
                retrieved_at="2026-07-01T00:00:00Z",
                run_id="run-page",
            ))

        status, body = dispatch_admin_agos(
            "/api/admin/agos/memories", "run_id=run-page&page=2&page_size=2",
            admin_headers, runtime
        )
        assert status == 200
        assert body["data"]["total"] == 5
        assert body["data"]["page"] == 2
        assert len(body["data"]["items"]) == 2


# ─── Skills Endpoint Tests ───────────────────────────────────────────────────


class TestSkillsEndpoint:
    def test_skills_requires_run_id(self, runtime, admin_headers, _set_admin_token):
        status, body = dispatch_admin_agos(
            "/api/admin/agos/skills", "", admin_headers, runtime
        )
        assert status == 400

    def test_skills_empty_run(self, runtime, admin_headers, _set_admin_token):
        status, body = dispatch_admin_agos(
            "/api/admin/agos/skills", "run_id=no-run", admin_headers, runtime
        )
        assert status == 200
        assert body["data"]["items"] == []


# ─── Tools Endpoint Tests ────────────────────────────────────────────────────


class TestToolsEndpoint:
    def test_tools_requires_run_id(self, runtime, admin_headers, _set_admin_token):
        status, body = dispatch_admin_agos(
            "/api/admin/agos/tools", "", admin_headers, runtime
        )
        assert status == 400

    def test_tools_with_invocations(self, runtime, admin_headers, _set_admin_token):
        runtime._tool_registry.register_tool(
            ToolCapability(tool_id="api-tool", name="API", side_effect_class="read_only")
        )
        inv_id = runtime.record_tool_invocation("run-tools-api", "api-tool", {"q": "BTC"})
        runtime.complete_tool_invocation(inv_id, output={"price": 100}, status="success")

        status, body = dispatch_admin_agos(
            "/api/admin/agos/tools", "run_id=run-tools-api", admin_headers, runtime
        )
        assert status == 200
        assert body["data"]["total"] == 1
        assert body["data"]["items"][0]["status"] == "success"

    def test_tools_filter_status(self, runtime, admin_headers, _set_admin_token):
        runtime._tool_registry.register_tool(
            ToolCapability(tool_id="filter-tool", name="F", side_effect_class="read_only")
        )
        inv1 = runtime.record_tool_invocation("run-filter", "filter-tool", {"a": 1})
        runtime.complete_tool_invocation(inv1, output="ok", status="success")
        inv2 = runtime.record_tool_invocation("run-filter", "filter-tool", {"a": 2})
        runtime.complete_tool_invocation(inv2, status="failed", error="err")

        status, body = dispatch_admin_agos(
            "/api/admin/agos/tools", "run_id=run-filter&status=failed",
            admin_headers, runtime
        )
        assert status == 200
        assert body["data"]["total"] == 1
        assert body["data"]["items"][0]["status"] == "failed"


# ─── Context Endpoint Tests ──────────────────────────────────────────────────


class TestContextEndpoint:
    def test_context_requires_run_id(self, runtime, admin_headers, _set_admin_token):
        status, body = dispatch_admin_agos(
            "/api/admin/agos/context", "", admin_headers, runtime
        )
        assert status == 400

    def test_context_not_found(self, runtime, admin_headers, _set_admin_token):
        status, body = dispatch_admin_agos(
            "/api/admin/agos/context", "run_id=ghost", admin_headers, runtime
        )
        assert status == 404
        assert body["error"]["code"] == "NOT_FOUND"

    def test_context_returns_manifest(self, runtime, admin_headers, _set_admin_token):
        # Build a context first
        runtime.build_context("run-ctx-api", question="BTC analysis")

        status, body = dispatch_admin_agos(
            "/api/admin/agos/context", "run_id=run-ctx-api", admin_headers, runtime
        )
        assert status == 200
        assert body["data"]["run_id"] == "run-ctx-api"
        assert body["data"]["content_hash"]
        assert "included_refs" in body["data"]
        assert "excluded_refs" in body["data"]
        assert "included_count" in body["data"]


# ─── Unknown Endpoint Test ───────────────────────────────────────────────────


class TestUnknownEndpoint:
    def test_unknown_path_returns_404(self, runtime, admin_headers, _set_admin_token):
        status, body = dispatch_admin_agos(
            "/api/admin/agos/unknown", "", admin_headers, runtime
        )
        assert status == 404
        assert body["error"]["code"] == "NOT_FOUND"
