"""Tests for Context Builder — immutable per-run manifest.

Issue: #921 | Epic: #914
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trustforge.context_builder import (
    EXCLUSION_APPROVAL_REQUIRED,
    EXCLUSION_OVER_BUDGET,
    EXCLUSION_STALE,
    ContextBuilder,
    ContextManifest,
    ExcludedRef,
    IncludedRefs,
    compute_manifest_hash,
    estimate_tokens,
    get_evidence_eligible_memories,
    manifest_summary,
)
from trustforge.memory_os import MemoryEntry, MemoryRepository
from trustforge.memory_retrieval import MemoryRef
from trustforge.skill_loader import FrozenSkillEntry, FrozenSkillManifest, SkillLoader
from trustforge.skill_registry import (
    SkillRegistryRepository,
    SkillRevision,
    TaskSkill,
    revision_hash_for,
)
from trustforge.tool_registry import ToolCapability, ToolRegistryRepository


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def memory_repo(tmp_db: Path) -> MemoryRepository:
    r = MemoryRepository(db_path=tmp_db / "memory.db")
    r.ensure_schema()
    return r


@pytest.fixture
def skill_registry(tmp_db: Path) -> SkillRegistryRepository:
    r = SkillRegistryRepository(db_path=tmp_db / "skill.db")
    r.ensure_schema()
    return r


@pytest.fixture
def skill_loader(skill_registry: SkillRegistryRepository) -> SkillLoader:
    return SkillLoader(skill_registry)


@pytest.fixture
def tool_registry(tmp_db: Path) -> ToolRegistryRepository:
    r = ToolRegistryRepository(db_path=tmp_db / "tool.db")
    r.ensure_schema()
    return r


@pytest.fixture
def builder(
    memory_repo: MemoryRepository,
    skill_loader: SkillLoader,
    tool_registry: ToolRegistryRepository,
    tmp_db: Path,
) -> ContextBuilder:
    b = ContextBuilder(
        memory_repo=memory_repo,
        skill_loader=skill_loader,
        tool_registry=tool_registry,
        db_path=tmp_db / "context.db",
    )
    yield b
    b.close()


def _make_memory_ref(memory_id: str = "m1", **kwargs) -> MemoryRef:
    defaults = {
        "memory_id": memory_id,
        "kind": "episodic",
        "rank": 1,
        "reason": "test_retrieval",
        "evidence_eligible": True,
        "content_preview": "BTC price data from coingecko",
        "run_id": "run-test",
    }
    defaults.update(kwargs)
    return MemoryRef(**defaults)


# ─── Build Tests ─────────────────────────────────────────────────────────────


class TestBuild:
    def test_build_produces_manifest(self, builder: ContextBuilder):
        manifest = builder.build(
            run_id="run-1",
            snapshot_ref="snap-abc",
            question_ref="q-123",
            token_budget=4096,
        )
        assert manifest.run_id == "run-1"
        assert manifest.manifest_id
        assert manifest.content_hash
        assert len(manifest.content_hash) == 64
        assert manifest.included_refs.snapshot_ref == "snap-abc"
        assert manifest.included_refs.question_ref == "q-123"

    def test_build_with_memory_refs(self, builder: ContextBuilder):
        refs = [
            _make_memory_ref("m1", rank=1),
            _make_memory_ref("m2", rank=2, content_preview="ETH data"),
        ]
        manifest = builder.build(run_id="run-2", memory_refs=refs)

        assert len(manifest.included_refs.memory_refs) == 2
        assert manifest.token_used > 0

    def test_build_with_skill_manifest(
        self, builder: ContextBuilder, skill_registry: SkillRegistryRepository
    ):
        # Create an active skill
        skill_registry.save_skill(
            TaskSkill(skill_id="ctx-skill", family="analysis", name="S", lifecycle="active")
        )
        content = {"rules": ["ctx"], "family": "analysis"}
        skill_registry.save_revision(SkillRevision(
            revision_hash=revision_hash_for(content),
            skill_id="ctx-skill",
            content=content,
            is_active=True,
        ))

        skill_manifest = FrozenSkillManifest(
            run_id="run-3",
            entries=[FrozenSkillEntry(skill_id="ctx-skill", revision_hash=revision_hash_for(content))],
        )
        manifest = builder.build(run_id="run-3", skill_manifest=skill_manifest)
        assert len(manifest.included_refs.skill_refs) == 1

    def test_build_with_tool_refs(
        self, builder: ContextBuilder, tool_registry: ToolRegistryRepository
    ):
        tool_registry.register_tool(
            ToolCapability(tool_id="cg-price", name="CoinGecko", side_effect_class="read_only")
        )
        manifest = builder.build(run_id="run-4", tool_refs=["cg-price"])
        assert len(manifest.included_refs.tool_refs) == 1


# ─── Exclusion Tests ─────────────────────────────────────────────────────────


class TestExclusion:
    def test_over_budget_excluded(self, builder: ContextBuilder):
        # Very low budget, long content
        refs = [
            _make_memory_ref("big", content_preview="x" * 1000),
        ]
        manifest = builder.build(run_id="run-budget", memory_refs=refs, token_budget=10)

        assert len(manifest.included_refs.memory_refs) == 0
        assert len(manifest.excluded_refs) == 1
        assert manifest.excluded_refs[0].reason == EXCLUSION_OVER_BUDGET

    def test_stale_memory_excluded(
        self, builder: ContextBuilder, memory_repo: MemoryRepository
    ):
        # Create an expired entry
        memory_repo.save(MemoryEntry(
            memory_id="expired-1",
            kind="episodic",
            provider="test",
            content_hash="a" * 64,
            content_ref="ref",
            published_at="2026-01-01T00:00:00Z",
            retrieved_at="2026-01-01T00:00:00Z",
            expires_at="2020-01-01T00:00:00Z",  # Past date
            evidence_eligible=True,
        ))

        ref = _make_memory_ref("expired-1")
        manifest = builder.build(run_id="run-stale", memory_refs=[ref])

        assert len(manifest.excluded_refs) == 1
        assert manifest.excluded_refs[0].reason == EXCLUSION_STALE

    def test_unknown_tool_excluded(
        self, builder: ContextBuilder, tool_registry: ToolRegistryRepository
    ):
        manifest = builder.build(run_id="run-unknown-tool", tool_refs=["ghost-tool"])
        assert len(manifest.excluded_refs) == 1
        assert manifest.excluded_refs[0].reason == EXCLUSION_STALE

    def test_approval_required_tool_excluded(
        self, builder: ContextBuilder, tool_registry: ToolRegistryRepository
    ):
        tool_registry.register_tool(ToolCapability(
            tool_id="ext-tool",
            name="External",
            side_effect_class="external_write",
            approval_requirement="always",
        ))
        manifest = builder.build(run_id="run-approval", tool_refs=["ext-tool"])
        assert len(manifest.excluded_refs) == 1
        assert manifest.excluded_refs[0].reason == EXCLUSION_APPROVAL_REQUIRED


# ─── Deterministic Hash Tests ────────────────────────────────────────────────


class TestDeterministicHash:
    def test_same_input_same_hash(self, builder: ContextBuilder):
        refs = [_make_memory_ref("det-1")]
        m1 = builder.build(run_id="run-det-1", memory_refs=refs)

        # Compute hash manually
        expected = compute_manifest_hash(
            "run-det-1",
            m1.included_refs,
            m1.excluded_refs,
            m1.token_budget,
            m1.token_used,
        )
        assert m1.content_hash == expected

    def test_different_input_different_hash(self):
        inc1 = IncludedRefs(snapshot_ref="a")
        inc2 = IncludedRefs(snapshot_ref="b")
        h1 = compute_manifest_hash("run", inc1, [], 4096, 0)
        h2 = compute_manifest_hash("run", inc2, [], 4096, 0)
        assert h1 != h2


# ─── Persistence Tests ───────────────────────────────────────────────────────


class TestPersistence:
    def test_persist_and_retrieve(self, builder: ContextBuilder):
        manifest = builder.build(run_id="run-persist", snapshot_ref="snap")
        loaded = builder.get_manifest("run-persist")

        assert loaded is not None
        assert loaded.run_id == "run-persist"
        assert loaded.content_hash == manifest.content_hash
        assert loaded.included_refs.snapshot_ref == "snap"

    def test_cannot_overwrite_manifest(self, builder: ContextBuilder):
        builder.build(run_id="run-once")
        # Second build with same run_id silently fails (no overwrite)
        builder.build(run_id="run-once", snapshot_ref="different")

        loaded = builder.get_manifest("run-once")
        # First manifest persists (snapshot_ref was None)
        assert loaded.included_refs.snapshot_ref is None

    def test_get_nonexistent_returns_none(self, builder: ContextBuilder):
        assert builder.get_manifest("no-such-run") is None


# ─── Helper Tests ────────────────────────────────────────────────────────────


class TestHelpers:
    def test_manifest_summary(self, builder: ContextBuilder):
        refs = [_make_memory_ref("sum-1"), _make_memory_ref("sum-2", rank=2, content_preview="ETH")]
        manifest = builder.build(run_id="run-summary", memory_refs=refs, snapshot_ref="s")

        summary = manifest_summary(manifest)
        assert summary["included_count"] >= 3  # 2 memory + 1 snapshot
        assert summary["excluded_count"] == 0
        assert summary["token_budget"] == 4096
        assert "token_used_pct" in summary

    def test_get_evidence_eligible_memories(self, builder: ContextBuilder):
        refs = [
            _make_memory_ref("elig-1", evidence_eligible=True),
            _make_memory_ref("elig-2", rank=2, evidence_eligible=False, content_preview="no"),
        ]
        manifest = builder.build(run_id="run-elig", memory_refs=refs)

        eligible = get_evidence_eligible_memories(manifest)
        assert len(eligible) == 1
        assert eligible[0]["memory_id"] == "elig-1"

    def test_estimate_tokens_ascii(self):
        assert estimate_tokens("hello world") >= 2

    def test_estimate_tokens_cjk(self):
        # CJK chars: ~2 chars per token
        assert estimate_tokens("你好世界") >= 2

    def test_estimate_tokens_empty(self):
        assert estimate_tokens("") == 0

    def test_manifest_to_dict_from_dict(self, builder: ContextBuilder):
        manifest = builder.build(run_id="run-serde", snapshot_ref="s")
        data = manifest.to_dict()
        restored = ContextManifest.from_dict(data)
        assert restored.run_id == manifest.run_id
        assert restored.content_hash == manifest.content_hash
