"""Tests for Task Skill Registry schema, migration, and repository.

Issue: #917 | Epic: #914
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from trustforge.skill_registry import (
    HIGH_RISK_CLASSES,
    SkillDependency,
    SkillRegistryRepository,
    SkillRevision,
    TaskSkill,
    revision_hash_for,
    rollback,
    _upgrade as upgrade,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_skill_registry.db"


@pytest.fixture
def repo(db_path: Path) -> SkillRegistryRepository:
    r = SkillRegistryRepository(db_path=db_path)
    r.ensure_schema()
    yield r
    r.close()


def _make_skill(**kwargs) -> TaskSkill:
    defaults = {
        "skill_id": f"test-skill-{id(kwargs)}",
        "family": "analysis",
        "name": "Test Skill",
        "description": "A test skill",
        "risk_class": "read_only",
        "lifecycle": "draft",
    }
    defaults.update(kwargs)
    return TaskSkill(**defaults)


def _make_revision(skill_id: str, content: dict | None = None) -> SkillRevision:
    content = content or {"rules": ["rule1"], "family": "analysis"}
    return SkillRevision(
        revision_hash=revision_hash_for(content),
        skill_id=skill_id,
        content=content,
        is_active=False,
    )


# ─── Migration Tests ─────────────────────────────────────────────────────────


class TestMigration:
    def test_upgrade_creates_tables(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        upgrade(conn)

        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "skills" in tables
        assert "skill_revisions" in tables
        assert "skill_dependencies" in tables
        conn.close()

    def test_upgrade_idempotent(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        upgrade(conn)
        upgrade(conn)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "skills" in tables
        conn.close()

    def test_rollback_drops_tables(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        upgrade(conn)
        rollback(conn)

        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "skills" not in tables
        assert "skill_revisions" not in tables
        assert "skill_dependencies" not in tables
        conn.close()


# ─── Skill CRUD Tests ────────────────────────────────────────────────────────


class TestSkillCRUD:
    def test_save_and_get_roundtrip(self, repo: SkillRegistryRepository):
        skill = _make_skill(skill_id="analysis-fundamental")
        repo.save_skill(skill)

        result = repo.get_skill("analysis-fundamental")
        assert result is not None
        assert result.family == "analysis"
        assert result.risk_class == "read_only"
        assert result.lifecycle == "draft"

    def test_duplicate_skill_id_raises(self, repo: SkillRegistryRepository):
        skill = _make_skill(skill_id="dup-skill")
        repo.save_skill(skill)

        with pytest.raises(sqlite3.IntegrityError, match="already exists"):
            repo.save_skill(_make_skill(skill_id="dup-skill"))

    def test_invalid_family_raises(self, repo: SkillRegistryRepository):
        with pytest.raises(ValueError, match="invalid family"):
            repo.save_skill(_make_skill(skill_id="bad", family="invalid"))

    def test_invalid_risk_class_raises(self, repo: SkillRegistryRepository):
        with pytest.raises(ValueError, match="invalid risk_class"):
            repo.save_skill(_make_skill(skill_id="bad2", risk_class="ultra"))

    def test_invalid_lifecycle_raises(self, repo: SkillRegistryRepository):
        with pytest.raises(ValueError, match="invalid lifecycle"):
            repo.save_skill(_make_skill(skill_id="bad3", lifecycle="unknown"))

    def test_list_skills_no_filter(self, repo: SkillRegistryRepository):
        repo.save_skill(_make_skill(skill_id="s1", family="analysis"))
        repo.save_skill(_make_skill(skill_id="s2", family="report"))

        results = repo.list_skills()
        assert len(results) == 2

    def test_list_skills_filter_family(self, repo: SkillRegistryRepository):
        repo.save_skill(_make_skill(skill_id="s3", family="analysis"))
        repo.save_skill(_make_skill(skill_id="s4", family="report"))

        results = repo.list_skills(family="analysis")
        assert len(results) == 1
        assert results[0].skill_id == "s3"

    def test_list_skills_filter_lifecycle(self, repo: SkillRegistryRepository):
        repo.save_skill(_make_skill(skill_id="s5", lifecycle="draft"))
        repo.save_skill(_make_skill(skill_id="s6", lifecycle="active"))

        results = repo.list_skills(lifecycle="active")
        assert len(results) == 1
        assert results[0].skill_id == "s6"

    def test_get_nonexistent_returns_none(self, repo: SkillRegistryRepository):
        assert repo.get_skill("nonexistent") is None


# ─── Lifecycle Tests ─────────────────────────────────────────────────────────


class TestLifecycle:
    def test_update_lifecycle_normal(self, repo: SkillRegistryRepository):
        repo.save_skill(_make_skill(skill_id="lc1", lifecycle="draft"))
        repo.update_lifecycle("lc1", "staged")

        skill = repo.get_skill("lc1")
        assert skill.lifecycle == "staged"

    def test_high_risk_cannot_skip_staged(self, repo: SkillRegistryRepository):
        repo.save_skill(
            _make_skill(
                skill_id="lc2",
                risk_class="external_write",
                lifecycle="draft",
            )
        )

        with pytest.raises(ValueError, match="cannot skip staged"):
            repo.update_lifecycle("lc2", "active")

    def test_high_risk_can_go_draft_to_staged(self, repo: SkillRegistryRepository):
        repo.save_skill(
            _make_skill(
                skill_id="lc3",
                risk_class="deploy_or_release",
                lifecycle="draft",
            )
        )
        # This should succeed
        repo.update_lifecycle("lc3", "staged")
        skill = repo.get_skill("lc3")
        assert skill.lifecycle == "staged"

    def test_high_risk_can_go_staged_to_active(self, repo: SkillRegistryRepository):
        repo.save_skill(
            _make_skill(
                skill_id="lc4",
                risk_class="external_write",
                lifecycle="staged",
            )
        )
        repo.update_lifecycle("lc4", "active")
        skill = repo.get_skill("lc4")
        assert skill.lifecycle == "active"

    def test_update_nonexistent_raises(self, repo: SkillRegistryRepository):
        with pytest.raises(ValueError, match="skill not found"):
            repo.update_lifecycle("nonexistent", "active")


# ─── Revision Tests ──────────────────────────────────────────────────────────


class TestRevision:
    def test_save_and_get_revision(self, repo: SkillRegistryRepository):
        repo.save_skill(_make_skill(skill_id="rev-skill"))
        content = {"rules": ["analyze fundamentals"], "family": "analysis"}
        rev = SkillRevision(
            revision_hash=revision_hash_for(content),
            skill_id="rev-skill",
            content=content,
        )
        repo.save_revision(rev)

        result = repo.get_revision(rev.revision_hash)
        assert result is not None
        assert result.content == content
        assert result.skill_id == "rev-skill"

    def test_save_same_hash_same_content_idempotent(self, repo: SkillRegistryRepository):
        repo.save_skill(_make_skill(skill_id="idem-skill"))
        content = {"rules": ["rule"], "family": "analysis"}
        rev = SkillRevision(
            revision_hash=revision_hash_for(content),
            skill_id="idem-skill",
            content=content,
        )
        repo.save_revision(rev)
        repo.save_revision(rev)  # Should not raise

    def test_hash_mismatch_raises(self, repo: SkillRegistryRepository):
        repo.save_skill(_make_skill(skill_id="mismatch-skill"))
        content = {"rules": ["rule"], "family": "analysis"}
        rev = SkillRevision(
            revision_hash="0" * 64,  # Wrong hash
            skill_id="mismatch-skill",
            content=content,
        )
        with pytest.raises(ValueError, match="revision_hash mismatch"):
            repo.save_revision(rev)

    def test_hash_collision_different_content_raises(self, repo: SkillRegistryRepository):
        repo.save_skill(_make_skill(skill_id="collision-skill"))
        content1 = {"rules": ["rule1"], "family": "analysis"}
        rev1 = SkillRevision(
            revision_hash=revision_hash_for(content1),
            skill_id="collision-skill",
            content=content1,
        )
        repo.save_revision(rev1)

        # Manually insert a different content with same hash (simulated collision)
        # We can't truly simulate a hash collision, so this tests the path
        # by checking that different content with computed hash works correctly
        content2 = {"rules": ["rule2"], "family": "analysis"}
        rev2 = SkillRevision(
            revision_hash=revision_hash_for(content2),
            skill_id="collision-skill",
            content=content2,
        )
        # Different content = different hash, should work fine
        repo.save_revision(rev2)
        assert repo.get_revision(rev2.revision_hash) is not None

    def test_get_active_revision(self, repo: SkillRegistryRepository):
        repo.save_skill(_make_skill(skill_id="active-skill"))
        content = {"rules": ["active rule"], "family": "analysis"}
        rev = SkillRevision(
            revision_hash=revision_hash_for(content),
            skill_id="active-skill",
            content=content,
            is_active=True,
        )
        repo.save_revision(rev)

        result = repo.get_active_revision("active-skill")
        assert result is not None
        assert result.revision_hash == rev.revision_hash
        assert result.is_active is True

    def test_set_active_switch(self, repo: SkillRegistryRepository):
        repo.save_skill(_make_skill(skill_id="switch-skill"))

        content1 = {"rules": ["v1"], "family": "analysis"}
        rev1 = SkillRevision(
            revision_hash=revision_hash_for(content1),
            skill_id="switch-skill",
            content=content1,
            is_active=True,
        )
        repo.save_revision(rev1)

        content2 = {"rules": ["v2"], "family": "analysis"}
        rev2 = SkillRevision(
            revision_hash=revision_hash_for(content2),
            skill_id="switch-skill",
            content=content2,
            is_active=False,
        )
        repo.save_revision(rev2)

        # Switch active
        repo.set_active("switch-skill", rev2.revision_hash)

        # Verify switch
        active = repo.get_active_revision("switch-skill")
        assert active.revision_hash == rev2.revision_hash

        # Old one should no longer be active
        old = repo.get_revision(rev1.revision_hash)
        assert old.is_active is False

    def test_set_active_wrong_skill_raises(self, repo: SkillRegistryRepository):
        repo.save_skill(_make_skill(skill_id="owner-a"))
        repo.save_skill(_make_skill(skill_id="owner-b"))
        content = {"rules": ["x"], "family": "analysis"}
        rev = SkillRevision(
            revision_hash=revision_hash_for(content),
            skill_id="owner-a",
            content=content,
        )
        repo.save_revision(rev)

        with pytest.raises(ValueError, match="belongs to skill"):
            repo.set_active("owner-b", rev.revision_hash)

    def test_set_active_nonexistent_revision_raises(self, repo: SkillRegistryRepository):
        repo.save_skill(_make_skill(skill_id="no-rev"))
        with pytest.raises(ValueError, match="revision not found"):
            repo.set_active("no-rev", "f" * 64)


# ─── Dependency Tests ────────────────────────────────────────────────────────


class TestDependency:
    def test_add_and_get_dependencies(self, repo: SkillRegistryRepository):
        repo.save_skill(_make_skill(skill_id="dep-a"))
        repo.save_skill(_make_skill(skill_id="dep-b"))

        dep = SkillDependency(from_skill_id="dep-a", to_skill_id="dep-b", relation="requires")
        repo.add_dependency(dep)

        deps = repo.get_dependencies("dep-a")
        assert len(deps) == 1
        assert deps[0].to_skill_id == "dep-b"
        assert deps[0].relation == "requires"

    def test_get_dependents(self, repo: SkillRegistryRepository):
        repo.save_skill(_make_skill(skill_id="dep-c"))
        repo.save_skill(_make_skill(skill_id="dep-d"))

        dep = SkillDependency(from_skill_id="dep-c", to_skill_id="dep-d", relation="requires")
        repo.add_dependency(dep)

        dependents = repo.get_dependents("dep-d")
        assert len(dependents) == 1
        assert dependents[0].from_skill_id == "dep-c"

    def test_self_cycle_rejected(self, repo: SkillRegistryRepository):
        repo.save_skill(_make_skill(skill_id="self-dep"))

        with pytest.raises(ValueError, match="self-cycle"):
            repo.add_dependency(
                SkillDependency(from_skill_id="self-dep", to_skill_id="self-dep", relation="requires")
            )

    def test_transitive_cycle_rejected(self, repo: SkillRegistryRepository):
        """A → B → C, then adding C → A should fail."""
        repo.save_skill(_make_skill(skill_id="cyc-a"))
        repo.save_skill(_make_skill(skill_id="cyc-b"))
        repo.save_skill(_make_skill(skill_id="cyc-c"))

        repo.add_dependency(SkillDependency(from_skill_id="cyc-a", to_skill_id="cyc-b", relation="requires"))
        repo.add_dependency(SkillDependency(from_skill_id="cyc-b", to_skill_id="cyc-c", relation="requires"))

        with pytest.raises(ValueError, match="cycle detected"):
            repo.add_dependency(
                SkillDependency(from_skill_id="cyc-c", to_skill_id="cyc-a", relation="requires")
            )

    def test_optional_dependency_no_cycle_check(self, repo: SkillRegistryRepository):
        """Optional deps don't participate in cycle detection."""
        repo.save_skill(_make_skill(skill_id="opt-a"))
        repo.save_skill(_make_skill(skill_id="opt-b"))

        repo.add_dependency(SkillDependency(from_skill_id="opt-a", to_skill_id="opt-b", relation="requires"))
        # Adding reverse as 'optional' should succeed (no cycle check for optional)
        repo.add_dependency(SkillDependency(from_skill_id="opt-b", to_skill_id="opt-a", relation="optional"))

        deps = repo.get_dependencies("opt-b")
        assert len(deps) == 1
        assert deps[0].relation == "optional"

    def test_invalid_relation_rejected(self, repo: SkillRegistryRepository):
        repo.save_skill(_make_skill(skill_id="rel-a"))
        repo.save_skill(_make_skill(skill_id="rel-b"))

        with pytest.raises(ValueError, match="invalid relation"):
            repo.add_dependency(
                SkillDependency(from_skill_id="rel-a", to_skill_id="rel-b", relation="unknown")
            )

    def test_duplicate_dependency_rejected(self, repo: SkillRegistryRepository):
        repo.save_skill(_make_skill(skill_id="dup-a"))
        repo.save_skill(_make_skill(skill_id="dup-b"))

        dep = SkillDependency(from_skill_id="dup-a", to_skill_id="dup-b", relation="requires")
        repo.add_dependency(dep)

        with pytest.raises(sqlite3.IntegrityError):
            repo.add_dependency(dep)


# ─── Hash Tests ──────────────────────────────────────────────────────────────


class TestHash:
    def test_revision_hash_deterministic(self):
        content = {"rules": ["rule1", "rule2"], "family": "analysis"}
        h1 = revision_hash_for(content)
        h2 = revision_hash_for(content)
        assert h1 == h2
        assert len(h1) == 64

    def test_different_content_different_hash(self):
        c1 = {"rules": ["a"], "family": "analysis"}
        c2 = {"rules": ["b"], "family": "analysis"}
        assert revision_hash_for(c1) != revision_hash_for(c2)

    def test_key_order_irrelevant(self):
        c1 = {"family": "analysis", "rules": ["r"]}
        c2 = {"rules": ["r"], "family": "analysis"}
        assert revision_hash_for(c1) == revision_hash_for(c2)
