"""Issue #874: point-in-time asset-intrinsic benchmark.

This module is a *measurement instrument*, not a ranker.  It replays a
symbol-blind profile corpus through the real shadow assessor
(:func:`trustforge.asset_intrinsic_shadow.assess_intrinsic_shadow`) and the
real observation builder (:func:`trustforge.asset_intrinsic_shadow.build_intrinsic_shadow_observation`)
at a single fixed ``pit_cutoff`` and records four purely observational
measurements.  It never re-implements scoring, never touches the official
scorer / calibration / decision state, and never asserts that one asset must
score above another.

Profiles are anonymous (``asset:bench-anon-*``); the corpus is generated
deterministically from this module so the checked-in ``profiles.json`` and
evidence bytes are reproducible artifacts.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import replace as dataclass_replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from trustforge.asset_intrinsic import (
    ASSET_INTRINSIC_SCHEMA_VERSION,
    AssetIntrinsicProfile,
    AssetIntrinsicRecord,
    AssetIntrinsicRepository,
    AssetIntrinsicView,
    IntrinsicDimension,
    IntrinsicDimensionName,
    IntrinsicFactStatus,
    IntrinsicProvenance,
    load_asset_intrinsic_records,
)
from trustforge.asset_intrinsic_shadow import (
    ASSESSMENT_SCHEMA_VERSION,
    INTRINSIC_SHADOW_OBSERVATION_VERSION,
    assess_intrinsic_shadow,
    build_intrinsic_shadow_observation,
)

BENCHMARK_VERSION = "1.0.0"
PIT_CUTOFF = datetime(2026, 7, 29, 0, 0, 0, tzinfo=timezone.utc)
DEFAULT_SEED = 874
NEUTRAL_ANCHOR_TRUST = 0.5

DIMENSION_ORDER = tuple(IntrinsicDimensionName)
SWEEP_VALUES = (0.0, 0.25, 0.5, 0.75, 1.0)

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

# Forbidden naming tokens: the corpus never encodes a trust judgment.  These
# words are rejected by the corpus builder as a structural guard, independent
# of the assessor's own forbidden-inference validator.
_FORBIDDEN_NAME_TOKENS = ("good", "bad", "safe", "risky")


# ---------------------------------------------------------------------------
# Corpus generation (deterministic).  The checked-in data files are artifacts
# of :func:`build_corpus_artifacts`; do not hand-edit them.
# ---------------------------------------------------------------------------


def _evidence_dir(repo_root: Path) -> Path:
    return repo_root / "data" / "asset_intrinsic_evidence"


def _bench_dir(repo_root: Path) -> Path:
    return repo_root / "data" / "asset_intrinsic_benchmark"


def _write_evidence(repo_root: Path, stem: str, body: str) -> tuple[str, str]:
    """Write one evidence file and return (relative_path, sha256_hex)."""
    for token in _FORBIDDEN_NAME_TOKENS:
        if token in stem.lower():
            raise ValueError(f"forbidden name token in evidence stem: {token}")
    directory = _evidence_dir(repo_root)
    directory.mkdir(parents=True, exist_ok=True)
    relative = f"data/asset_intrinsic_evidence/{stem}.txt"
    path = repo_root / relative
    payload = body.encode("utf-8")
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    return relative, digest


# Evidence bodies are neutral and reproducible.  They describe a single
# factual value only; no market, ownership, or issuer judgment.  One evidence
# file per distinct corpus value so the checked-in bytes are reproducible.


def _value_slug(value: float) -> str:
    return f"{value:.2f}".replace(".", "p")


def _value_excerpt_body(value: float) -> str:
    return (
        "benchmark corpus reproducible excerpt\n"
        f"value: {value:.2f}\n"
        "scope: single protocol-derived parameter; benchmark measurement only\n"
    )


_UNKNOWN_BODY = (
    "benchmark corpus decision record\n"
    "status: no verified upstream fact available for this dimension\n"
    "scope: benchmark measurement only\n"
)
_CONFLICTED_BODY = (
    "benchmark corpus decision record\n"
    "status: upstream observations diverge across independent sources\n"
    "scope: benchmark measurement only\n"
)


# Neutral provenance text.  Deliberately avoids every token scanned by the
# assessor's forbidden-inference validator (no trust/issuer/symbol/name/price/
# market/lost/address/popularity/institution vocabulary).
_METHODOLOGY_KNOWN = (
    "Benchmark corpus reproducible upstream parameter excerpt; a single "
    "protocol-derived parameter only."
)
_COVERAGE_KNOWN = "benchmark corpus; single dimension parameter excerpt"
_SOURCE_COORDINATES = "benchmark excerpt coordinates"
_METHODOLOGY_UNKNOWN = (
    "Benchmark corpus decision record; no verified upstream fact available "
    "for this dimension."
)
_COVERAGE_UNKNOWN = "benchmark corpus; no verified fact"
_METHODOLOGY_CONFLICTED = (
    "Benchmark corpus decision record; independent upstream observations "
    "diverge for this dimension."
)
_COVERAGE_CONFLICTED = "benchmark corpus; divergent observations"


def _host(family: str) -> str:
    return f"bench-{family}.example"


def _known_provenance(
    evidence_cache: dict[str, tuple[str, str]],
    repo_root: Path,
    family: str,
    value: float,
) -> IntrinsicProvenance:
    slug = _value_slug(value)
    if slug not in evidence_cache:
        body = _value_excerpt_body(value)
        evidence_cache[slug] = _write_evidence(repo_root, f"bench-value-{slug}", body)
    path, digest = evidence_cache[slug]
    return IntrinsicProvenance(
        source_urls=(f"https://{_host(family)}/excerpt",),
        methodology=_METHODOLOGY_KNOWN,
        content_hash=digest,
        coverage=_COVERAGE_KNOWN,
        evidence_path=path,
        source_revision=f"bench-value-{slug}",
        evidence_kind="upstream_excerpt",
        source_coordinates=_SOURCE_COORDINATES,
    )


def _unknown_provenance(
    evidence_cache: dict[str, tuple[str, str]],
    repo_root: Path,
) -> IntrinsicProvenance:
    if "unknown" not in evidence_cache:
        evidence_cache["unknown"] = _write_evidence(repo_root, "bench-unknown", _UNKNOWN_BODY)
    path, digest = evidence_cache["unknown"]
    return IntrinsicProvenance(
        source_urls=(),
        methodology=_METHODOLOGY_UNKNOWN,
        content_hash=digest,
        coverage=_COVERAGE_UNKNOWN,
        evidence_path=path,
        source_revision="bench-unknown",
        evidence_kind="decision_record",
        source_coordinates=_SOURCE_COORDINATES,
    )


def _conflicted_provenance(
    evidence_cache: dict[str, tuple[str, str]],
    repo_root: Path,
) -> IntrinsicProvenance:
    if "conflicted" not in evidence_cache:
        evidence_cache["conflicted"] = _write_evidence(
            repo_root, "bench-conflicted", _CONFLICTED_BODY
        )
    path, digest = evidence_cache["conflicted"]
    return IntrinsicProvenance(
        source_urls=(
            f"https://{_host('alpha')}/observation",
            f"https://{_host('beta')}/observation",
        ),
        methodology=_METHODOLOGY_CONFLICTED,
        content_hash=digest,
        coverage=_COVERAGE_CONFLICTED,
        evidence_path=path,
        source_revision="bench-conflicted",
        evidence_kind="decision_record",
        source_coordinates=_SOURCE_COORDINATES,
    )


def _known_dim(
    cache: dict[str, tuple[str, str]],
    repo_root: Path,
    name: IntrinsicDimensionName,
    family: str,
    value: float,
    *,
    as_of: datetime,
) -> IntrinsicDimension:
    return IntrinsicDimension(
        name=name,
        status=IntrinsicFactStatus.KNOWN,
        value=value,
        as_of=as_of,
        valid_from=as_of,
        valid_until=None,
        fetched_at=as_of,
        provenance=_known_provenance(cache, repo_root, family, value),
    )


def _unknown_dim(
    cache: dict[str, tuple[str, str]],
    repo_root: Path,
    name: IntrinsicDimensionName,
    *,
    as_of: datetime,
) -> IntrinsicDimension:
    return IntrinsicDimension(
        name=name,
        status=IntrinsicFactStatus.UNKNOWN,
        value=None,
        as_of=as_of,
        valid_from=as_of,
        valid_until=None,
        fetched_at=as_of,
        provenance=_unknown_provenance(cache, repo_root),
    )


def _conflicted_dim(
    cache: dict[str, tuple[str, str]],
    repo_root: Path,
    name: IntrinsicDimensionName,
    *,
    as_of: datetime,
) -> IntrinsicDimension:
    return IntrinsicDimension(
        name=name,
        status=IntrinsicFactStatus.CONFLICTED,
        value=None,
        as_of=as_of,
        valid_from=as_of,
        valid_until=None,
        fetched_at=as_of,
        provenance=_conflicted_provenance(cache, repo_root),
    )


def _record(
    asset_id: str,
    dimensions: tuple[IntrinsicDimension, ...],
    as_of: datetime,
) -> AssetIntrinsicRecord:
    return AssetIntrinsicRecord(
        profile=AssetIntrinsicProfile(
            schema_version=ASSET_INTRINSIC_SCHEMA_VERSION,
            asset_id=asset_id,
            dimensions=dimensions,
        ),
        valid_from=as_of,
        fetched_at=as_of,
    )


# Corpus specification.  Each entry is (asset_id, builder).  Builders receive
# the evidence cache + repo root and return a tuple of dimensions plus the
# record-wide as_of.  Names are measurement descriptors (value magnitude or
# coverage shape), never trust judgments.

_FRESH = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
_STALE_AS_OF = datetime(2025, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
_FUTURE_VALID_FROM = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)


def _dims_5known_high(cache, root):
    families = ("alpha", "beta", "alpha", "beta", "alpha")
    values = (1.0, 0.9, 1.0, 0.85, 0.95)
    return tuple(
        _known_dim(cache, root, name, fam, val, as_of=_FRESH)
        for name, fam, val in zip(DIMENSION_ORDER, families, values)
    )


def _dims_5known_low(cache, root):
    families = ("alpha", "beta", "alpha", "beta", "alpha")
    values = (0.0, 0.1, 0.0, 0.15, 0.05)
    return tuple(
        _known_dim(cache, root, name, fam, val, as_of=_FRESH)
        for name, fam, val in zip(DIMENSION_ORDER, families, values)
    )


def _dims_3known_boundary(cache, root):
    # Exactly three known (gate boundary), two unknown.
    return (
        _known_dim(cache, root, DIMENSION_ORDER[0], "alpha", 0.7, as_of=_FRESH),
        _known_dim(cache, root, DIMENSION_ORDER[1], "beta", 0.6, as_of=_FRESH),
        _known_dim(cache, root, DIMENSION_ORDER[2], "alpha", 0.8, as_of=_FRESH),
        _unknown_dim(cache, root, DIMENSION_ORDER[3], as_of=_FRESH),
        _unknown_dim(cache, root, DIMENSION_ORDER[4], as_of=_FRESH),
    )


def _dims_conflicted(cache, root):
    return (
        _known_dim(cache, root, DIMENSION_ORDER[0], "alpha", 0.8, as_of=_FRESH),
        _known_dim(cache, root, DIMENSION_ORDER[1], "beta", 0.7, as_of=_FRESH),
        _known_dim(cache, root, DIMENSION_ORDER[2], "alpha", 0.75, as_of=_FRESH),
        _conflicted_dim(cache, root, DIMENSION_ORDER[3], as_of=_FRESH),
        _unknown_dim(cache, root, DIMENSION_ORDER[4], as_of=_FRESH),
    )


def _dims_stale(cache, root):
    # All five known and PIT-eligible, but as_of > 365 days before the cutoff,
    # so every contribution is neutralized by the stale branch even though the
    # coverage gate counts them and passes.
    families = ("alpha", "beta", "alpha", "beta", "alpha")
    values = (1.0, 0.9, 1.0, 0.85, 0.95)
    return tuple(
        _known_dim(cache, root, name, fam, val, as_of=_STALE_AS_OF)
        for name, fam, val in zip(DIMENSION_ORDER, families, values)
    )


def _dims_single_family(cache, root):
    # Five known facts all from one source family -> coverage gate fails on
    # source_family_count, zeroing every contribution.
    families = ("alpha", "alpha", "alpha", "alpha", "alpha")
    values = (1.0, 0.9, 1.0, 0.85, 0.95)
    return tuple(
        _known_dim(cache, root, name, fam, val, as_of=_FRESH)
        for name, fam, val in zip(DIMENSION_ORDER, families, values)
    )


def _dims_future_gap(cache, root):
    # Four fresh known facts (gate passes) plus one known fact whose
    # valid_from is after pit_cutoff -> omitted from the PIT view ->
    # rendered as fact_unavailable.
    fresh = (
        _known_dim(cache, root, DIMENSION_ORDER[0], "alpha", 0.8, as_of=_FRESH),
        _known_dim(cache, root, DIMENSION_ORDER[1], "beta", 0.7, as_of=_FRESH),
        _known_dim(cache, root, DIMENSION_ORDER[2], "alpha", 0.75, as_of=_FRESH),
        _known_dim(cache, root, DIMENSION_ORDER[3], "beta", 0.7, as_of=_FRESH),
    )
    future = IntrinsicDimension(
        name=DIMENSION_ORDER[4],
        status=IntrinsicFactStatus.KNOWN,
        value=0.9,
        as_of=_FUTURE_VALID_FROM,
        valid_from=_FUTURE_VALID_FROM,
        valid_until=None,
        fetched_at=_FUTURE_VALID_FROM,
        provenance=_known_provenance(cache, root, "alpha", 0.9),
    )
    return fresh + (future,)


_CORPUS_SPEC: tuple[tuple[str, Any], ...] = (
    ("asset:bench-anon-5known-high", _dims_5known_high),
    ("asset:bench-anon-5known-low", _dims_5known_low),
    ("asset:bench-anon-3known-boundary", _dims_3known_boundary),
    ("asset:bench-anon-conflicted", _dims_conflicted),
    ("asset:bench-anon-stale", _dims_stale),
    ("asset:bench-anon-single-family", _dims_single_family),
    ("asset:bench-anon-future-gap", _dims_future_gap),
)


def _check_names(asset_id: str) -> None:
    lowered = asset_id.lower()
    for token in _FORBIDDEN_NAME_TOKENS:
        if token in lowered:
            raise ValueError(f"forbidden name token {token!r} in asset_id {asset_id}")


def build_corpus_records(repo_root: Path) -> tuple[AssetIntrinsicRecord, ...]:
    """Generate the benchmark corpus records (and write evidence files)."""
    cache: dict[str, tuple[str, str]] = {}
    records: list[AssetIntrinsicRecord] = []
    for asset_id, builder in _CORPUS_SPEC:
        _check_names(asset_id)
        if asset_id.endswith("anon-future-gap"):
            as_of = _FRESH
        elif asset_id.endswith("anon-stale"):
            as_of = _STALE_AS_OF
        else:
            as_of = _FRESH
        dimensions = builder(cache, repo_root)
        records.append(_record(asset_id, dimensions, as_of))
    return tuple(records)


def build_corpus_artifacts(repo_root: Path) -> None:
    """Write the reproducible ``profiles.json`` corpus and evidence bytes.

    Idempotent: stale ``bench-*.txt`` evidence from a prior build is removed
    before regeneration so the checked-in bytes match this build exactly.
    """
    evidence_dir = _evidence_dir(repo_root)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    for stale in evidence_dir.glob("bench-*.txt"):
        stale.unlink()
    records = build_corpus_records(repo_root)
    payload = [record_to_json(record) for record in records]
    bench_dir = _bench_dir(repo_root)
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "profiles.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def record_to_json(record: AssetIntrinsicRecord) -> dict[str, Any]:
    profile = record.profile
    return {
        "profile": {
            "schema_version": profile.schema_version,
            "asset_id": profile.asset_id,
            "dimensions": [dimension_to_json(dim) for dim in profile.dimensions],
        },
        "valid_from": _iso(record.valid_from),
        "fetched_at": _iso(record.fetched_at),
    }


def dimension_to_json(dimension: IntrinsicDimension) -> dict[str, Any]:
    return {
        "name": dimension.name.value,
        "status": dimension.status.value,
        "value": dimension.value,
        "as_of": _iso(dimension.as_of),
        "valid_from": _iso(dimension.valid_from),
        "valid_until": _iso(dimension.valid_until) if dimension.valid_until else None,
        "fetched_at": _iso(dimension.fetched_at),
        "provenance": dimension.provenance.to_dict(),
    }


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# PIT replay engine.  Calls the real assessor + observation builder only.
# ---------------------------------------------------------------------------


def load_corpus(corpus_path: Path, repo_root: Path) -> tuple[AssetIntrinsicRecord, ...]:
    return load_asset_intrinsic_records(corpus_path, evidence_root=repo_root)


def _query_for(pit_cutoff: datetime) -> str:
    return f"benchmark/pit-cutoff/{pit_cutoff.astimezone(timezone.utc).isoformat()}"


def replay_one(
    view: AssetIntrinsicView,
    pit_cutoff: datetime,
    *,
    baseline_trust: float = NEUTRAL_ANCHOR_TRUST,
    candidate_trust: float = NEUTRAL_ANCHOR_TRUST,
) -> dict[str, Any]:
    """Replay one PIT view through the real assessor + observation builder."""
    observation = build_intrinsic_shadow_observation(
        view,
        baseline_trust=baseline_trust,
        candidate_trust=candidate_trust,
        query=_query_for(pit_cutoff),
    )
    assessment = assess_intrinsic_shadow(view)
    return {"observation": observation, "assessment": assessment}


def _profile_entry(
    record: AssetIntrinsicRecord,
    repo: AssetIntrinsicRepository,
    pit_cutoff: datetime,
) -> tuple[dict[str, Any], AssetIntrinsicView, dict[str, Any]] | None:
    view = repo.pit_view(record.profile.asset_id, pit_cutoff)
    if view is None:
        return None
    replayed = replay_one(view, pit_cutoff)
    observation = replayed["observation"]
    assessment = replayed["assessment"]
    dimensions = [
        {
            "name": dim["name"],
            "status": dim["status"],
            "signed_delta": dim["signed_delta"],
            "reason_code": dim["reason_code"],
        }
        for dim in observation["dimensions"]
    ]
    entry = {
        "label": record.profile.asset_id,
        "pit_visible_dimension_count": len(view.dimensions),
        "total_delta": observation["total_delta"],
        "facts_hash": observation["facts_hash"],
        "gate": observation["gate"],
        "conflict_detected": assessment["conflict_detected"],
        "dimensions": dimensions,
    }
    return entry, view, assessment


def _clone_view_with_value(
    base_view: AssetIntrinsicView, target: IntrinsicDimensionName, value: float
) -> AssetIntrinsicView:
    new_dims = []
    for dim in base_view.dimensions:
        if dim.name is target:
            new_prov = dataclass_replace(dim.provenance)
            new_dims.append(
                dataclass_replace(dim, value=value, provenance=new_prov)
            )
        else:
            new_dims.append(dataclass_replace(dim, value=0.5))
    return AssetIntrinsicView(
        asset_id=base_view.asset_id, as_of=base_view.as_of, dimensions=tuple(new_dims)
    )


def _clone_view_single_family(base_view: AssetIntrinsicView) -> AssetIntrinsicView:
    single = "https://bench-alpha.example/excerpt"
    new_dims = []
    for dim in base_view.dimensions:
        if dim.status is not IntrinsicFactStatus.KNOWN:
            new_dims.append(dim)
            continue
        new_prov = dataclass_replace(dim.provenance, source_urls=(single,))
        new_dims.append(dataclass_replace(dim, provenance=new_prov))
    return AssetIntrinsicView(
        asset_id=base_view.asset_id, as_of=base_view.as_of, dimensions=tuple(new_dims)
    )


def _measurement_factual_distance(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Map factual distance (deviation from the 0.5 neutral) to score delta.

    Each row already carries the per-profile factual distance and the assessor's
    total_delta.  No ranking is produced: the table records the linear,
    cap-bounded mapping only.
    """
    totals = [row["total_delta"] for row in rows]
    return {
        "rows": rows,
        "score_spread": {
            "count": len(totals),
            "min": min(totals) if totals else None,
            "max": max(totals) if totals else None,
            "range": (max(totals) - min(totals)) if totals else None,
        },
        "interpretation": "records factual-to-score mapping only; no ranking",
    }


