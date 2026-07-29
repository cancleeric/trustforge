from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
from trustforge.agent.shadow_evidence_store import (
    CanonicalShadowObservation,
    ShadowEvidenceStore,
)
from trustforge.asset_intrinsic import INTRINSIC_DIMENSION_NAMES
from trustforge.asset_intrinsic_promotion_dataset import (
    IntrinsicPromotionDatasetError,
    build_promotion_evidence_dataset,
    promotion_observations,
)


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


def _intrinsic(asset: str, as_of: datetime, *, conflicted: bool = False):
    dimensions = []
    for index, name in enumerate(INTRINSIC_DIMENSION_NAMES):
        dimensions.append(
            {
                "name": name,
                "status": "conflicted" if conflicted and index == 0 else "known",
                "value": 0.5,
                "provenance": {
                    "source_urls": [
                        "https://family-a.example/evidence",
                        "https://family-b.example/evidence",
                    ]
                },
            }
        )
    return {
        "schema_version": "1.0.0",
        "assessment_schema_version": "1.0.0",
        "mode": "shadow",
        "affects_official_score": False,
        "asset_id": asset,
        "as_of": as_of.isoformat(),
        "baseline_trust": 0.5,
        "candidate_trust": 0.5,
        "trust_delta": 0.0,
        "total_delta": 0.0,
        "facts_hash": "sha256:" + "c" * 64,
        "gate": {
            "passed": True,
            "known_count": 5,
            "required_known": 3,
            "source_family_count": 2,
            "required_source_families": 2,
            "reason_code": "eligible",
        },
        "dimensions": dimensions,
    }


def _observation(
    asset: str,
    observed_at: datetime,
    *,
    request: str,
    conflicted: bool = False,
) -> ShadowObservation:
    canonical_input = ShadowInput(
        request_id=request,
        coin=asset,
        question_type="analysis",
        pit_epoch=(observed_at - timedelta(minutes=1)).timestamp(),
        query=f"{asset} intrinsic",
    )
    return ShadowObservation(
        release_identity=_identity(),
        canonical_input=canonical_input,
        input_digest=input_digest(to_dict(canonical_input)),
        observed_at=observed_at.isoformat(),
        status="success",
        parity_passed=True,
        confidence_delta=0.0,
        trust_delta=0.0,
        supporting_jaccard=1.0,
        elapsed_ms=10.0,
        provider_calls=0,
        cost_usd=0.0,
        intrinsic_shadow=_intrinsic(
            asset, observed_at - timedelta(minutes=2), conflicted=conflicted
        ),
    )


def _store(path: Path, observations: list[ShadowObservation]) -> ShadowEvidenceStore:
    path.parent.mkdir(mode=0o700, parents=True)
    path.parent.chmod(0o700)
    store = ShadowEvidenceStore(path)
    store.record_policy(load_policy())
    for observation in observations:
        store.record_observation(store.observation_event_id(observation), observation)
    store.close()
    return ShadowEvidenceStore(path, read_only=True)


def _build(store: ShadowEvidenceStore, **kwargs):
    return build_promotion_evidence_dataset(
        store,
        _identity(),
        load_policy(),
        pit_cutoff="2026-08-01T00:00:00Z",
        stale_after_days=30,
        **kwargs,
    )


def test_canonical_store_dataset_preserves_same_day_coverage_and_digest(tmp_path):
    base = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
    store = _store(
        tmp_path / "private" / "shadow.sqlite3",
        [
            _observation("ETH", base + timedelta(hours=1), request="two"),
            _observation("BTC", base, request="one"),
            _observation("SOL", base + timedelta(days=1), request="three"),
        ],
    )
    first = _build(store)
    second = _build(store)
    assert first == second
    assert first["counts"] == {
        "observations": 3,
        "assets": 3,
        "days": 2,
        "source_families": 2,
    }
    assert first["coverage_by_day"][0]["assets"] == ["BTC", "ETH"]
    assert first["coverage_by_day"][0]["source_families"] == [
        "family-a.example",
        "family-b.example",
    ]
    assert len(promotion_observations(first)) == 3


