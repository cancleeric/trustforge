from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trustforge.peer_metrics import PeerMetricMethod
from trustforge.throughput_gas_connector import parse_gas_metric, parse_observed_tps_metric


def utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def tps_payload(**overrides):
    payload = {
        "asset_id": "asset:arb",
        "observed_tps": 18.5,
        "observed_at": "2026-01-01T12:00:00Z",
        "source": "https://api.arbiscan.io/stats/tps",
    }
    payload.update(overrides)
    return payload


def gas_payload(**overrides):
    payload = {
        "asset_id": "asset:arb",
        "native_fee": 0.000012,
        "usd_fee": 0.03,
        "tx_type": "transfer",
        "observed_at": "2026-01-01T12:00:00Z",
        "source": "https://arbiscan.io/gastracker",
    }
    payload.update(overrides)
    return payload


def test_observed_tps_connector_keeps_observed_separate_from_theoretical() -> None:
    metric = parse_observed_tps_metric(tps_payload(), fetched_at=utc(2026, 1, 1, 13))

    assert metric.value == 18.5
    assert metric.unit == "count/s"
    assert metric.method is PeerMetricMethod.OBSERVED
    assert "theoretical" not in metric.source


def test_gas_connector_preserves_native_usd_tx_type_and_source() -> None:
    metric = parse_gas_metric(gas_payload(), fetched_at=utc(2026, 1, 1, 13))

    assert metric.value == 0.03
    assert metric.unit == "usd/transfer"
    assert metric.method is PeerMetricMethod.OBSERVED
    assert metric.source == "https://arbiscan.io/gastracker#native=1.2e-05"


def test_network_metric_connectors_reject_bad_hosts_missing_fields_and_nonfinite_values() -> None:
    with pytest.raises(ValueError, match="source host is not allowed"):
        parse_observed_tps_metric(tps_payload(source="http://169.254.169.254/latest"), fetched_at=utc(2026, 1, 1, 13))

    with pytest.raises(ValueError, match="missing TPS fields: observed_tps"):
        parse_observed_tps_metric(
            {key: value for key, value in tps_payload().items() if key != "observed_tps"},
            fetched_at=utc(2026, 1, 1, 13),
        )

    with pytest.raises(ValueError, match="Gas usd_fee must be finite non-negative"):
        parse_gas_metric(gas_payload(usd_fee=float("inf")), fetched_at=utc(2026, 1, 1, 13))


def test_network_metric_connectors_reject_stale_and_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="TPS observation is stale"):
        parse_observed_tps_metric(tps_payload(observed_at="2026-01-01T00:00:00Z"), fetched_at=utc(2026, 1, 1, 7))

    with pytest.raises(ValueError, match="observed_at must be timezone-aware"):
        parse_gas_metric(gas_payload(observed_at="2026-01-01T12:00:00"), fetched_at=utc(2026, 1, 1, 13))

    with pytest.raises(ValueError, match="fetched_at must be timezone-aware"):
        parse_gas_metric(gas_payload(), fetched_at=datetime(2026, 1, 1, 13))
