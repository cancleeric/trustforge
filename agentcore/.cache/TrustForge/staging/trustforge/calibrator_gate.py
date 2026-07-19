"""Hard eligibility gate for future confidence-calibrator experiments."""
from __future__ import annotations
from typing import Any, Iterable

MIN_ELIGIBLE_OUTCOMES = 100

def evaluate_calibrator_gate(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = sorted((row for row in rows if row.get("date") and row.get("hit") is not None), key=lambda row: str(row["date"]))
    if len(rows) < MIN_ELIGIBLE_OUTCOMES:
        return {"eligible": False, "reason": "insufficient_eligible_outcomes", "eligible_outcomes": len(rows), "minimum": MIN_ELIGIBLE_OUTCOMES}
    split = max(1, int(len(rows) * .8))
    train, holdout = rows[:split], rows[split:]
    if not holdout or str(train[-1]["date"]) >= str(holdout[0]["date"]):
        return {"eligible": False, "reason": "time_separated_holdout_missing", "eligible_outcomes": len(rows)}
    return {"eligible": True, "eligible_outcomes": len(rows), "train_count": len(train), "holdout_count": len(holdout), "rule": "compare logistic and isotonic only on this time-separated split"}
