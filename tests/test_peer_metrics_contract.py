from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trustforge.peer_metrics import (
    PEER_METRICS_SCHEMA_VERSION,
    MetricValue,
    PeerMetricMethod,
    PeerMetricsSnapshot,
    snapshots_comparable,
)
from trustforge.data_contracts import contract_schemas


def utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def metric(value: float | None, unit: str = "count/s") -> MetricValue:
    return MetricValue(
        value=value,
        unit=unit,
        method=PeerMetricMethod.OBSERVED,
        source="fixture://peer-metrics",
    )


def snapshot(asset_id: str = "asset:arb", *, tvl: float | None = 2_500_000_000.0) -> PeerMetricsSnapshot:
    return PeerMetricsSnapshot(
        asset_id=asset_id,
        observed_tps=metric(18.5),
        tvl=metric(tvl, "usd"),
        gas_fee=metric(0.03, "usd/transfer"),
        activity_breakdown={
            "swap": metric(11.2),
            "bridge": metric(3.4),
        },
        window_start=utc(2026, 1, 1),
        window_end=utc(2026, 1, 2),
        observed_at=utc(2026, 1, 2),
    )


def test_peer_metrics_snapshot_serializes_observed_metrics_and_lineage() -> None:
    payload = snapshot().to_dict()

    assert payload["schema_version"] == PEER_METRICS_SCHEMA_VERSION
    assert payload["observed_tps"]["value"] == 18.5
    assert payload["observed_tps"]["method"] == "observed"
    assert payload["tvl"]["unit"] == "usd"
    assert payload["gas_fee"]["unit"] == "usd/transfer"
    assert payload["activity_breakdown"]["bridge"]["source"] == "fixture://peer-metrics"
    assert payload["window_start"] == "2026-01-01T00:00:00+00:00"


def test_peer_metrics_schema_allows_null_missing_values() -> None:
    schema = contract_schemas()["PeerMetricsSnapshot"]

    assert schema["properties"]["schema_version"]["const"] == PEER_METRICS_SCHEMA_VERSION
    assert schema["properties"]["observed_tps"]["properties"]["value"]["type"] == ["number", "null"]
    assert schema["properties"]["activity_breakdown"]["additionalProperties"]["properties"]["method"][
        "enum"
    ] == ["observed", "estimated", "reported", "unknown"]


def test_peer_metrics_missing_values_remain_null_not_zero() -> None:
    payload = snapshot(tvl=None).to_dict()

    assert payload["tvl"]["value"] is None
    assert payload["tvl"]["value"] != 0


def test_peer_metrics_comparability_requires_same_window_method_unit_and_values() -> None:
    left = snapshot("asset:arb")
    right = snapshot("asset:op")

    assert snapshots_comparable(left, right) == (True, None)

    changed_unit = PeerMetricsSnapshot(
        asset_id="asset:op",
        observed_tps=metric(18.5, "tx/s"),
        tvl=metric(2_500_000_000.0, "usd"),
        gas_fee=metric(0.03, "usd/transfer"),
        activity_breakdown={},
        window_start=utc(2026, 1, 1),
        window_end=utc(2026, 1, 2),
        observed_at=utc(2026, 1, 2),
    )
    assert snapshots_comparable(left, changed_unit) == (False, "observed_tps unit differs")
    assert snapshots_comparable(left, snapshot("asset:op", tvl=None)) == (False, "tvl missing")


def test_peer_metrics_reject_invalid_values_and_windows() -> None:
    with pytest.raises(ValueError, match="finite non-negative"):
        metric(-1)

    with pytest.raises(ValueError, match="window_end must be after window_start"):
        PeerMetricsSnapshot(
            asset_id="asset:arb",
            observed_tps=metric(1),
            tvl=metric(1, "usd"),
            gas_fee=metric(1, "usd/transfer"),
            activity_breakdown={},
            window_start=utc(2026, 1, 2),
            window_end=utc(2026, 1, 1),
            observed_at=utc(2026, 1, 2),
        )

    with pytest.raises(ValueError, match="observed_at must be timezone-aware"):
        PeerMetricsSnapshot(
            asset_id="asset:arb",
            observed_tps=metric(1),
            tvl=metric(1, "usd"),
            gas_fee=metric(1, "usd/transfer"),
            activity_breakdown={},
            window_start=utc(2026, 1, 1),
            window_end=utc(2026, 1, 2),
            observed_at=datetime(2026, 1, 2),
        )
