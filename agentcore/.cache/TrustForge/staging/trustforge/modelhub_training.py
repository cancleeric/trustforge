"""Build auditable, non-networked ModelHub training packages for calibrators."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from .calibrator_gate import evaluate_calibrator_gate

_CANDIDATES = ("sklearn-logreg", "isotonic")
_FEATURE_CONTRACT = (
    "calibrated_confidence",
    "coin",
    "direction",
)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def eligible_calibrator_rows(label_documents: Iterable[dict[str, Any]], *, horizon: int = 1) -> list[dict[str, Any]]:
    """Extract only labelled, point-in-time replay outcomes for one horizon."""
    key = f"T+{horizon}"
    rows: list[dict[str, Any]] = []
    for document in label_documents:
        for label in document.get("labels", []):
            outcome = (label.get("outcomes") or {}).get(key) or {}
            if outcome.get("status") != "labeled" or outcome.get("hit") is None:
                continue
            lineage = label.get("ohlcv_lineage") or {}
            rows.append(
                {
                    "date": str(label.get("date", "")),
                    "coin": str(label.get("coin", "")),
                    "direction": str(label.get("direction", "")),
                    "calibrated_confidence": label.get("calibrated_confidence"),
                    "hit": bool(outcome["hit"]),
                    "directional_return_pct": outcome.get("directional_return_pct"),
                    "outcome": {"horizon": key, "start_close": outcome.get("start_close"), "end_close": outcome.get("end_close")},
                    "ohlcv_lineage": lineage,
                }
            )
    return sorted(rows, key=lambda row: (row["date"], row["coin"]))


def build_calibrator_training_package(label_documents: Iterable[dict[str, Any]], *, horizon: int = 1) -> dict[str, Any]:
    """Return a ModelHub-ready draft without credentials, networking, or model fitting."""
    rows = eligible_calibrator_rows(label_documents, horizon=horizon)
    gate = evaluate_calibrator_gate(rows)
    dataset_sha256 = hashlib.sha256(_canonical_json(rows)).hexdigest()
    result: dict[str, Any] = {
        "kind": "trustforge_confidence_calibrator_training_package",
        "version": 1,
        "network_action": "none",
        "dataset": {
            "row_count": len(rows),
            "sha256": dataset_sha256,
            "horizon": f"T+{horizon}",
            "source": "historical_replay_outcome_labels",
            "time_boundary": "each replay uses only published_at <= T; outcomes use later official OHLCV",
        },
        "feature_contract": list(_FEATURE_CONTRACT),
        "gate": gate,
        "rollback": {"strategy": "keep_current_calibrator_active", "activation": "human_approval_after_holdout_improvement"},
        "modelhub_submission_draft": {
            "product": "trustforge",
            "company": "hurricanesoft",
            "submitter": "trustforge-hermes",
            "priority": "P2",
            "model_type": "confidence_calibrator",
            "candidate_architectures": list(_CANDIDATES),
            "purpose": "Time-separated calibration of Hermes information completeness; not market direction prediction.",
            "contract_status": "requires_modelhub_api_contract_confirmation",
        },
    }
    if not gate["eligible"]:
        result["status"] = "blocked"
        result["blocked_reason"] = gate["reason"]
        return result

    split = gate["train_count"]
    result["status"] = "ready_for_modelhub_dry_run"
    result["split"] = {
        "strategy": "chronological_80_20",
        "train_count": split,
        "holdout_count": gate["holdout_count"],
        "train_end": rows[split - 1]["date"],
        "holdout_start": rows[split]["date"],
    }
    result["acceptance"] = {
        "requirement": "holdout calibration improvement over current deterministic baseline",
        "forbidden": ["future_leakage", "automatic_activation", "prediction_probability_claim"],
    }
    return result
