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

    with pytest.raises(ValueError, match="timestamp must be ISO timestamp string or null"):
        parse_utc_timestamp("")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="timestamp must be ISO timestamp string or null"):
        parse_utc_timestamp("   ")  # type: ignore[arg-type]


# ── DependencyEdge construction guards ─────────────────────────────────────


def test_edge_rejects_empty_required_strings() -> None:
    with pytest.raises(ValueError, match="DependencyEdge.source_asset_id must be non-empty string"):
        DependencyEdge(
            source_asset_id="",
            target_asset_id="asset:eth",
            kind=DependencyKind.SETTLEMENT,
            valid_from=utc(2026, 1, 1),
            valid_until=None,
            confidence=0.5,
            official_source_url="https://arbitrum.foundation/ecolink/dependencies",
            observed_at=utc(2026, 1, 1),
        )
    with pytest.raises(ValueError, match="DependencyEdge.target_asset_id must be non-empty string"):
        DependencyEdge(
            source_asset_id="asset:arB",
            target_asset_id=" ",
            kind=DependencyKind.SETTLEMENT,
            valid_from=utc(2026, 1, 1),
            valid_until=None,
            confidence=0.5,
            official_source_url="https://arbitrum.foundation/ecolink/dependencies",
            observed_at=utc(2026, 1, 1),
        )


def test_edge_rejects_non_enum_kind() -> None:
    with pytest.raises(ValueError, match="DependencyEdge.kind must be DependencyKind"):
        DependencyEdge(
            source_asset_id="asset:arb",
            target_asset_id="asset:eth",
            kind="settlement",  # type: ignore[arg-type]
            valid_from=utc(2026, 1, 1),
            valid_until=None,
            confidence=0.5,
            official_source_url="https://arbitrum.foundation/ecolink/dependencies",
            observed_at=utc(2026, 1, 1),
        )


def test_edge_rejects_confidence_out_of_range() -> None:
    with pytest.raises(ValueError, match="confidence must be between 0 and 1"):
        DependencyEdge(
            source_asset_id="asset:arb",
            target_asset_id="asset:eth",
            kind=DependencyKind.BRIDGE,
            valid_from=utc(2026, 1, 1),
            valid_until=None,
            confidence=-0.1,
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
            confidence=1.01,
            official_source_url="https://arbitrum.foundation/ecolink/dependencies",
            observed_at=utc(2026, 1, 1),
        )


def test_edge_rejects_non_allowlisted_official_source_host() -> None:
    with pytest.raises(ValueError, match="host is not allowlisted official source"):
        DependencyEdge(
            source_asset_id="asset:arb",
            target_asset_id="asset:eth",
            kind=DependencyKind.SETTLEMENT,
            valid_from=utc(2026, 1, 1),
            valid_until=None,
            confidence=0.5,
            official_source_url="https://example.com/ecolink",
            observed_at=utc(2026, 1, 1),
        )


def test_edge_rejects_naive_observed_at() -> None:
    with pytest.raises(ValueError, match="observed_at must be timezone-aware"):
        DependencyEdge(
            source_asset_id="asset:arb",
            target_asset_id="asset:eth",
            kind=DependencyKind.SETTLEMENT,
            valid_from=utc(2026, 1, 1),
            valid_until=None,
            confidence=0.5,
            official_source_url="https://arbitrum.foundation/ecolink/dependencies",
            observed_at=datetime(2026, 1, 1),
        )


# ── UpgradeEvent construction guards ───────────────────────────────────────


def test_event_rejects_empty_required_strings() -> None:
    with pytest.raises(ValueError, match="UpgradeEvent.event_id must be non-empty string"):
        UpgradeEvent(
            event_id="",
            asset_id="asset:arb",
            title="Upgrade",
            scheduled_at=None,
            actual_at=None,
            status=UpgradeEventStatus.ANNOUNCED,
            impact_direction=ImpactDirection.UNKNOWN,
            impacted_asset_ids=(),
            official_source_url="https://arbitrum.foundation/ecolink/upgrades",
            observed_at=utc(2026, 1, 1),
        )
    with pytest.raises(ValueError, match="UpgradeEvent.title must be non-empty string"):
        UpgradeEvent(
            event_id="upgrade:test",
            asset_id="asset:arb",
            title="   ",
            scheduled_at=None,
            actual_at=None,
            status=UpgradeEventStatus.ANNOUNCED,
            impact_direction=ImpactDirection.UNKNOWN,
            impacted_asset_ids=(),
            official_source_url="https://arbitrum.foundation/ecolink/upgrades",
            observed_at=utc(2026, 1, 1),
        )
    with pytest.raises(ValueError, match="UpgradeEvent.official_source_url must be non-empty string"):
        UpgradeEvent(
            event_id="upgrade:test",
            asset_id="asset:arb",
            title="Upgrade",
            scheduled_at=None,
            actual_at=None,
            status=UpgradeEventStatus.ANNOUNCED,
            impact_direction=ImpactDirection.UNKNOWN,
            impacted_asset_ids=(),
            official_source_url="",
            observed_at=utc(2026, 1, 1),
        )


