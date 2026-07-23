"""Eco-Link dependency and upgrade event contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

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


@dataclass(frozen=True)
class DependencyEdge:
    source_asset_id: str
    target_asset_id: str
    kind: DependencyKind
    confidence: float
    source: str
    observed_at: datetime

    def __post_init__(self) -> None:
        for field_name in ("source_asset_id", "target_asset_id", "source"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"DependencyEdge.{field_name} must be non-empty string")
        if self.source_asset_id == self.target_asset_id:
            raise ValueError("DependencyEdge cannot link asset to itself")
        if not isinstance(self.kind, DependencyKind):
            raise ValueError("DependencyEdge.kind must be DependencyKind")
        if not isinstance(self.confidence, (int, float)) or not 0 <= self.confidence <= 1:
            raise ValueError("DependencyEdge.confidence must be between 0 and 1")
        _ensure_aware(self.observed_at, "DependencyEdge.observed_at")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = ECOLINK_SCHEMA_VERSION
        payload["kind"] = self.kind.value
        payload["observed_at"] = self.observed_at.isoformat()
        return payload


@dataclass(frozen=True)
class UpgradeEvent:
    event_id: str
    asset_id: str
    title: str
    scheduled_at: datetime | None
    impact_direction: ImpactDirection
    impacted_asset_ids: tuple[str, ...]
    source: str
    observed_at: datetime

    def __post_init__(self) -> None:
        for field_name in ("event_id", "asset_id", "title", "source"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"UpgradeEvent.{field_name} must be non-empty string")
        if self.scheduled_at is not None:
            _ensure_aware(self.scheduled_at, "UpgradeEvent.scheduled_at")
        if not isinstance(self.impact_direction, ImpactDirection):
            raise ValueError("UpgradeEvent.impact_direction must be ImpactDirection")
        if not isinstance(self.impacted_asset_ids, tuple) or any(
            not isinstance(asset_id, str) or not asset_id.strip()
            for asset_id in self.impacted_asset_ids
        ):
            raise ValueError("UpgradeEvent.impacted_asset_ids must be tuple of non-empty strings")
        _ensure_aware(self.observed_at, "UpgradeEvent.observed_at")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = ECOLINK_SCHEMA_VERSION
        payload["impact_direction"] = self.impact_direction.value
        payload["impacted_asset_ids"] = list(self.impacted_asset_ids)
        payload["scheduled_at"] = self.scheduled_at.isoformat() if self.scheduled_at else None
        payload["observed_at"] = self.observed_at.isoformat()
        return payload


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
