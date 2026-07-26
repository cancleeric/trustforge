"""Peer metrics snapshot scheduler: assemble TVL+TPS+Gas into cache entries.

The scheduler is the **only** code path that calls real connectors for peer
metrics.  The request-path ``CachedPeerMetricsProvider`` reads exclusively from
cache and never triggers a live connector call.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from trustforge.ingestion.cache import (
    CacheBackend,
    CacheMissError,
    cache_get,
    cache_set,
    get_cache_backend,
    stale_after_for,
)
from trustforge.peer_metrics import (
    MetricValue,
    PeerMetricMethod,
    PeerMetricsSnapshot,
    utc_timestamp,
)
from trustforge.throughput_gas_connector import (
    GasConnectorResult,
    ObservedGasMetric,
    TpsConnectorResult,
    fetch_gas_metric,
    fetch_tps_metric,
)
from trustforge.tvl_connector import TvlConnectorResult, fetch_tvl_metric

# ---------------------------------------------------------------------------
# Cache-key convention for peer metrics snapshots
# ---------------------------------------------------------------------------

PEER_METRICS_CACHE_SOURCE = "peer_metrics"

# Default URLs — derivable from asset_id.  Callers may override.
_PEER_METRICS_DEFAULT_TVL_URL_TEMPLATE = "https://api.llama.fi/protocol/{slug}"
_PEER_METRICS_DEFAULT_TPS_URL_TEMPLATE = "https://api.arbiscan.io/stats/tps"
_PEER_METRICS_DEFAULT_GAS_URL_TEMPLATE = "https://arbiscan.io/gastracker"

# How long a snapshot in cache is considered fresh (before the hard-expiry
# multiplier is applied).  Aligned with the same DEFAULT_REFRESH_INTERVAL /
# DEFAULT_STALE_AFTER convention used by every other connector source.
PEER_METRICS_REFRESH_INTERVAL_SECONDS = 15 * 60  # 15 min cron cadence
PEER_METRICS_STALE_AFTER_SECONDS = int(
    stale_after_for(PEER_METRICS_REFRESH_INTERVAL_SECONDS)
)  # 45 min hard expiry (with STALE_AFTER_MULTIPLIER)


# ---------------------------------------------------------------------------
# Public helpers for key derivation
# ---------------------------------------------------------------------------


def peer_metrics_cache_key(asset_id: str, window_end: datetime) -> str:
    """Return the deterministic cache key for a peer-metrics snapshot.

    Format: ``peer_metrics:{asset_id}:{window_end_iso}``
    """
    if window_end.tzinfo is None:
        raise ValueError("window_end must be timezone-aware")
    return (
        f"{PEER_METRICS_CACHE_SOURCE}:{asset_id}:"
        f"{window_end.astimezone(timezone.utc).isoformat()}"
    )


def _asset_slug(asset_id: str) -> str:
    """Heuristic: turn ``asset:arb`` → ``arbitrum`` for DeFi Llama."""
    known: dict[str, str] = {
        "asset:arb": "arbitrum",
        "asset:op": "optimism",
        "asset:matic": "polygon",
        "asset:eth": "ethereum",
        "asset:sol": "solana",
    }
    return known.get(asset_id, asset_id.rsplit(":", 1)[-1].lower())


# ---------------------------------------------------------------------------
# Snapshot construction
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _SourceOutcome:
    tps: TpsConnectorResult | None = None
    tvl: TvlConnectorResult | None = None
    gas: GasConnectorResult | None = None


def build_peer_metrics_snapshot(
    asset_id: str,
    *,
    fetched_at: datetime,
    tps_url: str | None = None,
    tvl_url: str | None = None,
    gas_url: str | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> tuple[PeerMetricsSnapshot | None, _SourceOutcome]:
    """Fetch TVL+TPS+Gas from live connectors and assemble a single snapshot.

    Returns ``(snapshot, outcome)`` — even when a source fails the partial
    ``outcome`` is carried forward so the caller
    (``schedule_peer_metrics_snapshot``) can decide whether to preserve a
    stale entry.
    """
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at must be timezone-aware")

    resolved_window_end = window_end if window_end is not None else fetched_at
    resolved_window_start = (
        window_start if window_start is not None else resolved_window_end
    )

    # --- fetch each connector independently ---
    tps_result = fetch_tps_metric(
        tps_url or _PEER_METRICS_DEFAULT_TPS_URL_TEMPLATE,
        fetched_at=fetched_at,
    )
    tvl_result = fetch_tvl_metric(
        tvl_url
        or _PEER_METRICS_DEFAULT_TVL_URL_TEMPLATE.format(slug=_asset_slug(asset_id)),
        fetched_at=fetched_at,
    )
    gas_result = fetch_gas_metric(
        gas_url or _PEER_METRICS_DEFAULT_GAS_URL_TEMPLATE,
        fetched_at=fetched_at,
    )

    outcome = _SourceOutcome(tps=tps_result, tvl=tvl_result, gas=gas_result)

    # --- all three must be ok to form a complete snapshot ---
    if not (tps_result.ok and tvl_result.ok and gas_result.ok):
        return None, outcome

    # Build activity_breakdown from the gas metric's tx_type.
    gas_metric: ObservedGasMetric = gas_result.metric  # type: ignore[assignment]
    activity_breakdown: dict[str, MetricValue]
    if gas_metric is not None:
        activity_breakdown = {gas_metric.tx_type: gas_metric.metric}
    else:
        activity_breakdown = {}

    # Guard: PeerMetricsSnapshot requires at least one entry.
    if not activity_breakdown:
        activity_breakdown = {
            "default": MetricValue(
                value=0.0,
                unit="count/s",
                method=PeerMetricMethod.UNKNOWN,
                source="peer_metrics_scheduler",
            )
        }

    snapshot = PeerMetricsSnapshot(
        asset_id=asset_id,
        observed_tps=tps_result.metric,  # type: ignore[arg-type]
        tvl=tvl_result.metric,  # type: ignore[arg-type]
        gas_fee=gas_metric.metric,
        activity_breakdown=activity_breakdown,
        window_start=resolved_window_start,
        window_end=resolved_window_end,
        observed_at=fetched_at,
    )
    return snapshot, outcome


# ---------------------------------------------------------------------------
# Cache-entry helpers
# ---------------------------------------------------------------------------


def _snapshot_to_doc(
    snapshot: PeerMetricsSnapshot,
    *,
    stale_since: float | None = None,
    degraded: bool = False,
) -> dict[str, Any]:
    """Serialize *snapshot* into a single dict including scheduler metadata.

    The extra keys ``_stale_since`` and ``_degraded`` survive the roundtrip
    through ``cache_set`` / ``cache_get`` because they are embedded inside
    ``docs[0]``.
    """
    payload = snapshot.to_dict()
    payload["_stale_since"] = stale_since
    payload["_degraded"] = degraded
    return payload


def _load_cached_metadata(
    cache: CacheBackend,
    cache_key_val: str,
) -> dict[str, Any]:
    """Read the cached entry and return typed metadata.

    Returns a dict with keys:
      ``docs`` (list | None), ``fetched_at`` (float | None),
      ``stale_since`` (float | None), ``degraded`` (bool).
    """
    entry = cache_get(cache, cache_key_val)
    if entry is None:
        return {
            "docs": None,
            "fetched_at": None,
            "stale_since": None,
            "degraded": False,
        }
    docs: list[dict[str, Any]] | None = entry.get("docs")  # type: ignore[assignment]
    fetched_at_raw = entry.get("fetched_at")
    fetched_at = float(fetched_at_raw) if fetched_at_raw else None
    stale_since = None
    degraded = False
    if docs and isinstance(docs[0], dict):
        doc0 = docs[0]
        stale_meta = doc0.get("_stale_since")
        if stale_meta is not None:
            stale_since = float(stale_meta)
        degraded_meta = doc0.get("_degraded")
        if degraded_meta is True:
            degraded = True
    return {
        "docs": docs,
        "fetched_at": fetched_at,
        "stale_since": stale_since,
        "degraded": degraded,
    }


# ---------------------------------------------------------------------------
# Scheduler entry-point: assemble + cache
# ---------------------------------------------------------------------------


def schedule_peer_metrics_snapshot(
    asset_id: str,
    *,
    cache: CacheBackend | None = None,
    fetched_at: datetime | None = None,
    tps_url: str | None = None,
    tvl_url: str | None = None,
    gas_url: str | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> dict[str, Any]:
    """Fetch all three connectors, assemble a snapshot, and write to cache.

    Returns a status dict for observability::

        {
            "asset_id": str,
            "cache_key": str,
            "fetched_at": float,
            "wrote": bool,
            "degraded": bool,
            "stale_since": float | None,
            "sources_ok": {"tps": bool, "tvl": bool, "gas": bool},
        }

    On partial failure the scheduler preserves the most-recent valid snapshot
    and stamps ``stale_since``.  When **every** source fails the entry is
    marked ``degraded``.
    """
    resolved_fetched_at = (
        fetched_at if fetched_at is not None else datetime.now(timezone.utc)
    )
    fetched_at_epoch = resolved_fetched_at.timestamp()

    resolved_cache = cache if cache is not None else get_cache_backend()

    resolved_window_end = (
        window_end if window_end is not None else resolved_fetched_at
    )
    resolved_window_start = (
        window_start if window_start is not None else resolved_window_end
    )
    ck = peer_metrics_cache_key(asset_id, resolved_window_end)

    # 1. Attempt fresh snapshot
    snapshot, outcome = build_peer_metrics_snapshot(
        asset_id,
        fetched_at=resolved_fetched_at,
        tps_url=tps_url,
        tvl_url=tvl_url,
        gas_url=gas_url,
        window_start=resolved_window_start,
        window_end=resolved_window_end,
    )

    sources_ok = {
        "tps": outcome.tps is not None and outcome.tps.ok,
        "tvl": outcome.tvl is not None and outcome.tvl.ok,
        "gas": outcome.gas is not None and outcome.gas.ok,
    }
    all_ok = all(sources_ok.values())
    none_ok = not any(sources_ok.values())

    # 2. Read previous entry (if any)
    prev = _load_cached_metadata(resolved_cache, ck)
    prev_docs: list[dict[str, Any]] | None = prev["docs"]
    prev_stale_since: float | None = prev["stale_since"]
    prev_degraded: bool = prev["degraded"]

    now_epoch = time.time()
    stale_since: float | None = None
    degraded = False

    if all_ok:
        # Happy path: everything fresh
        doc = _snapshot_to_doc(snapshot, stale_since=None, degraded=False)  # type: ignore[arg-type]
        cache_set(
            resolved_cache,
            ck,
            [doc],
            fetched_at_epoch,
            ttl_seconds=PEER_METRICS_STALE_AFTER_SECONDS,
        )
        return {
            "asset_id": asset_id,
            "cache_key": ck,
            "fetched_at": fetched_at_epoch,
            "wrote": True,
            "degraded": False,
            "stale_since": None,
            "sources_ok": sources_ok,
        }

    # 3. Partial or total failure — carry forward previous snapshot if available
    if prev_stale_since is not None:
        # Staleness clock was already ticking — keep the original timestamp
        stale_since = prev_stale_since
    else:
        stale_since = now_epoch

    degraded = none_ok

    # If we have a previous snapshot, carry it forward with updated staleness.
    # Otherwise write an empty entry so the provider gets a firm CacheMiss on read.
    if prev_docs:
        doc_to_write = dict(prev_docs[0])
        doc_to_write["_stale_since"] = stale_since
        doc_to_write["_degraded"] = degraded
        docs_to_write = [doc_to_write]
    else:
        # No prior snapshot to preserve, and no fresh data — skip write
        # so the provider gets a clean CacheMiss on next read.
        return {
            "asset_id": asset_id,
            "cache_key": ck,
            "fetched_at": fetched_at_epoch,
            "wrote": False,
            "degraded": degraded,
            "stale_since": stale_since,
            "sources_ok": sources_ok,
        }

    cache_set(
        resolved_cache,
        ck,
        docs_to_write,
        fetched_at_epoch,
        ttl_seconds=PEER_METRICS_STALE_AFTER_SECONDS,
    )
    return {
        "asset_id": asset_id,
        "cache_key": ck,
        "fetched_at": fetched_at_epoch,
        "wrote": len(docs_to_write) > 0,
        "degraded": degraded,
        "stale_since": stale_since,
        "sources_ok": sources_ok,
    }


# ---------------------------------------------------------------------------
# Request-path cached provider
# ---------------------------------------------------------------------------


class CachedPeerMetricsProvider:
    """Request-path provider: reads peer-metrics snapshots **only** from cache.

    Never triggers a live connector call.  Cache-miss or hard-expired entries
    raise ``CacheMissError`` (matching the ``CachedSource`` pattern) so the
    caller can apply degradation logic.
    """

    def __init__(self, backend: CacheBackend | None = None) -> None:
        self._backend = backend if backend is not None else get_cache_backend()

    def fetch(
        self,
        asset_id: str,
        window_end: datetime,
    ) -> PeerMetricsSnapshot:
        """Return the cached snapshot for *asset_id* & *window_end*.

        Raises ``CacheMissError`` when:
        - The cache key has never been written.
        - The cached entry exceeds its hard-expiry margin
          (``PEER_METRICS_STALE_AFTER_SECONDS``).
        """
        if window_end.tzinfo is None:
            raise ValueError("window_end must be timezone-aware")

        ck = peer_metrics_cache_key(asset_id, window_end)
        entry = cache_get(self._backend, ck)
        if entry is None:
            raise CacheMissError(
                f"PeerMetrics: no cache entry for key={ck!r} — "
                "never written by schedule_peer_metrics_snapshot"
            )

        fetched_at_raw = entry.get("fetched_at")
        fetched_at = float(fetched_at_raw) if fetched_at_raw else 0.0
        age = time.time() - fetched_at
        if age > PEER_METRICS_STALE_AFTER_SECONDS:
            raise CacheMissError(
                f"PeerMetrics: cache entry for key={ck!r} hard-expired "
                f"(age={age:.0f}s > ttl={PEER_METRICS_STALE_AFTER_SECONDS}s)"
            )

        docs = entry.get("docs") or []
        if not docs or not isinstance(docs[0], dict):
            raise CacheMissError(
                f"PeerMetrics: cache entry for key={ck!r} has no snapshot docs"
            )

        # Strip scheduler-internal metadata keys before reconstituting.
        payload = dict(docs[0])
        payload.pop("_stale_since", None)
        payload.pop("_degraded", None)
        return _snapshot_from_payload(payload)


def _metric_from_dict(payload: dict[str, Any]) -> MetricValue:
    """Reconstitute a ``MetricValue`` from a ``to_dict()``-compatible dict.

    Unlike ``parse_metric_value()`` in ``peer_metrics_repository.py``, this
    does **not** require the ``fixture://`` source prefix — real connector
    data uses ``https://`` sources.
    """
    return MetricValue(
        value=payload["value"],
        unit=payload["unit"],
        method=PeerMetricMethod(payload["method"]),
        source=payload["source"],
    )


def _snapshot_from_payload(payload: dict[str, Any]) -> PeerMetricsSnapshot:
    """Reconstitute a ``PeerMetricsSnapshot`` from a ``to_dict()``-compatible payload."""
    activity_raw: dict[str, Any] = payload["activity_breakdown"]
    return PeerMetricsSnapshot(
        asset_id=payload["asset_id"],
        observed_tps=_metric_from_dict(payload["observed_tps"]),
        tvl=_metric_from_dict(payload["tvl"]),
        gas_fee=_metric_from_dict(payload["gas_fee"]),
        activity_breakdown={
            k: _metric_from_dict(v) for k, v in activity_raw.items()
        },
        window_start=utc_timestamp(payload["window_start"]),
        window_end=utc_timestamp(payload["window_end"]),
        observed_at=utc_timestamp(payload["observed_at"]),
    )
