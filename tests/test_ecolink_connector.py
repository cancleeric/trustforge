from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trustforge.ecolink_connector import (
    ConnectorResult,
    UpgradeEventConnector,
    UpgradeEventConnectorError,
    parse_upgrade_events_fixture,
)


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


# ---------------------------------------------------------------------------
# UpgradeEventConnector tests


def connector_payload(**overrides):
    return {**event_payload(), **overrides}


def test_connector_fetches_events_and_preserves_observed_at() -> None:
    connector = UpgradeEventConnector()
    result = connector.fetch_events(
        [connector_payload()],
        fetched_at=utc(2026, 1, 1),
    )
    assert len(result.events) == 1
    assert len(result.errors) == 0
    assert result.skipped_count == 0
    assert result.events[0].observed_at == utc(2026, 1, 1)


def test_connector_skips_malformed_fields() -> None:
    connector = UpgradeEventConnector()
    result = connector.fetch_events(
        [
            connector_payload(event_id="evt:good"),
            {"event_id": "evt:missing-everything"},  # malformed
        ],
        fetched_at=utc(2026, 1, 1),
    )
    assert len(result.events) == 1
    assert result.events[0].event_id == "evt:good"
    assert len(result.errors) == 1
    assert result.skipped_count == 0


def test_connector_skips_illegal_host() -> None:
    connector = UpgradeEventConnector()
    result = connector.fetch_events(
        [connector_payload(official_source_url="https://malware.test/hack")],
        fetched_at=utc(2026, 1, 1),
    )
    assert len(result.events) == 0
    assert len(result.errors) == 1
    assert result.skipped_count == 0


def test_connector_skips_stale_events() -> None:
    connector = UpgradeEventConnector()
    result = connector.fetch_events(
        [connector_payload(event_id="evt:old", scheduled_at="2025-06-01T00:00:00Z")],
        fetched_at=utc(2026, 1, 1),
    )
    assert len(result.events) == 0
    assert len(result.errors) == 0
    assert result.skipped_count == 1


def test_connector_deduplicates_by_event_id() -> None:
    connector = UpgradeEventConnector()
    result = connector.fetch_events(
        [
            connector_payload(event_id="evt:dup", title="first"),
            connector_payload(event_id="evt:dup", title="second"),
        ],
        fetched_at=utc(2026, 1, 1),
    )
    assert len(result.events) == 1
    assert result.events[0].title == "first"  # first wins


def test_connector_reschedule_picks_latest_scheduled_at() -> None:
    connector = UpgradeEventConnector()
    result = connector.fetch_events(
        [
            connector_payload(event_id="evt:re", scheduled_at="2026-02-01T00:00:00Z", title="early"),
            connector_payload(event_id="evt:re", scheduled_at="2026-03-01T00:00:00Z", title="later"),
        ],
        fetched_at=utc(2026, 1, 1),
    )
    assert len(result.events) == 1
    assert result.events[0].title == "later"


def test_connector_reschedule_ignores_earlier_scheduled() -> None:
    connector = UpgradeEventConnector()
    result = connector.fetch_events(
        [
            connector_payload(event_id="evt:re", scheduled_at="2026-03-01T00:00:00Z", title="later"),
            connector_payload(event_id="evt:re", scheduled_at="2026-02-01T00:00:00Z", title="early"),
        ],
        fetched_at=utc(2026, 1, 1),
    )
    assert len(result.events) == 1
    assert result.events[0].title == "later"


def test_connector_empty_payloads() -> None:
    connector = UpgradeEventConnector()
    result = connector.fetch_events([], fetched_at=utc(2026, 1, 1))
    assert len(result.events) == 0
    assert len(result.errors) == 0
    assert result.skipped_count == 0


def test_connector_all_bad_returns_empty_events() -> None:
    connector = UpgradeEventConnector()
    result = connector.fetch_events(
        [{"bad": 1}, {"bad": 2}],
        fetched_at=utc(2026, 1, 1),
    )
    assert len(result.events) == 0
    assert len(result.errors) == 2
    assert result.skipped_count == 0


def test_connector_rejects_naive_fetched_at() -> None:
    connector = UpgradeEventConnector()
    with pytest.raises(UpgradeEventConnectorError, match="fetched_at must be timezone-aware"):
        connector.fetch_events([connector_payload()], fetched_at=datetime(2026, 1, 1))


def test_connector_custom_allowed_hosts() -> None:
    connector = UpgradeEventConnector(allowed_hosts=frozenset({"custom.host"}))
    assert connector.allowed_hosts == frozenset({"custom.host"})


def test_connector_default_allowed_hosts() -> None:
    connector = UpgradeEventConnector()
    assert "arbitrum.foundation" in connector.allowed_hosts


def test_connector_errors_are_strings() -> None:
    connector = UpgradeEventConnector()
    result = connector.fetch_events(
        [connector_payload(official_source_url="https://bad.test/x")],
        fetched_at=utc(2026, 1, 1),
    )
    assert len(result.errors) == 1
    assert isinstance(result.errors[0], str)
