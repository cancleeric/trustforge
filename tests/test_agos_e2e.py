"""Agent OS End-to-End Tests — Replay, Security Guards, Regression, Lineage Consistency.

This file consolidates E2E verification for the entire Agent OS stack:
- Replay verification (frozen manifest → reproducible hashes)
- Security guard tests (historical memory, unknown skill/tool, approval)
- Non-regression tests (AGOS enabled vs disabled)
- Lineage consistency (runtime ↔ Admin API)

Issue: #925 | Epic: #914
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from trustforge.agos_admin_api import dispatch_admin_agos
from trustforge.agos_runtime import AgosRuntime
from trustforge.context_builder import compute_manifest_hash
from trustforge.memory_os import MemoryEntry, MemoryRepository, validate_evidence_eligible
from trustforge.memory_retrieval import MemoryRef
from trustforge.skill_loader import SkillLoader
from trustforge.skill_registry import (
    SkillDependency,
    SkillRegistryRepository,
    SkillRevision,
    TaskSkill,
    revision_hash_for,
)
from trustforge.tool_registry import ToolCapability, ToolRegistryRepository


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "e2e_data"
    d.mkdir()
    return d


@pytest.fixture
def runtime(data_dir: Path) -> AgosRuntime:
    with patch.dict(os.environ, {"TRUSTFORGE_AGOS_ENABLED": "1"}):
        r = AgosRuntime(data_dir=data_dir)
        r._ensure_init()
        yield r
        r.close()


def _enable_agos():
    return patch.dict(os.environ, {"TRUSTFORGE_AGOS_ENABLED": "1"})


def _disable_agos():
    return patch.dict(os.environ, {"TRUSTFORGE_AGOS_ENABLED": "0"})


# ═══════════════════════════════════════════════════════════════════════════════
# REPLAY VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestReplayVerification:
    """Verify that replaying a frozen manifest reproduces reference hashes."""

    def test_manifest_hash_reproducible(self, runtime: AgosRuntime):
        """Same inputs → same content_hash."""
        with _enable_agos():
            m1 = runtime.build_context("replay-run-1", question="BTC", snapshot_ref="snap-a")
            assert m1 is not None

            # Recompute hash from stored data
            recomputed = compute_manifest_hash(
                m1.run_id,
                m1.included_refs,
                m1.excluded_refs,
                m1.token_budget,
                m1.token_used,
            )
            assert recomputed == m1.content_hash

    def test_skill_revision_hash_reproducible(self, runtime: AgosRuntime):
        """Skill revision hashes are content-addressed and verifiable."""
        content = {"rules": ["replay test rule"], "family": "analysis"}
        expected_hash = revision_hash_for(content)

        runtime._skill_registry.save_skill(
            TaskSkill(skill_id="replay-skill", family="analysis", name="Replay", lifecycle="active")
        )
        runtime._skill_registry.save_revision(SkillRevision(
            revision_hash=expected_hash, skill_id="replay-skill", content=content, is_active=True,
        ))

        # Retrieve and verify
        rev = runtime._skill_registry.get_revision(expected_hash)
        assert rev is not None
        assert revision_hash_for(rev.content) == expected_hash

    def test_frozen_manifest_immutable_after_change(self, runtime: AgosRuntime):
        """Frozen manifest retains original revision even after active pointer changes."""
        with _enable_agos():
            content_v1 = {"rules": ["v1"], "family": "analysis"}
            hash_v1 = revision_hash_for(content_v1)
            runtime._skill_registry.save_skill(
                TaskSkill(skill_id="immutable-test", family="analysis", name="I", lifecycle="active")
            )
            runtime._skill_registry.save_revision(SkillRevision(
                revision_hash=hash_v1, skill_id="immutable-test", content=content_v1, is_active=True,
            ))

            manifest = runtime.build_context(
                "replay-run-2", skill_ids=["immutable-test"]
            )
            assert manifest is not None

            # Change active pointer to v2
            content_v2 = {"rules": ["v2"], "family": "analysis"}
            hash_v2 = revision_hash_for(content_v2)
            runtime._skill_registry.save_revision(SkillRevision(
                revision_hash=hash_v2, skill_id="immutable-test", content=content_v2,
            ))
            runtime._skill_registry.set_active("immutable-test", hash_v2)

            # Frozen manifest still references v1
            frozen = runtime._skill_loader.get_frozen_manifest("replay-run-2")
            assert frozen is not None
            assert any(e.revision_hash == hash_v1 for e in frozen.entries)


# ═══════════════════════════════════════════════════════════════════════════════
# SECURITY GUARD E2E TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestSecurityGuards:
    """Verify fail-closed security invariants across the integrated system."""

    def test_historical_memory_cannot_be_evidence(self, runtime: AgosRuntime):
        """Historical conclusions (hermes-*) cannot set evidence_eligible=true."""
        entry = MemoryEntry(
            memory_id="hist-guard-1",
            kind="semantic",
            provider="hermes-analysis",
            content_hash="a" * 64,
            content_ref="past conclusion",
            published_at="2026-01-01T00:00:00Z",
            retrieved_at="2026-01-01T00:00:00Z",
            evidence_eligible=True,  # Attempted
        )
        with pytest.raises(ValueError, match="historical conclusions"):
            validate_evidence_eligible(entry)

    def test_dialogue_memory_cannot_be_evidence(self, runtime: AgosRuntime):
        """Dialogue memory can never be evidence."""
        entry = MemoryEntry(
            memory_id="dial-guard-1",
            kind="dialogue",
            provider="user-session",
            content_hash="b" * 64,
            content_ref="user chat",
            published_at="2026-01-01T00:00:00Z",
            retrieved_at="2026-01-01T00:00:00Z",
            evidence_eligible=True,
        )
        with pytest.raises(ValueError, match="dialogue memory cannot be evidence"):
            validate_evidence_eligible(entry)

    def test_unknown_skill_cannot_be_frozen(self, runtime: AgosRuntime):
        """Unknown skill (no active revision) cannot enter manifest."""
        with _enable_agos():
            runtime._skill_registry.save_skill(
                TaskSkill(skill_id="unknown-rev", family="analysis", name="U", lifecycle="active")
            )
            # No revision → stale
            assert runtime._skill_loader.is_stale("unknown-rev") is True

            with pytest.raises(ValueError, match="cannot freeze stale"):
                runtime._skill_loader.freeze_manifest("guard-run-1", ["unknown-rev"])

    def test_retired_skill_excluded_from_manifest(self, runtime: AgosRuntime):
        """Retired skill cannot be selected."""
        with _enable_agos():
            runtime._skill_registry.save_skill(
                TaskSkill(skill_id="retired-guard", family="analysis", name="R", lifecycle="retired")
            )
            assert runtime._skill_loader.is_stale("retired-guard") is True

    def test_unknown_tool_cannot_execute(self, runtime: AgosRuntime):
        """Unknown tool is rejected (is_known=False)."""
        assert runtime._tool_registry.is_known("ghost-tool-xyz") is False
        assert runtime._tool_registry.requires_approval("ghost-tool-xyz") is True

    def test_external_write_tool_requires_approval(self, runtime: AgosRuntime):
        """external_write tool always requires approval."""
        runtime._tool_registry.register_tool(ToolCapability(
            tool_id="ext-guard",
            name="External",
            side_effect_class="external_write",
            approval_requirement="always",
        ))
        assert runtime._tool_registry.requires_approval("ext-guard") is True

    def test_deploy_tool_requires_approval(self, runtime: AgosRuntime):
        """deploy_or_release tool always requires approval."""
        runtime._tool_registry.register_tool(ToolCapability(
            tool_id="deploy-guard",
            name="Deploy",
            side_effect_class="deploy_or_release",
            approval_requirement="always",
        ))
        assert runtime._tool_registry.requires_approval("deploy-guard") is True

    def test_high_risk_skill_requires_activation_approval(self, runtime: AgosRuntime):
        """High-risk skill cannot be frozen without activation approval."""
        with _enable_agos():
            runtime._skill_registry.save_skill(TaskSkill(
                skill_id="hr-guard", family="analysis", name="HR",
                risk_class="external_write", lifecycle="active",
            ))
            content = {"rules": ["hr"], "family": "analysis"}
            runtime._skill_registry.save_revision(SkillRevision(
                revision_hash=revision_hash_for(content),
                skill_id="hr-guard", content=content, is_active=True,
            ))

            with pytest.raises(ValueError, match="requires activation approval"):
                runtime._skill_loader.freeze_manifest("guard-run-2", ["hr-guard"])


# ═══════════════════════════════════════════════════════════════════════════════
# NON-REGRESSION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestNonRegression:
    """Verify Agent OS does not break existing behavior."""

    def test_agos_disabled_no_side_effects(self, data_dir: Path):
        """With AGOS disabled, no DBs are created, no manifests produced."""
        with _disable_agos():
            rt = AgosRuntime(data_dir=data_dir)
            result = rt.build_context("noop-run", question="test")
            assert result is None

            inv_id = rt.record_tool_invocation("noop-run", "tool", {"x": 1})
            assert inv_id is None

            rt.finalize_run("noop-run")
            rt.close()

    def test_agos_enabled_produces_manifest(self, runtime: AgosRuntime):
        """With AGOS enabled, context manifest is produced."""
        with _enable_agos():
            result = runtime.build_context("regression-run", question="BTC")
            assert result is not None
            assert result.content_hash

    def test_tool_audited_fetch_does_not_alter_result(self, runtime: AgosRuntime):
        """Tool audit wrapper returns the same result as the raw function."""
        with _enable_agos():
            runtime._tool_registry.register_tool(ToolCapability(
                tool_id="passthrough", name="PT", side_effect_class="read_only",
            ))

            def raw_fetch(symbol="BTC"):
                return {"price": 65000, "symbol": symbol}

            result = runtime.tool_audited_fetch(
                "passthrough", raw_fetch, {"symbol": "BTC"}, run_id="reg-run"
            )
            assert result == {"price": 65000, "symbol": "BTC"}


# ═══════════════════════════════════════════════════════════════════════════════
# LINEAGE CONSISTENCY (Runtime ↔ Admin API)
# ═══════════════════════════════════════════════════════════════════════════════


class TestLineageConsistency:
    """Verify runtime lineage matches Admin API disclosure."""

    def test_context_manifest_consistent(self, runtime: AgosRuntime):
        """Runtime manifest == Admin API manifest."""
        with _enable_agos():
            manifest = runtime.build_context("consistency-run", question="ETH")
            assert manifest is not None

            # Query via lineage
            api_manifest = runtime.lineage.get_run_context("consistency-run")
            assert api_manifest is not None
            assert api_manifest.content_hash == manifest.content_hash
            assert api_manifest.run_id == manifest.run_id

    def test_tool_invocations_consistent(self, runtime: AgosRuntime):
        """Runtime tool invocations == Admin API tools."""
        with _enable_agos():
            runtime._tool_registry.register_tool(ToolCapability(
                tool_id="consist-tool", name="C", side_effect_class="read_only",
            ))
            inv_id = runtime.record_tool_invocation("consist-run", "consist-tool", {"q": "x"})
            runtime.complete_tool_invocation(inv_id, output="ok", status="success")

            # Query via lineage
            invocations = runtime.lineage.get_run_invocations("consist-run")
            assert len(invocations) == 1
            assert invocations[0]["status"] == "success"
            assert invocations[0]["invocation_id"] == inv_id

    def test_admin_api_dispatch_matches_lineage(self, runtime: AgosRuntime):
        """Admin API dispatch returns same data as direct lineage query."""
        with _enable_agos(), patch.dict(os.environ, {"TRUSTFORGE_ADMIN_TOKEN": "e2e-token"}):
            runtime.build_context("dispatch-run", question="SOL")
            headers = {"Authorization": "Bearer e2e-token"}

            status, body = dispatch_admin_agos(
                "/api/admin/agos/context", "run_id=dispatch-run", headers, runtime
            )
            assert status == 200
            assert body["data"]["run_id"] == "dispatch-run"

            # Compare with direct query
            direct = runtime.lineage.get_run_context("dispatch-run")
            assert direct is not None
            assert body["data"]["content_hash"] == direct.content_hash