def test_event_rejects_non_enum_status_and_direction() -> None:
    with pytest.raises(ValueError, match="UpgradeEvent.status must be UpgradeEventStatus"):
        UpgradeEvent(
            event_id="upgrade:test",
            asset_id="asset:arb",
            title="Upgrade",
            scheduled_at=None,
            actual_at=None,
            status="announced",  # type: ignore[arg-type]
            impact_direction=ImpactDirection.UNKNOWN,
            impacted_asset_ids=(),
            official_source_url="https://arbitrum.foundation/ecolink/upgrades",
            observed_at=utc(2026, 1, 1),
        )
    with pytest.raises(ValueError, match="UpgradeEvent.impact_direction must be ImpactDirection"):
        UpgradeEvent(
            event_id="upgrade:test",
            asset_id="asset:arb",
            title="Upgrade",
            scheduled_at=None,
            actual_at=None,
            status=UpgradeEventStatus.ANNOUNCED,
            impact_direction="positive",  # type: ignore[arg-type]
            impacted_asset_ids=(),
            official_source_url="https://arbitrum.foundation/ecolink/upgrades",
            observed_at=utc(2026, 1, 1),
        )


def test_event_rejects_empty_impacted_asset_ids() -> None:
    with pytest.raises(ValueError, match="impacted_asset_ids must be tuple of non-empty strings"):
        UpgradeEvent(
            event_id="upgrade:test",
            asset_id="asset:arb",
            title="Upgrade",
            scheduled_at=None,
            actual_at=None,
            status=UpgradeEventStatus.ANNOUNCED,
            impact_direction=ImpactDirection.UNKNOWN,
            impacted_asset_ids=("asset:eth", ""),  # type: ignore[arg-type]
            official_source_url="https://arbitrum.foundation/ecolink/upgrades",
            observed_at=utc(2026, 1, 1),
        )


def test_event_rejects_naive_scheduled_at() -> None:
    with pytest.raises(ValueError, match="scheduled_at must be timezone-aware"):
        UpgradeEvent(
            event_id="upgrade:test",
            asset_id="asset:arb",
            title="Upgrade",
            scheduled_at=datetime(2026, 1, 1),
            actual_at=None,
            status=UpgradeEventStatus.SCHEDULED,
            impact_direction=ImpactDirection.UNKNOWN,
            impacted_asset_ids=(),
            official_source_url="https://arbitrum.foundation/ecolink/upgrades",
            observed_at=utc(2026, 1, 1),
        )


# ── from_dict / schema round-trip ────────────────────────────────────────


def test_edge_from_dict_rejects_schema_version_mismatch() -> None:
    payload = edge().to_dict()
    payload["schema_version"] = "9.9.9"
    with pytest.raises(ValueError, match="schema_version unsupported"):
        dependency_edge_from_dict(payload)


def test_edge_from_dict_rejects_missing_required_fields() -> None:
    with pytest.raises(ValueError, match="missing DependencyEdge fields"):
        dependency_edge_from_dict({})


def test_edge_from_dict_rejects_unexpected_extra_fields() -> None:
    payload = edge().to_dict()
    payload["extra_field"] = 42
    with pytest.raises(ValueError, match="unexpected DependencyEdge fields"):
        dependency_edge_from_dict(payload)


def test_edge_from_dict_rejects_non_string_source_asset_id() -> None:
    payload = edge().to_dict()
    payload["source_asset_id"] = 123  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="source_asset_id must be non-empty string"):
        dependency_edge_from_dict(payload)


def test_edge_from_dict_rejects_non_allowlisted_host_in_payload() -> None:
    payload = edge().to_dict()
    payload["official_source_url"] = "https://evil.com/ecolink"
    with pytest.raises(ValueError, match="host is not allowlisted official source"):
        dependency_edge_from_dict(payload)


