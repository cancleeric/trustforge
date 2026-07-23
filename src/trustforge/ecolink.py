"""Eco-Link dependency and upgrade event contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

ECOLINK_SCHEMA_VERSION = "1.0.0"


class DependencyKind(StrEnum):
    BRIDGE = "bridge"
    ORACLE = "oracle"
    LIQUIDITY = "liquidity"
    SETTLEMENT = "settlement"
    GOVERNANCE = "governance"
    INFRASTRUCTURE = "infrastructure"
    UNKNOWN = "unknown"


class ImpactDirection(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class UpgradeEventStatus(StrEnum):
    ANNOUNCED = "announced"
    SCHEDULED = "scheduled"
    ACTIVATED = "activated"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class DependencyEdge:
    source_asset_id: str
    target_asset_id: str
    kind: DependencyKind
    valid_from: datetime
    valid_until: datetime | None
    confidence: float
    official_source_url: str
    observed_at: datetime

    def __post_init__(self) -> None:
        for field_name in ("source_asset_id", "target_asset_id", "official_source_url"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"DependencyEdge.{field_name} must be non-empty string")
        if _canonical_asset_id(self.source_asset_id) == _canonical_asset_id(self.target_asset_id):
            raise ValueError("DependencyEdge cannot link asset to itself")
        if not isinstance(self.kind, DependencyKind):
            raise ValueError("DependencyEdge.kind must be DependencyKind")
        _ensure_official_url(self.official_source_url, "DependencyEdge.official_source_url")
        _ensure_aware(self.valid_from, "DependencyEdge.valid_from")
        if self.valid_until is not None:
            _ensure_aware(self.valid_until, "DependencyEdge.valid_until")
            if self.valid_until <= self.valid_from:
                raise ValueError("DependencyEdge.valid_until must be after valid_from")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not 0 <= self.confidence <= 1
        ):
            raise ValueError("DependencyEdge.confidence must be between 0 and 1")
        _ensure_aware(self.observed_at, "DependencyEdge.observed_at")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = ECOLINK_SCHEMA_VERSION
        payload["kind"] = self.kind.value
        payload["valid_from"] = self.valid_from.isoformat()
        payload["valid_until"] = self.valid_until.isoformat() if self.valid_until else None
        payload["observed_at"] = self.observed_at.isoformat()
        return payload


@dataclass(frozen=True)
class UpgradeEvent:
    event_id: str
    asset_id: str
    title: str
    scheduled_at: datetime | None
    actual_at: datetime | None
    status: UpgradeEventStatus
    impact_direction: ImpactDirection
    impacted_asset_ids: tuple[str, ...]
    official_source_url: str
    observed_at: datetime

    def __post_init__(self) -> None:
        for field_name in ("event_id", "asset_id", "title", "official_source_url"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"UpgradeEvent.{field_name} must be non-empty string")
        if self.scheduled_at is not None:
            _ensure_aware(self.scheduled_at, "UpgradeEvent.scheduled_at")
        if self.actual_at is not None:
            _ensure_aware(self.actual_at, "UpgradeEvent.actual_at")
        if not isinstance(self.status, UpgradeEventStatus):
            raise ValueError("UpgradeEvent.status must be UpgradeEventStatus")
        if not isinstance(self.impact_direction, ImpactDirection):
            raise ValueError("UpgradeEvent.impact_direction must be ImpactDirection")
        _ensure_official_url(self.official_source_url, "UpgradeEvent.official_source_url")
        if not isinstance(self.impacted_asset_ids, tuple) or any(
            not isinstance(asset_id, str) or not asset_id.strip()
            for asset_id in self.impacted_asset_ids
        ):
            raise ValueError("UpgradeEvent.impacted_asset_ids must be tuple of non-empty strings")
        _ensure_aware(self.observed_at, "UpgradeEvent.observed_at")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = ECOLINK_SCHEMA_VERSION
        payload["status"] = self.status.value
        payload["impact_direction"] = self.impact_direction.value
        payload["impacted_asset_ids"] = list(self.impacted_asset_ids)
        payload["scheduled_at"] = self.scheduled_at.isoformat() if self.scheduled_at else None
        payload["actual_at"] = self.actual_at.isoformat() if self.actual_at else None
        payload["observed_at"] = self.observed_at.isoformat()
        return payload


def dependency_edge_from_dict(payload: dict[str, Any]) -> DependencyEdge:
    required = {
        "schema_version",
        "source_asset_id",
        "target_asset_id",
        "kind",
        "valid_from",
        "valid_until",
        "confidence",
        "official_source_url",
        "observed_at",
    }
    _validate_payload_keys(payload, required, "DependencyEdge")
    if payload["schema_version"] != ECOLINK_SCHEMA_VERSION:
        raise ValueError("DependencyEdge.schema_version unsupported")
    return DependencyEdge(
        source_asset_id=_required_string(payload, "source_asset_id", "DependencyEdge"),
        target_asset_id=_required_string(payload, "target_asset_id", "DependencyEdge"),
        kind=DependencyKind(_required_string(payload, "kind", "DependencyEdge")),
        valid_from=_required_timestamp(payload["valid_from"], "DependencyEdge.valid_from"),
        valid_until=parse_utc_timestamp(payload["valid_until"]),
        confidence=payload["confidence"],
        official_source_url=_required_string(payload, "official_source_url", "DependencyEdge"),
        observed_at=_required_timestamp(payload["observed_at"], "DependencyEdge.observed_at"),
    )


def upgrade_event_from_dict(payload: dict[str, Any]) -> UpgradeEvent:
    required = {
        "schema_version",
        "event_id",
        "asset_id",
        "title",
        "scheduled_at",
        "actual_at",
        "status",
        "impact_direction",
        "impacted_asset_ids",
        "official_source_url",
        "observed_at",
    }
    _validate_payload_keys(payload, required, "UpgradeEvent")
    if payload["schema_version"] != ECOLINK_SCHEMA_VERSION:
        raise ValueError("UpgradeEvent.schema_version unsupported")
    impacted_asset_ids = payload["impacted_asset_ids"]
    if not isinstance(impacted_asset_ids, list):
        raise ValueError("UpgradeEvent.impacted_asset_ids must be list")
    return UpgradeEvent(
        event_id=_required_string(payload, "event_id", "UpgradeEvent"),
        asset_id=_required_string(payload, "asset_id", "UpgradeEvent"),
        title=_required_string(payload, "title", "UpgradeEvent"),
        scheduled_at=parse_utc_timestamp(payload["scheduled_at"]),
        actual_at=parse_utc_timestamp(payload["actual_at"]),
        status=UpgradeEventStatus(_required_string(payload, "status", "UpgradeEvent")),
        impact_direction=ImpactDirection(_required_string(payload, "impact_direction", "UpgradeEvent")),
        impacted_asset_ids=tuple(impacted_asset_ids),
        official_source_url=_required_string(payload, "official_source_url", "UpgradeEvent"),
        observed_at=_required_timestamp(payload["observed_at"], "UpgradeEvent.observed_at"),
    )


def parse_utc_timestamp(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("timestamp must be ISO timestamp string or null")
    timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    _ensure_aware(timestamp, "timestamp")
    return timestamp.astimezone(timezone.utc)


def _ensure_aware(timestamp: datetime, field_name: str) -> None:
    if timestamp.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _canonical_asset_id(asset_id: str) -> str:
    return asset_id.strip().casefold()


def _ensure_official_url(url: str, field_name: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "fixture"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be official source URL")


def _required_timestamp(raw: object, field_name: str) -> datetime:
    timestamp = parse_utc_timestamp(raw)  # type: ignore[arg-type]
    if timestamp is None:
        raise ValueError(f"{field_name} must be timestamp")
    return timestamp


def _required_string(payload: dict[str, Any], field_name: str, contract_name: str) -> str:
    value = payload[field_name]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{contract_name}.{field_name} must be non-empty string")
    return value


def _validate_payload_keys(payload: dict[str, Any], required: set[str], contract_name: str) -> None:
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"missing {contract_name} fields: {', '.join(missing)}")
    extra = sorted(set(payload) - required)
    if extra:
        raise ValueError(f"unexpected {contract_name} fields: {', '.join(extra)}")
