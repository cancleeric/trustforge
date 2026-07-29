"""Tests for Skill Loader — discovery, frozen manifest, activation governance.

Issue: #920 | Epic: #914
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trustforge.skill_loader import (
    ActivationProposal,
    FrozenSkillManifest,
    SkillLoader,
)
from trustforge.skill_registry import (
    SkillDependency,
    SkillRegistryRepository,
    SkillRevision,
    TaskSkill,
    revision_hash_for,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_skill_loader.db"


@pytest.fixture
def registry(db_path: Path) -> SkillRegistryRepository:
    r = SkillRegistryRepository(db_path=db_path)
    r.ensure_schema()
    return r


@pytest.fixture
def loader(registry: SkillRegistryRepository) -> SkillLoader:
    return SkillLoader(registry)


def _create_active_skill(
    registry: SkillRegistryRepository,
    skill_id: str,
    *,
    family: str = "analysis",
    risk_class: str = "read_only",
    lifecycle: str = "active",
    content: dict | None = None,
) -> SkillRevision:
    """Helper: create a skill with an active revision."""
    registry.save_skill(
        TaskSkill(
            skill_id=skill_id,
            family=family,
            name=f"Skill {skill_id}",
            risk_class=risk_class,
            lifecycle=lifecycle,
        )
    )
    content = content or {"rules": [f"rule for {skill_id}"], "family": family}
    rev = SkillRevision(
        revision_hash=revision_hash_for(content),
        skill_id=skill_id,
        content=content,
        is_active=True,
    )
    registry.save_revision(rev)
    return rev


# ─── Discovery Tests ─────────────────────────────────────────────────────────


class TestDiscovery:
    def test_discover_active_only(self, registry, loader):
        _create_active_skill(registry, "s-active")
        registry.save_skill(
            TaskSkill(skill_id="s-draft", family="analysis", name="Draft", lifecycle="draft")
        )

        results = loader.discover()
        assert len(results) == 1
        assert results[0].skill_id == "s-active"

    def test_discover_filter_family(self, registry, loader):
        _create_active_skill(registry, "s-analysis", family="analysis")
        _create_active_skill(registry, "s-report", family="report",
                             content={"rules": ["r"], "family": "report"})

        results = loader.discover(family="report")
        assert len(results) == 1
        assert results[0].skill_id == "s-report"

    def test_discover_filter_risk_class(self, registry, loader):
        _create_active_skill(registry, "s-ro", risk_class="read_only")
        _create_active_skill(registry, "s-lw", risk_class="local_write",
                             content={"rules": ["lw"], "family": "analysis"})

        results = loader.discover(risk_class="local_write")
        assert len(results) == 1
        assert results[0].skill_id == "s-lw"


# ─── Dependency Resolution Tests ────────────────────────────────────────────


class TestDependencyResolution:
    def test_resolve_single_skill(self, registry, loader):
        rev = _create_active_skill(registry, "single")
        result = loader.resolve_dependencies("single")
        assert len(result) == 1
        assert result[0].skill_id == "single"

    def test_resolve_with_dependency(self, registry, loader):
        _create_active_skill(registry, "dep-leaf", content={"rules": ["leaf"], "family": "analysis"})
        _create_active_skill(registry, "dep-root", content={"rules": ["root"], "family": "analysis"})
        registry.add_dependency(
            SkillDependency(from_skill_id="dep-root", to_skill_id="dep-leaf", relation="requires")
        )

        result = loader.resolve_dependencies("dep-root")
        # Leaf-first order
        assert len(result) == 2
        assert result[0].skill_id == "dep-leaf"
        assert result[1].skill_id == "dep-root"

    def test_resolve_stale_dependency_raises(self, registry, loader):
        # Create a skill without active revision
        registry.save_skill(
            TaskSkill(skill_id="stale-dep", family="analysis", name="Stale", lifecycle="active")
        )
        _create_active_skill(registry, "depends-on-stale",
                             content={"rules": ["x"], "family": "analysis"})
        registry.add_dependency(
            SkillDependency(from_skill_id="depends-on-stale", to_skill_id="stale-dep", relation="requires")
        )

        with pytest.raises(ValueError, match="stale dependency"):
            loader.resolve_dependencies("depends-on-stale")


# ─── Stale Detection Tests ──────────────────────────────────────────────────


class TestStaleDetection:
    def test_active_with_revision_not_stale(self, registry, loader):
        _create_active_skill(registry, "healthy")
        assert loader.is_stale("healthy") is False

    def test_frozen_lifecycle_is_stale(self, registry, loader):
        registry.save_skill(
            TaskSkill(skill_id="frozen-s", family="analysis", name="F", lifecycle="frozen")
        )
        assert loader.is_stale("frozen-s") is True

    def test_retired_lifecycle_is_stale(self, registry, loader):
        registry.save_skill(
            TaskSkill(skill_id="retired-s", family="analysis", name="R", lifecycle="retired")
        )
        assert loader.is_stale("retired-s") is True

    def test_no_active_revision_is_stale(self, registry, loader):
        registry.save_skill(
            TaskSkill(skill_id="no-rev", family="analysis", name="NR", lifecycle="active")
        )
        assert loader.is_stale("no-rev") is True

    def test_stale_transitive_dep(self, registry, loader):
        # healthy depends on stale
        registry.save_skill(
            TaskSkill(skill_id="stale-trans", family="analysis", name="ST", lifecycle="retired")
        )
        _create_active_skill(registry, "has-stale-dep",
                             content={"rules": ["y"], "family": "analysis"})
        registry.add_dependency(
            SkillDependency(from_skill_id="has-stale-dep", to_skill_id="stale-trans", relation="requires")
        )
        assert loader.is_stale("has-stale-dep") is True

    def test_nonexistent_skill_is_stale(self, registry, loader):
        assert loader.is_stale("ghost") is True


# ─── Frozen Manifest Tests ───────────────────────────────────────────────────


class TestFrozenManifest:
    def test_freeze_captures_hashes(self, registry, loader):
        rev = _create_active_skill(registry, "freeze-me")
        manifest = loader.freeze_manifest("run-1", ["freeze-me"])

        assert manifest.run_id == "run-1"
        assert len(manifest.entries) == 1
        assert manifest.entries[0].skill_id == "freeze-me"
        assert manifest.entries[0].revision_hash == rev.revision_hash

    def test_freeze_includes_transitive_deps(self, registry, loader):
        _create_active_skill(registry, "f-leaf", content={"rules": ["fl"], "family": "analysis"})
        _create_active_skill(registry, "f-root", content={"rules": ["fr"], "family": "analysis"})
        registry.add_dependency(
            SkillDependency(from_skill_id="f-root", to_skill_id="f-leaf", relation="requires")
        )

        manifest = loader.freeze_manifest("run-2", ["f-root"])
        skill_ids = [e.skill_id for e in manifest.entries]
        assert "f-leaf" in skill_ids
        assert "f-root" in skill_ids

    def test_freeze_stale_skill_raises(self, registry, loader):
        registry.save_skill(
            TaskSkill(skill_id="stale-freeze", family="analysis", name="SF", lifecycle="retired")
        )
        with pytest.raises(ValueError, match="cannot freeze stale"):
            loader.freeze_manifest("run-3", ["stale-freeze"])

    def test_frozen_manifest_survives_active_change(self, registry, loader):
        content1 = {"rules": ["v1"], "family": "analysis"}
        _create_active_skill(registry, "mutable", content=content1)
        manifest = loader.freeze_manifest("run-4", ["mutable"])

        # Switch active to v2
        content2 = {"rules": ["v2"], "family": "analysis"}
        rev2 = SkillRevision(
            revision_hash=revision_hash_for(content2),
            skill_id="mutable",
            content=content2,
        )
        registry.save_revision(rev2)
        registry.set_active("mutable", rev2.revision_hash)

        # Frozen manifest still has v1
        loaded = loader.get_frozen_manifest("run-4")
        assert loaded is not None
        assert loaded.entries[0].revision_hash == revision_hash_for(content1)

    def test_freeze_high_risk_without_approval_raises(self, registry, loader):
        registry.save_skill(
            TaskSkill(
                skill_id="hr-no-approve",
                family="analysis",
                name="HR",
                risk_class="external_write",
                lifecycle="active",
            )
        )
        content = {"rules": ["hr"], "family": "analysis"}
        rev = SkillRevision(
            revision_hash=revision_hash_for(content),
            skill_id="hr-no-approve",
            content=content,
            is_active=True,
        )
        registry.save_revision(rev)

        with pytest.raises(ValueError, match="requires activation approval"):
            loader.freeze_manifest("run-5", ["hr-no-approve"])

    def test_get_frozen_manifest_nonexistent(self, registry, loader):
        assert loader.get_frozen_manifest("no-such-run") is None

    def test_freeze_with_reasons(self, registry, loader):
        _create_active_skill(registry, "reason-skill",
                             content={"rules": ["reason"], "family": "analysis"})
        manifest = loader.freeze_manifest(
            "run-6", ["reason-skill"], reasons={"reason-skill": "selected_by_trigger"}
        )
        assert manifest.entries[0].reason == "selected_by_trigger"


# ─── Activation Governance Tests ─────────────────────────────────────────────


class TestActivationGovernance:
    def test_propose_high_risk(self, registry, loader):
        registry.save_skill(
            TaskSkill(
                skill_id="hr-propose",
                family="analysis",
                name="HR",
                risk_class="deploy_or_release",
                lifecycle="active",
            )
        )
        proposal = loader.propose_activation("hr-propose", "hash123", "need deployment")
        assert proposal.status == "pending"
        assert proposal.skill_id == "hr-propose"

    def test_propose_non_high_risk_raises(self, registry, loader):
        _create_active_skill(registry, "lr-propose")
        with pytest.raises(ValueError, match="not high-risk"):
            loader.propose_activation("lr-propose", "hash", "reason")

    def test_approve_activation(self, registry, loader):
        registry.save_skill(
            TaskSkill(
                skill_id="hr-approve",
                family="analysis",
                name="HR",
                risk_class="external_write",
                lifecycle="active",
            )
        )
        content = {"rules": ["ap"], "family": "analysis"}
        rev_hash = revision_hash_for(content)
        registry.save_revision(SkillRevision(
            revision_hash=rev_hash, skill_id="hr-approve", content=content, is_active=True,
        ))

        proposal = loader.propose_activation("hr-approve", rev_hash, "needed")
        loader.approve_activation(proposal.proposal_id, "eric", sandbox_passed=True)

        assert loader.is_activation_approved("hr-approve", rev_hash) is True

    def test_reject_activation(self, registry, loader):
        registry.save_skill(
            TaskSkill(
                skill_id="hr-reject",
                family="analysis",
                name="HR",
                risk_class="external_write",
                lifecycle="active",
            )
        )
        proposal = loader.propose_activation("hr-reject", "hash456", "test")
        loader.reject_activation(proposal.proposal_id, "eric", "too risky")

        assert loader.is_activation_approved("hr-reject", "hash456") is False

    def test_approve_nonexistent_raises(self, registry, loader):
        with pytest.raises(ValueError, match="proposal not found"):
            loader.approve_activation("nonexistent-id", "eric")

    def test_double_approve_raises(self, registry, loader):
        registry.save_skill(
            TaskSkill(
                skill_id="hr-double",
                family="analysis",
                name="HR",
                risk_class="external_write",
                lifecycle="active",
            )
        )
        proposal = loader.propose_activation("hr-double", "hash789", "test")
        loader.approve_activation(proposal.proposal_id, "eric", sandbox_passed=True)

        with pytest.raises(ValueError, match="already approved"):
            loader.approve_activation(proposal.proposal_id, "eric", sandbox_passed=True)

    def test_freeze_after_approval_succeeds(self, registry, loader):
        registry.save_skill(
            TaskSkill(
                skill_id="hr-freeze-ok",
                family="analysis",
                name="HR",
                risk_class="external_write",
                lifecycle="active",
            )
        )
        content = {"rules": ["freeze-ok"], "family": "analysis"}
        rev_hash = revision_hash_for(content)
        registry.save_revision(SkillRevision(
            revision_hash=rev_hash, skill_id="hr-freeze-ok", content=content, is_active=True,
        ))

        proposal = loader.propose_activation("hr-freeze-ok", rev_hash, "approved")
        loader.approve_activation(proposal.proposal_id, "eric", sandbox_passed=True)

        # Now freeze should succeed
        manifest = loader.freeze_manifest("run-hr", ["hr-freeze-ok"])
        assert len(manifest.entries) == 1
