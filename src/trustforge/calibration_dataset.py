"""Build deterministic confidence calibration datasets from learning events."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from .learning_event_contract import LearningEvent


class CalibrationDatasetError(ValueError):
    pass


def build_confidence_calibration_dataset(
    analysis_events: Iterable[LearningEvent],
    outcome_events: Iterable[LearningEvent],
    *,
    producer_version: str,
    trusted_tenant_id: str,
    market_data_variant: str,
) -> dict[str, Any]:
    if not isinstance(producer_version, str) or not producer_version.strip():
        raise CalibrationDatasetError("producer_version is required")
    if not isinstance(trusted_tenant_id, str) or not trusted_tenant_id:
        raise CalibrationDatasetError("trusted_tenant_id is required")
    if market_data_variant not in {"as_first_known", "latest_official"}:
        raise CalibrationDatasetError("market_data_variant must be selected explicitly")
    analyses = [
        _analysis_row(event)
        for event in analysis_events
        if event.tenant_id == trusted_tenant_id
    ]
    outcomes = _latest_labeled_outcomes(
        outcome_events,
        trusted_tenant_id=trusted_tenant_id,
        market_data_variant=market_data_variant,
    )
    rows = []
    for analysis in analyses:
        for (_, analysis_id, horizon, _), outcome in outcomes.items():
            if analysis_id != analysis["analysis_id"]:
                continue
            if outcome["source_event_identity"] != analysis["analysis_identity"]:
                continue
            if _parse(outcome["outcome_available_time"]) <= _parse(
                analysis["analysis_available_time"]
            ):
                raise CalibrationDatasetError(
                    "outcome cannot be available before analysis availability"
                )
            rows.append({**analysis, **outcome})
    rows.sort(key=lambda row: (row["analysis_event_time"], row["analysis_id"], row["horizon"]))
    for index, row in enumerate(rows):
        row["split"] = _split(index, len(rows))
    manifest = {
        "kind": "confidence-calibration-dataset.v1",
        "producer_version": producer_version,
        "tenant_id": trusted_tenant_id,
        "market_data_variant": market_data_variant,
        "row_count": len(rows),
        "schema_versions": sorted({row["schema_version"] for row in rows}),
        "rows_sha256": _sha256(rows),
        "rows": rows,
    }
    manifest["manifest_sha256"] = _sha256(
        {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    )
    return manifest


def _analysis_row(event: LearningEvent) -> dict[str, Any]:
    if event.kind != "historical_non_evidentiary" or event.payload.get("event_type") != "analysis-quality.v1":
        raise CalibrationDatasetError("dataset source must be analysis-quality event")
    if "five_year_ohlcv_rows" in event.payload or event.payload.get("source_kind") == "five_year_ohlcv":
        raise CalibrationDatasetError("five-year OHLCV cannot be expanded as analysis samples")
    analysis_id = event.payload.get("analysis_id")
    if not isinstance(analysis_id, str) or not analysis_id:
        raise CalibrationDatasetError("analysis_id is required")
    confidence = event.payload.get("confidence")
    decision = event.payload.get("decision")
    if not isinstance(confidence, Mapping) or not isinstance(decision, Mapping):
        raise CalibrationDatasetError("analysis confidence and decision are required")
    return {
        "analysis_id": analysis_id,
        "tenant_id": event.tenant_id,
        "analysis_identity": event.identity,
        "schema_version": event.schema_version,
        "analysis_event_time": event.event_time,
        "analysis_available_time": event.available_time,
        "coin": event.payload.get("coin"),
        "mode": event.payload.get("mode"),
        "question_type": event.payload.get("question_type"),
        "calibrated_confidence": confidence.get("calibrated"),
        "raw_confidence": confidence.get("raw"),
        "direction": decision.get("direction"),
    }


def _latest_labeled_outcomes(
    outcome_events: Iterable[LearningEvent],
    *,
    trusted_tenant_id: str,
    market_data_variant: str,
) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    outcomes: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    revisions: dict[tuple[str, str, str, str], int] = {}
    for event in outcome_events:
        if event.kind != "delayed_outcome":
            raise CalibrationDatasetError("dataset outcome source must be delayed_outcome event")
        payload = event.payload
        if event.tenant_id != trusted_tenant_id:
            continue
        if payload.get("market_data_variant") != market_data_variant:
            continue
        if payload.get("status") != "labeled":
            continue
        analysis_id = payload.get("analysis_id")
        horizon = payload.get("horizon")
        if not isinstance(analysis_id, str) or not isinstance(horizon, str):
            raise CalibrationDatasetError("outcome analysis_id and horizon are required")
        if not isinstance(payload.get("source_event_identity"), str):
            raise CalibrationDatasetError("outcome source_event_identity is required")
        key = (event.tenant_id, analysis_id, horizon, market_data_variant)
        revision = payload.get("outcome_version")
        if type(revision) is not int or revision < 1:
            raise CalibrationDatasetError("canonical outcome_version is required")
        if revision < revisions.get(key, 0):
            continue
        if revision == revisions.get(key, 0):
            raise CalibrationDatasetError("duplicate outcome revision")
        revisions[key] = revision
        outcomes[key] = {
            "outcome_identity": event.identity,
            "source_event_identity": payload.get("source_event_identity"),
            "tenant_id": event.tenant_id,
            "market_data_variant": market_data_variant,
            "outcome_available_time": event.available_time,
            "outcome_source_version": payload.get("market_data_revision"),
            "horizon": horizon,
            "outcome_pct": payload.get("return_pct"),
            "ground_truth_direction": _ground_truth(payload),
        }
    return outcomes


def _ground_truth(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("return_pct")
    if not isinstance(value, str):
        return None
    try:
        numeric = Decimal(value)
    except InvalidOperation:
        raise CalibrationDatasetError("canonical return_pct is invalid") from None
    return "bullish" if numeric > 0 else "bearish" if numeric < 0 else "neutral"


def _split(index: int, total: int) -> str:
    if total <= 1:
        return "test"
    train_end = max(1, int(total * 0.7))
    validation_end = max(train_end + 1, int(total * 0.85))
    if index < train_end:
        return "train"
    if index < validation_end:
        return "validation"
    return "test"


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise CalibrationDatasetError("dataset timestamps must be timezone aware")
    return parsed.astimezone(timezone.utc)
