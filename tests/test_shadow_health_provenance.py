from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

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
from trustforge.agent.shadow_health import build_shadow_health_report
from trustforge.agent.shadow_health_provenance import (
    ShadowHealthProvenanceError,
    verify_shadow_health_provenance,
)


NOW_TEXT = "2026-07-28T01:00:00+00:00"
NOW = datetime.fromisoformat(NOW_TEXT)


def _identity(policy) -> ShadowReleaseIdentity:
    return ShadowReleaseIdentity(
        active_release="release:legacy@1.2.3",
        candidate_release="release:kernel@2.0.0-rc1",
        active_artifact_digest="sha256:" + "a" * 64,
        candidate_artifact_digest="sha256:" + "b" * 64,
        policy_digest=policy_digest(policy),
        contract_version=CONTRACT_VERSION,
    )


def _prepare(monkeypatch, tmp_path):
    policy = load_policy()
    identity = _identity(policy)
    store_path = tmp_path / "private-store" / "evidence.sqlite3"
    store_path.parent.mkdir(mode=0o700)
    store = ShadowEvidenceStore(store_path)
    store.record_policy(policy)
    base = datetime(2026, 7, 28, tzinfo=timezone.utc)
    for index in range(30):
        shadow_input = ShadowInput(
            request_id=f"request-{index}",
            coin=("BTC", "ETH", "SOL")[index % 3],
            question_type=("analysis", "hypothesis")[index % 2],
            pit_epoch=base.timestamp() + index,
            query=f"query-{index}",
        )
        observation = ShadowObservation(
            release_identity=identity,
            canonical_input=shadow_input,
            input_digest=input_digest(to_dict(shadow_input)),
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
        store.record_observation(
            store.observation_event_id(observation), observation
        )
    store.close()
    monkeypatch.setenv("TRUSTFORGE_SHADOW_DB_PATH", str(store_path))
    monkeypatch.setattr(
        "trustforge.agent.shadow_health.measured_release_identity",
        lambda ignored: type("Measured", (), {"identity": identity})(),
    )
    report = build_shadow_health_report(now=NOW_TEXT)
    export_path = tmp_path / "shadow-health.json"
    export_path.write_text(json.dumps(report, sort_keys=True))
    os.chmod(export_path, 0o600)
    return store_path, export_path, report, identity, policy


def _source_digest(path):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    info = path.stat()
    return digest, info.st_size, info.st_mtime_ns


def test_verifier_rebuilds_export_from_read_only_store(monkeypatch, tmp_path):
    store_path, export_path, report, identity, policy = _prepare(
        monkeypatch, tmp_path
    )
    before = _source_digest(store_path)
    verified = verify_shadow_health_provenance(
        export_path,
        store_path,
        identity=identity,
        policy=policy,
        now=NOW,
    )
    assert verified.report_digest == (
        "sha256:" + hashlib.sha256(
            json.dumps(
                report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
    )
    assert verified.ordered_observation_event_ids == tuple(
        report["evidence"]["ordered_observation_event_ids"]
    )
    assert verified.metrics == report["metrics"]
    assert _source_digest(store_path) == before
    assert not store_path.with_name(f"{store_path.name}-wal").exists()


@pytest.mark.parametrize(
    "field",
    [
        "identity",
        "root",
        "aggregate",
        "decision",
        "ordered",
        "checks",
        "metrics",
        "cost",
    ],
)
def test_forged_export_cannot_replace_sqlite_provenance(
    monkeypatch, tmp_path, field
):
    store_path, export_path, report, identity, policy = _prepare(
        monkeypatch, tmp_path
    )
    forged = json.loads(json.dumps(report))
    if field == "identity":
        forged["identity"]["candidate_release"] = "release:forged@9"
    elif field == "root":
        forged["evidence"]["observation_root_digest"] = "sha256:" + "f" * 64
    elif field == "aggregate":
        forged["evidence"]["aggregate_event_id"] = "sha256:" + "f" * 64
    elif field == "decision":
        forged["evidence"]["decision_event_id"] = "sha256:" + "f" * 64
    elif field == "ordered":
        forged["evidence"]["ordered_observation_event_ids"].reverse()
    elif field == "checks":
        forged["checks"]["completion_evidence"] = False
    elif field == "metrics":
        forged["metrics"]["observations"] += 1
    else:
        forged["metrics"]["cost_usd"] = 0.01
    export_path.write_text(json.dumps(forged))
    with pytest.raises(ShadowHealthProvenanceError):
        verify_shadow_health_provenance(
            export_path,
            store_path,
            identity=identity,
            policy=policy,
            now=NOW,
        )


def test_freshness_policy_identity_and_permissions_fail_closed(
    monkeypatch, tmp_path
):
    store_path, export_path, _, identity, policy = _prepare(
        monkeypatch, tmp_path
    )
    with pytest.raises(ShadowHealthProvenanceError, match="stale"):
        verify_shadow_health_provenance(
            export_path,
            store_path,
            identity=identity,
            policy=policy,
            now=NOW + timedelta(minutes=11),
        )
    with pytest.raises(ShadowHealthProvenanceError, match="policy digest"):
        verify_shadow_health_provenance(
            export_path,
            store_path,
            identity=replace(identity, policy_digest="sha256:" + "f" * 64),
            policy=policy,
            now=NOW,
        )
    os.chmod(export_path, 0o644)
    with pytest.raises(ShadowHealthProvenanceError, match="owner-protected"):
        verify_shadow_health_provenance(
            export_path,
            store_path,
            identity=identity,
            policy=policy,
            now=NOW,
        )
