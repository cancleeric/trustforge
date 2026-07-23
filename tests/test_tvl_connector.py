from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trustforge.peer_metrics import PeerMetricMethod
from trustforge.tvl_connector import parse_tvl_metric


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


def test_tvl_connector_returns_observed_metric_for_allowlisted_source() -> None:
    metric = parse_tvl_metric(payload(), fetched_at=utc(2026, 1, 1, 13))

    assert metric.value == 2_500_000_000.0
    assert metric.unit == "usd"
    assert metric.method is PeerMetricMethod.OBSERVED
    assert metric.source == "https://api.llama.fi/protocol/arbitrum"


def test_tvl_connector_rejects_unapproved_hosts_and_schema_drift() -> None:
    with pytest.raises(ValueError, match="source host is not allowed"):
        parse_tvl_metric(payload(source="https://metadata.google.internal/latest"), fetched_at=utc(2026, 1, 1, 13))

    with pytest.raises(ValueError, match="unexpected TVL fields: internal_notes"):
        parse_tvl_metric(payload(internal_notes="do not leak"), fetched_at=utc(2026, 1, 1, 13))


def test_tvl_connector_rejects_invalid_values_and_timestamps() -> None:
    with pytest.raises(ValueError, match="finite non-negative"):
        parse_tvl_metric(payload(tvl_usd=-1), fetched_at=utc(2026, 1, 1, 13))

    with pytest.raises(ValueError, match="observed_at must be timezone-aware"):
        parse_tvl_metric(payload(observed_at="2026-01-01T12:00:00"), fetched_at=utc(2026, 1, 1, 13))

    with pytest.raises(ValueError, match="fetched_at must be timezone-aware"):
        parse_tvl_metric(payload(), fetched_at=datetime(2026, 1, 1, 13))


def test_tvl_connector_rejects_stale_observations() -> None:
    with pytest.raises(ValueError, match="TVL observation is stale"):
        parse_tvl_metric(payload(observed_at="2025-12-31T00:00:00Z"), fetched_at=utc(2026, 1, 2, 1))
