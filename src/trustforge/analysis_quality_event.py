"""Build immutable analysis-quality learning events."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .learning_event_contract import (
    LearningEvent,
    LearningEventError,
    make_learning_event,
    provenance_checksum,
)


def build_analysis_quality_event(snapshot: dict[str, Any]) -> LearningEvent:
    """Create an `analysis-quality.v1` event from one completed analysis snapshot."""

    analysis_id = _required(snapshot, "analysis_id")
    tenant_id = _required(snapshot, "tenant_id")
    coin = _required(snapshot, "coin")
    mode = _required(snapshot, "mode")
    question_type = _required(snapshot, "question_type")
    event_time = _required(snapshot, "event_time")
    available_time = _required(snapshot, "available_time")
    as_of_time = _required(snapshot, "as_of_time")
    _reject_future_sources(snapshot.get("source_available_times", []), as_of_time)

    payload = {
        "historical_answer_id": analysis_id,
        "question": question_type,
        "event_type": "analysis-quality.v1",
        "analysis_id": analysis_id,
        "tenant_id": tenant_id,
        "coin": coin,
        "mode": mode,
        "question_type": question_type,
        "confidence": _object(snapshot, "confidence"),
        "decision": _object(snapshot, "decision"),
        "evidence_stats": _object(snapshot, "evidence_stats"),
        "quality": _object(snapshot, "quality"),
        "versions": _object(snapshot, "versions"),
        "stage_metrics": list(snapshot.get("stage_metrics", [])),
        "failure": snapshot.get("failure"),
        "retry": snapshot.get("retry"),
    }
    if "outcome_id" in snapshot or "label_id" in snapshot:
        raise LearningEventError("analysis-quality event cannot contain outcome or gold label identity")
    provenance = _object(snapshot, "provenance")
    if not isinstance(provenance.get("source"), str) or not provenance["source"].strip():
        raise LearningEventError("provenance.source is required")
    source_record = {
        "analysis_id": analysis_id,
        "event_time": event_time,
        "source": provenance["source"],
    }
    canonical_provenance = {
        **provenance,
        "tenant_id": tenant_id,
        "source_record": source_record,
        "version": str(_object(snapshot, "versions").get("contract", "analysis-quality.v1")),
        "checksum": provenance_checksum(source_record),
    }
    return make_learning_event(
        kind="historical_non_evidentiary",
        tenant_id=tenant_id,
        entity_id=f"analysis-quality:{analysis_id}",
        revision=1,
        event_time=event_time,
        available_time=available_time,
        as_of_time=as_of_time,
        provenance=canonical_provenance,
        payload=payload,
    )


def _required(snapshot: dict[str, Any], key: str) -> str:
    value = snapshot.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LearningEventError(f"{key} is required for analysis-quality event")
    return value


def _object(snapshot: dict[str, Any], key: str) -> dict[str, Any]:
    value = snapshot.get(key)
    if not isinstance(value, dict):
        raise LearningEventError(f"{key} is required for analysis-quality event")
    return dict(value)


def _parse_utc(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise LearningEventError(f"{field} must be ISO-8601") from None
    if parsed.tzinfo is None:
        raise LearningEventError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc)


def _reject_future_sources(source_times: Any, as_of_time: str) -> None:
    if not isinstance(source_times, list) or not all(isinstance(item, str) for item in source_times):
        raise LearningEventError("source_available_times is required for analysis-quality event")
    boundary = _parse_utc(as_of_time, "as_of_time")
    for source_time in source_times:
        if _parse_utc(source_time, "source_available_time") > boundary:
            raise LearningEventError("analysis-quality event cannot include future source data")
