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
    assert len(records) == 2
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
    for asset_id in ("asset:btc", "asset:bnb"):
        pit = repository.pit_view(asset_id, AS_OF)
        assert pit is not None
        result = assess_intrinsic_shadow(pit)
        assert result["total_delta"] == 0.0
        assert result["gate"]["passed"] is False
