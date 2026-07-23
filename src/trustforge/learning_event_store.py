"""Append-only storage compatibility helpers for learning events.

This is not a database adapter.  It is a small contract layer used by migration
and persistence implementations to prove that canonical events stay immutable
and replayable before any storage backend is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .learning_event_contract import (
    LearningEvent,
    LearningEventError,
    assert_append_only,
    deserialize_learning_event,
    serialize_learning_event,
)


@dataclass
class LearningEventAppendLog:
    _events: dict[str, str] = field(default_factory=dict)

    def append(self, event: LearningEvent) -> str:
        encoded = serialize_learning_event(event)
        current = self._events.get(event.identity)
        if current is None:
            self._events[event.identity] = encoded
            return "created"
        if current == encoded:
            return "idempotent"
        assert_append_only(deserialize_learning_event(current), event)
        raise LearningEventError("learning event append failed")

    def replay(self) -> list[LearningEvent]:
        return [deserialize_learning_event(raw) for raw in self._events.values()]

    def snapshot(self) -> tuple[str, ...]:
        return tuple(self._events[key] for key in sorted(self._events))


def plan_learning_event_migration(raw_events: Iterable[str | bytes], *, dry_run: bool = True) -> dict[str, Any]:
    """Validate raw canonical events and return a fail-closed migration plan."""

    append_log = LearningEventAppendLog()
    results: list[dict[str, str]] = []
    for raw in raw_events:
        try:
            event = deserialize_learning_event(raw)
            outcome = append_log.append(event)
        except LearningEventError as exc:
            return {
                "status": "blocked",
                "dry_run": dry_run,
                "reason": str(exc),
                "events_validated": len(results),
                "will_write": False,
            }
        results.append({"identity": event.identity, "kind": event.kind, "result": outcome})
    return {
        "status": "ready" if dry_run else "requires_backend_transaction",
        "dry_run": dry_run,
        "events_validated": len(results),
        "will_write": not dry_run,
        "results": results,
        "snapshot": append_log.snapshot(),
    }
