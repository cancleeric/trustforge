import json
from datetime import date, datetime, timedelta, timezone

import pytest

from trustforge.calibration_metrics import judge_direction_hit, weighted_ece
from trustforge.modelhub_client import ModelHubHTTPError, ModelHubPollTimeout, ModelHubTransportError
from trustforge.modelhub_submit import submit_calibrator_training


def write_rows(directory, coin="BTC", count=100):
    directory.mkdir(parents=True, exist_ok=True)
    start = date(2020, 1, 1)
    rows = [{
        "date": (start + timedelta(days=index)).isoformat(), "coin": coin, "direction": "不明",
        "confidence": 0.5, "outcome_pct": 0.0, "ground_truth_direction": "neutral",
        "split": "train" if index < int(count * 0.8) else "val",
    } for index in range(count)]
    (directory / f"{coin}.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows), encoding="utf-8"
    )
    return rows


class FakeClient:
    def __init__(self, rows, confidence=0.9, failure=None, aligned=True):
        self.rows, self.confidence, self.failure, self.aligned = rows, confidence, failure, aligned
        self.calls = []

    def trigger_retrain(self, req_no, payload):
        self.calls.append(("trigger", req_no, payload))
        self.payload = payload
        if self.failure and self.failure[0] == "trigger":
            raise self.failure[1]
        return {"status": "accepted"}

    def poll_training_result(self, req_no, *, max_wait):
        self.calls.append(("poll", req_no, max_wait))
        if self.failure and self.failure[0] == "poll":
            raise self.failure[1]
        holdout = self.payload["holdout_features"]
        predictions = [{"sample_id": row["sample_id"], "confidence": self.confidence} for row in holdout]
        if not self.aligned:
            predictions[0]["sample_id"] = "wrong-id"
        return {"status": "completed", "artifact_sha256": "a" * 64, "holdout_predictions": predictions}

    def get_model_path(self, product, name):
        self.calls.append(("path", product, name))
        return "/untrusted/absolute/model.pkl"


class BudgetLog:
    def __init__(self, remaining_values):
        self.values = iter(remaining_values)
        self.run_id = "budget-run"
        self.events = []

    def remaining(self):
        return next(self.values)

    def record(self, tool, params=None, summary=""):
        self.events.append({"tool": tool, "params": params or {}, "summary": summary})


def test_dry_run_never_builds_client_or_reads_request_number(tmp_path, monkeypatch):
    write_rows(tmp_path)
    monkeypatch.delenv("MODELHUB_REQ_NO", raising=False)
    built = []
    result = submit_calibrator_training(
        "BTC", training_dir=tmp_path, dry_run=True, client_factory=lambda: built.append(1)
    )
    assert result["status"] == "dry_run"
    assert result["row_count"] == 100
    assert built == []


def test_gate_99_blocks_and_100_proceeds(tmp_path):
    write_rows(tmp_path, count=99)
    blocked = submit_calibrator_training("BTC", training_dir=tmp_path, dry_run=True)
    assert blocked["status"] == "blocked"
    assert (blocked["eligible_outcomes"], blocked["minimum"], blocked["remaining"]) == (99, 100, 1)
    rows = write_rows(tmp_path, count=100)
    client = FakeClient(rows)
    assert submit_calibrator_training(
        "BTC", training_dir=tmp_path, req_no="REQ", client_factory=lambda: client,
        out_dir=tmp_path / "out",
    )["status"] == "candidate"


def test_payload_never_leaks_holdout_answers_and_sample_ids_are_disjoint(tmp_path):
    rows = write_rows(tmp_path)
    client = FakeClient(rows)
    result = submit_calibrator_training(
        "BTC", training_dir=tmp_path, req_no="REQ", client_factory=lambda: client,
        out_dir=tmp_path / "out",
    )
    assert result["status"] == "candidate"
    forbidden = {"hit", "outcome_pct", "ground_truth_direction", "split", "date", "coin"}
    assert all(forbidden.isdisjoint(row) for row in client.payload["holdout_features"])
    train_ids = {row["sample_id"] for row in client.payload["train_rows"]}
    holdout_ids = {row["sample_id"] for row in client.payload["holdout_features"]}
    assert train_ids.isdisjoint(holdout_ids)


