"""Canonical builder for immutable ``analysis-quality.v1`` learning events."""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Mapping

from .learning_event_contract import (
    LearningEvent,
    LearningEventError,
    canonical_integrity_checksum,
    make_learning_event,
)

EVENT_TYPE = "analysis-quality.v1"
_CONFIDENCE_FIELDS = {"raw", "calibrated"}
_DECISION_FIELDS = {"direction", "state"}
_EVIDENCE_FIELDS = {
    "supporting_count",
    "contrarian_count",
    "evidence_count",
    "average_trust",
    "independent_source_count",
    "source_distribution",
}
_QUALITY_FIELDS = {"freshness", "conflict", "missingness", "completeness"}
_VERSION_FIELDS = {
    "contract",
    "schema",
    "kernel",
    "scoring",
    "evidence",
    "model",
    "prompt",
    "policy",
    "rule",
}
_STAGE_FIELDS = {"stage", "latency_ms", "status", "attempts", "failure"}
_FAILURE_FIELDS = {"status", "failed_stage", "code", "message", "retryable"}
_PROVENANCE_INPUT_FIELDS = {"source", "collector", "observed_at"}
_SNAPSHOT_FIELDS = {
    "analysis_id",
    "run_id",
    "question_id",
    "answer_id",
    "evidence_snapshot_id",
    "question",
    "tenant_id",
    "coin",
    "mode",
    "question_type",
    "event_time",
    "available_time",
    "as_of_time",
    "source_available_times",
    "provenance",
    "confidence",
    "decision",
    "evidence_stats",
    "quality",
    "versions",
    "stage_metrics",
    "failure",
    # Explicitly recognized below only to produce a classification-specific error.
    "outcome_id",
    "label_id",
    # Transport-only metadata is explicitly recognized and rejected below.
    "retry",
    "transport",
}