def _measurement_coverage_bias(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(1 for p in profiles if p["gate"]["passed"])
    failed = len(profiles) - passed
    gate_reasons: dict[str, int] = {}
    known_counts: dict[int, int] = {}
    family_counts: dict[int, int] = {}
    for p in profiles:
        gate = p["gate"]
        gate_reasons[gate["reason_code"]] = gate_reasons.get(gate["reason_code"], 0) + 1
        known_counts[gate["known_count"]] = known_counts.get(gate["known_count"], 0) + 1
        family_counts[gate["source_family_count"]] = (
            family_counts.get(gate["source_family_count"], 0) + 1
        )
    total = len(profiles)
    return {
        "gate_passed_count": passed,
        "gate_failed_count": failed,
        "gate_passed_fraction": (passed / total) if total else None,
        "gate_reason_code_distribution": dict(sorted(gate_reasons.items())),
        "known_count_distribution": dict(sorted(known_counts.items())),
        "source_family_count_distribution": dict(sorted(family_counts.items())),
        "interpretation": "distribution only; no fairness conclusion",
    }


def _measurement_extreme_value_sensitivity(
    base_view: AssetIntrinsicView,
) -> dict[str, Any]:
    rows = []
    for name in DIMENSION_ORDER:
        per_value = {}
        for value in SWEEP_VALUES:
            sweep_view = _clone_view_with_value(base_view, name, value)
            result = assess_intrinsic_shadow(sweep_view)
            per_value[f"{value:.2f}"] = result["total_delta"]
        rows.append({"dimension": name.value, "total_delta_by_value": per_value})
    return {
        "base_label": "anon-sweep (multi-family, five known)",
        "sweep_values": list(SWEEP_VALUES),
        "rows": rows,
        "interpretation": "single-dimension monotonic response; measurement only",
    }


def _measurement_single_source_manipulation(base_view: AssetIntrinsicView) -> dict[str, Any]:
    before = assess_intrinsic_shadow(base_view)
    after = assess_intrinsic_shadow(_clone_view_single_family(base_view))
    return {
        "base_label": "anon-manipulation (five known, multi-family)",
        "before": {
            "gate_passed": before["gate"]["passed"],
            "source_family_count": before["gate"]["source_family_count"],
            "total_delta": before["total_delta"],
        },
        "after": {
            "gate_passed": after["gate"]["passed"],
            "source_family_count": after["gate"]["source_family_count"],
            "total_delta": after["total_delta"],
        },
        "gate_flipped": before["gate"]["passed"] and not after["gate"]["passed"],
        "interpretation": "single-family control zeroes every contribution",
    }


def _canonical_sweep_base(pit_cutoff: datetime) -> AssetIntrinsicView:
    """A fixed, corpus-independent anon view for the sensitivity/manipulation sweeps.

    Five known, neutral-to-high facts spanning two source families, fresh at
    ``pit_cutoff``.  Decoupling the sweep base from the corpus keeps the corpus
    replay fully identity-invariant (M5) and makes the sensitivity measurement a
    controlled probe of the assessor rather than a property of one corpus member.
    """
    dims: list[IntrinsicDimension] = []
    for index, name in enumerate(DIMENSION_ORDER):
        family = "alpha" if index % 2 == 0 else "beta"
        provenance = IntrinsicProvenance(
            source_urls=(f"https://{_host(family)}/excerpt",),
            methodology=_METHODOLOGY_KNOWN,
            content_hash="0" * 64,
            coverage=_COVERAGE_KNOWN,
            evidence_path="data/asset_intrinsic_evidence/bench-unknown.txt",
            source_revision="bench-sweep-canonical",
            evidence_kind="upstream_excerpt",
            source_coordinates=_SOURCE_COORDINATES,
        )
        dims.append(
            IntrinsicDimension(
                name=name,
                status=IntrinsicFactStatus.KNOWN,
                value=0.8,
                as_of=_FRESH,
                valid_from=_FRESH,
                valid_until=None,
                fetched_at=_FRESH,
                provenance=provenance,
            )
        )
    return AssetIntrinsicView(
        asset_id="asset:bench-anon-sweep-canonical",
        as_of=pit_cutoff,
        dimensions=tuple(dims),
    )


def _conflicted_probe_view(base_view: AssetIntrinsicView) -> AssetIntrinsicView:
    """Construct a direct anon view that carries one conflicted dimension.

    ``AssetIntrinsicRepository.pit_view`` does not surface conflicted facts at
    this schema version (a conflicted fact is neither ``eligible_at`` nor
    ``visible_unknown_at``), so the assessor's ``fact_conflicted`` branch is
    unreachable through the PIT replay path.  This probe constructs a view
    directly to exercise that branch for coverage completeness.  It carries no
    real symbol identity and uses the same neutral provenance vocabulary.
    """
    if not base_view.dimensions:
        raise ValueError("base view must expose at least one dimension")
    probe_dims: list[IntrinsicDimension] = []
    for index, dim in enumerate(base_view.dimensions):
        if index != 2:
            probe_dims.append(dim)
            continue
        conflicted_prov = IntrinsicProvenance(
            source_urls=(
                "https://bench-alpha.example/observation",
                "https://bench-beta.example/observation",
            ),
            methodology=_METHODOLOGY_CONFLICTED,
            content_hash=dim.provenance.content_hash,
            coverage=_COVERAGE_CONFLICTED,
            evidence_path=dim.provenance.evidence_path,
            source_revision="bench-conflicted-probe",
            evidence_kind="decision_record",
            source_coordinates=_SOURCE_COORDINATES,
        )
        probe_dims.append(
            IntrinsicDimension(
                name=dim.name,
                status=IntrinsicFactStatus.CONFLICTED,
                value=None,
                as_of=dim.as_of,
                valid_from=dim.valid_from,
                valid_until=dim.valid_until,
                fetched_at=dim.fetched_at,
                provenance=conflicted_prov,
            )
        )
    return AssetIntrinsicView(
        asset_id="asset:bench-anon-conflicted-probe",
        as_of=base_view.as_of,
        dimensions=tuple(probe_dims),
    )


def _measurement_coverage_probe(base_view: AssetIntrinsicView) -> dict[str, Any]:
    """Exercise the assessor's conflicted branch via a direct anon view."""
    probe_view = _conflicted_probe_view(base_view)
    result = assess_intrinsic_shadow(probe_view)
    return {
        "label": "anon-conflicted-direct-view",
        "path": "direct AssetIntrinsicView (not pit_view)",
        "conflict_detected": result["conflict_detected"],
        "dimensions": [
            {
                "name": dim["name"],
                "status": dim["status"],
                "reason_code": dim["reason_code"],
            }
            for dim in result["dimensions"]
        ],
        "note": (
            "pit_view does not surface conflicted facts at this schema "
            "version; this probe covers the fact_conflicted branch for "
            "coverage completeness"
        ),
    }


def _factual_distance_rows(
    ordered: list[AssetIntrinsicRecord],
    repo: AssetIntrinsicRepository,
    pit_cutoff: datetime,
) -> list[dict[str, Any]]:
    """Per-profile factual distance vs score, for gate-passing replays."""
    rows: list[dict[str, Any]] = []
    for record in ordered:
        view = repo.pit_view(record.profile.asset_id, pit_cutoff)
        if view is None:
            continue
        result = assess_intrinsic_shadow(view)
        if not result["gate"]["passed"]:
            continue
        known_values = [
            dim.value
            for dim in view.dimensions
            if dim.status is IntrinsicFactStatus.KNOWN and dim.eligible_at(pit_cutoff)
        ]
        signed_factual = round(sum(float(v) - 0.5 for v in known_values), 8)
        factual_distance = round(sum(abs(float(v) - 0.5) for v in known_values), 8)
        rows.append(
            {
                "label": record.profile.asset_id,
                "known_count": result["gate"]["known_count"],
                "signed_factual_distance": signed_factual,
                "factual_distance_l1": factual_distance,
                "total_delta": result["total_delta"],
            }
        )
    return rows


def run_benchmark_from_records(
    records: Iterable[AssetIntrinsicRecord],
    *,
    pit_cutoff: datetime,
    seed: int,
) -> dict[str, Any]:
    """Run the full benchmark over an iterable of records.

    Records are sorted by ``asset_id`` before replay so the manifest is
    invariant under input permutation.  ``seed`` is recorded for
    reproducibility; no non-deterministic sampling is performed.
    """
    rng = random.Random(seed)
    ordered = sorted(records, key=lambda r: r.profile.asset_id)
    repo = AssetIntrinsicRepository(ordered)
    # Touch the rng so the seed is observably consumed for reproducibility.
    _ = rng.random()

    profile_entries: list[dict[str, Any]] = []
    for record in ordered:
        outcome = _profile_entry(record, repo, pit_cutoff)
        if outcome is None:
            continue
        entry, _view, _assessment = outcome
        profile_entries.append(entry)

    # Corpus-independent synthetic base for the sensitivity + manipulation
    # sweeps.  Selection is structural (not by asset_id) so the corpus replay
    # stays fully identity-invariant.
    sweep_base_view = _canonical_sweep_base(pit_cutoff)

    factual_rows = _factual_distance_rows(ordered, repo, pit_cutoff)

    measurements = {
        "factual_distance_vs_score_spread": _measurement_factual_distance(factual_rows),
        "coverage_bias": _measurement_coverage_bias(profile_entries),
        "extreme_value_sensitivity": _measurement_extreme_value_sensitivity(sweep_base_view),
        "single_source_manipulation": _measurement_single_source_manipulation(sweep_base_view),
    }

    coverage_probe = _measurement_coverage_probe(sweep_base_view)

    return {
        "benchmark_version": BENCHMARK_VERSION,
        "assessment_schema_version": ASSESSMENT_SCHEMA_VERSION,
        "intrinsic_shadow_observation_version": INTRINSIC_SHADOW_OBSERVATION_VERSION,
        "asset_intrinsic_schema_version": ASSET_INTRINSIC_SCHEMA_VERSION,
        "pit_cutoff": _iso(pit_cutoff),
        "seed": seed,
        "neutral_anchor_trust": NEUTRAL_ANCHOR_TRUST,
        "reproducibility": {
            "record_order": "sorted by asset_id",
            "synthetic_sweep_base": "asset:bench-anon-sweep-canonical (corpus-independent)",
        },
        "profiles": profile_entries,
        "measurements": measurements,
        "coverage_probe": coverage_probe,
        "disposition": "remain-shadow",
        "ranking": None,
    }


def run_benchmark(
    corpus_path: Path,
    *,
    repo_root: Path,
    pit_cutoff: datetime = PIT_CUTOFF,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    records = load_corpus(corpus_path, repo_root)
    return run_benchmark_from_records(records, pit_cutoff=pit_cutoff, seed=seed)


def data_version(corpus_path: Path) -> str:
    return "sha256:" + hashlib.sha256(corpus_path.read_bytes()).hexdigest()


def evidence_fingerprints(repo_root: Path) -> dict[str, str]:
    directory = _evidence_dir(repo_root)
    fingerprints: dict[str, str] = {}
    for path in sorted(directory.glob("bench-*.txt")):
        fingerprints[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return fingerprints


def manifest_with_data_version(
    manifest: dict[str, Any],
    *,
    corpus_path: Path,
    repo_root: Path,
) -> dict[str, Any]:
    enriched = dict(manifest)
    enriched["data_version"] = data_version(corpus_path)
    enriched["evidence_version"] = evidence_fingerprints(repo_root)
    return enriched


def serialize_manifest(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
