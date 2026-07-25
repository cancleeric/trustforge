"""Cache-freshness view derived from durable cache and scheduler run records."""
from __future__ import annotations

import time
from collections import Counter
from typing import Any, Iterable

from .ingestion.cache import CacheBackend, cache_get, cache_key


def dashboard(
    backend: CacheBackend,
    coins: Iterable[str],
    sources: Iterable[str],
    *,
    now: float | None = None,
    runs: Iterable[dict[str, Any]] = (),
    degraded_stale_after: float | None = None,
) -> dict[str, Any]:
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

    # ── degraded 判定 ────────────────────────────────────────────────────────
    summary = dict(Counter(row["state"] for row in rows))
    fresh_count = summary.get("fresh", 0)
    stale_count = summary.get("stale", 0)
    missing_count = summary.get("missing", 0)
    total = len(rows)

    # 最後成功刷新時間與受影響來源
    last_refresh: float | None = None
    max_age: float = 0.0
    affected_sources: set[str] = set()
    for row in rows:
        if row["fetched_at"]:
            if last_refresh is None or row["fetched_at"] > last_refresh:
                last_refresh = row["fetched_at"]
            age = row["age_sec"] or 0.0
            if age > max_age:
                max_age = age
        if row["state"] in ("stale", "missing"):
            affected_sources.add(row["source"])

    degraded = False
    degraded_reason: str | None = None

    # fail-safe：觀測資料缺失時不得誤報 healthy
    if total == 0 or missing_count == total:
        degraded = True
        degraded_reason = "no_data"
    elif fresh_count == 0:
        # 全部 stale — 明確 degraded
        degraded = True
        degraded_reason = "all_freshness_stale"
    elif degraded_stale_after is not None and max_age > degraded_stale_after:
        # 部分 stale 但超過門檻
        degraded = True
        degraded_reason = "stale_exceeds_threshold"

    return {
        "observed_at_epoch": observed_at,
        "summary": summary,
        "rows": rows,
        "recent_scheduler_runs": len(recent_runs),
        "failure_labels": dict(sorted(failures.items())),
        "degraded": degraded,
        "degraded_reason": degraded_reason,
        "last_refresh_epoch": last_refresh,
        "max_stale_age_sec": round(max_age, 1),
        "affected_source_count": len(affected_sources),
        "total_entries": total,
    }
