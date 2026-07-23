"""Canonical three-track learning event contract.

This module defines the non-DB event envelope used to keep evidence, historical
context, delayed outcomes, human gold labels, and diagnostics from impersonating
one another.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

SCHEMA_VERSION = "learning-event.v1"
LearningEventKind = Literal[
    "evidentiary",
    "historical_non_evidentiary",
    "delayed_outcome",
    "human_gold_label",
    "candidate_diagnostic",
]
_KINDS = {
    "evidentiary",
    "historical_non_evidentiary",
    "delayed_outcome",
    "human_gold_label",
    "candidate_diagnostic",
}


class LearningEventError(ValueError):
    pass


@dataclass(frozen=True)
class LearningEvent:
    schema_version: str
    kind: LearningEventKind
    identity: str
    event_time: str
    available_time: str
    as_of_time: str
    provenance: dict[str, Any]
    payload: dict[str, Any]


def make_learning_event(
    *,
    kind: LearningEventKind,
    identity: str,
    event_time: str,
    available_time: str,
    as_of_time: str,
    provenance: dict[str, Any],
    payload: dict[str, Any],
) -> LearningEvent:
    event = LearningEvent(
        schema_version=SCHEMA_VERSION,
        kind=kind,
        identity=identity,
        event_time=event_time,
        available_time=available_time,
        as_of_time=as_of_time,
        provenance=dict(provenance),
        payload=dict(payload),
    )
    _validate_event(event)
    return event


def serialize_learning_event(event: LearningEvent) -> str:
    _validate_event(event)
    return json.dumps(_to_dict(event), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def deserialize_learning_event(raw: str | bytes) -> LearningEvent:
    try:
        value = json.loads(raw)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise LearningEventError("learning event JSON is invalid") from None
    if not isinstance(value, dict):
        raise LearningEventError("learning event must be an object")
    event = LearningEvent(
        schema_version=value.get("schema_version"),
        kind=value.get("kind"),
        identity=value.get("identity"),
        event_time=value.get("event_time"),
        available_time=value.get("available_time"),
        as_of_time=value.get("as_of_time"),
        provenance=value.get("provenance"),
        payload=value.get("payload"),
    )
    _validate_event(event)
    return event


def assert_append_only(original: LearningEvent, candidate: LearningEvent) -> None:
    """Reject in-place rewrites of an existing event identity."""

    _validate_event(original)
    _validate_event(candidate)
    if original.identity == candidate.identity and serialize_learning_event(original) != serialize_learning_event(candidate):
        raise LearningEventError("learning event is immutable; create a new identity for revisions")


def _to_dict(event: LearningEvent) -> dict[str, Any]:
    return {
        "schema_version": event.schema_version,
        "kind": event.kind,
        "identity": event.identity,
        "event_time": event.event_time,
        "available_time": event.available_time,
        "as_of_time": event.as_of_time,
        "provenance": event.provenance,
        "payload": event.payload,
    }


def _validate_event(event: LearningEvent) -> None:
    if event.schema_version != SCHEMA_VERSION:
        raise LearningEventError("unknown learning event schema version")
    if event.kind not in _KINDS:
        raise LearningEventError("unknown learning event kind")
    if not _nonempty(event.identity):
        raise LearningEventError("learning event identity is required")
    event_ts = _parse_utc(event.event_time, "event_time")
    available_ts = _parse_utc(event.available_time, "available_time")
    as_of_ts = _parse_utc(event.as_of_time, "as_of_time")
    if available_ts < event_ts:
        raise LearningEventError("available_time cannot precede event_time")
    if as_of_ts < event_ts:
        raise LearningEventError("as_of_time cannot precede event_time")
    _validate_provenance(event.provenance)
    if not isinstance(event.payload, dict):
        raise LearningEventError("learning event payload must be an object")
    _validate_kind_payload(event.kind, event.payload)


def _parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise LearningEventError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise LearningEventError(f"{field} must be ISO-8601") from None
    if parsed.tzinfo is None:
        raise LearningEventError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc)


def _validate_provenance(value: Any) -> None:
    if not isinstance(value, dict):
        raise LearningEventError("provenance is required")
    for key in ("source", "collector", "observed_at"):
        if not _nonempty(value.get(key)):
            raise LearningEventError(f"provenance.{key} is required")
    _parse_utc(value["observed_at"], "provenance.observed_at")


def _validate_kind_payload(kind: str, payload: dict[str, Any]) -> None:
    if kind == "evidentiary":
        _require(payload, "evidence_id", "claim", "source_url")
        _forbid(payload, "outcome_id", "label_id", "diagnostic_id", "historical_answer_id")
    elif kind == "historical_non_evidentiary":
        _require(payload, "historical_answer_id", "question")
        _forbid(payload, "evidence_id", "outcome_id", "label_id", "diagnostic_id")
    elif kind == "delayed_outcome":
        _require(payload, "outcome_id", "analysis_id", "horizon", "status")
        if payload["horizon"] not in {"T+1", "T+7", "T+14"}:
            raise LearningEventError("unsupported outcome horizon")
        _forbid(payload, "evidence_id", "label_id", "diagnostic_id")
    elif kind == "human_gold_label":
        _require(payload, "label_id", "analysis_id", "reviewer", "label")
        _forbid(payload, "evidence_id", "outcome_id", "diagnostic_id")
    elif kind == "candidate_diagnostic":
        _require(payload, "diagnostic_id", "analysis_id", "reason")
        _forbid(payload, "evidence_id", "outcome_id", "label_id", "approval_action", "activation")


def _require(payload: dict[str, Any], *keys: str) -> None:
    for key in keys:
        if not _nonempty(payload.get(key)):
            raise LearningEventError(f"{key} is required for learning event payload")


def _forbid(payload: dict[str, Any], *keys: str) -> None:
    for key in keys:
        if key in payload:
            raise LearningEventError(f"{key} is not allowed for this learning event kind")


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
