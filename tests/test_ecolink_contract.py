from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trustforge.ecolink import (
    ECOLINK_SCHEMA_VERSION,
    DependencyEdge,
    DependencyKind,
    ImpactDirection,
    UpgradeEvent,
    parse_utc_timestamp,
)


def utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def test_dependency_edge_serializes_lineage_and_controlled_kind() -> None:
    edge = DependencyEdge(
        source_asset_id="asset:arb",
        target_asset_id="asset:eth",
        kind=DependencyKind.SETTLEMENT,
        confidence=0.82,
        source="fixture://ecolink/dependencies",
        observed_at=utc(2026, 1, 1),
    )

    assert edge.to_dict() == {
        "schema_version": ECOLINK_SCHEMA_VERSION,
        "source_asset_id": "asset:arb",
        "target_asset_id": "asset:eth",
        "kind": "settlement",
        "confidence": 0.82,
        "source": "fixture://ecolink/dependencies",
        "observed_at": "2026-01-01T00:00:00+00:00",
    }


def test_upgrade_event_serializes_impacted_assets_and_optional_schedule() -> None:
    event = UpgradeEvent(
        event_id="upgrade:arb:stylus",
        asset_id="asset:arb",
        title="Stylus upgrade",
        scheduled_at=None,
        impact_direction=ImpactDirection.MIXED,
        impacted_asset_ids=("asset:arb", "asset:eth"),
        source="fixture://ecolink/upgrades",
        observed_at=utc(2026, 1, 2),
    )

    payload = event.to_dict()
    assert payload["schema_version"] == ECOLINK_SCHEMA_VERSION
    assert payload["scheduled_at"] is None
    assert payload["impact_direction"] == "mixed"
    assert payload["impacted_asset_ids"] == ["asset:arb", "asset:eth"]


def test_ecolink_contract_rejects_invalid_self_edges_and_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="cannot link asset to itself"):
        DependencyEdge(
            source_asset_id="asset:arb",
            target_asset_id="asset:arb",
            kind=DependencyKind.BRIDGE,
            confidence=0.5,
            source="fixture://ecolink/dependencies",
            observed_at=utc(2026, 1, 1),
        )

    with pytest.raises(ValueError, match="confidence must be between 0 and 1"):
        DependencyEdge(
            source_asset_id="asset:arb",
            target_asset_id="asset:eth",
            kind=DependencyKind.BRIDGE,
            confidence=1.1,
            source="fixture://ecolink/dependencies",
            observed_at=utc(2026, 1, 1),
        )

    with pytest.raises(ValueError, match="UpgradeEvent.observed_at must be timezone-aware"):
        UpgradeEvent(
            event_id="upgrade:arb:stylus",
            asset_id="asset:arb",
            title="Stylus upgrade",
            scheduled_at=None,
            impact_direction=ImpactDirection.UNKNOWN,
            impacted_asset_ids=("asset:arb",),
            source="fixture://ecolink/upgrades",
            observed_at=datetime(2026, 1, 2),
        )


def test_ecolink_timestamp_parser_preserves_null_and_requires_timezone() -> None:
    assert parse_utc_timestamp(None) is None
    assert parse_utc_timestamp("2026-01-01T00:00:00Z") == utc(2026, 1, 1)
    with pytest.raises(ValueError, match="timestamp must be timezone-aware"):
        parse_utc_timestamp("2026-01-01T00:00:00")