@pytest.mark.parametrize(
    "improvement,expected",
    [(0.0199, "no_improvement"), (0.0200, "candidate"), (0.0201, "candidate")],
)
def test_ece_improvement_threshold_includes_boundary(tmp_path, improvement, expected):
    rows = write_rows(tmp_path)
    client = FakeClient(rows, confidence=0.5 + improvement)
    result = submit_calibrator_training(
        "BTC", training_dir=tmp_path, out_dir=tmp_path / "out", req_no="REQ", client_factory=lambda: client
    )
    assert result["status"] == expected


def test_prediction_alignment_fails_closed(tmp_path):
    rows = write_rows(tmp_path)
    client = FakeClient(rows, aligned=False)
    assert submit_calibrator_training(
        "BTC", training_dir=tmp_path, req_no="REQ", client_factory=lambda: client,
        out_dir=tmp_path / "out",
    )["status"] == "error"


def test_failed_terminal_result_does_not_request_artifact_path(tmp_path):
    rows = write_rows(tmp_path)
    client = FakeClient(rows)

    def failed_poll(req_no, *, max_wait):
        client.calls.append(("poll", req_no, max_wait))
        return {"status": "failed"}

    client.poll_training_result = failed_poll
    result = submit_calibrator_training(
        "BTC", training_dir=tmp_path, req_no="REQ", client_factory=lambda: client,
        out_dir=tmp_path / "out",
    )
    assert result["status"] == "error"
    assert [call[0] for call in client.calls] == ["trigger", "poll"]


@pytest.mark.parametrize("failure,status", [
    (("trigger", ModelHubTransportError("secret")), "unavailable"),
    (("poll", ModelHubPollTimeout("secret")), "timeout"),
    (("trigger", ModelHubHTTPError(400)), "error"),
])
def test_structured_failure_statuses_do_not_leak(tmp_path, failure, status):
    rows = write_rows(tmp_path)
    client = FakeClient(rows, failure=failure)
    result = submit_calibrator_training(
        "BTC", training_dir=tmp_path, req_no="REQ", client_factory=lambda: client,
        out_dir=tmp_path / "out",
    )
    assert result["status"] == status
    assert "secret" not in json.dumps(result)


