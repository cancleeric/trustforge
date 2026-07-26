from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.error import HTTPError

import pytest

from trustforge import throughput_gas_connector
from trustforge.ingestion import safe_fetch
from trustforge.peer_metrics import PeerMetricMethod
from trustforge.throughput_gas_connector import (
    GasConnectorResult,
    ObservedGasMetric,
    TpsConnectorResult,
    fetch_gas_metric,
    fetch_tps_metric,
    parse_gas_metric,
    parse_observed_tps_metric,
)


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


# ---------------------------------------------------------------------------
# Fetch boundary tests


def test_fetch_tps_metric_accepts_valid_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch(url: str, *, user_agent: str, timeout: float, max_bytes: int) -> bytes:
        return json.dumps(tps_payload(source=url)).encode("utf-8")
    monkeypatch.setattr(safe_fetch, "fetch_url", fake_fetch)

    result = fetch_tps_metric("https://api.arbiscan.io/stats/tps", fetched_at=utc(2026, 1, 1, 13))
    assert result.ok is True
    assert result.metric is not None
    assert result.metric.value == 18.5
    assert result.error is None


def test_fetch_gas_metric_accepts_valid_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch(url: str, *, user_agent: str, timeout: float, max_bytes: int) -> bytes:
        return json.dumps(gas_payload(source=url)).encode("utf-8")
    monkeypatch.setattr(safe_fetch, "fetch_url", fake_fetch)

    result = fetch_gas_metric("https://arbiscan.io/gastracker", fetched_at=utc(2026, 1, 1, 13))
    assert result.ok is True
    assert result.metric is not None
    assert result.metric.metric.value == 0.03
    assert result.error is None


def test_fetch_tps_metric_rejects_url_before_network_call(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"value": False}
    def fake_fetch(_url: str, *, user_agent: str, timeout: float, max_bytes: int) -> bytes:
        called["value"] = True
        return json.dumps(tps_payload()).encode("utf-8")
    monkeypatch.setattr(safe_fetch, "fetch_url", fake_fetch)

    result = fetch_tps_metric("http://169.254.169.254/latest", fetched_at=utc(2026, 1, 1, 13))
    assert result.metric is None
    assert result.error is not None
    assert called["value"] is False


def test_fetch_gas_metric_rate_limit_returns_error_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    def rate_limited(_url: str) -> bytes:
        raise HTTPError(_url, 429, "Too Many Requests", {"Retry-After": "60"}, None)
    monkeypatch.setattr(throughput_gas_connector, "_fetch_network_metric", rate_limited)

    result = fetch_gas_metric("https://arbiscan.io/gastracker", fetched_at=utc(2026, 1, 1, 13))
    assert result.metric is None
    assert result.error == {"code": "rate_limited", "message": "Gas source rate limited"}


def test_fetch_tps_metric_http_error_returns_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    def server_error(_url: str) -> bytes:
        raise HTTPError(_url, 503, "Unavailable", {}, None)
    monkeypatch.setattr(throughput_gas_connector, "_fetch_network_metric", server_error)

    result = fetch_tps_metric("https://api.arbiscan.io/stats/tps", fetched_at=utc(2026, 1, 1, 13))
    assert result.metric is None
    assert result.error is not None
    assert result.error["code"] == "tps_connector_error"


def test_fetch_tps_metric_mismatched_source_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch(_url: str, *, user_agent: str, timeout: float, max_bytes: int) -> bytes:
        return json.dumps(tps_payload(source="https://etherscan.io/stats")).encode("utf-8")
    monkeypatch.setattr(safe_fetch, "fetch_url", fake_fetch)

    result = fetch_tps_metric("https://api.arbiscan.io/stats/tps", fetched_at=utc(2026, 1, 1, 13))
    assert result.metric is None
    assert result.error is not None
    assert "does not match fetched URL" in result.error["message"]


def test_fetch_tps_metric_injects_source_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    data = tps_payload()
    del data["source"]
    def fake_fetch(_url: str, *, user_agent: str, timeout: float, max_bytes: int) -> bytes:
        return json.dumps(data).encode("utf-8")
    monkeypatch.setattr(safe_fetch, "fetch_url", fake_fetch)

    result = fetch_tps_metric("https://api.arbiscan.io/stats/tps", fetched_at=utc(2026, 1, 1, 13))
    assert result.ok is True
    assert result.metric is not None
    assert result.metric.source == "https://api.arbiscan.io/stats/tps"


def test_fetch_tps_metric_oversize_response_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    oversized = json.dumps(tps_payload()).encode("utf-8")
    oversized += b" " * (throughput_gas_connector._MAX_BYTES + 1)
    monkeypatch.setattr(safe_fetch, "fetch_url", lambda *_a, **_k: oversized)

    result = fetch_tps_metric("https://api.arbiscan.io/stats/tps", fetched_at=utc(2026, 1, 1, 13))
    assert result.metric is None
    assert result.error is not None
    assert result.error["code"] == "tps_connector_error"


def test_fetch_tps_metric_non_dict_json_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safe_fetch, "fetch_url", lambda *_a, **_k: b"[1, 2, 3]")

    result = fetch_tps_metric("https://api.arbiscan.io/stats/tps", fetched_at=utc(2026, 1, 1, 13))
    assert result.metric is None
    assert result.error is not None
    assert result.error["code"] == "tps_connector_error"


def test_fetch_tps_metric_rejects_stale_in_fetch_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch(_url: str, *, user_agent: str, timeout: float, max_bytes: int) -> bytes:
        return json.dumps(tps_payload(observed_at="2025-12-31T00:00:00Z")).encode("utf-8")
    monkeypatch.setattr(safe_fetch, "fetch_url", fake_fetch)

    result = fetch_tps_metric("https://api.arbiscan.io/stats/tps", fetched_at=utc(2026, 1, 1, 13))
    assert result.metric is None
    assert result.error is not None
    assert "stale" in result.error["message"]


def test_connector_result_ok_property() -> None:
    assert TpsConnectorResult(metric=None, error=None).ok is False
    assert TpsConnectorResult(metric=None, error={"code": "x"}).ok is False
    assert TpsConnectorResult(
        metric=parse_observed_tps_metric(tps_payload(), fetched_at=utc(2026, 1, 1, 13)),
        error=None,
    ).ok is True
