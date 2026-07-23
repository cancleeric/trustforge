"""Delayed T+N outcome observations for analysis-quality events."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from .learning_event_contract import LearningEvent, LearningEventError, make_learning_event

_HORIZON_DAYS = {"T+1": 1, "T+7": 7, "T+14": 14}


def build_delayed_outcome_observation(
    analysis_event: LearningEvent,
    *,
    horizon: str,
    as_of_time: str,
    prices: dict[str, dict[str, Any]],
    source_version: str,
    revision: int = 1,
    dry_run: bool = False,
) -> LearningEvent:
    """Create an append-only delayed outcome observation for one analysis."""

    if analysis_event.kind != "historical_non_evidentiary":
        raise LearningEventError("delayed outcome requires analysis-quality source event")
    if analysis_event.payload.get("event_type") != "analysis-quality.v1":
        raise LearningEventError("delayed outcome source must be analysis-quality.v1")
    if horizon not in _HORIZON_DAYS:
        raise LearningEventError("unsupported outcome horizon")
    if revision < 1:
        raise LearningEventError("revision must be positive")

    event_date = _parse_datetime(analysis_event.event_time, "event_time").date()
    maturity_date = event_date + timedelta(days=_HORIZON_DAYS[horizon])
    as_of = _parse_datetime(as_of_time, "as_of_time")
    base = _price_for(prices, event_date)
    matured = as_of.date() >= maturity_date
    target = _price_for(prices, maturity_date)

    status = "pending"
    outcome: dict[str, Any] = {}
    if matured and (base is None or target is None):
        status = "unavailable"
    elif matured:
        status = "labeled"
        outcome = _outcome_values(base, target)

    payload = {
        "outcome_id": f"{analysis_event.identity}:{horizon}:v{revision}",
        "analysis_id": analysis_event.payload["analysis_id"],
        "horizon": horizon,
        "status": status,
        "source_event_identity": analysis_event.identity,
        "maturity_date": maturity_date.isoformat(),
        "dry_run": dry_run,
        "revision": str(revision),
        "source_version": source_version,
        "available_time": as_of_time,
        **outcome,
    }
    return make_learning_event(
        kind="delayed_outcome",
        identity=payload["outcome_id"],
        event_time=analysis_event.event_time,
        available_time=as_of_time,
        as_of_time=as_of_time,
        provenance={
            "source": "delayed-outcome-labeler",
            "collector": "trustforge",
            "observed_at": as_of_time,
            "source_version": source_version,
        },
        payload=payload,
    )


def _outcome_values(base: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    start_close = _number(base.get("close"), "start_close")
    end_close = _number(target.get("close"), "end_close")
    if start_close == 0:
        raise LearningEventError("start_close cannot be zero")
    pct = ((end_close - start_close) / start_close) * 100
    return {
        "start_close": start_close,
        "end_close": end_close,
        "outcome_pct": pct,
        "ground_truth_direction": "bullish" if pct > 0 else "bearish" if pct < 0 else "neutral",
        "source_lineage": {
            "start_source_id": str(base.get("source_id", "")),
            "end_source_id": str(target.get("source_id", "")),
            "start_available_time": str(base.get("available_time", "")),
            "end_available_time": str(target.get("available_time", "")),
        },
    }


def _price_for(prices: dict[str, dict[str, Any]], target_date: date) -> dict[str, Any] | None:
    value = prices.get(target_date.isoformat())
    if value is None:
        return None
    if not isinstance(value, dict):
        raise LearningEventError("price observation must be an object")
    available = value.get("available_time")
    if not isinstance(available, str):
        raise LearningEventError("price observation available_time is required")
    return value


def _parse_datetime(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise LearningEventError(f"{field} must be ISO-8601") from None
    if parsed.tzinfo is None:
        raise LearningEventError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc)


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LearningEventError(f"{field} must be numeric")
    return float(value)