def test_retry_is_idempotently_deduplicated_across_restart(tmp_path):
    base = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
    original = _observation("BTC", base, request="retry")
    retry = replace(original, elapsed_ms=25.0)
    path = tmp_path / "private" / "shadow.sqlite3"
    writer = _store(path, [original])
    writer.close()
    reopened = ShadowEvidenceStore(path)
    reopened.record_observation(reopened.observation_event_id(retry), retry)
    reopened.close()
    dataset = _build(ShadowEvidenceStore(path, read_only=True))
    assert dataset["counts"]["observations"] == 1


@pytest.mark.parametrize("case", ["future", "stale", "conflicted"])
def test_future_stale_and_conflicted_evidence_fail_closed(tmp_path, case):
    cutoff = datetime(2026, 8, 1, tzinfo=timezone.utc)
    observed = {
        "future": cutoff + timedelta(seconds=1),
        "stale": cutoff - timedelta(days=31),
        "conflicted": cutoff - timedelta(days=1),
    }[case]
    observation = _observation(
        "BTC", observed, request=case, conflicted=case == "conflicted"
    )
    store = _store(tmp_path / case / "shadow.sqlite3", [observation])
    with pytest.raises(IntrinsicPromotionDatasetError):
        _build(store)


def test_conflicting_semantic_retry_fails_closed(tmp_path):
    base = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
    original = _observation("BTC", base, request="conflict")
    changed = replace(
        original,
        intrinsic_shadow={**original.intrinsic_shadow, "candidate_trust": 0.6},
    )
    store = _store(
        tmp_path / "private" / "shadow.sqlite3", [original, changed]
    )
    with pytest.raises(IntrinsicPromotionDatasetError, match="conflicting retry"):
        _build(store)


def test_tampering_invalidates_immutable_digest(tmp_path):
    base = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
    store = _store(
        tmp_path / "private" / "shadow.sqlite3",
        [_observation("BTC", base, request="one")],
    )
    dataset = _build(store)
    dataset["counts"]["observations"] = 99
    with pytest.raises(IntrinsicPromotionDatasetError, match="digest"):
        promotion_observations(dataset)


def test_intrinsic_asset_must_match_canonical_input(tmp_path):
    base = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
    original = _observation("BTC", base, request="asset-mismatch")
    mismatched = replace(
        original,
        intrinsic_shadow={**original.intrinsic_shadow, "asset_id": "ETH"},
    )
    store = _store(tmp_path / "private" / "shadow.sqlite3", [mismatched])
    with pytest.raises(IntrinsicPromotionDatasetError, match="asset identity"):
        _build(store)


def test_completion_not_durable_at_cutoff_is_not_pit_visible():
    base = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
    item = CanonicalShadowObservation(
        event_id="sha256:" + "d" * 64,
        recorded_at="2026-07-20T12:00:01Z",
        completion_recorded_at="2026-08-01T00:00:01Z",
        observation=_observation("BTC", base, request="late-completion"),
    )

    class Store:
        def read_canonical_observations(
            self, identity, policy, *, pit_cutoff, limit
        ):
            return (item,)

    with pytest.raises(IntrinsicPromotionDatasetError, match="no PIT-visible"):
        _build(Store())


def test_malformed_gate_fails_closed(tmp_path):
    base = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
    original = _observation("BTC", base, request="bad-gate")
    bad_gate = {**original.intrinsic_shadow["gate"], "passed": "false"}
    malformed = replace(
        original,
        intrinsic_shadow={**original.intrinsic_shadow, "gate": bad_gate},
    )
    store = _store(tmp_path / "private" / "shadow.sqlite3", [malformed])
    with pytest.raises(IntrinsicPromotionDatasetError, match="coverage gate"):
        _build(store)


def test_production_builder_does_not_reference_checked_in_fixture():
    sources = [
        Path("src/trustforge/asset_intrinsic_promotion_dataset.py").read_text(),
        Path("scripts/build_intrinsic_promotion_dataset.py").read_text(),
    ]
    assert all("asset_intrinsic_records.json" not in source for source in sources)
