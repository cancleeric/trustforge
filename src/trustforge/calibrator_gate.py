"""Hard eligibility gate and status projection for calibrator experiments."""

from __future__ import annotations

from typing import Any, Iterable


MIN_ELIGIBLE_OUTCOMES = 100
HOLDOUT_RATIO = 0.2
DEFAULT_ACTIVE_CALIBRATOR = "heuristic_evidence_strength_v1"
DEFAULT_ABSTAIN_POLICY = "heuristic_three_state_abstain"


def _status_for_gate(gate: dict[str, Any]) -> str:
    return "ready_for_dry_run" if gate.get("eligible") else "blocked"


def evaluate_calibrator_gate(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = sorted(
        (row for row in rows if row.get("date") and row.get("hit") is not None),
        key=lambda row: str(row["date"]),
    )
    if len(rows) < MIN_ELIGIBLE_OUTCOMES:
        return {
            "eligible": False,
            "reason": "insufficient_eligible_outcomes",
            "eligible_outcomes": len(rows),
            "minimum": MIN_ELIGIBLE_OUTCOMES,
            "remaining": MIN_ELIGIBLE_OUTCOMES - len(rows),
        }

    explicit_split = all(row.get("split") in {"train", "val"} for row in rows)
    explicit_train_count: int | None = None
    if any("split" in row for row in rows) and not explicit_split:
        return {
            "eligible": False, "reason": "invalid_explicit_split", "eligible_outcomes": len(rows),
            "minimum": MIN_ELIGIBLE_OUTCOMES, "remaining": 0,
        }
    if explicit_split:
        split_values = [row["split"] for row in rows]
        train_count = split_values.count("train")
        explicit_train_count = train_count
        val_count = split_values.count("val")
        train_dates = {str(row["date"]) for row in rows if row["split"] == "train"}
        val_dates = {str(row["date"]) for row in rows if row["split"] == "val"}
        if (
            not train_count
            or not val_count
            or split_values != ["train"] * train_count + ["val"] * val_count
            or train_dates & val_dates
            or max(train_dates) >= min(val_dates)
        ):
            return {
                "eligible": False, "reason": "explicit_split_not_chronological",
                "eligible_outcomes": len(rows), "minimum": MIN_ELIGIBLE_OUTCOMES, "remaining": 0,
            }

    split = explicit_train_count if explicit_train_count is not None else max(
        1, int(len(rows) * (1 - HOLDOUT_RATIO))
    )
    train, holdout = rows[:split], rows[split:]
    if not holdout or str(train[-1]["date"]) >= str(holdout[0]["date"]):
        return {
            "eligible": False,
            "reason": "time_separated_holdout_missing",
            "eligible_outcomes": len(rows),
            "minimum": MIN_ELIGIBLE_OUTCOMES,
            "remaining": 0,
        }

    return {
        "eligible": True,
        "eligible_outcomes": len(rows),
        "minimum": MIN_ELIGIBLE_OUTCOMES,
        "remaining": 0,
        "train_count": len(train),
        "holdout_count": len(holdout),
        "train_end": str(train[-1]["date"]),
        "holdout_start": str(holdout[0]["date"]),
        "rule": "compare logistic and isotonic only on this time-separated split",
    }


def calibrator_model_gate_status(
    rows: Iterable[dict[str, Any]],
    *,
    active_calibrator: str = DEFAULT_ACTIVE_CALIBRATOR,
    abstain_policy: str = DEFAULT_ABSTAIN_POLICY,
) -> dict[str, Any]:
    """Return a UI/API friendly read-only status card for #267."""
    gate = evaluate_calibrator_gate(rows)
    return {
        "kind": "calibrator_model_gate_status",
        "status": _status_for_gate(gate),
        "active_calibrator": active_calibrator,
        "candidate_calibrator": "modelhub_logistic_or_isotonic" if gate["eligible"] else None,
        "abstain_policy": {
            "active": abstain_policy,
            "state": "unchanged",
            "model_gate": "dry_run_only" if gate["eligible"] else "locked_until_holdout_passes",
        },
        "gate": gate,
        "next_action": (
            "build_modelhub_training_package_and_compare_holdout"
            if gate["eligible"]
            else "collect_more_time_separated_outcomes"
        ),
        "automatic_apply": False,
        "requires_human_approval": True,
    }
