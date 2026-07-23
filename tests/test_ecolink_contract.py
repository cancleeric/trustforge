from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trustforge.data_contracts import contract_schemas
from trustforge.ecolink import (
    ECOLINK_SCHEMA_VERSION,
    DependencyEdge,
    DependencyKind,
    ImpactDirection,
    UpgradeEvent,
    UpgradeEventStatus,
    dependency_edge_from_dict,
    parse_utc_timestamp,
    upgrade_event_from_dict,
)


def utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def edge() -> DependencyEdge:
    return DependencyEdge(
        source_asset_id="asset:arb",
        target_asset_id="asset:eth",
        kind=DependencyKind.SETTLEMENT,
        valid_from=utc(2026, 1, 1),
        valid_until=None,
        confidence=0.82,
        official_source_url="https://arbitrum.foundation/ecolink/dependencies",
        observed_at=utc(2026, 1, 2),
    )


def event() -> UpgradeEvent:
    return UpgradeEvent(
        event_id="upgrade:arb:stylus",
        asset_id="asset:arb",
        title="Stylus upgrade",
        scheduled_at=utc(2026, 2, 1),
        actual_at=None,
        status=UpgradeEventStatus.SCHEDULED,
        impact_direction=ImpactDirection.MIXED,
        impacted_asset_ids=("asset:arb", "asset:eth"),
        official_source_url="https://arbitrum.foundation/ecolink/upgrades",
        observed_at=utc(2026, 1, 2),
    )


def test_dependency_edge_serializes_validity_lineage_and_controlled_kind() -> None:
    payload = edge().to_dict()

    assert payload == {
        "schema_version": ECOLINK_SCHEMA_VERSION,
        "source_asset_id": "asset:arb",
        "target_asset_id": "asset:eth",
        "kind": "settlement",
        "valid_from": "2026-01-01T00:00:00+00:00",
        "valid_until": None,
        "confidence": 0.82,
        "official_source_url": "https://arbitrum.foundation/ecolink/dependencies",
        "observed_at": "2026-01-02T00:00:00+00:00",
    }
    assert dependency_edge_from_dict(payload).to_dict() == payload


def test_upgrade_event_serializes_status_planned_actual_time_and_round_trips() -> None:
    payload = event().to_dict()

    assert payload["schema_version"] == ECOLINK_SCHEMA_VERSION
    assert payload["scheduled_at"] == "2026-02-01T00:00:00+00:00"
    assert payload["actual_at"] is None
    assert payload["status"] == "scheduled"
    assert payload["impact_direction"] == "mixed"
    assert payload["impacted_asset_ids"] == ["asset:arb", "asset:eth"]
    assert upgrade_event_from_dict(payload).to_dict() == payload


def test_ecolink_schemas_are_exported_for_contract_consumers() -> None:
    schemas = contract_schemas()

    assert schemas["DependencyEdge"]["properties"]["schema_version"]["const"] == ECOLINK_SCHEMA_VERSION
    assert "valid_from" in schemas["DependencyEdge"]["required"]
    assert "valid_until" in schemas["DependencyEdge"]["required"]
    assert schemas["UpgradeEvent"]["properties"]["status"]["enum"] == [
        "announced",
        "scheduled",
        "activated",
        "cancelled",
    ]
    assert "actual_at" in schemas["UpgradeEvent"]["required"]


def test_ecolink_contract_rejects_invalid_edges_and_bool_confidence() -> None:
    with pytest.raises(ValueError, match="cannot link asset to itself"):
        DependencyEdge(
            source_asset_id="asset:ARB ",
            target_asset_id="asset:arb",
            kind=DependencyKind.BRIDGE,
            valid_from=utc(2026, 1, 1),
            valid_until=None,
            confidence=0.5,
            official_source_url="https://arbitrum.foundation/ecolink/dependencies",
            observed_at=utc(2026, 1, 1),
        )

    with pytest.raises(ValueError, match="confidence must be between 0 and 1"):
        DependencyEdge(
            source_asset_id="asset:arb",
            target_asset_id="asset:eth",
            kind=DependencyKind.BRIDGE,
            valid_from=utc(2026, 1, 1),
            valid_until=None,
            confidence=True,  # type: ignore[arg-type]
            official_source_url="https://arbitrum.foundation/ecolink/dependencies",
            observed_at=utc(2026, 1, 1),
        )

    with pytest.raises(ValueError, match="valid_until must be after valid_from"):
        DependencyEdge(
            source_asset_id="asset:arb",
            target_asset_id="asset:eth",
            kind=DependencyKind.BRIDGE,
            valid_from=utc(2026, 1, 2),
            valid_until=utc(2026, 1, 1),
            confidence=0.5,
            official_source_url="https://arbitrum.foundation/ecolink/dependencies",
            observed_at=utc(2026, 1, 1),
        )


def test_ecolink_contract_requires_official_source_and_timezone() -> None:
    with pytest.raises(ValueError, match="official_source_url must be official source URL"):
        DependencyEdge(
            source_asset_id="asset:arb",
            target_asset_id="asset:eth",
            kind=DependencyKind.BRIDGE,
            valid_from=utc(2026, 1, 1),
            valid_until=None,
            confidence=0.5,
            official_source_url="not-a-url",
            observed_at=utc(2026, 1, 1),
        )

    with pytest.raises(ValueError, match="UpgradeEvent.actual_at must be timezone-aware"):
        UpgradeEvent(
            event_id="upgrade:arb:stylus",
            asset_id="asset:arb",
            title="Stylus upgrade",
            scheduled_at=None,
            actual_at=datetime(2026, 1, 2),
            status=UpgradeEventStatus.ACTIVATED,
            impact_direction=ImpactDirection.UNKNOWN,
            impacted_asset_ids=("asset:arb",),
            official_source_url="https://arbitrum.foundation/ecolink/upgrades",
            observed_at=utc(2026, 1, 2),
        )


def test_ecolink_timestamp_parser_preserves_null_and_requires_timezone() -> None:
    assert parse_utc_timestamp(None) is None
    assert parse_utc_timestamp("2026-01-01T00:00:00Z") == utc(2026, 1, 1)
    with pytest.raises(ValueError, match="timestamp must be timezone-aware"):
        parse_utc_timestamp("2026-01-01T00:00:00")
