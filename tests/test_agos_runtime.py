"""Tests for Agent OS Runtime Integration.

Issue: #922 | Epic: #914
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from trustforge.agos_runtime import AgosRuntime, agos_enabled
from trustforge.memory_retrieval import MemoryRef
from trustforge.skill_registry import (
    SkillRegistryRepository,
    SkillRevision,
    TaskSkill,
    revision_hash_for,
)
from trustforge.tool_registry import ToolCapability, ToolRegistryRepository


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "agos_data"
    d.mkdir()
    return d


@pytest.fixture
def runtime(data_dir: Path) -> AgosRuntime:
    r = AgosRuntime(data_dir=data_dir)
    yield r
    r.close()


def _enable_agos():
    return patch.dict(os.environ, {"TRUSTFORGE_AGOS_ENABLED": "1"})


def _disable_agos():
    return patch.dict(os.environ, {"TRUSTFORGE_AGOS_ENABLED": "0"})


# ─── Feature Flag Tests ─────────────────────────────────────────────────────


class TestFeatureFlag:
    def test_agos_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            # Remove the key if present
            os.environ.pop("TRUSTFORGE_AGOS_ENABLED", None)
            assert agos_enabled() is False

    def test_agos_enabled_when_set(self):
        with _enable_agos():
            assert agos_enabled() is True

    def test_agos_disabled_when_zero(self):
        with _disable_agos():
            assert agos_enabled() is False


# ─── Context Build Tests ─────────────────────────────────────────────────────


class TestContextBuild:
    def test_build_context_disabled_returns_none(self, runtime: AgosRuntime):
        with _disable_agos():
            result = runtime.build_context("run-1", question="BTC analysis")
            assert result is None

    def test_build_context_enabled_produces_manifest(self, runtime: AgosRuntime):
        with _enable_agos():
            manifest = runtime.build_context(
                "run-enabled",
                question="BTC 走勢",
                snapshot_ref="snap-1",
                token_budget=4096,
            )
            assert manifest is not None
            assert manifest.run_id == "run-enabled"
            assert manifest.content_hash
            assert manifest.included_refs.question_ref == "BTC 走勢"

    def test_build_context_with_memory_refs(self, runtime: AgosRuntime):
        with _enable_agos():
            refs = [
                MemoryRef(
                    memory_id="m1", kind="episodic", rank=1,
                    reason="test", evidence_eligible=True,
                    content_preview="BTC price data", run_id="run-mem",
                )
            ]
            manifest = runtime.build_context("run-mem", memory_refs=refs)
            assert manifest is not None
            assert len(manifest.included_refs.memory_refs) == 1

    def test_build_context_graceful_degradation(self, runtime: AgosRuntime):
        """Even if internal error occurs, should not crash."""
        with _enable_agos():
            # Force init to succeed first
            runtime._ensure_init()
            # Corrupt context builder
            runtime._context_builder = None
            result = runtime.build_context("run-broken")
            # Should return None, not crash
            assert result is None


# ─── Tool Invocation Audit Tests ─────────────────────────────────────────────


class TestToolAudit:
    def test_record_invocation_disabled(self, runtime: AgosRuntime):
        with _disable_agos():
            result = runtime.record_tool_invocation("run-1", "tool-x", {"q": "BTC"})
            assert result is None

    def test_record_and_complete_invocation(self, runtime: AgosRuntime, data_dir: Path):
        with _enable_agos():
            runtime._ensure_init()
            # Register a tool first
            runtime._tool_registry.register_tool(
                ToolCapability(tool_id="cg-price", name="CoinGecko", side_effect_class="read_only")
            )

            inv_id = runtime.record_tool_invocation("run-tool", "cg-price", {"coin": "BTC"})
            assert inv_id is not None

            runtime.complete_tool_invocation(
                inv_id, output={"price": 65000}, status="success"
            )

            # Verify via lineage query
            invocations = runtime.lineage.get_run_invocations("run-tool")
            assert len(invocations) == 1
            assert invocations[0]["status"] == "success"
            assert invocations[0]["output_hash"] is not None

    def test_tool_audited_fetch_success(self, runtime: AgosRuntime):
        with _enable_agos():
            runtime._ensure_init()
            runtime._tool_registry.register_tool(
                ToolCapability(tool_id="fetch-tool", name="Fetch", side_effect_class="read_only")
            )

            def mock_fetch(url="", **kwargs):
                return {"data": "result"}

            result = runtime.tool_audited_fetch(
                "fetch-tool", mock_fetch, {"url": "https://api.example.com"},
                run_id="run-fetch",
            )
            assert result == {"data": "result"}

    def test_tool_audited_fetch_failure(self, runtime: AgosRuntime):
        with _enable_agos():
            runtime._ensure_init()
            runtime._tool_registry.register_tool(
                ToolCapability(tool_id="fail-fetch", name="Fail", side_effect_class="read_only")
            )

            def failing_fetch(**kwargs):
                raise ConnectionError("timeout")

            with pytest.raises(ConnectionError):
                runtime.tool_audited_fetch(
                    "fail-fetch", failing_fetch, {},
                    run_id="run-fail-fetch",
                )

            # Invocation should be recorded as failed
            invocations = runtime.lineage.get_run_invocations("run-fail-fetch")
            assert len(invocations) == 1
            assert invocations[0]["status"] == "failed"
            assert "timeout" in invocations[0]["error"]

    def test_tool_audited_fetch_disabled_still_executes(self, runtime: AgosRuntime):
        """When AGOS disabled, fetch still executes without audit."""
        with _disable_agos():
            def simple_fetch(**kwargs):
                return "ok"

            result = runtime.tool_audited_fetch(
                "any-tool", simple_fetch, {},
                run_id="run-no-audit",
            )
            assert result == "ok"

    def test_tool_audited_fetch_blocks_unknown_tool(self, runtime: AgosRuntime):
        """When AGOS enabled, unknown tool is blocked with PermissionError."""
        with _enable_agos():
            runtime._ensure_init()

            def should_not_run(**kwargs):
                raise AssertionError("should not execute")

            with pytest.raises(PermissionError, match="not registered"):
                runtime.tool_audited_fetch(
                    "unregistered-ghost", should_not_run, {},
                    run_id="run-blocked",
                )

    def test_tool_audited_fetch_blocks_high_risk(self, runtime: AgosRuntime):
        """When AGOS enabled, high-risk tool is blocked with PermissionError."""
        with _enable_agos():
            runtime._ensure_init()
            runtime._tool_registry.register_tool(ToolCapability(
                tool_id="deploy-blocked",
                name="Deploy",
                side_effect_class="deploy_or_release",
                approval_requirement="always",
            ))

            def should_not_run(**kwargs):
                raise AssertionError("should not execute")

            with pytest.raises(PermissionError, match="requires human approval"):
                runtime.tool_audited_fetch(
                    "deploy-blocked", should_not_run, {},
                    run_id="run-deploy-block",
                )


# ─── Lineage Query Tests ────────────────────────────────────────────────────


class TestLineageQuery:
    def test_get_run_context(self, runtime: AgosRuntime):
        with _enable_agos():
            runtime.build_context("run-lineage", question="test")
            manifest = runtime.lineage.get_run_context("run-lineage")
            assert manifest is not None
            assert manifest.run_id == "run-lineage"

    def test_get_run_context_not_found(self, runtime: AgosRuntime):
        with _enable_agos():
            runtime._ensure_init()
            assert runtime.lineage.get_run_context("nonexistent") is None

    def test_get_run_memories_empty(self, runtime: AgosRuntime):
        with _enable_agos():
            runtime._ensure_init()
            assert runtime.lineage.get_run_memories("no-run") == []

    def test_get_run_skills_none(self, runtime: AgosRuntime):
        with _enable_agos():
            runtime._ensure_init()
            assert runtime.lineage.get_run_skills("no-run") is None

    def test_get_run_invocations_empty(self, runtime: AgosRuntime):
        with _enable_agos():
            runtime._ensure_init()
            assert runtime.lineage.get_run_invocations("no-run") == []


# ─── Finalize Tests ──────────────────────────────────────────────────────────


class TestFinalize:
    def test_finalize_disabled_noop(self, runtime: AgosRuntime):
        with _disable_agos():
            runtime.finalize_run("run-fin")  # Should not crash

    def test_finalize_enabled_noop(self, runtime: AgosRuntime):
        with _enable_agos():
            runtime.finalize_run("run-fin")  # Placeholder, should not crash
