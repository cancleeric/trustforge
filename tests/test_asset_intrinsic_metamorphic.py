from __future__ import annotations

import inspect
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trustforge.asset_intrinsic import (
    AssetIntrinsicRepository,
    AssetIntrinsicView,
    IntrinsicDimension,
    IntrinsicDimensionName,
    IntrinsicFactStatus,
    IntrinsicProvenance,
    load_asset_intrinsic_records,
)
from trustforge.asset_intrinsic_shadow import assess_intrinsic_shadow

FIXTURE = Path(__file__).parents[1] / "data" / "asset_intrinsic_records.json"
AS_OF = datetime(2026, 7, 28, tzinfo=timezone.utc)


def provenance(host: str) -> IntrinsicProvenance:
    return IntrinsicProvenance(
        source_urls=(f"https://{host}/source",),
        methodology="test methodology",
        content_hash="a" * 64,
        coverage="test coverage",
        evidence_path="data/asset_intrinsic_evidence/btc-issuance-v30.txt",
        source_revision="test-revision",
        evidence_kind="upstream_excerpt",
        source_coordinates="test coordinates",
    )


def dimension(
    name: IntrinsicDimensionName,
    value: float,
    host: str,
    *,
    valid_from: datetime = AS_OF,
) -> IntrinsicDimension:
    return IntrinsicDimension(
        name=name,
        status=IntrinsicFactStatus.KNOWN,
        value=value,
        as_of=valid_from,
        valid_from=valid_from,
        valid_until=None,
        fetched_at=valid_from,
        provenance=provenance(host),
    )


def view(*dimensions: IntrinsicDimension, asset_id: str = "asset:test") -> AssetIntrinsicView:
    return AssetIntrinsicView(asset_id=asset_id, as_of=AS_OF, dimensions=dimensions)


# M1
@pytest.mark.parametrize(
    ("first_id", "second_id"),
    [
        ("asset:first", "asset:second"),
        ("asset:a", "asset:b"),
    ],
)
def test_same_three_known_different_asset_id_produces_identical_output(
    first_id: str, second_id: str
) -> None:
    dims = (
        dimension(IntrinsicDimensionName.ISSUANCE_PREDICTABILITY, 1.0, "a.example"),
        dimension(IntrinsicDimensionName.CONTROL_DISPERSION, 0.0, "b.example"),
        dimension(IntrinsicDimensionName.SUPPLY_VERIFIABILITY, 1.0, "a.example"),
    )
    first = assess_intrinsic_shadow(view(*dims, asset_id=first_id))
    second = assess_intrinsic_shadow(view(*dims, asset_id=second_id))

    assert first["dimensions"] == second["dimensions"]
    assert first["total_delta"] == second["total_delta"]
    assert first["gate"] == second["gate"]


# M2
def test_dimension_order_permutation_does_not_change_result() -> None:
    dims = [
        dimension(IntrinsicDimensionName.ISSUANCE_PREDICTABILITY, 1.0, "a.example"),
        dimension(IntrinsicDimensionName.CONTROL_DISPERSION, 0.25, "b.example"),
        dimension(IntrinsicDimensionName.SUPPLY_VERIFIABILITY, 0.75, "a.example"),
    ]
    baseline = assess_intrinsic_shadow(view(*dims))
    for seed in range(10):
        rng = random.Random(seed)
        shuffled = dims[:]
        rng.shuffle(shuffled)
        result = assess_intrinsic_shadow(view(*shuffled))
        assert result["dimensions"] == baseline["dimensions"]
        assert result["total_delta"] == baseline["total_delta"]


# M3
def test_asset_id_does_not_affect_eligible_dimensions() -> None:
    dims = (
        dimension(IntrinsicDimensionName.ISSUANCE_PREDICTABILITY, 1.0, "a.example"),
        dimension(IntrinsicDimensionName.CONTROL_DISPERSION, 0.0, "b.example"),
        dimension(IntrinsicDimensionName.SUPPLY_VERIFIABILITY, 1.0, "a.example"),
    )
    first_view = view(*dims, asset_id="asset:first")
    second_view = view(*dims, asset_id="asset:second")

    assert first_view.eligible_dimensions == second_view.eligible_dimensions


# M4
def test_assess_intrinsic_shadow_does_not_import_or_read_asset_context() -> None:
    import trustforge.asset_intrinsic_shadow as shadow_mod

    source = inspect.getsource(shadow_mod)
    assert "asset_context" not in source.lower()
    assert "AssetContext" not in source


