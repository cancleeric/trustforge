"""Integrated Agent OS security invariants for issue #925."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from trustforge.agos_runtime import AgosRuntime
from trustforge.context_builder import get_evidence_eligible_memories
from trustforge.memory_os import MemoryEntry, validate_evidence_eligible
from trustforge.memory_retrieval import MemoryRef
from trustforge.skill_registry import SkillRevision, TaskSkill, revision_hash_for
from trustforge.tool_registry import ToolCapability


@pytest.fixture(autouse=True)
def _authorize_test_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "trustforge.agos_db_auth.verify_db_authorization", lambda purpose: None
    )


@pytest.fixture
def runtime(tmp_path: Path):
    with patch.dict(os.environ, {"TRUSTFORGE_AGOS_ENABLED": "1"}):
        value = AgosRuntime(data_dir=tmp_path)
        value._ensure_init()
        yield value
        value.close()


def test_historical_memory_cannot_enter_scoring_input(runtime: AgosRuntime) -> None:
    """A forged retrieval flag is rechecked against durable memory governance."""
    historical = MemoryEntry(
        memory_id="historical-1",
        kind="semantic",
        provider="hermes-analysis",
        content_hash="a" * 64,
        content_ref="old model conclusion",
        published_at="2026-01-01T00:00:00Z",
        retrieved_at="2026-01-02T00:00:00Z",
        evidence_eligible=False,
        run_id="guard-run",
    )
    runtime._memory_repo.save(historical)
    forged = MemoryRef(
        memory_id=historical.memory_id,
        kind=historical.kind,
        rank=1,
        reason="attempted injection",
        evidence_eligible=True,
        content_preview=historical.content_ref,
        run_id="guard-run",
    )

    manifest = runtime.build_context("guard-run", memory_refs=[forged])

    assert manifest is not None
    assert manifest.included_refs.memory_refs[0]["evidence_eligible"] is False
    assert get_evidence_eligible_memories(manifest) == []


def test_historical_memory_cannot_be_marked_evidence_eligible() -> None:
    historical = MemoryEntry(
        memory_id="historical-forged",
        kind="semantic",
        provider="hermes-analysis",
        content_hash="c" * 64,
        content_ref="old conclusion",
        published_at="2026-01-01T00:00:00Z",
        retrieved_at="2026-01-02T00:00:00Z",
        evidence_eligible=True,
    )

    with pytest.raises(ValueError, match="historical conclusions"):
        validate_evidence_eligible(historical)


@pytest.mark.parametrize("lifecycle", ["retired"])
def test_retired_skill_is_excluded(runtime: AgosRuntime, lifecycle: str) -> None:
    content = {"family": "analysis", "rules": ["do not select"]}
    runtime._skill_registry.save_skill(
        TaskSkill(
            skill_id="retired-skill",
            family="analysis",
            name="Retired",
            lifecycle=lifecycle,
        )
    )
    runtime._skill_registry.save_revision(
        SkillRevision(
            revision_hash=revision_hash_for(content),
            skill_id="retired-skill",
            content=content,
            is_active=True,
        )
    )

    assert runtime._skill_loader.is_stale("retired-skill") is True
    with pytest.raises(ValueError, match="cannot freeze stale"):
        runtime._skill_loader.freeze_manifest("retired-run", ["retired-skill"])
    assert runtime._skill_loader.get_frozen_manifest("retired-run") is None


@pytest.mark.parametrize("skill_id", ["unknown-skill", "stale-skill"])
def test_unknown_and_stale_skills_fail_closed(
    runtime: AgosRuntime, skill_id: str
) -> None:
    if skill_id == "stale-skill":
        runtime._skill_registry.save_skill(
            TaskSkill(
                skill_id=skill_id,
                family="analysis",
                name="Stale",
                lifecycle="active",
            )
        )

    with pytest.raises(ValueError, match="unknown skill|cannot freeze stale"):
        runtime._skill_loader.freeze_manifest("skill-guard-run", [skill_id])


def test_unknown_tool_never_reaches_callable(runtime: AgosRuntime) -> None:
    called = False

    def forbidden() -> None:
        nonlocal called
        called = True

    assert runtime._tool_registry.is_known("unknown-tool") is False
    with pytest.raises(PermissionError, match="unknown tools cannot execute"):
        runtime.tool_audited_fetch("unknown-tool", forbidden, {}, run_id="tool-run")
    assert called is False


@pytest.mark.parametrize("side_effect", ["external_write", "deploy_or_release"])
def test_high_risk_tool_without_runtime_approval_never_executes(
    runtime: AgosRuntime, side_effect: str
) -> None:
    tool_id = f"guard-{side_effect}"
    runtime._tool_registry.register_tool(
        ToolCapability(
            tool_id=tool_id,
            name=tool_id,
            side_effect_class=side_effect,
            approval_requirement="always",
        )
    )
    called = False

    def forbidden() -> None:
        nonlocal called
        called = True

    with pytest.raises(PermissionError, match="requires human approval"):
        runtime.tool_audited_fetch(tool_id, forbidden, {}, run_id="high-risk-run")
    assert called is False
