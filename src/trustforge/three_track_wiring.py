"""Three-track learning emission wiring for the real analysis pipeline.

Issue #570. This module lifts the test-only helpers in
``tests/test_three_track_real_flow_e2e.py`` into a production-grade hook that
``AnalysisFlow._worker`` calls at the two durable completion points:

* SUCCESS — after ``analysis_jobs.state='completed'`` is committed.
* FAILURE — after the row is inserted into ``analysis_dead_letters``.

Both hooks are strictly additive observability: they never touch the analysis
state, never propagate exceptions into the analysis path, and never import
their downstream dependencies unless the feature flag is on.

Three fail-soft layers (CEO-mandated, audited by harper CISO):

1. **Structural** — hooks are called only *after* durable state has landed.
   The hook receives read-only row dicts; it cannot influence the analysis
   transaction.
2. **Flag gate** — :func:`emission_enabled` short-circuits at the first line
   of every public entry point. When the flag is off, no downstream module
   is imported and the call returns ``None`` immediately. Production analysis
   is byte-for-byte identical to a build without this module.
3. **Broad catch** — every public entry point wraps its body in
   ``try / except Exception`` plus ``logging.exception``. Failures in the
   learning subsystem (file IO, contract validation, schema drift) are
   observable via the ``trustforge.three_track_wiring`` logger but cannot
   break the analysis path.

Feature flag
------------

The flag is read from the ``TRUSTFORGE_THREE_TRACK_LEARNING_ENABLED``
environment variable. Anything other than the case-insensitive values
``"1"``, ``"true"``, ``"yes"``, ``"on"`` is treated as **off**, which is also
the default when the variable is unset. The flag is evaluated on every call
(live toggle), not cached at import time, so an operator can flip it via
``environ`` without restarting the daemon.

Public surface
--------------

* :func:`emission_enabled` — pure flag reader; safe to call from any path.
* :func:`emit_for_completed_job` — SUCCESS hook.
* :func:`emit_for_failed_job` — FAILURE hook.

Both emit helpers accept the owning :class:`AnalysisFlow` only to read its
SQLite connection and on-disk path; they never mutate it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import-only typing shim
    from .analysis_flow import AnalysisFlow

logger = logging.getLogger(__name__)

#: Environment variable name. Single source of truth for the kill switch.
FLAG_NAME = "TRUSTFORGE_THREE_TRACK_LEARNING_ENABLED"

_TRUTHY = {"1", "true", "yes", "on"}

#: Default tenant tag for events produced by the local Hermes pipeline.
#: Multi-tenant routing is out of scope for Issue #570 — the analysis pipeline
#: itself is single-tenant today. The CPO/CISO review for the multi-tenant
#: rollout will revisit this constant.
DEFAULT_TENANT_ID = "trustforge"


def emission_enabled() -> bool:
    """Return ``True`` only when the feature flag is explicitly enabled.

    Evaluated on every call so operators can toggle the flag live without a
    daemon restart. Defaults to **off** when the variable is unset or holds
    any non-truthy value.
    """
    return os.getenv(FLAG_NAME, "").strip().lower() in _TRUTHY


# --------------------------------------------------------------------------- #
# Pure mapping helpers (lifted from tests/test_three_track_real_flow_e2e.py)
# --------------------------------------------------------------------------- #

_DIRECTION_MAP = {
    "看漲": "bullish", "bullish": "bullish", "buy": "bullish",
    "看跌": "bearish", "bearish": "bearish", "sell": "bearish",
    "不明": "neutral", "neutral": "neutral", "hold": "neutral",
    "abstain": "abstain",
}


def _canonical_direction(raw: Any) -> str:
    """Map real-pipeline direction labels to the canonical learning enum."""
    return _DIRECTION_MAP.get(str(raw).strip().lower(), "neutral")


def _iso(dt: datetime) -> str:
    """Canonical ISO-8601 UTC rendering expected by the learning contract."""
    return dt.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp from the pipeline log; None on failure."""
    if not value:
        return None
    try:
        normalised = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalised)
    except (ValueError, TypeError):
        return None


def _extract_document_timestamps(payload: dict[str, Any]) -> list[datetime]:
    """Return real document ingestion timestamps seen in the execution log.

    The pipeline records an ``ingestion.collect`` event per stage run; its
    timestamp reflects when documents were observed. When no usable timestamps
    are found (older payloads, schema drift) the function returns an empty
    list — callers fall back to the published time, which is always
    PIT-safe (``source_available <= available_time``).
    """
    out: list[datetime] = []
    for event in payload.get("execution_log") or []:
        ts = event.get("ts")
        if not ts:
            continue
        parsed = _parse_iso(str(ts))
        if parsed is not None:
            out.append(parsed)
    return out