def test_candidate_proposal_is_atomic_safe_and_has_no_path_or_key(tmp_path, monkeypatch):
    rows = write_rows(tmp_path)
    monkeypatch.setenv("MODELHUB_API_KEY", "never-write-this-key")
    out = tmp_path / "proposals"
    result = submit_calibrator_training(
        "BTC", training_dir=tmp_path, out_dir=out, req_no="REQ", client_factory=lambda: FakeClient(rows),
        now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    proposal_path = next(out.glob("BTC-*.json"))
    proposal = json.loads(proposal_path.read_text())
    assert result["status"] == "candidate"
    assert proposal["automatic_apply"] is False and proposal["requires_human_approval"] is True
    serialized = json.dumps(proposal)
    assert "never-write-this-key" not in serialized and "/untrusted/absolute" not in serialized
    assert not list(out.glob("*.tmp"))


def test_rejected_or_stale_trigger_response_stops_before_poll(tmp_path):
    rows = write_rows(tmp_path)
    client = FakeClient(rows)
    client.trigger_retrain = lambda req_no, payload: {"status": "completed"}
    result = submit_calibrator_training(
        "BTC", training_dir=tmp_path, req_no="REQ", client_factory=lambda: client,
        out_dir=tmp_path / "out",
    )
    assert result["status"] == "error"
    assert not any(call[0] in {"poll", "path"} for call in client.calls)


def test_outbound_payload_cap_fails_before_client(tmp_path, monkeypatch):
    write_rows(tmp_path)
    built = []
    monkeypatch.setattr("trustforge.modelhub_submit.MAX_OUTBOUND_PAYLOAD_BYTES", 1)
    result = submit_calibrator_training(
        "BTC", training_dir=tmp_path, req_no="REQ", client_factory=lambda: built.append(1),
        out_dir=tmp_path / "out",
    )
    assert result["status"] == "error" and built == []


def test_zero_initial_budget_never_builds_client_or_triggers(tmp_path):
    write_rows(tmp_path)
    built = []
    log = BudgetLog([0])
    result = submit_calibrator_training(
        "BTC", training_dir=tmp_path, req_no="REQ", client_factory=lambda: built.append(1),
        execution_log=log, out_dir=tmp_path / "out",
    )
    assert result["status"] == "timeout" and built == []
    assert log.events[-1]["params"]["status"] == "timeout"


def test_invalid_coin_records_error_without_creating_manifest(tmp_path):
    log = BudgetLog([])
    result = submit_calibrator_training("../bad", out_dir=tmp_path, execution_log=log)
    assert result["status"] == "error" and result["coin"] == ""
    assert log.events[-1]["params"]["status"] == "error"
    assert not (tmp_path / ".json").exists()
    assert list(tmp_path.iterdir()) == []


def test_budget_expiring_after_artifact_lookup_writes_no_candidate(tmp_path):
    rows = write_rows(tmp_path)
    client = FakeClient(rows)
    log = BudgetLog([100, 100, 100, 100, 100, 0])
    out = tmp_path / "out"
    result = submit_calibrator_training(
        "BTC", training_dir=tmp_path, req_no="REQ", client_factory=lambda: client,
        execution_log=log, out_dir=out,
    )
    assert result["status"] == "timeout"
    assert [call[0] for call in client.calls][-1] == "path"
    assert not list(out.glob("BTC-*.json"))


def test_current_manifest_does_not_reference_stale_candidate(tmp_path):
    rows = write_rows(tmp_path)
    out = tmp_path / "out"
    first = submit_calibrator_training(
        "BTC", training_dir=tmp_path, req_no="REQ-1", client_factory=lambda: FakeClient(rows, 0.9),
        out_dir=out,
    )
    assert first["status"] == "candidate"
    history = out / first["proposal_file"]
    assert history.exists()
    second = submit_calibrator_training(
        "BTC", training_dir=tmp_path, req_no="REQ-2", client_factory=lambda: FakeClient(rows, 0.5),
        out_dir=out,
    )
    assert second["status"] == "no_improvement" and history.exists()
    current = json.loads((out / "BTC.json").read_text())
    assert current["status"] == "no_improvement" and "proposal_file" not in current
    failing = FakeClient(rows, failure=("trigger", ModelHubHTTPError(400)))
    third = submit_calibrator_training(
        "BTC", training_dir=tmp_path, req_no="REQ-3", client_factory=lambda: failing,
        out_dir=out,
    )
    assert third["status"] == "error" and history.exists()
    current = json.loads((out / "BTC.json").read_text())
    assert current["status"] == "error" and "proposal_file" not in current


def test_metrics_and_shared_direction_semantics():
    assert weighted_ece([0.5, 1.0], [True, True]) == pytest.approx(0.25)
    assert judge_direction_hit("unknown", 0.0199) is True
    assert judge_direction_hit("unknown", 0.02) is False


@pytest.mark.parametrize(
    "confidences,hits,bins",
    [([], [], 10), ([1.1], [True], 10), ([0.5], ["yes"], 10), ([0.5], [True], 0)],
)
def test_weighted_ece_rejects_invalid_inputs(confidences, hits, bins):
    with pytest.raises(ValueError):
        weighted_ece(confidences, hits, bins=bins)
