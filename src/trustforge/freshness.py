"""Cache-freshness view derived from durable cache and scheduler run records."""
from __future__ import annotations

import time
from collections import Counter
from typing import Any, Iterable

from .ingestion.cache import CacheBackend, cache_get, cache_key


def dashboard(backend: CacheBackend, coins: Iterable[str], sources: Iterable[str], *, now: float | None = None, runs: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    observed_at = time.time() if now is None else now
    rows = []
    for coin in sorted(set(coins)):
        for source in sorted(set(sources)):
            entry = cache_get(backend, cache_key(source, coin))
            fetched = float((entry or {}).get("fetched_at", 0.0) or 0.0)
            ttl = float((entry or {}).get("ttl", 0.0) or 0.0)
            state = "missing" if entry is None else ("stale" if ttl and ttl <= observed_at else "fresh")
            rows.append({"coin": coin, "source": source, "state": state, "fetched_at": fetched or None, "age_sec": round(observed_at - fetched, 1) if fetched else None, "document_count": len((entry or {}).get("docs") or [])})
    recent_runs = list(runs)
    failures = Counter(str(item) for run in recent_runs for item in (run.get("failures") or []))
    return {
        "observed_at_epoch": observed_at,
        "summary": dict(Counter(row["state"] for row in rows)),
        "rows": rows,
        "recent_scheduler_runs": len(recent_runs),
        "failure_labels": dict(sorted(failures.items())),
    }