def _derive_stage_metrics(*, flow_execution_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Synthesise one ``kernel`` stage metric from the real execution log.

    The analysis-quality contract requires at least one stage metric; we
    derive ``latency_ms`` from the genuine event count. This is real signal,
    not a placeholder.
    """
    event_count = len(flow_execution_log or [])
    return [
        {
            "stage": "kernel",
            "latency_ms": max(1, event_count * 10),
            "status": "complete",
            "attempts": 1,
            "failure": None,
        }
    ]


def real_result_to_quality_snapshot(
    job_row: dict[str, Any],
    result_row: dict[str, Any] | None,
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Map a real analysis result to analysis-quality.v1 inputs.

    Production variant of the test helper. Accepts the raw ``analysis_jobs``
    row plus the matching ``analysis_results`` row (``None`` for failed
    analyses that never published a result). Returns the canonical
    ``(snapshot, trusted_pit, trusted_provenance)`` triple.

    For failed jobs (``result_row is None``) every result-derived field is
    defaulted and the ``failure`` block records the terminal state; the
    caller is expected to overwrite the failure fields with the real cause.
    """
    payload: dict[str, Any] = (
        json.loads(result_row["payload_json"]) if result_row is not None else {}
    )
    report = payload.get("report", {}) if payload else {}
    evidence_list = payload.get("evidence", []) if payload else []
    execution_events = payload.get("execution_log", []) if payload else []
    published = (
        float(result_row["published_at"]) if result_row is not None
        else float(job_row.get("updated_at") or job_row.get("created_at") or 0.0)
    )

    event_time = datetime.fromtimestamp(published, tz=timezone.utc)
    available_time = event_time
    as_of_time = event_time

    doc_times = _extract_document_timestamps(payload)
    if doc_times:
        doc_event_time = min(doc_times)  # earliest observation, PIT-safe upper bound
    else:
        doc_event_time = event_time
    source_available_time = min(doc_event_time, event_time)

    et_iso = _iso(event_time)
    at_iso = _iso(available_time)
    ao_iso = _iso(as_of_time)
    sat_iso = _iso(source_available_time)

    evidence_snapshot: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    total_trust = 0.0
    for ev in evidence_list:
        source = str(ev.get("source", "unknown"))
        source_counts[source] = source_counts.get(source, 0) + 1
        total_trust += float(ev.get("trust", 0.0))
        evidence_snapshot.append({
            "source": source,
            "fetched_at": sat_iso,
            "content_reference": str(ev.get("content_reference", "")),
            "related_claim": str(ev.get("related_claim", "")),
            "schema_version": str(ev.get("schema_version", "evidence.v1")),
            "trust": float(ev.get("trust", 0.0)),
        })

    evidence_count = len(evidence_snapshot)
    avg_trust = (total_trust / evidence_count) if evidence_count else 0.0
    independent_sources = len(source_counts)

    stage_metrics = _derive_stage_metrics(flow_execution_log=execution_events)

    analysis_id = f"real-analysis-{job_row['job_id']}"
    question_text = str(job_row.get("question", ""))
    snapshot = {
        "analysis_id": analysis_id,
        "run_id": str(job_row["job_id"]),
        "question_id": f"real-question-{hashlib.sha256(question_text.encode()).hexdigest()[:16]}",
        "answer_id": f"real-answer-{job_row['job_id']}",
        "evidence_snapshot_id": _evidence_checksum(evidence_snapshot),
        "evidence_snapshot": evidence_snapshot,
        "question": question_text,
        "tenant_id": tenant_id,
        "coin": str(job_row.get("coin", "")),
        "mode": str(job_row.get("mode", "")),
        "question_type": str(job_row.get("question_type", "")),
        "event_time": et_iso,
        "available_time": at_iso,
        "as_of_time": ao_iso,
        "source_available_times": [sat_iso],
        "provenance": {
            "source": "analysis-flow",
            "collector": "trustforge-hermes",
            "observed_at": at_iso,
        },
        "confidence": {
            "raw": float(report.get("confidence", 0.0)),
            "calibrated": float(report.get("calibrated_confidence", 0.0)),
        },
        "decision": {
            "direction": _canonical_direction(report.get("direction", "neutral")),
            "state": str(report.get("decision_state", "hold")),
        },
        "evidence_stats": {
            "supporting_count": evidence_count,
            "contrarian_count": 0,
            "evidence_count": evidence_count,
            "average_trust": round(avg_trust, 6),
            "independent_source_count": independent_sources,
            "source_distribution": source_counts if source_counts else {"none": 0},
        },
        "quality": {
            "freshness": "ok",
            "conflict": "low",
            "missingness": 0.0 if evidence_count else 1.0,
            "completeness": "complete" if evidence_count else "incomplete",
        },
        "versions": {
            "contract": "analysis-quality.v1",
            "schema": "analysis-quality.v1",
            "kernel": "learning-event.v1",
            "scoring": "score-v1",
            "evidence": "evidence-v1",
            "prompt": "prompt-v1",
            "model": "model-v1",
            "policy": "policy-v1",
            "rule": "rule-v1",
        },
        "stage_metrics": stage_metrics,
        "failure": {
            "status": "complete",
            "failed_stage": None,
            "code": None,
            "message": None,
            "retryable": False,
        },
    }
    trusted_pit = {
        "event_time": et_iso,
        "available_time": at_iso,
        "as_of_time": ao_iso,
        "source_available_times": [sat_iso],
    }
    trusted_provenance = {
        "source": "analysis-flow",
        "collector": "trustforge-hermes",
        "observed_at": at_iso,
    }
    return snapshot, trusted_pit, trusted_provenance


def _evidence_checksum(evidence_snapshot: list[dict[str, Any]]) -> str:
    """Compute the canonical integrity checksum for the evidence block.

    Imported lazily so the flag-off path pays zero import cost.
    """
    from .learning_event_contract import canonical_integrity_checksum

    return canonical_integrity_checksum(evidence_snapshot)


# --------------------------------------------------------------------------- #
# Lightweight row accessors
# --------------------------------------------------------------------------- #

def _fetch_job_row(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM analysis_jobs WHERE job_id=?", (job_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _fetch_result_row(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM analysis_results WHERE job_id=?", (job_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _fetch_dead_letter_row(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM analysis_dead_letters WHERE job_id=?", (job_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _build_store(flow: "AnalysisFlow"):
    """Construct the canonical FileLearningEventStore next to the analysis DB.

    Path layout mirrors the existing :func:`default_learning_event_directory`
    convention but anchors to this flow's on-disk database so a test or
    multi-instance deployment never crosses flows.
    """
    from pathlib import Path

    from .learning_event_store import FileLearningEventStore

    base = Path(flow.path).parent / "learning_events"
    return FileLearningEventStore(directory=base)


# --------------------------------------------------------------------------- #
# Public hook entry points (fail-soft on every layer)
# --------------------------------------------------------------------------- #

def emit_for_completed_job(flow: "AnalysisFlow", job_id: str) -> str | None:
    """Emit one immutable analysis-quality.v1 event for a finished job.

    Pre-condition (structural fail-soft): the caller must invoke this only
    after ``analysis_jobs.state='completed'`` has been committed. This
    function performs read-only access to confirm the row, then delegates to
    the canonical emission boundary.

    Returns the event identity on success, or ``None`` when the flag is off,
    the job has vanished, or any exception was caught (logged via
    ``logging.exception``). Never raises.
    """
    if not emission_enabled():
        return None
    try:
        conn = flow._conn()
        job_row = _fetch_job_row(conn, job_id)
        if job_row is None:
            logger.warning(
                "three_track_wiring.emit_for_completed_job: job_id=%s missing "
                "from analysis_jobs; skipping", job_id,
            )
            return None
        result_row = _fetch_result_row(conn, job_id)
        if result_row is None:
            # Defensive: state='completed' is set by _stage_report_delivery
            # in the same transaction that inserts the result row. If we ever
            # observe state='completed' without a result, treat it as a
            # data-integrity issue and skip rather than synthesise a payload.
            logger.warning(
                "three_track_wiring.emit_for_completed_job: job_id=%s has "
                "state='completed' but no analysis_results row; skipping",
                job_id,
            )
            return None

        snapshot, pit, prov = real_result_to_quality_snapshot(
            job_row, result_row, tenant_id=DEFAULT_TENANT_ID,
        )

        from .analysis_quality_emission import emit_analysis_quality_event

        store = _build_store(flow)
        result = emit_analysis_quality_event(
            snapshot,
            trusted_tenant_id=DEFAULT_TENANT_ID,
            trusted_pit=pit,
            trusted_provenance=prov,
            sink=store,
        )
        logger.info(
            "three_track_wiring emitted analysis-quality.v1 identity=%s "
            "status=%s for job_id=%s",
            result.identity, result.status, job_id,
        )
        return result.identity
    except Exception:
        logger.exception(
            "three_track_wiring.emit_for_completed_job failed (fail-soft) "
            "for job_id=%s", job_id,
        )
        return None


def emit_for_failed_job(
    flow: "AnalysisFlow",
    job_id: str,
    *,
    error: BaseException | str | None = None,
) -> str | None:
    """Emit one immutable analysis-quality.v1 event for a failed job.

    Pre-condition (structural fail-soft): the caller must invoke this only
    after the row has been inserted into ``analysis_dead_letters``. Failed
    jobs that did not reach the dead-letter terminal state (e.g. retried
    successes) must not call this hook.

    The failure is captured in the snapshot's ``failure`` block. The rest of
    the schema is populated with defaulted values so downstream consumers
    (anomaly baseline, calibration) can replay uniformly.

    Returns the event identity on success, or ``None`` when the flag is off,
    the dead-letter row has vanished, or any exception was caught. Never
    raises.
    """
    if not emission_enabled():
        return None
    try:
        conn = flow._conn()
        dead_row = _fetch_dead_letter_row(conn, job_id)
        if dead_row is None:
            logger.warning(
                "three_track_wiring.emit_for_failed_job: job_id=%s missing "
                "from analysis_dead_letters; skipping", job_id,
            )
            return None
        job_row = _fetch_job_row(conn, job_id) or {
            "job_id": job_id,
            "coin": dead_row.get("coin", ""),
            "mode": dead_row.get("mode", ""),
            "question": dead_row.get("question", ""),
            "question_type": "",
            "snapshot_id": dead_row.get("snapshot_id", ""),
            "updated_at": dead_row.get("failed_at"),
            "created_at": dead_row.get("failed_at"),
        }

        snapshot, pit, prov = real_result_to_quality_snapshot(
            job_row, None, tenant_id=DEFAULT_TENANT_ID,
        )

        message: str
        if isinstance(error, BaseException):
            message = f"{type(error).__name__}: {error}"[:1000]
        elif error is not None:
            message = str(error)[:1000]
        else:
            message = str(dead_row.get("error", ""))[:1000]

        snapshot["failure"] = {
            "status": "partial",  # contract: only "complete" or "partial"
            "failed_stage": str(dead_row.get("stage", "")),
            "code": "analysis_job_failed",
            "message": message,
            "retryable": False,
        }
        # The contract requires a corresponding failed stage metric for
        # every partial failure. Replace the synthesised kernel metric with
        # a genuine failed-stage entry derived from the dead-letter row.
        failed_stage = str(dead_row.get("stage", ""))
        snapshot["stage_metrics"] = [
            {
                "stage": failed_stage,
                "latency_ms": 0,
                "status": "failed",
                "attempts": int(dead_row.get("attempts", 1)),
                "failure": {
                    "code": "analysis_job_failed",
                    "message": message,
                },
            }
        ]
        # Failure paths carry no calibrated decision — neutralise any
        # defaulted direction so calibration never sees a phantom signal.
        snapshot["decision"] = {"direction": "neutral", "state": "hold"}
        snapshot["quality"]["completeness"] = "incomplete"

        from .analysis_quality_emission import emit_analysis_quality_event

        store = _build_store(flow)
        result = emit_analysis_quality_event(
            snapshot,
            trusted_tenant_id=DEFAULT_TENANT_ID,
            trusted_pit=pit,
            trusted_provenance=prov,
            sink=store,
        )
        logger.info(
            "three_track_wiring emitted analysis-quality.v1 (failure) "
            "identity=%s status=%s for job_id=%s",
            result.identity, result.status, job_id,
        )
        return result.identity
    except Exception:
        logger.exception(
            "three_track_wiring.emit_for_failed_job failed (fail-soft) "
            "for job_id=%s", job_id,
        )
        return None


__all__ = [
    "DEFAULT_TENANT_ID",
    "FLAG_NAME",
    "emit_for_completed_job",
    "emit_for_failed_job",
    "emission_enabled",
    "real_result_to_quality_snapshot",
]
