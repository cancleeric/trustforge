"""Orchestrate a fail-closed calibrator retraining proposal."""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .calibration_metrics import weighted_ece
from .execlog import ExecutionLog
from .modelhub_client import (
    ModelHubClient,
    ModelHubError,
    ModelHubPollTimeout,
    ModelHubTransportError,
)
from .modelhub_training import (
    TrainingDataError,
    build_flat_training_package,
    load_flat_training_rows,
)

MIN_ECE_IMPROVEMENT = 0.02
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")


def _summary(status: str, coin: str, **values: Any) -> dict[str, Any]:
    return {"status": status, "coin": coin, **values}


def _candidate_predictions(result: dict[str, Any], holdout: list[dict[str, Any]]) -> list[float]:
    predictions = result.get("holdout_predictions")
    if not isinstance(predictions, list) or len(predictions) != len(holdout):
        raise ValueError("holdout predictions are missing or incomplete")
    expected = {row["sample_id"]: (row["date"], row["coin"]) for row in holdout}
    observed: dict[str, float] = {}
    for prediction in predictions:
        if not isinstance(prediction, dict):
            raise ValueError("invalid holdout prediction")
        sample_id = prediction.get("sample_id")
        value = prediction.get("confidence", prediction.get("calibrated_confidence"))
        if (
            sample_id not in expected
            or sample_id in observed
            or (prediction.get("date"), prediction.get("coin")) != expected[sample_id]
        ):
            raise ValueError("holdout predictions are not aligned")
        observed[sample_id] = value
    if set(observed) != set(expected):
        raise ValueError("holdout predictions are not aligned")
    return [observed[row["sample_id"]] for row in holdout]


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def submit_calibrator_training(
    coin: str,
    *,
    training_dir: Path = Path("data/training"),
    out_dir: Path = Path("out/modelhub-proposals"),
    req_no: str | None = None,
    dry_run: bool = False,
    client_factory: Callable[[], ModelHubClient] = ModelHubClient,
    execution_log: ExecutionLog | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """Load, gate, submit, independently score, and atomically propose one coin."""
    if not isinstance(coin, str) or not re.fullmatch(r"[A-Za-z0-9]{2,10}", coin):
        return _summary("error", "")
    normalized_coin = coin.upper()
    log = execution_log or ExecutionLog()
    try:
        rows = load_flat_training_rows(Path(training_dir) / f"{normalized_coin}.jsonl", coin=normalized_coin)
        package = build_flat_training_package(rows)
    except TrainingDataError:
        log.record("modelhub.training.error", {"coin": normalized_coin}, "training data rejected")
        return _summary("error", normalized_coin)
    if package["status"] == "blocked":
        log.record("modelhub.training.blocked", {"coin": normalized_coin}, package["blocked_reason"])
        gate = package["gate"]
        return _summary(
            "blocked", normalized_coin, reason=package["blocked_reason"],
            eligible_outcomes=gate["eligible_outcomes"], minimum=gate["minimum"],
            remaining=gate["remaining"],
        )

    train_rows = [row for row in package["rows"] if row["split"] == "train"]
    holdout_rows = [row for row in package["rows"] if row["split"] == "val"]
    safe_package = {
        "dataset_sha256": package["dataset"]["sha256"],
        "split": package["split"],
        "train_rows": train_rows,
        "holdout_features": [
            {key: row[key] for key in ("sample_id", "date", "coin", "direction", "confidence")}
            for row in holdout_rows
        ],
        "automatic_apply": False,
        "requires_human_approval": True,
    }
    if dry_run:
        log.record("modelhub.training.dry_run", {"coin": normalized_coin}, "gate and package ready")
        return _summary(
            "dry_run", normalized_coin, dataset_sha256=package["dataset"]["sha256"],
            row_count=package["dataset"]["row_count"], split=package["split"]
        )

    selected_req_no = req_no if req_no is not None else os.getenv("MODELHUB_REQ_NO")
    if not selected_req_no:
        log.record("modelhub.training.error", {"coin": normalized_coin}, "request number unavailable")
        return _summary("error", normalized_coin)
    try:
        client = client_factory()
        client.trigger_retrain(selected_req_no, safe_package)
        remaining = min(300.0, log.remaining())
        if remaining <= 0:
            log.record("modelhub.training.timeout", {"coin": normalized_coin}, "execution budget exhausted")
            return _summary("timeout", normalized_coin, req_no=selected_req_no)
        training_result = client.poll_training_result(selected_req_no, max_wait=remaining)
        if str(training_result.get("status", "")).lower() not in {"completed", "complete", "succeeded", "success"}:
            raise ValueError("training did not complete successfully")
        client.get_model_path("trustforge", f"{normalized_coin.lower()}-calibrator")
        artifact_sha = training_result.get("artifact_sha256")
        if not isinstance(artifact_sha, str) or not _SHA256.fullmatch(artifact_sha):
            raise ValueError("invalid artifact digest")
        candidate_confidences = _candidate_predictions(training_result, holdout_rows)
        hits = [row["hit"] for row in holdout_rows]
        baseline_ece = weighted_ece([row["confidence"] for row in holdout_rows], hits)
        candidate_ece = weighted_ece(candidate_confidences, hits)
        improvement = baseline_ece - candidate_ece
    except ModelHubPollTimeout:
        log.record("modelhub.training.timeout", {"coin": normalized_coin}, "poll deadline exhausted")
        return _summary("timeout", normalized_coin, req_no=selected_req_no)
    except ModelHubTransportError:
        log.record("modelhub.training.unavailable", {"coin": normalized_coin}, "ModelHub unavailable")
        return _summary("unavailable", normalized_coin)
    except (ModelHubError, ValueError, OSError):
        log.record("modelhub.training.error", {"coin": normalized_coin}, "ModelHub contract rejected")
        return _summary("error", normalized_coin)

    if improvement + 1e-12 < MIN_ECE_IMPROVEMENT:
        log.record("modelhub.training.no_improvement", {"coin": normalized_coin}, "holdout threshold not met")
        return _summary(
            "no_improvement", normalized_coin, baseline_ece=baseline_ece,
            candidate_ece=candidate_ece, improvement=improvement,
        )
    proposal = {
        "schema_version": 1, "coin": normalized_coin, "req_no": selected_req_no,
        "dataset_sha256": package["dataset"]["sha256"], "split": package["split"],
        "baseline_ece": baseline_ece, "candidate_ece": candidate_ece, "improvement": improvement,
        "artifact_sha256": artifact_sha.lower(), "timestamp": now().astimezone(timezone.utc).isoformat(),
        "automatic_apply": False, "requires_human_approval": True,
    }
    try:
        _atomic_json(Path(out_dir) / f"{normalized_coin}.json", proposal)
    except (OSError, TypeError, ValueError):
        return _summary("error", normalized_coin)
    log.record("modelhub.training.candidate", {"coin": normalized_coin}, "candidate proposal recorded")
    return _summary("candidate", normalized_coin, proposal=proposal)
