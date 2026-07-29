from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trustforge.asset_intrinsic import (
    AssetIntrinsicProfile,
    IntrinsicDimension,
    IntrinsicDimensionName,
    IntrinsicFactStatus,
    IntrinsicProvenance,
    load_asset_intrinsic_records,
    parse_asset_intrinsic_profile,
)
from trustforge.asset_intrinsic_shadow import validate_intrinsic_forbidden_inferences

FIXTURE = Path(__file__).parents[1] / "data" / "asset_intrinsic_records.json"
AS_OF = datetime(2026, 7, 27, tzinfo=timezone.utc)


def provenance(methodology: str) -> IntrinsicProvenance:
    return IntrinsicProvenance(
        source_urls=("https://example.com/source",),
        methodology=methodology,
        content_hash="a" * 64,
        coverage="test coverage",
        evidence_path="data/asset_intrinsic_evidence/btc-issuance-v30.txt",
        source_revision="test-revision",
        evidence_kind="upstream_excerpt",
        source_coordinates="test coordinates",
    )


def profile_with_methodology(methodology: str) -> AssetIntrinsicProfile:
    dimensions = tuple(
        IntrinsicDimension(
            name=name,
            status=IntrinsicFactStatus.KNOWN if name is not IntrinsicDimensionName.HOLDER_CONCENTRATION else IntrinsicFactStatus.UNKNOWN,
            value=0.5 if name is not IntrinsicDimensionName.HOLDER_CONCENTRATION else None,
            as_of=AS_OF,
            valid_from=AS_OF,
            valid_until=None,
            fetched_at=AS_OF,
            provenance=provenance(methodology),
        )
        for name in IntrinsicDimensionName
    )
    return AssetIntrinsicProfile(
        asset_id="asset:test",
        dimensions=dimensions,
    )


# F1: price-inferred
def test_forbidden_inference_price_inferred_detected() -> None:
    violations = validate_intrinsic_forbidden_inferences(
        profile_with_methodology("We use price data to infer intrinsic values")
    )
    assert any("price-inferred" in v for v in violations)

    violations_cn = validate_intrinsic_forbidden_inferences(
        profile_with_methodology("价格推論を使って intrinsic を推測する")
    )
    assert any("price-inferred" in v for v in violations_cn)


# F2: lost-key estimates
def test_forbidden_inference_lost_key_estimates_detected() -> None:
    violations = validate_intrinsic_forbidden_inferences(
        profile_with_methodology("Based on estimating lost coins in early blocks")
    )
    assert any("lost-key estimates" in v for v in violations)


# F3: address=entity
def test_forbidden_inference_address_equals_entity_detected() -> None:
    violations = validate_intrinsic_forbidden_inferences(
        profile_with_methodology("Each address is entity analysis")
    )
    assert any("address=entity" in v for v in violations)


# F4: popularity-inferred
def test_forbidden_inference_popularity_inferred_detected() -> None:
    violations = validate_intrinsic_forbidden_inferences(
        profile_with_methodology("The popularity of this asset implies strong governance")
    )
    assert any("popularity-inferred" in v for v in violations)


# F5: Wall Street ownership
def test_forbidden_inference_wall_street_ownership_detected() -> None:
    violations = validate_intrinsic_forbidden_inferences(
        profile_with_methodology("Wall Street institutional ownership indicates safety")
    )
    assert any("Wall Street ownership" in v for v in violations)


# F6: issuer/symbol hardcode
def test_forbidden_inference_issuer_symbol_hardcode_detected() -> None:
    violations = validate_intrinsic_forbidden_inferences(
        profile_with_methodology("The issuer is known to be deterministic and secure")
    )
    assert any("issuer/symbol hardcode" in v for v in violations)


# F7: existing legitimate methodology passes clean
def test_existing_fixture_methodologies_pass_clean() -> None:
    records = load_asset_intrinsic_records(FIXTURE)
    for record in records:
        violations = validate_intrinsic_forbidden_inferences(record.profile)
        assert violations == [], (
            f"unexpected forbidden inference in {record.profile.asset_id}: {violations}"
        )
