"""Orchestrate a fail-closed calibrator retraining proposal."""
from __future__ import annotations

import json
import hashlib
import os
import re
import secrets
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
from .safe_fs import SafePathError, pinned_directory, write_atomic

MIN_ECE_IMPROVEMENT = 0.02
MAX_OUTBOUND_PAYLOAD_BYTES = 8 * 1024 * 1024
MAX_OPAQUE_ID_ATTEMPTS = 5
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")


def _summary(status: str, coin: str, **values: Any) -> dict[str, Any]:
    return {"status": status, "coin": coin, **values}


def ensure_safe_directory(path: Path) -> Path:
    """Compatibility validator; security-sensitive operations use the returned dirfd."""
    absolute = Path(os.path.abspath(path))
    with pinned_directory(absolute, create=True):
        pass
    return absolute


def _safe_run_id(run_id: str) -> str:
    return run_id if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id) else hashlib.sha256(run_id.encode()).hexdigest()


def _fsync_directory(path: Path) -> None:
    with pinned_directory(path) as descriptor:
        os.fsync(descriptor)


def _candidate_predictions(result: dict[str, Any], holdout: list[dict[str, Any]]) -> list[float]:
    predictions = result.get("holdout_predictions")
    if not isinstance(predictions, list) or len(predictions) != len(holdout):
        raise ValueError("holdout predictions are missing or incomplete")
    expected = {row["opaque_id"] for row in holdout}
    observed: dict[str, float] = {}
    for prediction in predictions:
        if not isinstance(prediction, dict):
            raise ValueError("invalid holdout prediction")
        sample_id = prediction.get("opaque_id")
        value = prediction.get("confidence", prediction.get("calibrated_confidence"))
        if (
            sample_id not in expected or sample_id in observed
        ):
            raise ValueError("holdout predictions are not aligned")
        observed[sample_id] = value
    if set(observed) != set(expected):
        raise ValueError("holdout predictions are not aligned")
    return [observed[row["opaque_id"]] for row in holdout]


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    write_atomic(path, encoded, immutable=False)


def write_current_manifest(out_dir: Path, coin: str, value: dict[str, Any]) -> None:
    """Public safe writer for the per-coin current manifest."""
    _atomic_json(Path(out_dir) / f"{coin}.json", value)


def _immutable_json(path: Path, value: dict[str, Any]) -> None:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    _immutable_bytes(path, encoded)


def _immutable_bytes(path: Path, encoded: bytes) -> None:
    write_atomic(path, encoded, immutable=True)


def persist_execution_log(out_dir: Path, log: ExecutionLog) -> tuple[str, str]:
    """Persist one immutable terminal log and return its relative name and digest."""
    encoded = log.to_jsonl().encode("utf-8")
    filename = f"execution-{_safe_run_id(log.run_id)}.jsonl"
    _immutable_bytes(Path(out_dir) / filename, encoded)
    return filename, hashlib.sha256(encoded).hexdigest()


def _persist_failure_execution_log(out_dir: Path, log: ExecutionLog) -> tuple[str, str]:
    encoded = log.to_jsonl().encode("utf-8")
    filename = f"execution-{_safe_run_id(log.run_id)}-error.jsonl"
    _immutable_bytes(Path(out_dir) / filename, encoded)
    return filename, hashlib.sha256(encoded).hexdigest()


