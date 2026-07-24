from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.error import HTTPError

import pytest

from trustforge import tvl_connector
from trustforge.ingestion import safe_fetch
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


def test_tvl_connector_fetch_validates_url_before_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"value": False}

    def fetch_bytes(_url: str):
        called["value"] = True
        return json.dumps(payload()).encode("utf-8")

    monkeypatch.setattr(tvl_connector, "_fetch_url", fetch_bytes)

    result = fetch_tvl_metric(
        "http://169.254.169.254/latest",
        fetched_at=utc(2026, 1, 1, 13),
    )

    assert result.metric is None
    assert result.error is not None
    assert result.error["code"] == "tvl_connector_error"
    assert called["value"] is False


def test_tvl_connector_uses_shared_safe_fetch_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_fetch_url(url: str, *, user_agent: str, timeout: float, max_bytes: int):
        calls.append((url, user_agent, timeout, max_bytes))
        return json.dumps(payload(source=url)).encode("utf-8")

    monkeypatch.setattr(safe_fetch, "fetch_url", fake_fetch_url)

    result = fetch_tvl_metric(
        "https://api.llama.fi/protocol/arbitrum",
        fetched_at=utc(2026, 1, 1, 13),
    )

    assert result.ok is True
    assert result.metric is not None
    assert result.metric.source == "https://api.llama.fi/protocol/arbitrum"
    assert calls == [("https://api.llama.fi/protocol/arbitrum", "TrustForge/1.0 (tvl-connector)", 5, 65537)]


@pytest.mark.parametrize("parameter", ["fetch_json", "fetch_bytes"])
def test_tvl_connector_exposes_no_fetcher_injection_parameters(parameter: str) -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        fetch_tvl_metric(
            "https://api.llama.fi/protocol/arbitrum",
            fetched_at=utc(2026, 1, 1, 13),
            **{parameter: lambda _url: json.dumps(payload()).encode("utf-8")},
        )


def test_tvl_connector_returns_observed_metric_for_allowlisted_source() -> None:
    metric = parse_tvl_metric(payload(), fetched_at=utc(2026, 1, 1, 13))

    assert metric.value == 2_500_000_000.0
    assert metric.unit == "usd"
    assert metric.method is PeerMetricMethod.OBSERVED
    assert metric.source == "https://api.llama.fi/protocol/arbitrum"


def test_tvl_connector_error_envelope_does_not_publish_fake_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tvl_connector,
        "_fetch_url",
        lambda _url: json.dumps(payload(tvl_usd=True)).encode("utf-8"),
    )

    result = fetch_tvl_metric(
        "https://api.llama.fi/protocol/arbitrum",
        fetched_at=utc(2026, 1, 1, 13),
    )

    assert result.metric is None
    assert result.error is not None
    assert "finite non-negative" in result.error["message"]


def test_tvl_connector_rate_limit_error_envelope_has_no_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def rate_limited(_url: str) -> bytes:
        raise HTTPError(_url, 429, "Too Many Requests", {"Retry-After": "60"}, None)

    monkeypatch.setattr(tvl_connector, "_fetch_url", rate_limited)

    result = fetch_tvl_metric(
        "https://api.llama.fi/protocol/arbitrum",
        fetched_at=utc(2026, 1, 1, 13),
    )

    assert result.metric is None
    assert result.error == {"code": "rate_limited", "message": "TVL source rate limited"}


@pytest.mark.parametrize(
    "reason",
    [
        "initial host resolved to private address",
        "redirect target resolved to private address",
    ],
)
def test_tvl_connector_ssrf_blocks_never_publish_metric(
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
) -> None:
    def blocked(url: str) -> bytes:
        raise safe_fetch.SSRFBlockedError(url, reason)

    monkeypatch.setattr(tvl_connector, "_fetch_url", blocked)

    result = fetch_tvl_metric(
        "https://api.llama.fi/protocol/arbitrum",
        fetched_at=utc(2026, 1, 1, 13),
    )

    assert result.metric is None
    assert result.error is not None
    assert result.error["code"] == "tvl_connector_error"
    assert "SSRF blocked" in result.error["message"]


def test_tvl_connector_oversize_response_never_publishes_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oversized_response = json.dumps(payload()).encode("utf-8")
    oversized_response += b" " * (tvl_connector._MAX_BYTES + 1 - len(oversized_response))
    monkeypatch.setattr(safe_fetch, "fetch_url", lambda *_args, **_kwargs: oversized_response)

    result = fetch_tvl_metric(
        "https://api.llama.fi/protocol/arbitrum",
        fetched_at=utc(2026, 1, 1, 13),
    )

    assert result.metric is None
    assert result.error is not None
    assert result.error["code"] == "tvl_connector_error"
    assert "exceeds maximum size" in result.error["message"]


@pytest.mark.parametrize(
    "claimed_source",
    [
        "https://defillama.com/protocol/arbitrum",
        "https://api.llama.fi/protocol/ethereum",
    ],
)
def test_tvl_connector_rejects_allowlisted_but_mismatched_source(
    monkeypatch: pytest.MonkeyPatch,
    claimed_source: str,
) -> None:
    monkeypatch.setattr(
        tvl_connector,
        "_fetch_url",
        lambda _url: json.dumps(payload(source=claimed_source)).encode("utf-8"),
    )

    result = fetch_tvl_metric(
        "https://api.llama.fi/protocol/arbitrum",
        fetched_at=utc(2026, 1, 1, 13),
    )

    assert result.metric is None
    assert result.error is not None
    assert result.error["code"] == "tvl_connector_error"
    assert "source does not match fetched URL" in result.error["message"]


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
