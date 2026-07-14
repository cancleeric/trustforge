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
    records: Iterable[dict[str, Any]], *, required_consecutive_successes: int = 7
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
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_runs": len(ordered),
        "required_consecutive_successes": required_consecutive_successes,
        "sources": rows,
        "passing_sources": sum(bool(row["meets_reliability_gate"]) for row in rows),
        "total_sources": len(rows),
    }
