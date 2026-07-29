"""Read-only aggregate dashboard regressions for Issue #871."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import trustforge.agent.shadow_dashboard as dashboard_module
from trustforge.agent.shadow_contracts import (
    CONTRACT_VERSION,
    ShadowInput,
    ShadowObservation,
    ShadowReleaseIdentity,
    input_digest,
    load_policy,
    policy_digest,
    to_dict,
)
from trustforge.agent.shadow_evidence_store import ShadowEvidenceStore

NOW = "2026-07-28T01:00:00+00:00"


def _identity() -> ShadowReleaseIdentity:
    policy = load_policy()
    return ShadowReleaseIdentity(
        active_release="release:legacy@1.2.3",
        candidate_release="release:kernel@2.0.0-rc1",
        active_artifact_digest="sha256:" + "a" * 64,
        candidate_artifact_digest="sha256:" + "b" * 64,
        policy_digest=policy_digest(policy),
        contract_version=CONTRACT_VERSION,
    )


def _observations(identity, *, with_intrinsic=slice(0, 0)) -> list[ShadowObservation]:
    base = datetime(2026, 7, 28, tzinfo=timezone.utc)
    observations: list[ShadowObservation] = []
    for index in range(30):
        canonical_input = ShadowInput(
            request_id=f"request-{index}",
            coin=("BTC", "ETH", "SOL")[index % 3],
            question_type=("analysis", "hypothesis")[index % 2],
            pit_epoch=base.timestamp() + index,
            query=f"query-{index}",
        )
        observation = ShadowObservation(
            release_identity=identity,
            canonical_input=canonical_input,
            input_digest=input_digest(to_dict(canonical_input)),
            observed_at=(base + timedelta(minutes=index)).isoformat(),
            status="success",
            parity_passed=True,
            confidence_delta=0.01,
            trust_delta=0.01,
            supporting_jaccard=0.9,
            elapsed_ms=100,
            provider_calls=0,
            cost_usd=0,
            claim_ids=(f"claim-{index}",),
        )
        if with_intrinsic.start <= index < with_intrinsic.stop:
            observation = replace(
                observation,
                intrinsic_shadow={
                    "total_delta": 0.02,
                    "trust_delta": 0.01,
                    "gate": {"passed": index % 2 == 0},
                },
            )
        observations.append(observation)
    return observations


def _prepare(monkeypatch, tmp_path, observations):
    path = tmp_path / "shadow-private" / "evidence.sqlite3"
    path.parent.mkdir(mode=0o700)
    path.parent.chmod(0o700)
    identity = _identity()
    store = ShadowEvidenceStore(path)
    store.record_policy(load_policy())
    for observation in observations:
        store.record_observation(
            store.observation_event_id(observation), observation,
        )
    store.close()
    monkeypatch.setenv("TRUSTFORGE_SHADOW_DB_PATH", str(path))
    monkeypatch.setattr(
        dashboard_module,
        "measured_release_identity",
        lambda policy: SimpleNamespace(identity=identity),
    )
    return path


def test_dashboard_is_read_only_and_deterministic(monkeypatch, tmp_path):
    path = _prepare(monkeypatch, tmp_path, _observations(_identity()))
    connection = sqlite3.connect(path)
    before = {
        table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("observations", "observation_completions", "aggregates", "decisions")
    }
    connection.close()

    first = dashboard_module.build_shadow_dashboard_report(now=NOW)
    second = dashboard_module.build_shadow_dashboard_report(now=NOW)

    assert first == second
    assert first["report_version"] == "trustforge.shadow-dashboard/v1"
    assert first["read_only"] is True
    assert first["enabled"] is True
    assert first["coverage"]["observation_count"] == 30
    assert first["coverage"]["coin_count"] == 3
    assert first["coverage"]["question_type_count"] == 2
    assert first["coverage"]["minimum_per_cell"] == 5

    connection = sqlite3.connect(path)
    after = {
        table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in before
    }
    connection.close()
    assert after == before


def test_dashboard_counts_missing_intrinsic_and_exposes_delta_distributions(
    monkeypatch, tmp_path,
):
    _prepare(monkeypatch, tmp_path, _observations(_identity(), with_intrinsic=slice(0, 10)))
    report = dashboard_module.build_shadow_dashboard_report(now=NOW)

    assert report["coverage"]["observation_count"] == 30
    assert report["missing"]["observations_without_intrinsic_shadow"] == 20
    assert report["missing"]["fraction"] == pytest.approx(20 / 30)
    assert report["deltas"]["trust_delta"]["count"] == 30
    assert report["deltas"]["intrinsic_total_delta"]["count"] == 10
    assert report["deltas"]["intrinsic_trust_delta"]["count"] == 10
    assert report["deltas"]["intrinsic_total_delta"]["min"] == pytest.approx(0.02)


def test_dashboard_reports_conflict_and_gate_failures(monkeypatch, tmp_path):
    identity = _identity()
    base = _observations(identity)
    conflicted = [replace(base[0], parity_passed=False), *base[1:]]
    _prepare(monkeypatch, tmp_path, conflicted)
    report = dashboard_module.build_shadow_dashboard_report(now=NOW)

    assert report["conflict"]["parity_failed"] == 1
    assert report["conflict"]["fraction"] == pytest.approx(1 / 30)


def test_dashboard_reports_intrinsic_gate_failures(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path, _observations(_identity(), with_intrinsic=slice(0, 4)))
    report = dashboard_module.build_shadow_dashboard_report(now=NOW)
    # indices 0..3 carry intrinsic context; odd indices (1, 3) gate failed.
    assert report["conflict"]["intrinsic_gate_failed"] == 2


def test_dashboard_fail_closed_without_attestation(monkeypatch):
    monkeypatch.delenv("TRUSTFORGE_SHADOW_DEDICATED_RUNTIME", raising=False)
    report = dashboard_module.build_shadow_dashboard_report(now=NOW)
    assert report["enabled"] is False
    assert report["reason"] == "identity_policy_or_attestation_invalid"
    assert report["coverage"]["observation_count"] == 0
    assert report["deltas"]["trust_delta"]["count"] == 0


def test_dashboard_payload_is_json_serializable(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path, _observations(_identity(), with_intrinsic=slice(0, 5)))
    report = dashboard_module.build_shadow_dashboard_report(now=NOW)
    # Must round-trip through JSON for the /api/shadow/dashboard envelope.
    assert json.loads(json.dumps(report)) == report