# ---------------------------------------------------------------------------
# Issue #874: benchmark replay-layer metamorphic + coverage tests (M5-M7).
# ---------------------------------------------------------------------------

from dataclasses import replace as dataclass_replace  # noqa: E402

from trustforge import asset_intrinsic_benchmark as bm  # noqa: E402
from trustforge.asset_intrinsic import (  # noqa: E402
    AssetIntrinsicView,
)
from trustforge.asset_intrinsic_shadow import (  # noqa: E402
    assess_intrinsic_shadow,
    build_intrinsic_shadow_observation,
)

BENCHMARK_CORPUS = Path(__file__).parents[1] / "data" / "asset_intrinsic_benchmark" / "profiles.json"
REAL_CORPUS = FIXTURE
PIT_CUTOFF = datetime(2026, 7, 29, tzinfo=timezone.utc)

_EXPECTED_DIM_REASONS = {
    "eligible",
    "coverage_gate_not_met",
    "fact_unknown",
    "fact_unavailable",
    "fact_conflicted",
    "stale",
}
_EXPECTED_DIM_STATUSES = {"known", "unknown", "stale", "conflicted"}
_EXPECTED_GATE_REASONS = {"eligible", "insufficient_coverage"}


def _strip_labels(manifest: dict) -> dict:
    """Return a copy with every identity-bearing label field removed."""
    stripped = json.loads(json.dumps(manifest))
    for profile in stripped.get("profiles", []):
        profile.pop("label", None)
    for row in stripped.get("measurements", {}).get(
        "factual_distance_vs_score_spread", {}
    ).get("rows", []):
        row.pop("label", None)
    return stripped


# M5: identity rename.  Renaming every corpus asset_id leaves the manifest
# byte-identical except for the label fields.
def test_m5_identity_rename_leaves_manifest_identical_except_labels() -> None:
    records = list(bm.load_corpus(BENCHMARK_CORPUS, Path(__file__).parents[1]))
    ordered = sorted(records, key=lambda r: r.profile.asset_id)
    baseline = bm.run_benchmark_from_records(
        ordered, pit_cutoff=PIT_CUTOFF, seed=bm.DEFAULT_SEED
    )

    renamed = []
    for index, record in enumerate(ordered):
        new_id = f"asset:renamed-{index:02d}"
        renamed.append(
            dataclass_replace(
                record, profile=dataclass_replace(record.profile, asset_id=new_id)
            )
        )
    renamed_manifest = bm.run_benchmark_from_records(
        renamed, pit_cutoff=PIT_CUTOFF, seed=bm.DEFAULT_SEED
    )

    assert _strip_labels(baseline) == _strip_labels(renamed_manifest)
    # And the labels themselves did change (sanity).
    base_labels = {p["label"] for p in baseline["profiles"]}
    renamed_labels = {p["label"] for p in renamed_manifest["profiles"]}
    assert base_labels.isdisjoint(renamed_labels)


# M5 (direct): per-asset, renaming asset_id changes only the observation's
# asset_id field; total_delta / gate / facts_hash / dimensions are invariant.
def test_m5_build_observation_is_identity_invariant_per_asset() -> None:
    records = bm.load_corpus(BENCHMARK_CORPUS, Path(__file__).parents[1])
    repo = AssetIntrinsicRepository(records)
    sample = next(r for r in records if r.profile.asset_id.endswith("anon-5known-high"))
    original_view = repo.pit_view(sample.profile.asset_id, PIT_CUTOFF)
    assert original_view is not None
    renamed_view = AssetIntrinsicView(
        asset_id="asset:carrier-xyz",
        as_of=original_view.as_of,
        dimensions=original_view.dimensions,
    )
    original = build_intrinsic_shadow_observation(
        original_view, baseline_trust=0.5, candidate_trust=0.5, query="q"
    )
    renamed = build_intrinsic_shadow_observation(
        renamed_view, baseline_trust=0.5, candidate_trust=0.5, query="q"
    )
    assert original["asset_id"] != renamed["asset_id"]
    for field in ("total_delta", "facts_hash", "gate", "dimensions"):
        assert original[field] == renamed[field]