def build_analysis_quality_event(
    snapshot: Mapping[str, Any],
    *,
    trusted_tenant_id: str,
) -> LearningEvent:
    """Build one immutable event from a completed or partially failed analysis.

    ``trusted_tenant_id`` is the sole tenant authority.  A snapshot tenant, when
    present for compatibility, is only an assertion and must match that trusted
    authority.  Transport retry metadata is deliberately not accepted here:
    delivery attempts must not alter canonical event bytes.
    """

    tenant_id = _required_string_value(trusted_tenant_id, "trusted_tenant_id")
    if not isinstance(snapshot, Mapping):
        raise LearningEventError("analysis snapshot must be an object")
    unknown = set(snapshot) - _SNAPSHOT_FIELDS
    if unknown:
        raise LearningEventError(
            f"unknown analysis snapshot fields: {', '.join(sorted(unknown))}"
        )
    if "retry" in snapshot or "transport" in snapshot:
        raise LearningEventError("transport retry metadata is not canonical analysis data")
    asserted_tenant = snapshot.get("tenant_id")
    if asserted_tenant is not None and asserted_tenant != tenant_id:
        raise LearningEventError("snapshot tenant_id must match trusted_tenant_id")

    analysis_id = _required_string(snapshot, "analysis_id")
    run_id = _required_string(snapshot, "run_id")
    question_id = _required_string(snapshot, "question_id")
    answer_id = _required_string(snapshot, "answer_id")
    evidence_snapshot_id = _required_string(snapshot, "evidence_snapshot_id")
    question = _required_string(snapshot, "question")
    coin = _required_string(snapshot, "coin")
    mode = _required_string(snapshot, "mode")
    question_type = _required_string(snapshot, "question_type")
    event_time = _required_time(snapshot, "event_time")
    available_time = _required_time(snapshot, "available_time")
    as_of_time = _required_time(snapshot, "as_of_time")
    if available_time < event_time or available_time > as_of_time:
        raise LearningEventError("analysis PIT times are inconsistent")

    source_times = _source_times(snapshot, as_of_time)
    confidence = _strict_object(snapshot, "confidence", _CONFIDENCE_FIELDS)
    _require_keys(confidence, "confidence", _CONFIDENCE_FIELDS)
    for key in _CONFIDENCE_FIELDS:
        _unit_interval(confidence[key], f"confidence.{key}")
    decision = _strict_object(snapshot, "decision", _DECISION_FIELDS)
    _require_keys(decision, "decision", _DECISION_FIELDS)
    for key in _DECISION_FIELDS:
        _required_string_value(decision[key], f"decision.{key}")
    evidence_stats = _strict_object(snapshot, "evidence_stats", _EVIDENCE_FIELDS)
    _require_keys(evidence_stats, "evidence_stats", _EVIDENCE_FIELDS)
    for key in (
        "supporting_count",
        "contrarian_count",
        "evidence_count",
        "independent_source_count",
    ):
        _nonnegative_integer(evidence_stats[key], f"evidence_stats.{key}")
    _unit_interval(evidence_stats["average_trust"], "evidence_stats.average_trust")
    distribution = evidence_stats["source_distribution"]
    if not isinstance(distribution, Mapping) or not distribution:
        raise LearningEventError("evidence_stats.source_distribution must be a non-empty object")
    for key, value in distribution.items():
        _required_string_value(key, "evidence_stats.source_distribution key")
        _nonnegative_integer(value, f"evidence_stats.source_distribution.{key}")
    # Buckets are mutually exclusive source categories in this contract.
    if sum(distribution.values()) != evidence_stats["evidence_count"]:
        raise LearningEventError(
            "evidence_stats.source_distribution must sum to evidence_count"
        )
    if (
        evidence_stats["supporting_count"] + evidence_stats["contrarian_count"]
        > evidence_stats["evidence_count"]
    ):
        raise LearningEventError(
            "supporting and contrarian counts cannot exceed evidence_count"
        )
    if evidence_stats["independent_source_count"] > evidence_stats["evidence_count"]:
        raise LearningEventError(
            "independent_source_count cannot exceed evidence_count"
        )
    quality = _strict_object(snapshot, "quality", _QUALITY_FIELDS)
    _require_keys(quality, "quality", _QUALITY_FIELDS)
    for key in ("freshness", "conflict", "completeness"):
        _required_string_value(quality[key], f"quality.{key}")
    _unit_interval(quality["missingness"], "quality.missingness")
    versions = _strict_object(snapshot, "versions", _VERSION_FIELDS)
    _require_keys(versions, "versions", _VERSION_FIELDS)
    for key, value in versions.items():
        _required_string_value(value, f"versions.{key}")
    if versions["contract"] != EVENT_TYPE or versions["schema"] != EVENT_TYPE:
        raise LearningEventError(f"versions.contract and versions.schema must be {EVENT_TYPE}")

    stage_metrics = _stage_metrics(snapshot)
    failure = _failure(snapshot)
    if failure["status"] == "partial" and not any(
        metric["status"] == "failed" for metric in stage_metrics
    ):
        raise LearningEventError("partial failure requires a failed stage metric")
    if failure["status"] == "partial" and failure["failed_stage"] not in {
        metric["stage"] for metric in stage_metrics if metric["status"] == "failed"
    }:
        raise LearningEventError("failure.failed_stage must identify a failed stage metric")
    if failure["status"] == "complete" and any(
        metric["status"] == "failed" for metric in stage_metrics
    ):
        raise LearningEventError("complete analysis cannot contain a failed stage")

    if "outcome_id" in snapshot or "label_id" in snapshot:
        raise LearningEventError("analysis-quality event cannot contain outcome or gold label identity")

    provenance_input = _strict_object(snapshot, "provenance", _PROVENANCE_INPUT_FIELDS)
    _require_keys(provenance_input, "provenance", _PROVENANCE_INPUT_FIELDS)
    observed_at = _parse_time(provenance_input["observed_at"], "provenance.observed_at")
    if observed_at > as_of_time:
        raise LearningEventError("provenance.observed_at cannot follow as_of_time")
    for field in ("source", "collector"):
        _required_string_value(provenance_input[field], f"provenance.{field}")

    source_record = {
        "analysis_id": analysis_id,
        "run_id": run_id,
        "question_id": question_id,
        "answer_id": answer_id,
        "evidence_snapshot_id": evidence_snapshot_id,
        "pit": {
            "event_time": _canonical_time(event_time),
            "available_time": _canonical_time(available_time),
            "as_of_time": _canonical_time(as_of_time),
            "source_available_times": [_canonical_time(value) for value in source_times],
        },
        "versions": versions,
    }
    provenance = {
        **provenance_input,
        "tenant_id": tenant_id,
        "source_record": source_record,
        "version": EVENT_TYPE,
        "checksum": canonical_integrity_checksum(source_record),
    }
    payload = {
        "historical_answer_id": answer_id,
        "question": question,
        "event_type": EVENT_TYPE,
        "analysis_id": analysis_id,
        "run_id": run_id,
        "question_id": question_id,
        "answer_id": answer_id,
        "evidence_snapshot_id": evidence_snapshot_id,
        "coin": coin,
        "mode": mode,
        "question_type": question_type,
        "confidence": confidence,
        "decision": decision,
        "evidence_stats": evidence_stats,
        "quality": quality,
        "versions": versions,
        "stage_metrics": stage_metrics,
        "failure": failure,
    }
    return make_learning_event(
        kind="historical_non_evidentiary",
        tenant_id=tenant_id,
        entity_id=f"analysis-quality:{analysis_id}",
        revision=1,
        event_time=_canonical_time(event_time),
        available_time=_canonical_time(available_time),
        as_of_time=_canonical_time(as_of_time),
        provenance=provenance,
        payload=payload,
    )


