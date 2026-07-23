from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trustforge.peer_metrics import PeerMetricMethod
from trustforge.throughput_gas_connector import ObservedGasMetric, parse_gas_metric, parse_observed_tps_metric


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


def test_observed_tps_connector_rejects_theoretical_tps_shape() -> None:
    with pytest.raises(ValueError, match="unexpected TPS fields: theoretical_tps"):
        parse_observed_tps_metric(tps_payload(theoretical_tps=40000), fetched_at=utc(2026, 1, 1, 13))

    metric = parse_observed_tps_metric(tps_payload(), fetched_at=utc(2026, 1, 1, 13))
    assert metric.value == 18.5
    assert metric.unit == "count/s"
    assert metric.method is PeerMetricMethod.OBSERVED


def test_gas_connector_preserves_native_usd_tx_type_time_and_source() -> None:
    gas = parse_gas_metric(gas_payload(), fetched_at=utc(2026, 1, 1, 13))

    assert isinstance(gas, ObservedGasMetric)
    assert gas.metric.value == 0.03
    assert gas.metric.unit == "usd"
    assert gas.native_fee == 0.000012
    assert gas.usd_fee == 0.03
    assert gas.tx_type == "transfer"
    assert gas.observed_at == utc(2026, 1, 1, 12)
    assert gas.source == "https://arbiscan.io/gastracker"
    assert gas.to_dict()["source"] == "https://arbiscan.io/gastracker"


def test_network_metric_connectors_reject_bad_hosts_schemes_and_missing_fields() -> None:
    with pytest.raises(ValueError, match="source URL must use https"):
        parse_observed_tps_metric(tps_payload(source="file://etherscan.io/etc/passwd"), fetched_at=utc(2026, 1, 1, 13))

    with pytest.raises(ValueError, match="source host is not allowed"):
        parse_observed_tps_metric(tps_payload(source="https://evil.example/stats"), fetched_at=utc(2026, 1, 1, 13))

    with pytest.raises(ValueError, match="missing TPS fields: observed_tps"):
        parse_observed_tps_metric(
            {key: value for key, value in tps_payload().items() if key != "observed_tps"},
            fetched_at=utc(2026, 1, 1, 13),
        )


def test_network_metric_connectors_reject_nonfinite_bool_and_unapproved_tx_types() -> None:
    with pytest.raises(ValueError, match="TPS value must be finite non-negative"):
        parse_observed_tps_metric(tps_payload(observed_tps=True), fetched_at=utc(2026, 1, 1, 13))

    with pytest.raises(ValueError, match="Gas usd_fee must be finite non-negative"):
        parse_gas_metric(gas_payload(usd_fee=float("inf")), fetched_at=utc(2026, 1, 1, 13))

    with pytest.raises(ValueError, match="Gas usd_fee must be finite non-negative"):
        parse_gas_metric(gas_payload(usd_fee=True), fetched_at=utc(2026, 1, 1, 13))

    with pytest.raises(ValueError, match="Gas tx_type must be approved"):
        parse_gas_metric(gas_payload(tx_type="swap/complex"), fetched_at=utc(2026, 1, 1, 13))


def test_network_metric_connectors_reject_stale_future_and_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="TPS observation is stale"):
        parse_observed_tps_metric(tps_payload(observed_at="2026-01-01T00:00:00Z"), fetched_at=utc(2026, 1, 1, 7))

    with pytest.raises(ValueError, match="TPS observation is in the future"):
        parse_observed_tps_metric(tps_payload(observed_at="2026-01-02T12:00:00Z"), fetched_at=utc(2026, 1, 1, 13))

    with pytest.raises(ValueError, match="observed_at must be timezone-aware"):
        parse_gas_metric(gas_payload(observed_at="2026-01-01T12:00:00"), fetched_at=utc(2026, 1, 1, 13))

    with pytest.raises(ValueError, match="fetched_at must be timezone-aware"):
        parse_gas_metric(gas_payload(), fetched_at=datetime(2026, 1, 1, 13))