def _replace_terminal(log: ExecutionLog, status: str, record: Callable[..., None]) -> None:
    """Replace the uncommitted in-memory terminal after publication fails."""
    if log.events:
        params = log.events[-1].get("params", {})
        if log.events[-1].get("tool") == "modelhub.training.terminal" and params.get("stage") == "terminal":
            log.events.pop()
    record("manifest_update_failed", status="error")
    record("terminal", status=status)


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
    opaque_id_factory: Callable[[], str] = secrets.token_urlsafe,
    execution_log_writer: Callable[[Path, ExecutionLog], tuple[str, str]] = persist_execution_log,
) -> dict[str, Any]:
    """Load, gate, submit, independently score, and atomically propose one coin."""
    log = execution_log or ExecutionLog()
    normalized_coin = coin.upper() if isinstance(coin, str) else ""
    selected_req_no: str | None = None
    dataset_sha256: str | None = None

    def record(stage: str, status: str | None = None, **values: Any) -> None:
        params: dict[str, Any] = {"coin": normalized_coin, "stage": stage, **values}
        if status is not None:
            params["status"] = status
        if selected_req_no:
            params["req_no"] = selected_req_no
        if dataset_sha256:
            params["dataset_sha256"] = dataset_sha256
        log.record(f"modelhub.training.{stage}", params, f"ModelHub stage: {stage}")

    def finish(status: str, *, persist_current: bool = True, **values: Any) -> dict[str, Any]:
        record("terminal", status=status)
        try:
            log_file, log_sha = execution_log_writer(Path(out_dir), log)
        except (OSError, TypeError, ValueError):
            return _summary(
                "error", normalized_coin, run_id=log.run_id, manifest_updated=False,
                reason="log_persistence_failed",
            )
        result = _summary(
            status, normalized_coin, run_id=log.run_id, execution_log_file=log_file,
            execution_log_sha256=log_sha, **values,
        )
        if persist_current and not dry_run and status in {
            "blocked", "no_improvement", "unavailable", "timeout", "error",
        }:
            current = {
                "schema_version": 1, "coin": normalized_coin, "status": status, "run_id": log.run_id,
                "automatic_apply": False, "requires_human_approval": True,
                "execution_log_file": log_file, "execution_log_sha256": log_sha,
            }
            if selected_req_no:
                current["req_no"] = selected_req_no
            if dataset_sha256:
                current["dataset_sha256"] = dataset_sha256
            for key in ("dataset_sha256", "eligible_outcomes", "minimum", "remaining"):
                if key in result:
                    current[key] = result[key]
            try:
                write_current_manifest(Path(out_dir), normalized_coin, current)
                result["manifest_updated"] = True
            except (OSError, TypeError, ValueError):
                _replace_terminal(log, "error", record)
                failure = _summary(
                    "error", normalized_coin, run_id=log.run_id, manifest_updated=False,
                    reason="manifest_update_failed",
                )
                try:
                    failure_file, failure_sha = _persist_failure_execution_log(Path(out_dir), log)
                    failure.update(execution_log_file=failure_file, execution_log_sha256=failure_sha)
                except (OSError, TypeError, ValueError):
                    failure["reason"] = "log_persistence_failed"
                return failure
        return result

    if not re.fullmatch(r"[A-Z0-9]{2,10}", normalized_coin):
        normalized_coin = ""
        return finish("error", persist_current=False)

    log_file: str | None = None
    log_sha: str | None = None
    try:
        rows = load_flat_training_rows(Path(training_dir) / f"{normalized_coin}.jsonl", coin=normalized_coin)
        package = build_flat_training_package(rows)
        dataset_sha256 = package["dataset"]["sha256"]
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
    reserved_ids = {row["sample_id"] for row in train_rows}
    opaque_ids: set[str] = set()
    evaluation_holdout: list[dict[str, Any]] = []
    for row in holdout_rows:
        token: str | None = None
        for _ in range(MAX_OPAQUE_ID_ATTEMPTS):
            candidate = opaque_id_factory()
            prefixed_candidate = f"holdout_{candidate}" if isinstance(candidate, str) else ""
            if (
                isinstance(candidate, str)
                and candidate
                and len(prefixed_candidate) <= 256
                and not any(ord(character) < 32 or ord(character) == 127 for character in prefixed_candidate)
                and candidate not in reserved_ids
                and prefixed_candidate not in reserved_ids
                and prefixed_candidate not in opaque_ids
            ):
                token = prefixed_candidate
                break
        if token is None:
            return finish("error")
        opaque_ids.add(token)
        evaluation_holdout.append({**row, "opaque_id": token})
    safe_package = {
        "dataset_sha256": package["dataset"]["sha256"],
        "split": package["split"],
        "train_rows": train_rows,
        "holdout_features": [
            {key: row[key] for key in ("opaque_id", "direction", "confidence")}
            for row in evaluation_holdout
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
        if (
            not isinstance(trigger_result, dict)
            or set(("status", "req_no", "dataset_sha256")) - set(trigger_result)
            or not all(isinstance(trigger_result[key], str) for key in ("status", "req_no", "dataset_sha256"))
            or trigger_result["status"].lower() not in {
            "accepted", "queued", "running",
            }
        ):
            raise ValueError("retrain request was not accepted")
        if trigger_result["req_no"] != selected_req_no:
            raise ValueError("retrain request identity mismatch")
        if trigger_result["dataset_sha256"] != package["dataset"]["sha256"]:
            raise ValueError("retrain dataset identity mismatch")
        record("trigger_accepted", status=trigger_result["status"].lower())
        remaining = min(300.0, log.remaining())
        if remaining <= 0:
            return finish("timeout", req_no=selected_req_no)
        training_result = client.poll_training_result(selected_req_no, max_wait=remaining)
        if log.remaining() <= 0:
            return finish("timeout", req_no=selected_req_no)
        if not isinstance(training_result, dict):
            raise ValueError("invalid training result")
        if str(training_result.get("status", "")).lower() not in {"completed", "complete", "succeeded", "success"}:
            raise ValueError("training did not complete successfully")
        record("poll_terminal", status=str(training_result["status"]).lower())
        if log.remaining() <= 0:
            return finish("timeout", req_no=selected_req_no)
        client.get_model_path("trustforge", f"{normalized_coin.lower()}-calibrator")
        if log.remaining() <= 0:
            return finish("timeout", req_no=selected_req_no)
        artifact_sha = training_result.get("artifact_sha256")
        if not isinstance(artifact_sha, str) or not _SHA256.fullmatch(artifact_sha):
            raise ValueError("invalid artifact digest")
        record("artifact_verified", status="verified")
        candidate_confidences = _candidate_predictions(training_result, evaluation_holdout)
        hits = [row["hit"] for row in holdout_rows]
        if log.remaining() <= 0:
            return finish("timeout", req_no=selected_req_no)
        baseline_ece = weighted_ece([row["confidence"] for row in holdout_rows], hits)
        candidate_ece = weighted_ece(candidate_confidences, hits)
        improvement = baseline_ece - candidate_ece
        record("metric_compared", status="compared")
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
    safe_run_id = (
        log.run_id
        if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", log.run_id)
        else hashlib.sha256(log.run_id.encode()).hexdigest()
    )
    proposal_name = f"{normalized_coin}-{package['dataset']['sha256']}-{safe_run_id}.json"
    if log.remaining() <= 0:
        return finish("timeout", req_no=selected_req_no)
    try:
        _immutable_json(Path(out_dir) / proposal_name, proposal)
        record("terminal", status="candidate")
        log_file, log_sha = execution_log_writer(Path(out_dir), log)
        current = {
            "schema_version": 1, "coin": normalized_coin, "status": "candidate", "run_id": log.run_id,
            "req_no": selected_req_no,
            "proposal_file": proposal_name, "dataset_sha256": package["dataset"]["sha256"],
            "baseline_ece": baseline_ece, "candidate_ece": candidate_ece, "improvement": improvement,
            "artifact_sha256": artifact_sha.lower(),
            "automatic_apply": False, "requires_human_approval": True,
            "execution_log_file": log_file, "execution_log_sha256": log_sha,
        }
        write_current_manifest(Path(out_dir), normalized_coin, current)
    except (OSError, TypeError, ValueError):
        if not (log_file and log_sha):
            return finish("error", persist_current=False, reason="candidate_publication_failed", manifest_updated=False)
        if log_file and log_sha:
            _replace_terminal(log, "error", record)
        failure = _summary(
            "error", normalized_coin, run_id=log.run_id, manifest_updated=False,
            reason="candidate_publication_failed",
        )
        if log_file and log_sha:
            try:
                failure_file, failure_sha = _persist_failure_execution_log(Path(out_dir), log)
                failure.update(execution_log_file=failure_file, execution_log_sha256=failure_sha)
            except (OSError, TypeError, ValueError):
                failure["reason"] = "log_persistence_failed"
        return failure
    return _summary(
        "candidate", normalized_coin, run_id=log.run_id, proposal=proposal,
        proposal_file=proposal_name, manifest_updated=True, execution_log_file=log_file,
        execution_log_sha256=log_sha,
    )
