from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trustforge.ecolink_connector import parse_upgrade_events_fixture


def utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def event_payload(**overrides):
    payload = {
        "event_id": "upgrade:arb:stylus",
        "asset_id": "asset:arb",
        "title": "Stylus upgrade",
        "scheduled_at": "2026-02-01T00:00:00Z",
        "actual_at": None,
        "impact_direction": "mixed",
        "status": "scheduled",
        "impacted_asset_ids": ["asset:arb", "asset:eth"],
        "official_source_url": "https://arbitrum.foundation/upgrade/stylus",
    }
    payload.update(overrides)
    return payload


def test_upgrade_event_connector_accepts_official_sources_and_deduplicates() -> None:
    events = parse_upgrade_events_fixture(
        [event_payload(), event_payload(title="duplicate")],
        fetched_at=utc(2026, 1, 1),
    )

    assert len(events) == 1
    assert events[0].to_dict()["status"] == "scheduled"
    assert events[0].to_dict()["observed_at"] == "2026-01-01T00:00:00+00:00"
    assert events[0].official_source_url == "https://arbitrum.foundation/upgrade/stylus"


def test_upgrade_event_connector_distinguishes_status_values() -> None:
    events = parse_upgrade_events_fixture(
        [
            event_payload(event_id="upgrade:arb:active", status="activated", scheduled_at=None, actual_at="2026-01-02T00:00:00Z"),
            event_payload(event_id="upgrade:arb:cancelled", status="cancelled", scheduled_at=None),
        ],
        fetched_at=utc(2026, 1, 1),
    )

    assert [event.status.value for event in events] == ["activated", "cancelled"]
    assert events[0].actual_at == utc(2026, 1, 2)


def test_upgrade_event_connector_rejects_unapproved_hosts_and_schema_drift() -> None:
    with pytest.raises(ValueError, match="source host is not allowed"):
        parse_upgrade_events_fixture(
            [event_payload(official_source_url="https://evil.example/upgrade")],
            fetched_at=utc(2026, 1, 1),
        )

    with pytest.raises(ValueError, match="unexpected upgrade event fields: internal_notes"):
        parse_upgrade_events_fixture(
            [event_payload(internal_notes="do not leak")],
            fetched_at=utc(2026, 1, 1),
        )


def test_upgrade_event_connector_rejects_stale_events_and_naive_fetch_time() -> None:
    with pytest.raises(ValueError, match="scheduled_at is stale"):
        parse_upgrade_events_fixture(
            [event_payload(scheduled_at="2025-12-31T00:00:00Z")],
            fetched_at=utc(2026, 1, 1),
        )

    with pytest.raises(ValueError, match="fetched_at must be timezone-aware"):
        parse_upgrade_events_fixture([event_payload()], fetched_at=datetime(2026, 1, 1))
