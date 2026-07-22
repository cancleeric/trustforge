"""Build auditable, non-networked ModelHub training packages for calibrators."""
from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from datetime import date, datetime, timezone
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
TRAINING_SCHEMA_VERSION = 1
MAX_TRAINING_FILE_BYTES = 16 * 1024 * 1024
MAX_TRAINING_LINE_BYTES = 256 * 1024
MAX_TRAINING_SOURCE_LINES = 10_000
MAX_ELIGIBLE_ROWS = 10_000


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
    candidates: dict[str, list[tuple[tuple[Any, ...], dict[str, Any], tuple[Any, ...]]]] = {}
    base_required = {"date", "coin", "direction"}
    label_fields = {"outcome_pct", "ground_truth_direction", "split"}
    descriptor: int | None = None
    try:
        path_stat = os.lstat(path)
        if stat.S_ISLNK(path_stat.st_mode):
            raise TrainingDataError("training data symlinks are not allowed")
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            os.close(descriptor)
            descriptor = None
            raise TrainingDataError("training data must be a regular file")
        if (path_stat.st_dev, path_stat.st_ino) != (file_stat.st_dev, file_stat.st_ino):
            os.close(descriptor)
            descriptor = None
            raise TrainingDataError("training data identity changed during open")
        if file_stat.st_size > MAX_TRAINING_FILE_BYTES:
            os.close(descriptor)
            descriptor = None
            raise TrainingDataError("training data exceeds file size limit")
        handle = os.fdopen(descriptor, "rb")
        descriptor = None
    except OSError:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise TrainingDataError("training data is unavailable") from None
    eligible_candidates = 0
    streamed_bytes = 0
    with handle:
        for line_number, encoded_line in enumerate(handle, 1):
            streamed_bytes += len(encoded_line)
            if streamed_bytes > MAX_TRAINING_FILE_BYTES:
                raise TrainingDataError("training data exceeds file size limit")
            if line_number > MAX_TRAINING_SOURCE_LINES:
                raise TrainingDataError("training data exceeds source line limit")
            if len(encoded_line) > MAX_TRAINING_LINE_BYTES:
                raise TrainingDataError(f"training line exceeds size limit at line {line_number}")
            if not encoded_line.strip():
                continue
            try:
                raw = json.loads(encoded_line)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
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
            generated_at = raw.get("generated_at")
            try:
                parsed_generated_at = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
                if parsed_generated_at.tzinfo is None:
                    raise ValueError("timestamp must be timezone aware")
                parsed_generated_at = parsed_generated_at.astimezone(timezone.utc)
            except (AttributeError, TypeError, ValueError):
                raise TrainingDataError(f"invalid generated_at at line {line_number}") from None
            if (
                confidence is None or not 0 <= confidence <= 1 or outcome is None
                or raw["split"] not in {"train", "val"}
                or raw["ground_truth_direction"] not in {"bullish", "bearish", "neutral"}
            ):
                raise TrainingDataError(f"invalid training values at line {line_number}")
            eligible_candidates += 1
            if eligible_candidates > MAX_ELIGIBLE_ROWS:
                raise TrainingDataError("training data exceeds eligible row limit")
            sample_id = hashlib.sha256(
                f"{TRAINING_SCHEMA_VERSION}|{expected}|{raw['date']}".encode("utf-8")
            ).hexdigest()
            row = {
                "sample_id": sample_id,
                "date": raw["date"], "coin": expected, "direction": raw["direction"],
                "calibrated_confidence": confidence, "confidence": confidence,
                "outcome_pct": outcome, "hit": judge_direction_hit(raw["direction"], outcome / 100),
                "ground_truth_direction": raw["ground_truth_direction"], "split": raw["split"],
            }
            label_identity = (raw["split"], raw["ground_truth_direction"], outcome)
            source_identity = json.dumps(
                {
                    "generated_at": raw.get("generated_at"),
                    "sources": raw.get("sources"),
                    "model_id": raw.get("model_id"),
                },
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            )
            inference_identity = (raw["direction"], confidence, source_identity)
            candidates.setdefault(raw["date"], []).append(
                ((parsed_generated_at, inference_identity), row, label_identity)
            )
    rows: list[dict[str, Any]] = []
    for row_date, duplicate_rows in candidates.items():
        if len({candidate[2] for candidate in duplicate_rows}) != 1:
            raise TrainingDataError(f"conflicting duplicate outcome for {row_date}")
        earliest = min(candidate[0][0] for candidate in duplicate_rows)
        earliest_rows = [candidate for candidate in duplicate_rows if candidate[0][0] == earliest]
        if len({candidate[0][1] for candidate in earliest_rows}) != 1:
            raise TrainingDataError(f"conflicting earliest inference for {row_date}")
        rows.append(earliest_rows[0][1])
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
