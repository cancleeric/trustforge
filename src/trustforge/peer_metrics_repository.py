"""Peer metrics fixture repository with explicit peer-group lookups.

All snapshots loaded through this repository originate from illustrative
fixture data (not live network fetches). Every fixture record is tagged
``illustrative: true`` and every ``MetricValue.source`` starts with
``fixture://`` so callers never mistake demo data for a real observation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from trustforge.peer_metrics import MetricValue, PeerMetricMethod, PeerMetricsSnapshot


@dataclass(frozen=True)
class PeerMetricsRecord:
    snapshot: PeerMetricsSnapshot
    peer_group: tuple[str, ...]
    illustrative: bool

    def __post_init__(self) -> None:
        if not self.illustrative:
            raise ValueError("PeerMetricsRecord.illustrative must be true for fixture data")
        if self.snapshot.asset_id not in self.peer_group:
            raise ValueError("PeerMetricsRecord.peer_group must include its own asset_id")
        if len(set(self.peer_group)) != len(self.peer_group):
            raise ValueError("PeerMetricsRecord.peer_group must not contain duplicates")

    def to_dict(self) -> dict[str, Any]:
        return {
            "illustrative": self.illustrative,
            "peer_group": list(self.peer_group),
            "snapshot": self.snapshot.to_dict(),
        }


class PeerMetricsRepository:
    def __init__(self, records: Iterable[PeerMetricsRecord] = ()) -> None:
        self._records = tuple(records)
        self._by_asset_id = {record.snapshot.asset_id: record for record in self._records}

    def by_asset_id(self, asset_id: str) -> PeerMetricsSnapshot | None:
        record = self._by_asset_id.get(asset_id)
        return record.snapshot if record is not None else None

    def peer_group(self, asset_id: str) -> tuple[str, ...]:
        record = self._by_asset_id.get(asset_id)
        if record is None:
            return ()
        return tuple(peer for peer in record.peer_group if peer != asset_id)


def parse_metric_value(payload: dict[str, Any]) -> MetricValue:
    required = {"value", "unit", "method", "source"}
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"missing MetricValue fields: {', '.join(missing)}")
    extra = sorted(set(payload) - required)
    if extra:
        raise ValueError(f"unexpected MetricValue fields: {', '.join(extra)}")
    source = payload["source"]
    if not isinstance(source, str) or not source.startswith("fixture://"):
        raise ValueError(
            f"MetricValue.source must start with 'fixture://' for illustrative fixture data, got {source!r}"
        )
    return MetricValue(
        value=payload["value"],
        unit=payload["unit"],
        method=PeerMetricMethod(payload["method"]),
        source=source,
    )


def parse_peer_metrics_snapshot(payload: dict[str, Any]) -> PeerMetricsSnapshot:
    required = {
        "asset_id",
        "observed_tps",
        "tvl",
        "gas_fee",
        "activity_breakdown",
        "window_start",
        "window_end",
        "observed_at",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"missing PeerMetricsSnapshot fields: {', '.join(missing)}")
    extra = sorted(set(payload) - required)
    if extra:
        raise ValueError(f"unexpected PeerMetricsSnapshot fields: {', '.join(extra)}")
    activity_breakdown = payload["activity_breakdown"]
    if not isinstance(activity_breakdown, dict):
        raise ValueError("PeerMetricsSnapshot.activity_breakdown must be object")
    return PeerMetricsSnapshot(
        asset_id=payload["asset_id"],
        observed_tps=parse_metric_value(payload["observed_tps"]),
        tvl=parse_metric_value(payload["tvl"]),
        gas_fee=parse_metric_value(payload["gas_fee"]),
        activity_breakdown={
            key: parse_metric_value(value) for key, value in activity_breakdown.items()
        },
        window_start=_parse_timestamp(payload["window_start"], "window_start"),
        window_end=_parse_timestamp(payload["window_end"], "window_end"),
        observed_at=_parse_timestamp(payload["observed_at"], "observed_at"),
    )


def parse_peer_metrics_record(payload: dict[str, Any]) -> PeerMetricsRecord:
    required = {"illustrative", "peer_group", "snapshot"}
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"missing PeerMetricsRecord fields: {', '.join(missing)}")
    extra = sorted(set(payload) - required)
    if extra:
        raise ValueError(f"unexpected PeerMetricsRecord fields: {', '.join(extra)}")
    peer_group = payload["peer_group"]
    if not isinstance(peer_group, list) or not all(isinstance(item, str) for item in peer_group):
        raise ValueError("PeerMetricsRecord.peer_group must be list of strings")
    snapshot_payload = payload["snapshot"]
    if not isinstance(snapshot_payload, dict):
        raise ValueError("PeerMetricsRecord.snapshot must be object")
    illustrative = payload["illustrative"]
    if illustrative is not True:
        raise ValueError(
            f"PeerMetricsRecord.illustrative must be the boolean true, got {illustrative!r}"
        )
    return PeerMetricsRecord(
        snapshot=parse_peer_metrics_snapshot(snapshot_payload),
        peer_group=tuple(peer_group),
        illustrative=illustrative,
    )


def load_peer_metrics_fixture(path: Path) -> tuple[PeerMetricsRecord, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Peer metrics fixture must be a list")
    return tuple(parse_peer_metrics_record(item) for item in raw)


def _parse_timestamp(raw: Any, field_name: str) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"PeerMetricsSnapshot.{field_name} must be ISO timestamp string")
    timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError(f"PeerMetricsSnapshot.{field_name} must be timezone-aware")
    return timestamp.astimezone(timezone.utc)
