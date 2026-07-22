"""Build auditable, non-networked ModelHub training packages for calibrators."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from .calibrator_gate import calibrator_model_gate_status, evaluate_calibrator_gate
from .calibration_metrics import judge_direction_hit

_CANDIDATES = ("sklearn-logreg", "isotonic")
_FEATURE_CONTRACT = (
    "calibrated_confidence",
    "coin",
    "direction",
)
_ACTIVE_MODEL_ROUTE = "bedrock-direct"
_CANDIDATE_MODEL_ROUTE = "agentcore-gateway"
_ROUTE_DEPENDENCIES = ("historical-calibration", "rag-index", "rag-reranker")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class TrainingDataError(ValueError):
    pass


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        converted = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return converted if math.isfinite(converted) else None


def load_flat_training_rows(path: Path, *, coin: str) -> list[dict[str, Any]]:
    """Load the strict, version-controlled flat JSONL contract for one coin."""
    expected = coin.upper()
    rows: list[dict[str, Any]] = []
    base_required = {"date", "coin", "direction"}
    label_fields = {"outcome_pct", "ground_truth_direction", "split"}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise TrainingDataError("training data is unavailable") from None
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except (json.JSONDecodeError, ValueError, RecursionError):
            raise TrainingDataError(f"invalid JSONL at line {line_number}") from None
        if not isinstance(raw, dict) or not base_required.issubset(raw):
            raise TrainingDataError(f"invalid training schema at line {line_number}")
        if raw["coin"] != expected or not isinstance(raw["direction"], str):
            raise TrainingDataError(f"invalid coin or direction at line {line_number}")
        try:
            date.fromisoformat(raw["date"])
        except (TypeError, ValueError):
            raise TrainingDataError(f"invalid date at line {line_number}") from None
        present_labels = label_fields.intersection(raw)
        if (
            not present_labels
            or "confidence" not in raw
            or (raw.get("outcome_pct") is None and raw.get("ground_truth_direction") is None)
        ):
            continue
        if present_labels != label_fields:
            raise TrainingDataError(f"partially labelled training row at line {line_number}")
        confidence = _finite_number(raw["confidence"])
        outcome = _finite_number(raw["outcome_pct"])
        if (
            confidence is None or not 0 <= confidence <= 1 or outcome is None
            or raw["split"] not in {"train", "val"}
            or raw["ground_truth_direction"] not in {"bullish", "bearish", "neutral"}
        ):
            raise TrainingDataError(f"invalid training values at line {line_number}")
        canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        sample_id = hashlib.sha256(f"{line_number}:{canonical}".encode("utf-8")).hexdigest()
        rows.append({
            "sample_id": sample_id,
            "date": raw["date"], "coin": expected, "direction": raw["direction"],
            "calibrated_confidence": confidence, "confidence": confidence,
            "outcome_pct": outcome, "hit": judge_direction_hit(raw["direction"], outcome / 100),
            "ground_truth_direction": raw["ground_truth_direction"], "split": raw["split"],
        })
    rows.sort(key=lambda row: (row["date"], row["coin"]))
    return rows


def build_flat_training_package(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows = sorted(rows, key=lambda row: (str(row.get("date", "")), str(row.get("coin", ""))))
    gate = evaluate_calibrator_gate(rows)
    digest = hashlib.sha256(_canonical_json(rows)).hexdigest()
    result: dict[str, Any] = {
        "status": "ready_for_modelhub_dry_run" if gate["eligible"] else "blocked",
        "dataset": {"row_count": len(rows), "sha256": digest},
        "gate": gate,
        "rows": rows,
        "automatic_apply": False,
        "requires_human_approval": True,
    }
    if gate["eligible"]:
        result["split"] = {
            "strategy": "chronological_explicit_split", "train_count": gate["train_count"],
            "holdout_count": gate["holdout_count"], "train_end": gate["train_end"],
            "holdout_start": gate["holdout_start"],
        }
    else:
        result["blocked_reason"] = gate["reason"]
    return result


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
                    "outcome": {
                        "horizon": key,
                        "start_close": outcome.get("start_close"),
                        "end_close": outcome.get("end_close"),
                    },
                    "ohlcv_lineage": lineage,
                }
            )
    return sorted(rows, key=lambda row: (row["date"], row["coin"]))


def build_calibrator_training_package(
    label_documents: Iterable[dict[str, Any]], *, horizon: int = 1
) -> dict[str, Any]:
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
        "model_gate_status": calibrator_model_gate_status(rows),
        "rollback": {
            "strategy": "keep_current_calibrator_active",
            "activation": "human_approval_after_holdout_improvement",
        },
        "modelhub_submission_draft": {
            "product": "trustforge",
            "company": "hurricanesoft",
            "submitter": "trustforge-hermes",
            "priority": "P2",
            "model_type": "confidence_calibrator",
            "candidate_architectures": list(_CANDIDATES),
            "purpose": (
                "Time-separated calibration of Hermes information completeness; "
                "not market direction prediction."
            ),
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


def model_route_gate_status(
    gates: dict[str, dict[str, Any]] | None = None,
    *,
    active_route: str = _ACTIVE_MODEL_ROUTE,
    candidate_route: str = _CANDIDATE_MODEL_ROUTE,
) -> dict[str, Any]:
    """Project whether the active model route can move behind AgentCore."""
    gate_map = gates or {}
    checks: list[dict[str, Any]] = []
    for dependency in _ROUTE_DEPENDENCIES:
        status = str((gate_map.get(dependency) or {}).get("status", "missing"))
        passed = status in {"pass", "ready", "ready_for_dry_run"}
        checks.append({"dependency": dependency, "status": status, "passed": passed})

    ready = all(check["passed"] for check in checks)
    return {
        "kind": "model_route_gate_status",
        "status": "ready_for_route_dry_run" if ready else "locked",
        "active_route": active_route,
        "candidate_route": candidate_route,
        "route_gate": "dry_run_only" if ready else "locked_until_dependencies_pass",
        "dependencies": checks,
        "automatic_apply": False,
        "requires_human_approval": True,
    }
