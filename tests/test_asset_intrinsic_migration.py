from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trustforge.asset_intrinsic import (
    ASSET_INTRINSIC_SCHEMA_VERSION,
    STALE_WINDOW_DAYS,
    AssetIntrinsicProfile,
    AssetIntrinsicRepository,
    IntrinsicDimension,
    IntrinsicDimensionName,
    IntrinsicFactStatus,
    IntrinsicProvenance,
    asset_intrinsic_migration_contract,
    load_asset_intrinsic_records,
    parse_asset_intrinsic_profile,
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


# B1
def test_existing_fixture_loads_all_records() -> None:
    records = load_asset_intrinsic_records(FIXTURE)
    assert len(records) >= 2
    asset_ids = {record.profile.asset_id for record in records}
    assert "asset:btc" in asset_ids
    assert "asset:bnb" in asset_ids
    for record in records:
        assert record.profile.schema_version == ASSET_INTRINSIC_SCHEMA_VERSION
        assert len(record.profile.dimensions) == len(IntrinsicDimensionName)


# B2
def test_migration_contract_has_required_fields() -> None:
    contract = asset_intrinsic_migration_contract()
    assert contract["schema_version"] == ASSET_INTRINSIC_SCHEMA_VERSION
    assert isinstance(contract["supported_migrations"], list)
    assert isinstance(contract["description"], str)
    assert isinstance(contract["breaking_changes"], list)
    assert set(contract.keys()) == {
        "schema_version", "supported_migrations", "description", "breaking_changes",
    }


# B3
def test_unknown_schema_version_rejected() -> None:
    base = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload = copy.deepcopy(base[0]["profile"])
    payload["schema_version"] = "9.9.9"

    with pytest.raises(ValueError, match="unsupported.*schema_version"):
        parse_asset_intrinsic_profile(payload)


# B4
def test_stale_window_days_exists_and_has_correct_type() -> None:
    assert hasattr(
        __import__("trustforge.asset_intrinsic", fromlist=["STALE_WINDOW_DAYS"]),
        "STALE_WINDOW_DAYS",
    )
    assert isinstance(STALE_WINDOW_DAYS, int)
    assert STALE_WINDOW_DAYS == 365


# B5
def test_btc_bnb_honest_zero_within_stale_window() -> None:
    repository = AssetIntrinsicRepository(load_asset_intrinsic_records(FIXTURE))

    # BNB has no curated control/governance evidence → honest zero (gate fail)
    bnb_pit = repository.pit_view("asset:bnb", AS_OF)
    assert bnb_pit is not None
    bnb_result = assess_intrinsic_shadow(bnb_pit)
    assert bnb_result["total_delta"] == 0.0
    assert bnb_result["gate"]["passed"] is False

    # BTC now carries real curated control_dispersion + governance evidence (#870).
    # Its non-zero delta must come from verified known dimensions, never hardcode.
    btc_pit = repository.pit_view("asset:btc", AS_OF)
    assert btc_pit is not None
    btc_result = assess_intrinsic_shadow(btc_pit)
    known_count = btc_result["gate"]["known_count"]
    if btc_result["gate"]["passed"]:
        # delta is derived purely from (value - 0.5) * weight over known dims
        derived = round(sum(d["signed_delta"] for d in btc_result["dimensions"]), 8)
        assert abs(derived - btc_result["total_delta"]) < 1e-9
        assert abs(btc_result["total_delta"]) <= btc_result["total_delta_cap"]
        assert known_count >= 3
    else:
        # If gate still fails, delta must be honest zero
        assert btc_result["total_delta"] == 0.0
