from __future__ import annotations

import copy
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
from trustforge.asset_intrinsic_shadow import intrinsic_facts_hash


# Keep persisted SQLite wall-clock timestamps PIT-visible without tying this
# suite to a calendar date. Domain observations remain a fixed distance from
# the cutoff, so stale/future boundary assertions are deterministic.
TEST_CUTOFF = datetime.now(timezone.utc) + timedelta(days=2)
TEST_BASE = TEST_CUTOFF - timedelta(days=12, hours=12)


def _iso_z(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


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
        is_conflicted = conflicted and index == 0
        dimensions.append(
            {
                "name": name,
                "status": "conflicted" if is_conflicted else "known",
                "raw": None if is_conflicted else 0.5,
                "normalized": None if is_conflicted else 0.5,
                "weight": 0.032,
                "signed_delta": 0.0,
                "reason_code": "fact_conflicted" if is_conflicted else "eligible",
                "coverage": "fixture",
                "provenance": {
                    "source_urls": [
                        "https://family-a.example/evidence",
                        "https://family-b.example/evidence",
                    ],
                    "source_revision": "fixture",
                    "content_hash": f"{index + 1:064x}",
                    "evidence_kind": (
                        "decision_record" if is_conflicted else "upstream_excerpt"
                    ),
                    "source_coordinates": f"fixture:{index}",
                    "as_of": as_of.isoformat(),
                    "fetched_at": as_of.isoformat(),
                },
            }
        )
    known_count = 4 if conflicted else 5
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
        "total_delta_cap": 0.08,
        "facts_hash": intrinsic_facts_hash(dimensions),
        "query_hash": "sha256:" + "d" * 64,
        "gate": {
            "passed": True,
            "known_count": known_count,
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
        pit_cutoff=_iso_z(TEST_CUTOFF),
        stale_after_days=30,
        **kwargs,
    )


def test_canonical_store_dataset_preserves_same_day_coverage_and_digest(tmp_path):
    base = TEST_BASE
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
    base = TEST_BASE
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
    cutoff = TEST_CUTOFF
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
    base = TEST_BASE
    original = _observation("BTC", base, request="conflict")
    changed = replace(
        original,
        intrinsic_shadow={
            **original.intrinsic_shadow,
            "candidate_trust": 0.6,
            "trust_delta": 0.1,
        },
    )
    store = _store(
        tmp_path / "private" / "shadow.sqlite3", [original, changed]
    )
    with pytest.raises(IntrinsicPromotionDatasetError, match="conflicting retry"):
        _build(store)


def test_tampering_invalidates_immutable_digest(tmp_path):
    base = TEST_BASE
    store = _store(
        tmp_path / "private" / "shadow.sqlite3",
        [_observation("BTC", base, request="one")],
    )
    dataset = _build(store)
    dataset["counts"]["observations"] = 99
    with pytest.raises(IntrinsicPromotionDatasetError, match="digest"):
        promotion_observations(dataset)


def test_intrinsic_asset_must_match_canonical_input(tmp_path):
    base = TEST_BASE
    original = _observation("BTC", base, request="asset-mismatch")
    mismatched = replace(
        original,
        intrinsic_shadow={**original.intrinsic_shadow, "asset_id": "ETH"},
    )
    store = _store(tmp_path / "private" / "shadow.sqlite3", [mismatched])
    with pytest.raises(IntrinsicPromotionDatasetError, match="asset identity"):
        _build(store)


def test_completion_not_durable_at_cutoff_is_not_pit_visible():
    base = TEST_BASE
    item = CanonicalShadowObservation(
        event_id="sha256:" + "d" * 64,
        recorded_at=_iso_z(base + timedelta(seconds=1)),
        completion_recorded_at=_iso_z(TEST_CUTOFF + timedelta(seconds=1)),
        observation=_observation("BTC", base, request="late-completion"),
    )

    class Store:
        def read_canonical_observations(
            self, identity, policy, *, pit_cutoff, limit
        ):
            return (item,)

    with pytest.raises(IntrinsicPromotionDatasetError, match="no PIT-visible"):
        _build(Store())


@pytest.mark.parametrize(
    "case",
    [
        "facts_hash",
        "trust_delta",
        "total_delta",
        "total_delta_cap",
        "dimension_value",
        "dimension_weight",
        "dimension_delta",
        "dimension_reason",
        "dimension_order",
        "provenance_hash",
        "provenance_time_malformed",
        "provenance_as_of_future",
        "provenance_fetched_future",
        "provenance_time_order",
        "assessment_schema",
        "gate_count",
        "gate_pass",
    ],
)
def test_intrinsic_reconstruction_inconsistency_fails_closed(tmp_path, case):
    base = TEST_BASE
    original = _observation("BTC", base, request=f"bad-{case}")
    intrinsic = copy.deepcopy(original.intrinsic_shadow)
    if case == "facts_hash":
        intrinsic["facts_hash"] = "sha256:" + "f" * 64
    elif case == "trust_delta":
        intrinsic["trust_delta"] = 0.01
    elif case == "total_delta":
        intrinsic["total_delta"] = 0.01
    elif case == "total_delta_cap":
        intrinsic["total_delta_cap"] = 0.09
    elif case == "dimension_value":
        intrinsic["dimensions"][0]["normalized"] = 0.6
    elif case == "dimension_weight":
        intrinsic["dimensions"][0]["weight"] = 0.031
    elif case == "dimension_delta":
        intrinsic["dimensions"][0]["signed_delta"] = 0.01
    elif case == "dimension_reason":
        intrinsic["dimensions"][0]["reason_code"] = "fact_unknown"
    elif case == "dimension_order":
        intrinsic["dimensions"][0], intrinsic["dimensions"][1] = (
            intrinsic["dimensions"][1],
            intrinsic["dimensions"][0],
        )
        intrinsic["facts_hash"] = intrinsic_facts_hash(intrinsic["dimensions"])
    elif case == "provenance_hash":
        intrinsic["dimensions"][0]["provenance"]["content_hash"] = "not-a-digest"
        intrinsic["facts_hash"] = intrinsic_facts_hash(intrinsic["dimensions"])
    elif case == "provenance_time_malformed":
        intrinsic["dimensions"][0]["provenance"]["as_of"] = "not-a-time"
    elif case == "provenance_as_of_future":
        intrinsic["dimensions"][0]["provenance"]["as_of"] = (
            base + timedelta(minutes=1)
        ).isoformat()
        intrinsic["dimensions"][0]["provenance"]["fetched_at"] = (
            base + timedelta(minutes=1)
        ).isoformat()
    elif case == "provenance_fetched_future":
        intrinsic["dimensions"][0]["provenance"]["fetched_at"] = (
            base + timedelta(minutes=1)
        ).isoformat()
    elif case == "provenance_time_order":
        intrinsic["dimensions"][0]["provenance"]["fetched_at"] = (
            base - timedelta(minutes=3)
        ).isoformat()
    elif case == "assessment_schema":
        intrinsic["assessment_schema_version"] = "9.9.9"
    elif case == "gate_count":
        intrinsic["gate"]["known_count"] = 4
    elif case == "gate_pass":
        intrinsic["gate"]["passed"] = False
    malformed = replace(
        original,
        intrinsic_shadow=intrinsic,
    )
    store = _store(tmp_path / "private" / "shadow.sqlite3", [malformed])
    with pytest.raises(IntrinsicPromotionDatasetError, match="reconstruction"):
        _build(store)


def test_production_builder_does_not_reference_checked_in_fixture():
    sources = [
        Path("src/trustforge/asset_intrinsic_promotion_dataset.py").read_text(),
        Path("scripts/build_intrinsic_promotion_dataset.py").read_text(),
    ]
    assert all("asset_intrinsic_records.json" not in source for source in sources)
