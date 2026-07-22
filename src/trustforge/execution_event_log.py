"""Generic execution event log primitives.

The public application can map these records to product-specific workflow
graphs outside this module.  This layer only owns run/event/step structure,
JSONL compatibility, and defensive secret redaction.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any


REDACTED = "[REDACTED]"
_SECRET_KEY_MARKERS = (
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)


@dataclass(frozen=True)
class ExecutionStepRecord:
    """Generic execution step metadata."""

    step_id: str
    label: str = ""
    order: int = 0
    status: str = "observed"


@dataclass(frozen=True)
class ExecutionEventRecord:
    """One provider-neutral execution event."""

    ts: str
    elapsed_sec: float
    tool: str
    params: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    step: ExecutionStepRecord | None = None

    def to_legacy_dict(self) -> dict[str, Any]:
        """Return the existing execution_log.jsonl event shape."""

        return {
            "ts": self.ts,
            "elapsed_sec": self.elapsed_sec,
            "tool": self.tool,
            "params": self.params,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class ExecutionRunRecord:
    """Generic execution run envelope."""

    run_id: str
    started_at: str
    elapsed_sec: float
    budget_sec: int
    steps: list[ExecutionStepRecord] = field(default_factory=list)


class ExecutionEventLog:
    """In-memory generic event log with JSONL compatibility serializer."""

    def __init__(self, run_id: str, started_at: str, budget_sec: int):
        self.run_id = run_id
        self.started_at = started_at
        self.budget_sec = budget_sec
        self.events: list[ExecutionEventRecord] = []

    def append(
        self,
        *,
        ts: str,
        elapsed_sec: float,
        tool: str,
        params: dict[str, Any] | None = None,
        summary: str = "",
        step: ExecutionStepRecord | None = None,
    ) -> ExecutionEventRecord:
        event = ExecutionEventRecord(
            ts=ts,
            elapsed_sec=round(float(elapsed_sec), 2),
            tool=tool,
            params=redact_secrets(params or {}),
            summary=summary,
            step=step,
        )
        self.events.append(event)
        return event

    def to_jsonl(self) -> str:
        return serialize_legacy_jsonl(self.events)

    def manifest(self, *, elapsed_sec: float | None = None) -> ExecutionRunRecord:
        elapsed = self.events[-1].elapsed_sec if elapsed_sec is None and self.events else 0.0
        if elapsed_sec is not None:
            elapsed = round(float(elapsed_sec), 2)
        return ExecutionRunRecord(
            run_id=self.run_id,
            started_at=self.started_at,
            elapsed_sec=elapsed,
            budget_sec=self.budget_sec,
            steps=[event.step for event in self.events if event.step is not None],
        )


def serialize_legacy_jsonl(events: list[ExecutionEventRecord]) -> str:
    """Serialize events using the existing JSONL contract."""

    return "\n".join(json.dumps(event.to_legacy_dict(), ensure_ascii=False) for event in events)


def record_to_dict(record: ExecutionRunRecord) -> dict[str, Any]:
    """Convert run envelope to a JSON-compatible dict."""

    return asdict(record)


def redact_secrets(value: Any) -> Any:
    """Recursively redact obvious secrets from JSON-like structures."""

    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if _is_secret_key(str(key)):
                redacted[key] = REDACTED
            else:
                redacted[key] = redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return [redact_secrets(item) for item in value]
    return value


def _is_secret_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    if lowered in _SECRET_KEY_MARKERS:
        return True
    parts = set(filter(None, re.split(r"[_\W]+", lowered)))
    return any(marker in parts for marker in _SECRET_KEY_MARKERS)
