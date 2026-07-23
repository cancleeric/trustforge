from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trustforge.peer_metrics import PeerMetricMethod
from trustforge.tvl_connector import fetch_tvl_metric, parse_tvl_metric


def utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def payload(**overrides):
    data = {
        "asset_id": "asset:arb",
        "tvl_usd": 2_500_000_000.0,
        "observed_at": "2026-01-01T12:00:00Z",
        "source": "https://api.llama.fi/protocol/arbitrum",
    }
    data.update(overrides)
    return data


def test_tvl_connector_fetch_validates_url_before_network_call() -> None:
    called = {"value": False}

    def fetch_json(_url: str):
        called["value"] = True
        return payload()

    result = fetch_tvl_metric(
        "http://169.254.169.254/latest",
        fetched_at=utc(2026, 1, 1, 13),
        fetch_json=fetch_json,
    )

    assert result.metric is None
    assert result.error is not None
    assert result.error["code"] == "tvl_connector_error"
    assert called["value"] is False


def test_tvl_connector_returns_observed_metric_for_allowlisted_source() -> None:
    metric = parse_tvl_metric(payload(), fetched_at=utc(2026, 1, 1, 13))

    assert metric.value == 2_500_000_000.0
    assert metric.unit == "usd"
    assert metric.method is PeerMetricMethod.OBSERVED
    assert metric.source == "https://api.llama.fi/protocol/arbitrum"


def test_tvl_connector_error_envelope_does_not_publish_fake_metric() -> None:
    result = fetch_tvl_metric(
        "https://api.llama.fi/protocol/arbitrum",
        fetched_at=utc(2026, 1, 1, 13),
        fetch_json=lambda _url: payload(tvl_usd=True),
    )

    assert result.metric is None
    assert result.error is not None
    assert "finite non-negative" in result.error["message"]


def test_tvl_connector_rejects_unapproved_hosts_and_schema_drift() -> None:
    with pytest.raises(ValueError, match="source host is not allowed"):
        parse_tvl_metric(payload(source="https://metadata.google.internal/latest"), fetched_at=utc(2026, 1, 1, 13))

    with pytest.raises(ValueError, match="unexpected TVL fields: internal_notes"):
        parse_tvl_metric(payload(internal_notes="do not leak"), fetched_at=utc(2026, 1, 1, 13))


def test_tvl_connector_rejects_invalid_values_and_timestamps() -> None:
    with pytest.raises(ValueError, match="finite non-negative"):
        parse_tvl_metric(payload(tvl_usd=True), fetched_at=utc(2026, 1, 1, 13))

    with pytest.raises(ValueError, match="finite non-negative"):
        parse_tvl_metric(payload(tvl_usd=-1), fetched_at=utc(2026, 1, 1, 13))

    with pytest.raises(ValueError, match="observed_at must be timezone-aware"):
        parse_tvl_metric(payload(observed_at="2026-01-01T12:00:00"), fetched_at=utc(2026, 1, 1, 13))

    with pytest.raises(ValueError, match="fetched_at must be timezone-aware"):
        parse_tvl_metric(payload(), fetched_at=datetime(2026, 1, 1, 13))


def test_tvl_connector_rejects_stale_and_future_observations() -> None:
    with pytest.raises(ValueError, match="TVL observation is stale"):
        parse_tvl_metric(payload(observed_at="2025-12-31T00:00:00Z"), fetched_at=utc(2026, 1, 2, 1))

    with pytest.raises(ValueError, match="TVL observation is in the future"):
        parse_tvl_metric(payload(observed_at="2027-01-01T00:00:00Z"), fetched_at=utc(2026, 1, 1, 13))
