"""Explainable analysis-quality anomaly baseline."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from statistics import mean, pstdev
from typing import Any, Iterable

from .learning_event_contract import LearningEvent, LearningEventError, make_learning_event

MIN_BASELINE_ROWS = 3


def build_quality_anomaly_diagnostic(
    events: Iterable[LearningEvent],
    *,
    baseline_version: str,
    as_of_time: str,
) -> LearningEvent:
    as_of = _parse_datetime(as_of_time, "as_of_time")
    rows = [_row(event) for event in events if _parse_datetime(event.available_time, "event available_time") <= as_of]
    baseline = _baseline(rows, baseline_version)
    findings = _findings(rows, baseline)
    digest = _sha256({"baseline": baseline, "findings": findings})
    return make_learning_event(
        kind="candidate_diagnostic",
        identity=f"candidate-diagnostic:analysis-quality:{baseline_version}:{digest[:16]}",
        event_time=as_of_time,
        available_time=as_of_time,
        as_of_time=as_of_time,
        provenance={"source": "analysis-anomaly-baseline", "collector": "trustforge", "observed_at": as_of_time},
        payload={
            "diagnostic_id": f"analysis-quality:{baseline_version}:{digest[:16]}",
            "analysis_id": "analysis-quality-baseline",
            "reason": "rule_based_analysis_quality_anomaly_scan",
            "baseline_version": baseline_version,
            "baseline_sha256": _sha256(baseline),
            "input_count": len(rows),
            "findings": findings,
            "reproducible_query": {
                "event_type": "analysis-quality.v1",
                "as_of_time": as_of_time,
                "baseline_version": baseline_version,
            },
        },
    )


def _row(event: LearningEvent) -> dict[str, Any]:
    if event.kind != "historical_non_evidentiary" or event.payload.get("event_type") != "analysis-quality.v1":
        raise LearningEventError("anomaly baseline requires analysis-quality events")
    confidence = event.payload.get("confidence")
    evidence_stats = event.payload.get("evidence_stats")
    quality = event.payload.get("quality")
    if not isinstance(confidence, dict) or not isinstance(evidence_stats, dict) or not isinstance(quality, dict):
        raise LearningEventError("analysis-quality event missing baseline inputs")
    return {
        "analysis_id": event.payload["analysis_id"],
        "identity": event.identity,
        "event_time": event.event_time,
        "confidence": float(confidence.get("calibrated", 0.0)),
        "missingness": float(evidence_stats.get("missingness", 0.0)),
        "supporting": int(evidence_stats.get("supporting", 0)),
        "contrarian": int(evidence_stats.get("contrarian", 0)),
        "source_concentration": float(evidence_stats.get("source_concentration", 0.0)),
    }


def _baseline(rows: list[dict[str, Any]], version: str) -> dict[str, Any]:
    if not isinstance(version, str) or not version.strip():
        raise LearningEventError("baseline_version is required")
    if len(rows) < MIN_BASELINE_ROWS:
        return {"version": version, "status": "insufficient_data", "minimum_rows": MIN_BASELINE_ROWS}
    confidences = [row["confidence"] for row in rows]
    missingness = [row["missingness"] for row in rows]
    return {
        "version": version,
        "status": "ready",
        "confidence_mean": mean(confidences),
        "confidence_std": pstdev(confidences),
        "missingness_mean": mean(missingness),
        "row_count": len(rows),
    }


def _findings(rows: list[dict[str, Any]], baseline: dict[str, Any]) -> list[dict[str, Any]]:
    if baseline["status"] == "insufficient_data":
        return [{"kind": "insufficient_data", "minimum_rows": baseline["minimum_rows"], "observed_rows": len(rows)}]
    findings: list[dict[str, Any]] = []
    confidence_mean = baseline["confidence_mean"]
    confidence_std = max(baseline["confidence_std"], 0.05)
    for row in rows:
        if abs(row["confidence"] - confidence_mean) > confidence_std * 1.5:
            findings.append(_finding(row, "confidence_drift", "confidence differs from baseline by >1.5 sigma"))
        if row["missingness"] > max(0.5, baseline["missingness_mean"] * 2):
            findings.append(_finding(row, "evidence_missingness", "evidence missingness exceeds baseline"))
        if row["source_concentration"] > 0.8:
            findings.append(_finding(row, "source_concentration", "source concentration exceeds 0.8"))
        if row["supporting"] + row["contrarian"] == 0:
            findings.append(_finding(row, "evidence_absent", "no supporting or contrarian evidence"))
    return sorted(findings, key=lambda item: (item["analysis_id"], item["kind"]))


def _finding(row: dict[str, Any], kind: str, reason: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "analysis_id": row["analysis_id"],
        "input_identity": row["identity"],
        "reason": reason,
    }


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _parse_datetime(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise LearningEventError(f"{field} must be ISO-8601") from None
    if parsed.tzinfo is None:
        raise LearningEventError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc)