# M6: input permutation.  Shuffling the input records across 10 seeds leaves
# every benchmark statistic unchanged.
def test_m6_input_permutation_leaves_statistics_unchanged() -> None:
    records = list(bm.load_corpus(BENCHMARK_CORPUS, Path(__file__).parents[1]))
    canonical = bm.serialize_manifest(
        bm.run_benchmark_from_records(records, pit_cutoff=PIT_CUTOFF, seed=bm.DEFAULT_SEED)
    )
    for seed in range(10):
        rng = random.Random(seed)
        shuffled = records[:]
        rng.shuffle(shuffled)
        manifest = bm.serialize_manifest(
            bm.run_benchmark_from_records(shuffled, pit_cutoff=PIT_CUTOFF, seed=bm.DEFAULT_SEED)
        )
        assert manifest == canonical


# M7: same-facts cross-symbol.  Identical facts carried under a different
# symbol produce an identical total_delta.
def test_m7_same_facts_cross_symbol_produces_identical_total_delta() -> None:
    records = load_asset_intrinsic_records(REAL_CORPUS)
    repo = AssetIntrinsicRepository(records)
    btc_view = repo.pit_view("asset:btc", PIT_CUTOFF)
    assert btc_view is not None
    carrier_view = AssetIntrinsicView(
        asset_id="asset:carrier-cross-symbol",
        as_of=btc_view.as_of,
        dimensions=btc_view.dimensions,
    )
    btc = assess_intrinsic_shadow(btc_view)
    carrier = assess_intrinsic_shadow(carrier_view)
    assert btc["total_delta"] == carrier["total_delta"]
    assert btc["gate"] == carrier["gate"]
    assert btc["dimensions"] == carrier["dimensions"]


# No-leak: the synthetic measurement sections must not name any real asset.
@pytest.mark.parametrize("token", ["BTC", "BNB", "ETH", "bitcoin", "asset:btc", "asset:eth"])
def test_no_real_symbol_leaks_into_synthetic_manifest_sections(token: str) -> None:
    records = bm.load_corpus(BENCHMARK_CORPUS, Path(__file__).parents[1])
    manifest = bm.run_benchmark_from_records(records, pit_cutoff=PIT_CUTOFF, seed=bm.DEFAULT_SEED)
    synthetic = json.dumps(
        {
            "extreme_value_sensitivity": manifest["measurements"]["extreme_value_sensitivity"],
            "single_source_manipulation": manifest["measurements"]["single_source_manipulation"],
            "coverage_probe": manifest["coverage_probe"],
        }
    )
    assert token not in synthetic


# Coverage completeness: every dimension status, dimension reason_code, and gate
# reason_code the assessor can emit is exercised by the benchmark corpus plus
# the conflicted direct-view probe.
def test_coverage_completeness_all_statuses_and_reason_codes_triggered() -> None:
    records = bm.load_corpus(BENCHMARK_CORPUS, Path(__file__).parents[1])
    manifest = bm.run_benchmark_from_records(records, pit_cutoff=PIT_CUTOFF, seed=bm.DEFAULT_SEED)

    dim_reasons: set[str] = set()
    dim_statuses: set[str] = set()
    gate_reasons: set[str] = set()
    for profile in manifest["profiles"]:
        for dim in profile["dimensions"]:
            dim_reasons.add(dim["reason_code"])
            dim_statuses.add(dim["status"])
        gate_reasons.add(profile["gate"]["reason_code"])
    for dim in manifest["coverage_probe"]["dimensions"]:
        dim_reasons.add(dim["reason_code"])
        dim_statuses.add(dim["status"])

    assert dim_reasons == _EXPECTED_DIM_REASONS
    assert dim_statuses == _EXPECTED_DIM_STATUSES
    assert gate_reasons == _EXPECTED_GATE_REASONS


# Real-asset replay: empirical coverage facts at the benchmark pit_cutoff.
def test_real_asset_replay_btc_passes_eth_and_bnb_fail_gate() -> None:
    records = load_asset_intrinsic_records(REAL_CORPUS)
    repo = AssetIntrinsicRepository(records)
    btc = assess_intrinsic_shadow(repo.pit_view("asset:btc", PIT_CUTOFF))
    eth = assess_intrinsic_shadow(repo.pit_view("asset:eth", PIT_CUTOFF))
    bnb = assess_intrinsic_shadow(repo.pit_view("asset:bnb", PIT_CUTOFF))
    assert btc["gate"]["passed"] is True
    assert eth["gate"]["passed"] is False
    assert bnb["gate"]["passed"] is False
    assert btc["total_delta"] != 0.0
    assert eth["total_delta"] == 0.0
    assert bnb["total_delta"] == 0.0
