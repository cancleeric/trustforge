"""Issue #874: replay-layer tests for the asset-intrinsic benchmark.

These tests treat the benchmark as a measurement instrument and verify:

* The checked-in golden manifest is byte-identical to a fresh run
  (reproducibility / determinism).
* The benchmark never asserts that one asset outranks another (no ranking
  field, no real-symbol comparison anywhere in the corpus or output).
* The benchmark calls the real assessor + observation builder only; it does
  not re-implement scoring (import-surface guard).
* All checked-in corpus artifacts load through the real, evidence-verifying
  loader.
"""

from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trustforge import asset_intrinsic_benchmark as bm
from trustforge.asset_intrinsic import load_asset_intrinsic_records

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS = REPO_ROOT / "data" / "asset_intrinsic_benchmark" / "profiles.json"
MANIFEST = REPO_ROOT / "data" / "asset_intrinsic_benchmark" / "manifest.json"

_FORBIDDEN_NAME_RE = re.compile(r"\b(good|bad|safe|risky)\b", re.IGNORECASE)


def _fresh_manifest() -> dict:
    records = bm.load_corpus(CORPUS, REPO_ROOT)
    manifest = bm.run_benchmark_from_records(records, pit_cutoff=bm.PIT_CUTOFF, seed=bm.DEFAULT_SEED)
    return bm.manifest_with_data_version(
        manifest, corpus_path=CORPUS, repo_root=REPO_ROOT
    )


# ---------------------------------------------------------------------------
# Reproducibility: the checked-in golden manifest matches a fresh run exactly.
# ---------------------------------------------------------------------------


def test_golden_manifest_matches_fresh_run_byte_for_byte() -> None:
    fresh = bm.serialize_manifest(_fresh_manifest())
    golden = MANIFEST.read_text(encoding="utf-8")
    assert fresh == golden


def test_manifest_records_required_reproducibility_fields() -> None:
    manifest = _fresh_manifest()
    assert manifest["benchmark_version"] == bm.BENCHMARK_VERSION
    assert manifest["assessment_schema_version"]
    assert manifest["intrinsic_shadow_observation_version"]
    assert manifest["asset_intrinsic_schema_version"]
    assert manifest["data_version"].startswith("sha256:")
    assert manifest["pit_cutoff"] == "2026-07-29T00:00:00Z"
    assert manifest["seed"] == bm.DEFAULT_SEED
    assert manifest["disposition"] == "remain-shadow"
    assert "evidence_version" in manifest
    # Each profile carries baseline/candidate/gate/facts_hash as required.
    for entry in manifest["profiles"]:
        assert entry["facts_hash"].startswith("sha256:")
        assert "gate" in entry
        assert "total_delta" in entry


def test_corpus_artifacts_round_trip_through_real_loader() -> None:
    records = bm.load_corpus(CORPUS, REPO_ROOT)
    assert len(records) >= 6
    # The loader verifies every evidence hash and every forbidden-inference rule.
    profiles = [record.profile for record in records]
    assert len({profile.asset_id for profile in profiles}) == len(profiles)


def test_corpus_carries_no_trust_judgment_in_names_or_text() -> None:
    """asset_ids and human-readable provenance text must be judgment-free.

    Hex content hashes are excluded (a hash may incidentally contain a forbidden
    substring); only identifiers and prose are scanned with word boundaries.
    """
    records = bm.load_corpus(CORPUS, REPO_ROOT)
    for record in records:
        assert not _FORBIDDEN_NAME_RE.search(record.profile.asset_id)
        for dim in record.profile.dimensions:
            prov = dim.provenance
            for field in (
                prov.methodology,
                prov.coverage,
                prov.source_coordinates,
                prov.source_revision,
            ):
                assert not _FORBIDDEN_NAME_RE.search(field), field
            # Evidence stems must also be judgment-free.
            stem = prov.evidence_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            assert not _FORBIDDEN_NAME_RE.search(stem), stem


def test_corpus_profiles_are_symbol_blind() -> None:
    records = bm.load_corpus(CORPUS, REPO_ROOT)
    for record in records:
        assert record.profile.asset_id.startswith("asset:bench-anon-")
    # No real asset identifier leaks into the corpus payload.
    blob = json.dumps([bm.record_to_json(r) for r in records])
    for token in ("asset:btc", "asset:bnb", "asset:eth", "bitcoin"):
        assert token not in blob


# ---------------------------------------------------------------------------
# Measurement correctness: four measurements, no ranking.
# ---------------------------------------------------------------------------


def test_four_measurements_present_and_shaped() -> None:
    measurements = _fresh_manifest()["measurements"]
    assert set(measurements) == {
        "factual_distance_vs_score_spread",
        "coverage_bias",
        "extreme_value_sensitivity",
        "single_source_manipulation",
    }
    sweep = measurements["extreme_value_sensitivity"]
    assert sweep["sweep_values"] == [0.0, 0.25, 0.5, 0.75, 1.0]
    # Single-dimension sweep must be monotonic in the value.
    for row in sweep["rows"]:
        values = [row["total_delta_by_value"][f"{v:.2f}"] for v in sweep["sweep_values"]]
        assert values == sorted(values)


def test_coverage_bias_records_distribution_without_fairness_conclusion() -> None:
    bias = _fresh_manifest()["measurements"]["coverage_bias"]
    assert "no fairness conclusion" in bias["interpretation"]
    assert bias["gate_passed_count"] + bias["gate_failed_count"] >= 6
    assert 0.0 <= bias["gate_passed_fraction"] <= 1.0


def test_single_source_manipulation_zeroes_contribution() -> None:
    manip = _fresh_manifest()["measurements"]["single_source_manipulation"]
    assert manip["before"]["gate_passed"] is True
    assert manip["after"]["gate_passed"] is False
    assert manip["gate_flipped"] is True
    assert manip["after"]["total_delta"] == 0.0
    assert manip["before"]["total_delta"] != 0.0


def test_stale_facts_neutralize_contribution_even_when_gate_passes() -> None:
    profiles = {p["label"]: p for p in _fresh_manifest()["profiles"]}
    stale = profiles["asset:bench-anon-stale"]
    assert stale["gate"]["passed"] is True
    assert all(dim["reason_code"] == "stale" for dim in stale["dimensions"])
    assert stale["total_delta"] == 0.0


# ---------------------------------------------------------------------------
# No-ranking guard: the benchmark is an instrument, not a ranker.
# ---------------------------------------------------------------------------


def test_manifest_has_no_ranking_field() -> None:
    assert _fresh_manifest()["ranking"] is None


# ---------------------------------------------------------------------------
# Import-surface guard: the benchmark must not re-implement scoring.
# ---------------------------------------------------------------------------


def _imported_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(
            "trustforge"
        ):
            for alias in node.names:
                names.add(alias.name)
    return names


def test_benchmark_module_delegates_scoring_and_does_not_reimplement() -> None:
    source = Path(bm.__file__).read_text(encoding="utf-8")
    imported = _imported_names(source)
    # Must delegate to the real assessor + observation builder.
    assert "assess_intrinsic_shadow" in imported
    assert "build_intrinsic_shadow_observation" in imported
    # Must not import scoring internals (would enable re-implementation).
    for forbidden in ("DIMENSION_WEIGHT", "TOTAL_DELTA_CAP", "_dimension_output"):
        assert forbidden not in imported, f"benchmark must not import {forbidden}"
    # Must not import the official scorer / calibration / decision-state layers.
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("trustforge.calibration"), node.module
            assert not node.module.startswith("trustforge_core.scoring"), node.module
            assert "decision_state" not in node.module, node.module

