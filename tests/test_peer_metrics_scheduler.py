"""Tests for peer_metrics_scheduler + CachedPeerMetricsProvider."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from trustforge import peer_metrics_scheduler
from trustforge.ingestion.cache import (
    CacheMissError,
    JsonCacheBackend,
    STALE_AFTER_MULTIPLIER,
    stale_after_for,
)
from trustforge.peer_metrics import MetricValue, PeerMetricMethod, PeerMetricsSnapshot
from trustforge.peer_metrics_scheduler import (
    PEER_METRICS_STALE_AFTER_SECONDS,
    CachedPeerMetricsProvider,
    build_peer_metrics_snapshot,
    peer_metrics_cache_key,
    schedule_peer_metrics_snapshot,
)
from trustforge.throughput_gas_connector import (
    GasConnectorResult,
    ObservedGasMetric,
    TpsConnectorResult,
)
from trustforge.tvl_connector import TvlConnectorResult


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _tps_ok(_url: str = "", *, fetched_at: datetime | None = None) -> TpsConnectorResult:
    return TpsConnectorResult(
        metric=MetricValue(
            value=18.5,
            unit="count/s",
            method=PeerMetricMethod.OBSERVED,
            source="https://api.arbiscan.io/stats/tps",
        ),
        error=None,
    )


def _tvl_ok(_url: str = "", *, fetched_at: datetime | None = None) -> TvlConnectorResult:
    return TvlConnectorResult(
        metric=MetricValue(
            value=2_500_000_000.0,
            unit="usd",
            method=PeerMetricMethod.OBSERVED,
            source="https://api.llama.fi/protocol/arbitrum",
        ),
        error=None,
    )


def _gas_ok(_url: str = "", *, fetched_at: datetime | None = None) -> GasConnectorResult:
    return GasConnectorResult(
        metric=ObservedGasMetric(
            metric=MetricValue(
                value=0.03,
                unit="usd",
                method=PeerMetricMethod.OBSERVED,
                source="https://arbiscan.io/gastracker",
            ),
            native_fee=0.000012,
            usd_fee=0.03,
            tx_type="transfer",
            observed_at=(fetched_at or utc(2026, 1, 2)),
            source="https://arbiscan.io/gastracker",
        ),
        error=None,
    )


def _tps_fail(_url: str = "", **kw: object) -> TpsConnectorResult:
    return TpsConnectorResult(
        metric=None,
        error={"code": "tps_connector_error", "message": "boom"},
    )


def _tvl_fail(_url: str = "", **kw: object) -> TvlConnectorResult:
    return TvlConnectorResult(
        metric=None,
        error={"code": "tvl_connector_error", "message": "boom"},
    )


def _gas_fail(_url: str = "", **kw: object) -> GasConnectorResult:
    return GasConnectorResult(
        metric=None,
        error={"code": "gas_connector_error", "message": "boom"},
    )


# ---------------------------------------------------------------------------
# cache key
# ---------------------------------------------------------------------------


def test_cache_key_is_deterministic():
    a = peer_metrics_cache_key("asset:arb", utc(2026, 1, 2))
    b = peer_metrics_cache_key("asset:arb", utc(2026, 1, 2))
    assert a == b
    assert a.startswith("peer_metrics:asset:arb:2026-01-02T00:00:00+00:00")


def test_cache_key_rejects_naive_window_end():
    with pytest.raises(ValueError, match="timezone-aware"):
        peer_metrics_cache_key("asset:arb", datetime(2026, 1, 2))


# ---------------------------------------------------------------------------
# build_peer_metrics_snapshot — normal path
# ---------------------------------------------------------------------------


def test_build_snapshot_all_ok(monkeypatch: pytest.MonkeyPatch):
    ft = utc(2026, 1, 2, 12)
    monkeypatch.setattr(
        peer_metrics_scheduler, "fetch_tps_metric", _tps_ok
    )
    monkeypatch.setattr(
        peer_metrics_scheduler, "fetch_tvl_metric", _tvl_ok
    )
    monkeypatch.setattr(
        peer_metrics_scheduler, "fetch_gas_metric", _gas_ok
    )

    snap, outcome = build_peer_metrics_snapshot(
        "asset:arb",
        fetched_at=ft,
        window_start=utc(2026, 1, 1),
        window_end=utc(2026, 1, 2),
    )

    assert snap is not None
    assert snap.asset_id == "asset:arb"
    assert snap.observed_tps.value == 18.5
    assert snap.tvl.value == 2_500_000_000.0
    assert snap.gas_fee.value == 0.03
    assert "transfer" in snap.activity_breakdown
    assert snap.window_start == utc(2026, 1, 1)
    assert snap.window_end == utc(2026, 1, 2)
    assert snap.observed_at == ft

    assert outcome.tps is not None and outcome.tps.ok
    assert outcome.tvl is not None and outcome.tvl.ok
    assert outcome.gas is not None and outcome.gas.ok


# ---------------------------------------------------------------------------
# build_peer_metrics_snapshot — partial failure
# ---------------------------------------------------------------------------


def test_build_snapshot_one_source_fails_yields_no_snapshot(
    monkeypatch: pytest.MonkeyPatch,
):
    ft = utc(2026, 1, 2, 12)
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_tps_metric", _tps_fail)
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_tvl_metric", _tvl_ok)
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_gas_metric", _gas_ok)

    snap, outcome = build_peer_metrics_snapshot("asset:arb", fetched_at=ft)

    assert snap is None
    assert not outcome.tps.ok  # type: ignore[union-attr]
    assert outcome.tvl is not None and outcome.tvl.ok


# ---------------------------------------------------------------------------
# schedule → cache write + CachedPeerMetricsProvider read
# ---------------------------------------------------------------------------


def test_schedule_and_read_roundtrip(monkeypatch: pytest.MonkeyPatch):
    ft = utc(2026, 1, 2, 12)
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_tps_metric", _tps_ok)
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_tvl_metric", _tvl_ok)
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_gas_metric", _gas_ok)

    backend = JsonCacheBackend()

    status = schedule_peer_metrics_snapshot(
        "asset:arb",
        cache=backend,
        fetched_at=ft,
        window_start=utc(2026, 1, 1),
        window_end=utc(2026, 1, 2),
    )

    assert status["wrote"] is True
    assert status["degraded"] is False
    assert status["stale_since"] is None
    assert status["sources_ok"] == {"tps": True, "tvl": True, "gas": True}

    # Read back via CachedPeerMetricsProvider
    monkeypatch.setattr(time, "time", lambda: ft.timestamp() + 10)
    provider = CachedPeerMetricsProvider(backend)
    snap = provider.fetch("asset:arb", utc(2026, 1, 2))
    assert snap.asset_id == "asset:arb"
    assert snap.observed_tps.value == 18.5


# ---------------------------------------------------------------------------
# schedule → partial failure preserves old snapshot with stale_since
# ---------------------------------------------------------------------------


def test_schedule_partial_failure_stale_since_preserves_old(
    monkeypatch: pytest.MonkeyPatch,
):
    ft_good = utc(2026, 1, 2, 12)
    ft_bad = utc(2026, 1, 2, 13)

    backend = JsonCacheBackend()

    # --- Run 1: all ok → writes fresh snapshot ---
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_tps_metric", _tps_ok)
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_tvl_metric", _tvl_ok)
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_gas_metric", _gas_ok)
    schedule_peer_metrics_snapshot(
        "asset:arb",
        cache=backend,
        fetched_at=ft_good,
        window_start=utc(2026, 1, 1),
        window_end=utc(2026, 1, 2),
    )
    monkeypatch.setattr(time, "time", lambda: ft_good.timestamp() + 10)
    snap1 = CachedPeerMetricsProvider(backend).fetch("asset:arb", utc(2026, 1, 2))
    assert snap1.observed_tps.value == 18.5

    # --- Run 2: TVL fails → should preserve old snapshot + stale_since ---
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_tps_metric", _tps_ok)
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_tvl_metric", _tvl_fail)
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_gas_metric", _gas_ok)

    status2 = schedule_peer_metrics_snapshot(
        "asset:arb",
        cache=backend,
        fetched_at=ft_bad,
        window_start=utc(2026, 1, 1),
        window_end=utc(2026, 1, 2),
    )

    assert status2["wrote"] is True  # old snapshot carried forward
    assert status2["degraded"] is False  # not all sources stale
    stale_since = status2["stale_since"]
    assert stale_since is not None
    assert stale_since > 0

    # Read back — should still get the old data
    monkeypatch.setattr(time, "time", lambda: ft_bad.timestamp() + 10)
    snap2 = CachedPeerMetricsProvider(backend).fetch("asset:arb", utc(2026, 1, 2))
    assert snap2.observed_tps.value == 18.5  # old value preserved


# ---------------------------------------------------------------------------
# schedule → all sources fail → degraded
# ---------------------------------------------------------------------------


def test_schedule_all_sources_stale_marked_degraded(
    monkeypatch: pytest.MonkeyPatch,
):
    ft = utc(2026, 1, 2, 12)
    backend = JsonCacheBackend()

    # --- Run 1: all ok (seed data) ---
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_tps_metric", _tps_ok)
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_tvl_metric", _tvl_ok)
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_gas_metric", _gas_ok)
    schedule_peer_metrics_snapshot(
        "asset:arb",
        cache=backend,
        fetched_at=ft,
        window_start=utc(2026, 1, 1),
        window_end=utc(2026, 1, 2),
    )

    # --- Run 2: every connector fails ---
    ft_bad = utc(2026, 1, 2, 13)
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_tps_metric", _tps_fail)
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_tvl_metric", _tvl_fail)
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_gas_metric", _gas_fail)

    status = schedule_peer_metrics_snapshot(
        "asset:arb",
        cache=backend,
        fetched_at=ft_bad,
        window_start=utc(2026, 1, 1),
        window_end=utc(2026, 1, 2),
    )

    assert status["degraded"] is True
    assert status["stale_since"] is not None
    assert status["sources_ok"] == {"tps": False, "tvl": False, "gas": False}

    # Read back — old snapshot still available but metadata marks degraded
    monkeypatch.setattr(time, "time", lambda: ft_bad.timestamp() + 10)
    snap = CachedPeerMetricsProvider(backend).fetch("asset:arb", utc(2026, 1, 2))
    assert snap.observed_tps.value == 18.5  # old value was preserved


# ---------------------------------------------------------------------------
# CachedPeerMetricsProvider — cache miss
# ---------------------------------------------------------------------------


def test_provider_cache_miss_raises():
    provider = CachedPeerMetricsProvider(JsonCacheBackend())
    with pytest.raises(CacheMissError, match="no cache entry"):
        provider.fetch("asset:unknown", utc(2026, 1, 2))


# ---------------------------------------------------------------------------
# CachedPeerMetricsProvider — hard-expired entry
# ---------------------------------------------------------------------------


def test_provider_hard_expired_raises(monkeypatch: pytest.MonkeyPatch):
    """When the cached entry is older than PEER_METRICS_STALE_AFTER_SECONDS,
    fetch() raises CacheMissError."""
    ft = utc(2026, 1, 2, 12)
    backend = JsonCacheBackend()

    monkeypatch.setattr(peer_metrics_scheduler, "fetch_tps_metric", _tps_ok)
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_tvl_metric", _tvl_ok)
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_gas_metric", _gas_ok)
    schedule_peer_metrics_snapshot(
        "asset:arb",
        cache=backend,
        fetched_at=ft,
        window_start=utc(2026, 1, 1),
        window_end=utc(2026, 1, 2),
    )

    # Move time forward past the hard expiry
    future = ft.timestamp() + PEER_METRICS_STALE_AFTER_SECONDS + 60
    monkeypatch.setattr(time, "time", lambda: future)

    provider = CachedPeerMetricsProvider(backend)
    with pytest.raises(CacheMissError, match="hard-expired"):
        provider.fetch("asset:arb", utc(2026, 1, 2))


# ---------------------------------------------------------------------------
# CachedPeerMetricsProvider — fresh entry works
# ---------------------------------------------------------------------------


def test_provider_fresh_entry_returns_snapshot(monkeypatch: pytest.MonkeyPatch):
    ft = utc(2026, 1, 2, 12)
    backend = JsonCacheBackend()

    monkeypatch.setattr(peer_metrics_scheduler, "fetch_tps_metric", _tps_ok)
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_tvl_metric", _tvl_ok)
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_gas_metric", _gas_ok)
    schedule_peer_metrics_snapshot(
        "asset:arb",
        cache=backend,
        fetched_at=ft,
        window_start=utc(2026, 1, 1),
        window_end=utc(2026, 1, 2),
    )

    # Patch time so the entry is still within the fresh window
    fresh_now = ft.timestamp() + 60  # only 60s old
    monkeypatch.setattr(time, "time", lambda: fresh_now)

    provider = CachedPeerMetricsProvider(backend)
    snap = provider.fetch("asset:arb", utc(2026, 1, 2))
    assert snap.asset_id == "asset:arb"
    assert snap.observed_tps.value == 18.5


# ---------------------------------------------------------------------------
# schedule → all sources fail with NO prior snapshot → empty write, no docs
# ---------------------------------------------------------------------------


def test_schedule_all_fail_no_prior_does_not_write_docs(
    monkeypatch: pytest.MonkeyPatch,
):
    ft = utc(2026, 1, 2, 12)
    backend = JsonCacheBackend()

    monkeypatch.setattr(peer_metrics_scheduler, "fetch_tps_metric", _tps_fail)
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_tvl_metric", _tvl_fail)
    monkeypatch.setattr(peer_metrics_scheduler, "fetch_gas_metric", _gas_fail)
    status = schedule_peer_metrics_snapshot(
        "asset:arb",
        cache=backend,
        fetched_at=ft,
        window_start=utc(2026, 1, 1),
        window_end=utc(2026, 1, 2),
    )

    assert status["degraded"] is True
    assert status["wrote"] is False  # nothing to preserve

    # Provider should get a clean CacheMiss (no docs)
    with pytest.raises(CacheMissError, match="no cache entry"):
        CachedPeerMetricsProvider(backend).fetch(
            "asset:arb", utc(2026, 1, 2)
        )


# ---------------------------------------------------------------------------
# reject naive fetched_at
# ---------------------------------------------------------------------------


def test_schedule_rejects_naive_fetched_at():
    with pytest.raises(ValueError, match="timezone-aware"):
        build_peer_metrics_snapshot(
            "asset:arb",
            fetched_at=datetime(2026, 1, 2, 12),
        )