def test_edge_from_dict_requires_valid_from_timestamp() -> None:
    payload = edge().to_dict()
    payload["valid_from"] = "not-a-timestamp"
    with pytest.raises(ValueError):
        dependency_edge_from_dict(payload)


def test_event_from_dict_rejects_schema_version_mismatch() -> None:
    payload = event().to_dict()
    payload["schema_version"] = "9.9.9"
    with pytest.raises(ValueError, match="schema_version unsupported"):
        upgrade_event_from_dict(payload)


def test_event_from_dict_rejects_missing_required_fields() -> None:
    with pytest.raises(ValueError, match="missing UpgradeEvent fields"):
        upgrade_event_from_dict({})


def test_event_from_dict_rejects_unexpected_extra_fields() -> None:
    payload = event().to_dict()
    payload["extra_key"] = "nope"
    with pytest.raises(ValueError, match="unexpected UpgradeEvent fields"):
        upgrade_event_from_dict(payload)


def test_event_from_dict_rejects_non_list_impacted_asset_ids() -> None:
    payload = event().to_dict()
    payload["impacted_asset_ids"] = "not-a-list"  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="impacted_asset_ids must be list"):
        upgrade_event_from_dict(payload)


# ── Edge case: edge with valid_until = None round-trips correctly ─────────

def test_edge_with_valid_until_null_round_trips() -> None:
    payload = {
        "schema_version": ECOLINK_SCHEMA_VERSION,
        "source_asset_id": "asset:arb",
        "target_asset_id": "asset:eth",
        "kind": "bridge",
        "valid_from": "2026-03-01T00:00:00Z",
        "valid_until": None,
        "confidence": 0.9,
        "official_source_url": "https://arbitrum.foundation/ecolink/dependencies",
        "observed_at": "2026-03-01T00:00:00Z",
    }
    edge_ = dependency_edge_from_dict(payload)
    assert edge_.valid_until is None
    assert edge_.to_dict()["valid_until"] is None


# ── Edge case: event with actual_at = None round-trips correctly ──────────

def test_event_with_actual_at_null_round_trips() -> None:
    payload = {
        "schema_version": ECOLINK_SCHEMA_VERSION,
        "event_id": "upgrade:test",
        "asset_id": "asset:arb",
        "title": "Test upgrade",
        "scheduled_at": "2027-01-01T00:00:00Z",
        "actual_at": None,
        "status": "scheduled",
        "impact_direction": "mixed",
        "impacted_asset_ids": ["asset:eth"],
        "official_source_url": "https://arbitrum.foundation/ecolink/upgrades",
        "observed_at": "2026-01-01T00:00:00Z",
    }
    ev = upgrade_event_from_dict(payload)
    assert ev.actual_at is None
    assert ev.scheduled_at == utc(2027, 1, 1)
    assert ev.to_dict()["actual_at"] is None


# ── Schema contract: invalid graph structural invariants ─────────────────

def test_ecolink_schema_kind_constrained_to_known_values() -> None:
    schemas = contract_schemas()
    kind_enum = schemas["DependencyEdge"]["properties"]["kind"]["enum"]
    assert set(kind_enum) == {k.value for k in DependencyKind}


def test_ecolink_schema_official_source_url_has_allowlisted_pattern() -> None:
    schemas = contract_schemas()
    url_schema = schemas["DependencyEdge"]["properties"]["official_source_url"]
    assert url_schema["type"] == "string"
    assert "pattern" in url_schema
    # Same pattern must be enforced on both edges and events
    assert schemas["UpgradeEvent"]["properties"]["official_source_url"] == url_schema


def test_ecolink_schema_additional_fields_strictly_forbidden() -> None:
    schemas = contract_schemas()
    assert schemas["DependencyEdge"]["additionalProperties"] is False
    assert schemas["UpgradeEvent"]["additionalProperties"] is False


def test_edge_cannot_be_self_referential_canonically() -> None:
    """Self-loops are rejected even with different casing/whitespace."""
    with pytest.raises(ValueError, match="cannot link asset to itself"):
        DependencyEdge(
            source_asset_id="asset:ARB",
            target_asset_id="asset:arb",
            kind=DependencyKind.INFRASTRUCTURE,
            valid_from=utc(2026, 1, 1),
            valid_until=None,
            confidence=0.5,
            official_source_url="https://arbitrum.foundation/ecolink/dependencies",
            observed_at=utc(2026, 1, 1),
        )
