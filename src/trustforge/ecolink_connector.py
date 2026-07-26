"""Official-source upgrade event connector helpers."""

from __future__ import annotations

from dataclasses import dataclass
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


class UpgradeEventConnectorError(Exception):
    """Connector-level failure (e.g. naive timestamp, malformed payload)."""


@dataclass(frozen=True)
class ConnectorResult:
    """Immutable batch-parse result."""

    events: tuple[UpgradeEvent, ...]
    errors: tuple[str, ...]
    skipped_count: int


class UpgradeEventConnector:
    """Fail-soft batch connector for official upgrade events.

    * malformed items are skipped and recorded in ``errors``
    * duplicate ``event_id`` is deduplicated (first wins)
    * same ``event_id`` with different ``scheduled_at`` → latest scheduled_at wins
    * stale items (scheduled_at < fetched_at) are skipped
    * provenance: each event carries ``observed_at`` and ``official_source_url``
    """

    _allowed_hosts: frozenset[str] | None = None

    def __init__(self, allowed_hosts: frozenset[str] | None = None) -> None:
        self._allowed_hosts = allowed_hosts

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return self._allowed_hosts if self._allowed_hosts is not None else ALLOWED_UPGRADE_EVENT_HOSTS

    def fetch_events(self, payloads: list[dict], *, fetched_at: datetime) -> ConnectorResult:
        if fetched_at.tzinfo is None:
            raise UpgradeEventConnectorError("fetched_at must be timezone-aware")

        events: list[UpgradeEvent] = []
        errors: list[str] = []
        skipped = 0
        seen: dict[str, UpgradeEvent] = {}  # event_id → event (latest scheduled_at wins)

        for item in payloads:
            # Pre-filter stale scheduled_at before strict parsing
            raw_scheduled = item.get("scheduled_at")
            if raw_scheduled is not None:
                scheduled = parse_utc_timestamp(raw_scheduled)
                if scheduled is not None and scheduled < fetched_at.astimezone(timezone.utc):
                    skipped += 1
                    continue
            try:
                event = parse_upgrade_event(item, fetched_at=fetched_at)
            except ValueError as exc:
                errors.append(str(exc))
                continue

            existing = seen.get(event.event_id)
            if existing is None:
                seen[event.event_id] = event
            elif event.scheduled_at is not None and (existing.scheduled_at is None or event.scheduled_at > existing.scheduled_at):
                seen[event.event_id] = event
            # else: duplicate with same or earlier schedule → drop silently

        events = list(seen.values())
        return ConnectorResult(
            events=tuple(events),
            errors=tuple(errors),
            skipped_count=skipped,
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
