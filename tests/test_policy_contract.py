"""Contract tests: schema ↔ compiler ↔ consumer consistency.

Verifies that:
- Every FAMILY_SCHEMA entry can be compiled from a minimal valid artifact
- Compiled policies have the expected frozen dataclass fields
- Consumers (executor) can read typed fields via attribute access
- Round-trip: compile → asdict → compile produces identical objects
"""
from __future__ import annotations

import dataclasses
import json

import pytest

from trustforge.policy.compiler import compile_policy
from trustforge.policy.executor import PolicyExecutor
from trustforge.policy.guards import SecurityError
from trustforge.policy.schema import (
    FAMILY_SCHEMA,
    AnalysisPolicy,
    EvaluationPolicy,
    ImprovementPolicy,
    ReportPolicy,
    SourcePolicy,
)
from trustforge.skills import SKILL_FAMILIES, write_artifact


def _make_artifact(family: str, policy: dict | None = None) -> dict:
    """Create a minimal valid artifact for a given family."""
    artifact = {
        "family": family,
        "name": f"test-{family}",
        "rules": [f"baseline rule for {family}"],
    }
    if policy:
        artifact["policy"] = policy
    return artifact


class TestSchemaCompilerContract:
    """Every family in FAMILY_SCHEMA can be compiled with defaults."""

    def test_all_families_have_schema(self):
        """SKILL_FAMILIES and FAMILY_SCHEMA cover the same set."""
        assert set(FAMILY_SCHEMA) == SKILL_FAMILIES

    @pytest.mark.parametrize("family", sorted(FAMILY_SCHEMA))
    def test_compile_default(self, family: str):
        """Compile with no policy overrides → defaults from dataclass."""
        artifact = _make_artifact(family)
        policy = compile_policy(artifact)
        schema_cls = FAMILY_SCHEMA[family]
        assert isinstance(policy, schema_cls)
        # Frozen: cannot mutate via normal attribute assignment.
        first_field = dataclasses.fields(schema_cls)[0].name
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(policy, first_field, None)

    @pytest.mark.parametrize("family", sorted(FAMILY_SCHEMA))
    def test_compile_with_overrides(self, family: str):
        """Compile with valid policy overrides → fields updated."""
        schema_cls = FAMILY_SCHEMA[family]
        fields = dataclasses.fields(schema_cls)
        # Pick the first int/float field and override it.
        overrides = {}
        for f in fields:
            if f.type == "int" or f.type is int:
                overrides[f.name] = 99
                break
            elif f.type == "float" or f.type is float:
                overrides[f.name] = 0.99
                break
            elif f.type == "bool" or f.type is bool:
                overrides[f.name] = not f.default
                break
        if not overrides:
            pytest.skip("no suitable field for override test")
        artifact = _make_artifact(family, policy=overrides)
        policy = compile_policy(artifact)
        for key, val in overrides.items():
            assert getattr(policy, key) == val

    @pytest.mark.parametrize("family", sorted(FAMILY_SCHEMA))
    def test_round_trip_asdict(self, family: str):
        """compile → asdict → compile produces identical object."""
        artifact = _make_artifact(family)
        policy1 = compile_policy(artifact)
        # Simulate round-trip via JSON.
        policy_dict = dataclasses.asdict(policy1)
        artifact2 = _make_artifact(family, policy=policy_dict)
        policy2 = compile_policy(artifact2)
        assert policy1 == policy2


class TestCompilerValidation:
    """Compiler rejects invalid inputs."""

    def test_unsupported_family(self):
        with pytest.raises(ValueError, match="unsupported policy family"):
            compile_policy({"family": "unknown", "name": "x", "rules": ["r"]})

    def test_wrong_field_type(self):
        artifact = _make_artifact("source", policy={"timeout_sec": "not_an_int"})
        with pytest.raises(ValueError, match="expects int"):
            compile_policy(artifact)

    def test_unknown_policy_fields_ignored(self):
        """Forward-compatible: unknown keys in 'policy' are silently ignored."""
        artifact = _make_artifact("source", policy={"future_field": 42})
        policy = compile_policy(artifact)
        assert isinstance(policy, SourcePolicy)
        assert not hasattr(policy, "future_field")


class TestConsumerContract:
    """Consumers access typed policy fields via attribute access."""

    def test_source_policy_fields(self):
        policy = compile_policy(_make_artifact("source", policy={"timeout_sec": 15, "max_concurrent": 3}))
        assert policy.timeout_sec == 15
        assert policy.max_concurrent == 3
        assert policy.retry_limit == 2  # default

    def test_analysis_policy_fields(self):
        policy = compile_policy(_make_artifact("analysis", policy={"claim_extraction_budget": 20}))
        assert policy.claim_extraction_budget == 20
        assert policy.contrarian_search_enabled is True  # default

    def test_report_policy_fields(self):
        policy = compile_policy(_make_artifact("report", policy={"language": "en"}))
        assert policy.language == "en"
        assert policy.max_sections == 6  # default

    def test_evaluation_policy_fields(self):
        policy = compile_policy(_make_artifact("evaluation", policy={"min_pass_score": 0.8}))
        assert policy.min_pass_score == 0.8

    def test_improvement_policy_fields(self):
        policy = compile_policy(_make_artifact("improvement", policy={"proposal_limit": 5}))
        assert policy.proposal_limit == 5
        assert policy.auto_stage is False  # default, never auto-approve


class TestExecutorIntegration:
    """PolicyExecutor resolves and produces snapshots (with real artifacts on disk)."""

    def test_resolve_effective_from_baselines(self, tmp_path):
        """Executor resolves all families from baseline artifacts."""
        root = tmp_path / "skills"
        log = tmp_path / "changes.jsonl"

        for family in sorted(SKILL_FAMILIES):
            artifact = _make_artifact(family)
            write_artifact(artifact, root=root)

        executor = PolicyExecutor(root=root, log_path=log)
        policies = executor.resolve_effective()

        assert set(policies) == SKILL_FAMILIES
        for family, policy in policies.items():
            assert isinstance(policy, FAMILY_SCHEMA[family])

    def test_snapshot_for_log(self, tmp_path):
        """Snapshot contains revision hash and policy_summary per family."""
        root = tmp_path / "skills"
        log = tmp_path / "changes.jsonl"

        for family in sorted(SKILL_FAMILIES):
            write_artifact(_make_artifact(family), root=root)

        executor = PolicyExecutor(root=root, log_path=log)
        executor.resolve_effective()
        snapshot = executor.snapshot_for_log()

        assert snapshot["event"] == "policy_snapshot"
        assert snapshot["requires_human_approval"] is True
        assert set(snapshot["policies"]) == SKILL_FAMILIES

        for family, entry in snapshot["policies"].items():
            assert "revision" in entry
            assert len(entry["revision"]) == 64  # SHA-256 hex
            assert "origin" in entry
            assert "policy_summary" in entry
            assert isinstance(entry["policy_summary"], dict)
