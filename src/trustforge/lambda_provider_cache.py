"""Bounded on-demand provider refresh for the competition Live Lambda.

The normal product pipeline remains cache-only. An authenticated Live Lambda
analysis refreshes only the four owner-authorized provider sources into its
execution environment's JSON cache before the pipeline reads that cache.
Errors expose source name and exception type only; exception text may contain
credential-bearing upstream URLs and is never logged or returned.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .ingestion.cache import cache_get, cache_key, cache_set, doc_to_dict, get_cache_backend
from .ingestion.cmc import build_cmc_sources
from .ingestion.etherscan import build_etherscan_sources
from .ingestion.whale_trades import build_whale_sources


_PROVIDER_NAMES = frozenset(
    {"arkham-intel", "coinmarketcap-price", "etherscan-whale", "whale-alert"}
)
_CACHE_TTL_SECONDS = 600.0
_refresh_lock = threading.Lock()


def _sources():
    built = build_whale_sources() + build_cmc_sources() + build_etherscan_sources()
    selected = {source.name: source for source in built if source.name in _PROVIDER_NAMES}
    if set(selected) != _PROVIDER_NAMES:
        raise RuntimeError("competition provider registry is incomplete")
    return [selected[name] for name in sorted(selected)]


def _is_fresh(backend, source_name: str, coin: str) -> bool:
    entry = cache_get(backend, cache_key(source_name, coin))
    if entry is None:
        return False
    fetched_at = float(entry.get("fetched_at", 0.0) or 0.0)
    return 0.0 <= time.time() - fetched_at <= _CACHE_TTL_SECONDS


def _refresh_one(source, coin: str, backend) -> tuple[str, str, int]:
    if _is_fresh(backend, source.name, coin):
        entry = cache_get(backend, cache_key(source.name, coin)) or {}
        return source.name, "cached", len(entry.get("docs") or [])
    try:
        docs = source.fetch("", coin=coin)
        result = cache_set(
            backend,
            cache_key(source.name, coin),
            [doc_to_dict(doc) for doc in docs],
            fetched_at=time.time(),
            ttl_seconds=_CACHE_TTL_SECONDS,
            allow_json_fallback=False,
        )
        if not result.ok:
            return source.name, "cache-write-failed", 0
        return source.name, "refreshed", len(docs)
    except Exception as exc:  # credential-bearing URL may exist in exception internals
        return source.name, f"failed:{type(exc).__name__}", 0


def refresh_provider_cache(coin: str) -> dict[str, tuple[str, int]]:
    """Refresh missing/stale provider entries in parallel within connector timeouts."""
    normalized_coin = (coin or "").strip().upper()
    with _refresh_lock:
        backend = get_cache_backend()
        sources = _sources()
        with ThreadPoolExecutor(max_workers=len(sources), thread_name_prefix="provider") as pool:
            results = list(
                pool.map(lambda source: _refresh_one(source, normalized_coin, backend), sources)
            )
    return {name: (status, count) for name, status, count in results}
