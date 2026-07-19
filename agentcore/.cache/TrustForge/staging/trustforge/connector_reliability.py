"""Deterministic connector reliability measurements from scheduler run records."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable


def _failed_sources(record: dict[str, Any], known_sources: set[str]) -> set[str]:
    failed: set[str] = set()
    for raw in record.get("failures", []):
        label = str(raw)
        matches = [source for source in known_sources if label == source or label.startswith(source + ":")]
        if matches:
            failed.add(max(matches, key=len))
    return failed


def build_reliability_report(
    records: Iterable[dict[str, Any]], *, required_consecutive_successes: int = 7,
    source_metrics: Iterable[dict[str, Any]] = (), freshness_slo_seconds: float = 3600.0,
    latency_p95_slo_ms: float = 2000.0,
) -> dict[str, Any]:
    """Summarize actual connector attempts; freshness skips are not successes."""
    if required_consecutive_successes < 1:
        raise ValueError("required_consecutive_successes must be >= 1")

    ordered = sorted(
        (record for record in records if isinstance(record, dict)),
        key=lambda record: str(record.get("ts", "")),
        reverse=True,
    )
    known_sources = {
        str(source)
        for record in ordered
        for source in record.get("targets", [])
        if str(source)
    }
    known_sources.update(
        str(source)
        for record in ordered
        for source in (record.get("source_calls") or {})
        if str(source)
    )
    metrics_by_source = {
        str(row.get("source")): row for row in source_metrics
        if isinstance(row, dict) and str(row.get("source", ""))
    }
    known_sources.update(metrics_by_source)

    rows: list[dict[str, Any]] = []
    for source in sorted(known_sources):
        attempts: list[tuple[dict[str, Any], bool]] = []
        for record in ordered:
            failures = _failed_sources(record, known_sources)
            calls = record.get("source_calls") or {}
            if source in failures:
                attempts.append((record, False))
            elif int(calls.get(source, 0) or 0) > 0:
                attempts.append((record, True))

        streak = 0
        for _record, succeeded in attempts:
            if not succeeded:
                break
            streak += 1
        failures = [record for record, succeeded in attempts if not succeeded]
        success_count = len(attempts) - len(failures)
        metrics = metrics_by_source.get(source, {})
        freshness_age = metrics.get("freshness_age_seconds")
        p95 = metrics.get("latency_p95_ms")
        rows.append({
            "source": source,
            "attempted_runs": len(attempts),
            "successful_runs": success_count,
            "failed_runs": len(failures),
            "failure_rate": round(len(failures) / len(attempts), 4) if attempts else None,
            "consecutive_successes": streak,
            "required_consecutive_successes": required_consecutive_successes,
            "meets_reliability_gate": streak >= required_consecutive_successes,
            "last_attempt_at": attempts[0][0].get("ts") if attempts else None,
            "last_failure_at": failures[0].get("ts") if failures else None,
            "fetches": int(metrics.get("fetches", 0) or 0),
            "documents": int(metrics.get("documents", 0) or 0),
            "empty_fetches": int(metrics.get("empty_fetches", 0) or 0),
            "freshness_age_seconds": freshness_age,
            "freshness_slo_met": freshness_age is not None and float(freshness_age) <= freshness_slo_seconds,
            "duplicate_fetch_ratio": metrics.get("duplicate_fetch_ratio"),
            "latency_p50_ms": metrics.get("latency_p50_ms"),
            "latency_p95_ms": p95,
            "latency_slo_met": p95 is not None and float(p95) <= latency_p95_slo_ms,
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_runs": len(ordered),
        "required_consecutive_successes": required_consecutive_successes,
        "sources": rows,
        "passing_sources": sum(bool(row["meets_reliability_gate"]) for row in rows),
        "total_sources": len(rows),
        "freshness_slo_seconds": freshness_slo_seconds,
        "latency_p95_slo_ms": latency_p95_slo_ms,
    }
