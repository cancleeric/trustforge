"""Peer metric snapshot contract and comparability rules."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from math import isfinite
from typing import Any

PEER_METRICS_SCHEMA_VERSION = "1.0.0"


class PeerMetricMethod(StrEnum):
    OBSERVED = "observed"
    ESTIMATED = "estimated"
    REPORTED = "reported"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MetricValue:
    value: float | None
    unit: str
    method: PeerMetricMethod
    source: str

    def __post_init__(self) -> None:
        if self.value is not None and (not isinstance(self.value, (int, float)) or not isfinite(self.value) or self.value < 0):
            raise ValueError("MetricValue.value must be finite non-negative number or null")
        if not self.unit.strip():
            raise ValueError("MetricValue.unit must be non-empty string")
        if not isinstance(self.method, PeerMetricMethod):
            raise ValueError("MetricValue.method must be PeerMetricMethod")
        if not self.source.strip():
            raise ValueError("MetricValue.source must be non-empty string")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["method"] = self.method.value
        return payload


@dataclass(frozen=True)
class PeerMetricsSnapshot:
    asset_id: str
    observed_tps: MetricValue
    tvl: MetricValue
    gas_fee: MetricValue
    activity_breakdown: dict[str, MetricValue]
    window_start: datetime
    window_end: datetime
    observed_at: datetime

    def __post_init__(self) -> None:
        if not self.asset_id.strip():
            raise ValueError("PeerMetricsSnapshot.asset_id must be non-empty string")
        for field_name in ("observed_tps", "tvl", "gas_fee"):
            if not isinstance(getattr(self, field_name), MetricValue):
                raise ValueError(f"PeerMetricsSnapshot.{field_name} must be MetricValue")
        if (
            not isinstance(self.activity_breakdown, dict)
            or not self.activity_breakdown
            or any(
            not key.strip() or not isinstance(value, MetricValue)
            for key, value in self.activity_breakdown.items()
            )
        ):
            raise ValueError("PeerMetricsSnapshot.activity_breakdown must map strings to MetricValue")
        for field_name in ("window_start", "window_end", "observed_at"):
            timestamp = getattr(self, field_name)
            if timestamp.tzinfo is None:
                raise ValueError(f"PeerMetricsSnapshot.{field_name} must be timezone-aware")
        if self.window_end <= self.window_start:
            raise ValueError("PeerMetricsSnapshot.window_end must be after window_start")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PEER_METRICS_SCHEMA_VERSION,
            "asset_id": self.asset_id,
            "observed_tps": self.observed_tps.to_dict(),
            "tvl": self.tvl.to_dict(),
            "gas_fee": self.gas_fee.to_dict(),
            "activity_breakdown": {
                key: value.to_dict() for key, value in sorted(self.activity_breakdown.items())
            },
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "observed_at": self.observed_at.isoformat(),
        }


def snapshots_comparable(left: PeerMetricsSnapshot, right: PeerMetricsSnapshot) -> tuple[bool, str | None]:
    if left.window_start != right.window_start or left.window_end != right.window_end:
        return False, "metric windows differ"
    for metric_name in ("observed_tps", "tvl", "gas_fee"):
        left_metric = getattr(left, metric_name)
        right_metric = getattr(right, metric_name)
        comparable, reason = _metric_comparable(metric_name, left_metric, right_metric)
        if not comparable:
            return False, reason
    if set(left.activity_breakdown) != set(right.activity_breakdown):
        return False, "activity_breakdown keys differ"
    for activity_name in sorted(left.activity_breakdown):
        comparable, reason = _metric_comparable(
            f"activity_breakdown.{activity_name}",
            left.activity_breakdown[activity_name],
            right.activity_breakdown[activity_name],
        )
        if not comparable:
            return False, reason
    return True, None


def _metric_comparable(
    metric_name: str, left_metric: MetricValue, right_metric: MetricValue
) -> tuple[bool, str | None]:
    if left_metric.value is None or right_metric.value is None:
        return False, f"{metric_name} missing"
    if left_metric.unit != right_metric.unit:
        return False, f"{metric_name} unit differs"
    if left_metric.method != right_metric.method:
        return False, f"{metric_name} method differs"
    if left_metric.source != right_metric.source:
        return False, f"{metric_name} source differs"
    return True, None


def utc_timestamp(raw: str) -> datetime:
    timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return timestamp.astimezone(timezone.utc)
