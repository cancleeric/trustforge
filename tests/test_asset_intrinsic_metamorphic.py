from __future__ import annotations

import inspect
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
