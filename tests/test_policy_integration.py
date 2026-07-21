"""Integration tests: approve → run-freeze → rollback lifecycle.

Verifies:
- Staged (unapproved) revisions do NOT appear in effective policies
- Approved revisions are picked up by the executor
- Rollback correctly switches the active revision
- Run-freeze snapshot captures the correct state at resolution time
- Concurrent approve during a run does not affect the frozen snapshot
"""
from __future__ import annotations

import dataclasses
import json

import pytest

from trustforge.policy.executor import PolicyExecutor
from trustforge.policy.schema import FAMILY_SCHEMA, ReportPolicy, SourcePolicy
from trustforge.skill_changes import approve, rollback, stage
from trustforge.skills import SKILL_FAMILIES, skill_id_for, write_artifact


def _make_artifact(family: str, name: str = "test", policy: dict | None = None) -> dict:
    artifact = {
        "family": family,
        "name": name,
        "rules": [f"rule for {name}"],
    }
    if policy:
        artifact["policy"] = policy
    return artifact


def _setup_all_baselines(root, policy_overrides: dict | None = None) -> dict[str, str]:
    """Write baseline artifacts for all families, return {family: revision_hash}."""
    revisions = {}
    for family in sorted(SKILL_FAMILIES):
        policy = (policy_overrides or {}).get(family)
        artifact = _make_artifact(family, name="baseline", policy=policy)
        revision_hash, _ = write_artifact(artifact, root=root)
        revisions[family] = revision_hash
    return revisions


class TestStagedInvisibleToExecutor:
    """Staged (unapproved) revisions must not affect formal runs."""

    def test_staged_not_visible(self, tmp_path):
        root = tmp_path / "skills"
        log = tmp_path / "changes.jsonl"
        baselines = _setup_all_baselines(root)

        # Stage a new source artifact — NOT approved.
        # We need to approve the baseline first so the system has an explicit
        # active pointer (otherwise with 2 files it can't find "exactly one baseline").
        baseline_art = _make_artifact("source", name="baseline")
        stage(
            skill_id_for("source"),
            json.dumps(baseline_art, sort_keys=True, separators=(",", ":")),
            "baseline seed",
            log_path=log,
        )
        from trustforge.skill_changes import approve as sc_approve
        sc_approve(skill_id_for("source"), baselines["source"], {"seed": True}, log_path=log)

        candidate = _make_artifact("source", name="staged-candidate", policy={"timeout_sec": 99})
        candidate_hash, _ = write_artifact(candidate, root=root)
        stage(
            skill_id_for("source"),
            json.dumps(candidate, sort_keys=True, separators=(",", ":")),
            "staged but not approved",
            log_path=log,
        )

        # Executor should still resolve the approved baseline, not the staged candidate.
        executor = PolicyExecutor(root=root, log_path=log)
        policies = executor.resolve_effective()
        # Baseline has default timeout_sec=30 (no policy override in baseline).
        assert policies["source"].timeout_sec == 30

    def test_staged_then_approved_becomes_visible(self, tmp_path):
        root = tmp_path / "skills"
        log = tmp_path / "changes.jsonl"
        _setup_all_baselines(root)

        # Stage and approve a new source artifact.
        candidate = _make_artifact("source", name="approved-v2", policy={"timeout_sec": 15})
        candidate_hash, _ = write_artifact(candidate, root=root)
        stage(
            skill_id_for("source"),
            json.dumps(candidate, sort_keys=True, separators=(",", ":")),
            "candidate v2",
            log_path=log,
        )
        approve(
            skill_id_for("source"),
            candidate_hash,
            {"sandbox": "passed", "replay": "ok"},
            log_path=log,
        )

        # Executor should now resolve the approved candidate.
        executor = PolicyExecutor(root=root, log_path=log)
        policies = executor.resolve_effective()
        assert policies["source"].timeout_sec == 15