def _required_string(snapshot: Mapping[str, Any], key: str) -> str:
    return _required_string_value(snapshot.get(key), key)


def _required_string_value(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LearningEventError(f"{field} is required for analysis-quality event")
    return value


def _strict_object(
    snapshot: Mapping[str, Any],
    key: str,
    allowed: set[str],
) -> dict[str, Any]:
    value = snapshot.get(key)
    if not isinstance(value, Mapping):
        raise LearningEventError(f"{key} is required for analysis-quality event")
    unknown = set(value) - allowed
    if unknown:
        raise LearningEventError(f"unknown {key} fields: {', '.join(sorted(unknown))}")
    return dict(value)


def _require_keys(value: Mapping[str, Any], field: str, required: set[str]) -> None:
    missing = required - set(value)
    if missing:
        raise LearningEventError(f"missing {field} fields: {', '.join(sorted(missing))}")


def _unit_interval(value: Any, field: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        or value > 1
    ):
        raise LearningEventError(f"{field} must be finite and between 0 and 1")


def _nonnegative_integer(value: Any, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LearningEventError(f"{field} must be a nonnegative integer")


def _required_time(snapshot: Mapping[str, Any], key: str) -> datetime:
    if key not in snapshot:
        raise LearningEventError(f"{key} is required for analysis-quality event")
    return _parse_time(snapshot[key], key)


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise LearningEventError(f"{field} must be ISO-8601")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise LearningEventError(f"{field} must be ISO-8601") from None
    if parsed.tzinfo is None:
        raise LearningEventError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc)


def _canonical_time(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _source_times(snapshot: Mapping[str, Any], as_of_time: datetime) -> list[datetime]:
    raw = snapshot.get("source_available_times")
    if not isinstance(raw, list) or not raw:
        raise LearningEventError("source_available_times is required for analysis-quality event")
    parsed = [_parse_time(item, "source_available_time") for item in raw]
    if any(value > as_of_time for value in parsed):
        raise LearningEventError("analysis-quality event cannot include future source data")
    return sorted(set(parsed))


def _stage_metrics(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = snapshot.get("stage_metrics")
    if not isinstance(raw, list) or not raw:
        raise LearningEventError("stage_metrics is required for analysis-quality event")
    metrics: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise LearningEventError(f"stage_metrics[{index}] must be an object")
        metric = dict(item)
        unknown = set(metric) - _STAGE_FIELDS
        missing = _STAGE_FIELDS - set(metric)
        if unknown or missing:
            raise LearningEventError(f"stage_metrics[{index}] schema is invalid")
        stage = _required_string_value(metric["stage"], f"stage_metrics[{index}].stage")
        if stage in seen:
            raise LearningEventError("stage_metrics stages must be unique")
        seen.add(stage)
        if metric["status"] not in {"complete", "failed", "skipped"}:
            raise LearningEventError(f"stage_metrics[{index}].status is invalid")
        if (
            isinstance(metric["latency_ms"], bool)
            or not isinstance(metric["latency_ms"], int)
            or metric["latency_ms"] < 0
        ):
            raise LearningEventError(f"stage_metrics[{index}].latency_ms is invalid")
        if (
            isinstance(metric["attempts"], bool)
            or not isinstance(metric["attempts"], int)
            or metric["attempts"] < 1
        ):
            raise LearningEventError(f"stage_metrics[{index}].attempts is invalid")
        failure = metric["failure"]
        if metric["status"] == "failed":
            if not isinstance(failure, Mapping) or set(failure) != {"code", "message"}:
                raise LearningEventError(f"stage_metrics[{index}].failure is invalid")
        elif failure is not None:
            raise LearningEventError(f"stage_metrics[{index}].failure must be null")
        metrics.append(metric)
    return metrics


def _failure(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    failure = _strict_object(snapshot, "failure", _FAILURE_FIELDS)
    _require_keys(failure, "failure", _FAILURE_FIELDS)
    if failure["status"] not in {"complete", "partial"}:
        raise LearningEventError("failure.status is invalid")
    if not isinstance(failure["retryable"], bool):
        raise LearningEventError("failure.retryable must be boolean")
    nullable = ("failed_stage", "code", "message")
    if failure["status"] == "complete":
        if any(failure[field] is not None for field in nullable) or failure["retryable"]:
            raise LearningEventError("complete failure schema must contain null details")
    else:
        for field in nullable:
            _required_string_value(failure[field], f"failure.{field}")
    return failure
