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
MAX_OUTBOUND_PAYLOAD_BYTES = 8 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")


def _summary(status: str, coin: str, **values: Any) -> dict[str, Any]:
    return {"status": status, "coin": coin, **values}


def _candidate_predictions(result: dict[str, Any], holdout: list[dict[str, Any]]) -> list[float]:
    predictions = result.get("holdout_predictions")
    if not isinstance(predictions, list) or len(predictions) != len(holdout):
        raise ValueError("holdout predictions are missing or incomplete")
    expected = {row["sample_id"] for row in holdout}
    observed: dict[str, float] = {}
    for prediction in predictions:
        if not isinstance(prediction, dict):
            raise ValueError("invalid holdout prediction")
        sample_id = prediction.get("sample_id")
        value = prediction.get("confidence", prediction.get("calibrated_confidence"))
        if (
            sample_id not in expected or sample_id in observed
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
    log = execution_log or ExecutionLog()
    normalized_coin = coin.upper() if isinstance(coin, str) else ""

    def finish(status: str, **values: Any) -> dict[str, Any]:
        log.record(
            f"modelhub.training.{status}", {"coin": normalized_coin, "status": status},
            f"ModelHub training terminal status: {status}",
        )
        result = _summary(status, normalized_coin, run_id=log.run_id, **values)
        if not dry_run and status in {"blocked", "no_improvement", "unavailable", "timeout", "error"}:
            current = {"schema_version": 1, "coin": normalized_coin, "status": status, "run_id": log.run_id}
            for key in ("dataset_sha256", "eligible_outcomes", "minimum", "remaining"):
                if key in result:
                    current[key] = result[key]
            try:
                _atomic_json(Path(out_dir) / f"{normalized_coin}.json", current)
            except OSError:
                pass
        return result

    if not re.fullmatch(r"[A-Z0-9]{2,10}", normalized_coin):
        log.record(
            "modelhub.training.error", {"coin": "", "status": "error"},
            "ModelHub training terminal status: error",
        )
        return _summary("error", "", run_id=log.run_id)

    try:
        rows = load_flat_training_rows(Path(training_dir) / f"{normalized_coin}.jsonl", coin=normalized_coin)
        package = build_flat_training_package(rows)
    except TrainingDataError:
        return finish("error")
    if package["status"] == "blocked":
        gate = package["gate"]
        return finish(
            "blocked", reason=package["blocked_reason"],
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
            {key: row[key] for key in ("sample_id", "direction", "confidence")}
            for row in holdout_rows
        ],
        "automatic_apply": False,
        "requires_human_approval": True,
    }
    try:
        serialized_payload = json.dumps(
            safe_package, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        return finish("error")
    if len(serialized_payload) > MAX_OUTBOUND_PAYLOAD_BYTES:
        return finish("error")
    if dry_run:
        return finish(
            "dry_run", dataset_sha256=package["dataset"]["sha256"],
            row_count=package["dataset"]["row_count"], split=package["split"]
        )

    selected_req_no = req_no if req_no is not None else os.getenv("MODELHUB_REQ_NO")
    if not selected_req_no:
        return finish("error")
    if log.remaining() <= 30.0:
        return finish("timeout")
    try:
        client = client_factory()
        if log.remaining() <= float(getattr(client, "timeout", 30.0)):
            return finish("timeout")
        trigger_result = client.trigger_retrain(selected_req_no, safe_package)
        if not isinstance(trigger_result, dict) or str(trigger_result.get("status", "")).lower() not in {
            "accepted", "queued", "running",
        }:
            raise ValueError("retrain request was not accepted")
        if trigger_result.get("req_no", selected_req_no) != selected_req_no:
            raise ValueError("retrain request identity mismatch")
        if trigger_result.get("dataset_sha256", package["dataset"]["sha256"]) != package["dataset"]["sha256"]:
            raise ValueError("retrain dataset identity mismatch")
        remaining = min(300.0, log.remaining())
        if remaining <= 0:
            return finish("timeout", req_no=selected_req_no)
        training_result = client.poll_training_result(selected_req_no, max_wait=remaining)
        if log.remaining() <= 0:
            return finish("timeout", req_no=selected_req_no)
        if str(training_result.get("status", "")).lower() not in {"completed", "complete", "succeeded", "success"}:
            raise ValueError("training did not complete successfully")
        if log.remaining() <= 0:
            return finish("timeout", req_no=selected_req_no)
        client.get_model_path("trustforge", f"{normalized_coin.lower()}-calibrator")
        if log.remaining() <= 0:
            return finish("timeout", req_no=selected_req_no)
        artifact_sha = training_result.get("artifact_sha256")
        if not isinstance(artifact_sha, str) or not _SHA256.fullmatch(artifact_sha):
            raise ValueError("invalid artifact digest")
        candidate_confidences = _candidate_predictions(training_result, holdout_rows)
        hits = [row["hit"] for row in holdout_rows]
        if log.remaining() <= 0:
            return finish("timeout", req_no=selected_req_no)
        baseline_ece = weighted_ece([row["confidence"] for row in holdout_rows], hits)
        candidate_ece = weighted_ece(candidate_confidences, hits)
        improvement = baseline_ece - candidate_ece
        if log.remaining() <= 0:
            return finish("timeout", req_no=selected_req_no)
    except ModelHubPollTimeout:
        return finish("timeout", req_no=selected_req_no)
    except ModelHubTransportError:
        return finish("unavailable")
    except (ModelHubError, ValueError, OSError):
        return finish("error")

    if improvement + 1e-12 < MIN_ECE_IMPROVEMENT:
        return finish(
            "no_improvement", baseline_ece=baseline_ece,
            candidate_ece=candidate_ece, improvement=improvement,
            dataset_sha256=package["dataset"]["sha256"],
        )
    proposal = {
        "schema_version": 1, "coin": normalized_coin, "req_no": selected_req_no,
        "dataset_sha256": package["dataset"]["sha256"], "split": package["split"],
        "baseline_ece": baseline_ece, "candidate_ece": candidate_ece, "improvement": improvement,
        "artifact_sha256": artifact_sha.lower(), "timestamp": now().astimezone(timezone.utc).isoformat(),
        "automatic_apply": False, "requires_human_approval": True,
    }
    proposal_name = f"{normalized_coin}-{package['dataset']['sha256'][:12]}.json"
    if log.remaining() <= 0:
        return finish("timeout", req_no=selected_req_no)
    try:
        _atomic_json(Path(out_dir) / proposal_name, proposal)
        current = {
            "schema_version": 1, "coin": normalized_coin, "status": "candidate", "run_id": log.run_id,
            "proposal_file": proposal_name, "dataset_sha256": package["dataset"]["sha256"],
            "baseline_ece": baseline_ece, "candidate_ece": candidate_ece, "improvement": improvement,
        }
        _atomic_json(Path(out_dir) / f"{normalized_coin}.json", current)
    except (OSError, TypeError, ValueError):
        return finish("error")
    return finish("candidate", proposal=proposal, proposal_file=proposal_name)