class TestRollbackLifecycle:
    """Rollback correctly reverts to previous approved revision."""

    def test_rollback_restores_previous(self, tmp_path):
        root = tmp_path / "skills"
        log = tmp_path / "changes.jsonl"
        baselines = _setup_all_baselines(root)

        # Approve v2.
        v2 = _make_artifact("report", name="v2", policy={"max_sections": 10})
        v2_hash, _ = write_artifact(v2, root=root)
        stage(skill_id_for("report"), json.dumps(v2, sort_keys=True, separators=(",", ":")), "v2", log_path=log)
        approve(skill_id_for("report"), v2_hash, {"test": "ok"}, log_path=log)

        # Verify v2 is active.
        executor = PolicyExecutor(root=root, log_path=log)
        assert executor.resolve_effective()["report"].max_sections == 10

        # Now approve v3 (to have two approved revisions for rollback target).
        # We need to seed baseline as approved first for rollback target.
        baseline_art = _make_artifact("report", name="baseline")
        stage(
            skill_id_for("report"),
            json.dumps(baseline_art, sort_keys=True, separators=(",", ":")),
            "baseline re-stage",
            log_path=log,
        )
        approve(skill_id_for("report"), baselines["report"], {"test": "ok"}, log_path=log)

        # Rollback to v2.
        rollback(skill_id_for("report"), v2_hash, "v3 regression", log_path=log)

        # Executor should now see v2 again.
        executor2 = PolicyExecutor(root=root, log_path=log)
        assert executor2.resolve_effective()["report"].max_sections == 10


class TestRunFreezeSnapshot:
    """Run-freeze snapshot captures correct state."""

    def test_snapshot_captures_state_at_resolution_time(self, tmp_path):
        root = tmp_path / "skills"
        log = tmp_path / "changes.jsonl"
        _setup_all_baselines(root, policy_overrides={
            "source": {"timeout_sec": 25},
            "analysis": {"max_llm_calls": 5},
        })

        executor = PolicyExecutor(root=root, log_path=log)
        policies = executor.resolve_effective()
        snapshot = executor.snapshot_for_log()

        # Snapshot structure validation.
        assert snapshot["event"] == "policy_snapshot"
        assert snapshot["requires_human_approval"] is True
        assert "policies" in snapshot

        # Each family present with revision + summary.
        for family in SKILL_FAMILIES:
            entry = snapshot["policies"][family]
            assert "revision" in entry
            assert "origin" in entry
            assert entry["origin"] == "baseline"
            assert "policy_summary" in entry

        # Values match what was resolved.
        assert snapshot["policies"]["source"]["policy_summary"]["timeout_sec"] == 25
        assert snapshot["policies"]["analysis"]["policy_summary"]["max_llm_calls"] == 5

    def test_frozen_snapshot_not_affected_by_later_approve(self, tmp_path):
        """Once resolved, the executor's cached state is immutable for the run."""
        root = tmp_path / "skills"
        log = tmp_path / "changes.jsonl"
        _setup_all_baselines(root)

        executor = PolicyExecutor(root=root, log_path=log)
        policies = executor.resolve_effective()
        snapshot_before = executor.snapshot_for_log()

        # Approve a new version AFTER the executor has resolved.
        v2 = _make_artifact("source", name="v2-after-freeze", policy={"timeout_sec": 1})
        v2_hash, _ = write_artifact(v2, root=root)
        stage(skill_id_for("source"), json.dumps(v2, sort_keys=True, separators=(",", ":")), "after freeze", log_path=log)
        approve(skill_id_for("source"), v2_hash, {"test": "ok"}, log_path=log)

        # The executor's cached view should NOT change.
        snapshot_after = executor.snapshot_for_log()
        assert snapshot_before == snapshot_after
        assert executor.resolve_effective()["source"].timeout_sec == 30  # baseline default


class TestExecutorGetPolicy:
    """get_policy accessor tests."""

    def test_get_policy_before_resolve_raises(self, tmp_path):
        root = tmp_path / "skills"
        log = tmp_path / "changes.jsonl"
        _setup_all_baselines(root)

        executor = PolicyExecutor(root=root, log_path=log)
        with pytest.raises(RuntimeError, match="resolve_effective"):
            executor.get_policy("source")

    def test_get_policy_invalid_family(self, tmp_path):
        root = tmp_path / "skills"
        log = tmp_path / "changes.jsonl"
        _setup_all_baselines(root)

        executor = PolicyExecutor(root=root, log_path=log)
        executor.resolve_effective()
        with pytest.raises(ValueError, match="unsupported"):
            executor.get_policy("deploy")

    def test_get_policy_returns_typed_instance(self, tmp_path):
        root = tmp_path / "skills"
        log = tmp_path / "changes.jsonl"
        _setup_all_baselines(root)

        executor = PolicyExecutor(root=root, log_path=log)
        executor.resolve_effective()
        assert isinstance(executor.get_policy("source"), SourcePolicy)
        assert isinstance(executor.get_policy("report"), ReportPolicy)
