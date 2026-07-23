"""Official-source upgrade event connector helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

from trustforge.ecolink import ImpactDirection, UpgradeEvent, UpgradeEventStatus, parse_utc_timestamp

ALLOWED_UPGRADE_EVENT_HOSTS = frozenset(
    {
        "arbitrum.foundation",
        "blog.arbitrum.io",
        "forum.arbitrum.foundation",
        "gov.optimism.io",
        "ethereum.org",
    }
)


def parse_upgrade_events_fixture(payload: list[dict], *, fetched_at: datetime) -> tuple[UpgradeEvent, ...]:
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at must be timezone-aware")
    seen: set[str] = set()
    events: list[UpgradeEvent] = []
    for item in payload:
        event = parse_upgrade_event(item, fetched_at=fetched_at)
        if event.event_id in seen:
            continue
        seen.add(event.event_id)
        events.append(event)
    return tuple(events)


def parse_upgrade_event(payload: dict, *, fetched_at: datetime) -> UpgradeEvent:
    required = {
        "event_id",
        "asset_id",
        "title",
        "scheduled_at",
        "actual_at",
        "impact_direction",
        "status",
        "impacted_asset_ids",
        "official_source_url",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"missing upgrade event fields: {', '.join(missing)}")
    extra = sorted(set(payload) - required)
    if extra:
        raise ValueError(f"unexpected upgrade event fields: {', '.join(extra)}")

    source_url = payload["official_source_url"]
    if not isinstance(source_url, str) or not source_url.strip():
        raise ValueError("UpgradeEvent.official_source_url must be non-empty string")
    host = urlparse(source_url).hostname
    if host not in ALLOWED_UPGRADE_EVENT_HOSTS:
        raise ValueError(f"upgrade event source host is not allowed: {host}")

    impacted_asset_ids = payload["impacted_asset_ids"]
    if not isinstance(impacted_asset_ids, list):
        raise ValueError("UpgradeEvent.impacted_asset_ids must be list")

    scheduled_at = parse_utc_timestamp(payload["scheduled_at"])
    if scheduled_at is not None and scheduled_at < fetched_at.astimezone(timezone.utc):
        raise ValueError("UpgradeEvent.scheduled_at is stale before fetched_at")

    return UpgradeEvent(
        event_id=_required_string(payload, "event_id"),
        asset_id=_required_string(payload, "asset_id"),
        title=_required_string(payload, "title"),
        scheduled_at=scheduled_at,
        actual_at=parse_utc_timestamp(payload["actual_at"]),
        status=UpgradeEventStatus(_required_string(payload, "status")),
        impact_direction=ImpactDirection(_required_string(payload, "impact_direction")),
        impacted_asset_ids=tuple(impacted_asset_ids),
        official_source_url=source_url,
        observed_at=fetched_at.astimezone(timezone.utc),
    )


def _required_string(payload: dict, field_name: str) -> str:
    value = payload[field_name]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"UpgradeEvent.{field_name} must be non-empty string")
    return value
