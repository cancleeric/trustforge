import json
import hashlib

import pytest

from trustforge import cli
from trustforge.execlog import ExecutionLog


def _write_reference(directory, *, run_id="run-a", coin="BTC", status="dry_run", filename=None):
    log = ExecutionLog(run_id=run_id)
    log.record(
        "modelhub.training.terminal",
        {"coin": coin, "stage": "terminal", "status": status},
        "terminal",
    )
    encoded = log.to_jsonl().encode()
    name = filename or f"execution-{run_id}.jsonl"
    (directory / name).write_bytes(encoded)
    return {
        "run_id": run_id, "coin": coin, "status": status,
        "execution_log_file": name, "execution_log_sha256": hashlib.sha256(encoded).hexdigest(),
    }


@pytest.mark.parametrize("mutation", ["run", "coin", "status", "missing_terminal", "invalid_json"])
def test_execution_reference_binds_jsonl_to_result(tmp_path, mutation):
    result = _write_reference(tmp_path)
    if mutation == "run":
        result["run_id"] = "run-b"
    elif mutation == "coin":
        result["coin"] = "ETH"
    elif mutation == "status":
        result["status"] = "candidate"
    elif mutation == "missing_terminal":
        path = tmp_path / result["execution_log_file"]
        encoded = path.read_bytes().splitlines()[0] + b"\n"
        path.write_bytes(encoded)
        result["execution_log_sha256"] = hashlib.sha256(encoded).hexdigest()
    else:
        path = tmp_path / result["execution_log_file"]
        path.write_bytes(b"not json\n")
        result["execution_log_sha256"] = hashlib.sha256(b"not json\n").hexdigest()
    assert cli._valid_modelhub_execution_reference(tmp_path, result) is False


def test_execution_reference_rejects_valid_log_renamed_across_run(tmp_path):
    result = _write_reference(tmp_path, run_id="run-a", filename="execution-run-b.jsonl")
    assert cli._valid_modelhub_execution_reference(tmp_path, result) is False


def test_execution_reference_rejects_duplicate_contradictory_terminal(tmp_path):
    result = _write_reference(tmp_path)
    path = tmp_path / result["execution_log_file"]
    events = [json.loads(line) for line in path.read_text().splitlines()]
    contradictory = json.loads(json.dumps(events[-1]))
    contradictory["params"]["status"] = "error"
    events.insert(-1, contradictory)
    encoded = "\n".join(json.dumps(event, ensure_ascii=False) for event in events).encode()
    path.write_bytes(encoded)
    result["execution_log_sha256"] = hashlib.sha256(encoded).hexdigest()
    assert cli._valid_modelhub_execution_reference(tmp_path, result) is False


def test_cli_requires_exactly_one_coin_target():
    with pytest.raises(SystemExit):
        cli.main(["modelhub-train", "--dry-run"])
    with pytest.raises(SystemExit):
        cli.main(["modelhub-train", "--coin", "BTC", "--all"])


@pytest.mark.parametrize("status,code", [
    ("candidate", 0), ("dry_run", 0), ("blocked", 2), ("no_improvement", 2),
    ("unavailable", 1), ("timeout", 1), ("error", 1),
])
def test_cli_status_exit_mapping(monkeypatch, capsys, tmp_path, status, code):
    monkeypatch.setattr(
        "trustforge.modelhub_submit.submit_calibrator_training",
        lambda coin, **kwargs: {"coin": coin, "status": status},
    )
    monkeypatch.setattr("trustforge.cli._valid_modelhub_execution_reference", lambda out_dir, result: True)
    assert cli.main(["modelhub-train", "--coin", "BTC", "--dry-run", "--out-dir", str(tmp_path)]) == code
    assert json.loads(capsys.readouterr().out)["status"] == status


def test_cli_all_isolates_coin_failures(monkeypatch, capsys, tmp_path):
    calls = []

    def submit(coin, **kwargs):
        calls.append(coin)
        if coin == "ETH":
            raise RuntimeError("isolated")
        return {"coin": coin, "status": "dry_run"}

    monkeypatch.setattr("trustforge.modelhub_submit.submit_calibrator_training", submit)
    assert cli.main(["modelhub-train", "--all", "--dry-run", "--out-dir", str(tmp_path)]) == 1
    output = json.loads(capsys.readouterr().out)
    assert len(calls) == len(cli.COIN_POOL)
    assert any(item["coin"] == "ETH" and item["status"] == "error" for item in output)


def test_live_all_mapping_validates_before_any_submit(monkeypatch, capsys, tmp_path):
    calls = []
    monkeypatch.setattr(
        "trustforge.modelhub_submit.submit_calibrator_training",
        lambda coin, **kwargs: calls.append((coin, kwargs["req_no"])),
    )
    assert cli.main([
        "modelhub-train", "--all", "--out-dir", str(tmp_path),
        "--req-no-map", "BTC=R", "--req-no-map", "ETH=R",
    ]) == 1
    assert calls == []


def test_single_mode_rejects_request_map_before_submit(monkeypatch, capsys, tmp_path):
    calls = []
    monkeypatch.setattr(
        "trustforge.modelhub_submit.submit_calibrator_training",
        lambda coin, **kwargs: calls.append(coin),
    )
    assert cli.main([
        "modelhub-train", "--coin", "BTC", "--req-no-map", "BTC=REQ",
        "--out-dir", str(tmp_path),
    ]) == 1
    assert calls == []


def test_live_all_passes_distinct_request_numbers(monkeypatch, capsys, tmp_path):
    calls = []

    def submit(coin, **kwargs):
        calls.append((coin, kwargs["req_no"]))
        return {"coin": coin, "status": "candidate"}

    monkeypatch.setattr("trustforge.modelhub_submit.submit_calibrator_training", submit)
    monkeypatch.setattr("trustforge.cli._valid_modelhub_execution_reference", lambda out_dir, result: True)
    arguments = ["modelhub-train", "--all", "--out-dir", str(tmp_path)]
    for index, coin in enumerate(cli.COIN_POOL):
        arguments += ["--req-no-map", f"{coin}=REQ-{index}"]
    assert cli.main(arguments) == 0
    assert len({request for _, request in calls}) == len(cli.COIN_POOL)
    output = json.loads(capsys.readouterr().out)
    assert all("run_id" in result for result in output)
    assert not list(tmp_path.glob("execution-*.jsonl"))  # CLI never duplicates submit-owned logs.


def test_malformed_submit_result_becomes_logged_error(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        "trustforge.modelhub_submit.submit_calibrator_training", lambda coin, **kwargs: {"wrong": True}
    )
    assert cli.main([
        "modelhub-train", "--coin", "BTC", "--dry-run", "--out-dir", str(tmp_path)
    ]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error" and result["run_id"]
    content = next(tmp_path.glob("execution-*.jsonl")).read_text()
    assert "InvalidResult" in content


def test_log_persistence_failure_is_coin_error_and_all_continues(monkeypatch, capsys, tmp_path):
    calls = []

    def submit(coin, **kwargs):
        calls.append(coin)
        return {"coin": coin, "status": "dry_run"}

    monkeypatch.setattr("trustforge.modelhub_submit.submit_calibrator_training", submit)
    monkeypatch.setattr(
        "trustforge.modelhub_submit.persist_execution_log",
        lambda out_dir, log: (_ for _ in ()).throw(OSError("log failed")),
    )
    assert cli.main([
        "modelhub-train", "--all", "--dry-run", "--out-dir", str(tmp_path)
    ]) == 1
    output = json.loads(capsys.readouterr().out)
    assert calls == list(cli.COIN_POOL)
    assert all(result["status"] == "error" for result in output)


def test_dry_run_log_failure_never_changes_existing_live_current(monkeypatch, capsys, tmp_path):
    current = tmp_path / "BTC.json"
    current.write_bytes(b'{"status":"candidate","protected":true}')
    before = current.read_bytes()
    monkeypatch.setattr(
        "trustforge.modelhub_submit.submit_calibrator_training",
        lambda coin, **kwargs: {"coin": coin, "status": "dry_run"},
    )
    monkeypatch.setattr(
        "trustforge.modelhub_submit.persist_execution_log",
        lambda out_dir, log: (_ for _ in ()).throw(OSError("log failed")),
    )
    assert cli.main([
        "modelhub-train", "--coin", "BTC", "--dry-run", "--out-dir", str(tmp_path)
    ]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["manifest_updated"] is False
    assert current.read_bytes() == before


@pytest.mark.parametrize("mode", ["unexpected", "malformed"])
def test_live_unexpected_or_malformed_result_replaces_stale_current(monkeypatch, capsys, tmp_path, mode):
    current = tmp_path / "BTC.json"
    current.write_text('{"status":"candidate","proposal_file":"stale.json"}')

    def submit(coin, **kwargs):
        if mode == "unexpected":
            raise RuntimeError("must not leak")
        return {"wrong": True}

    monkeypatch.setattr("trustforge.modelhub_submit.submit_calibrator_training", submit)
    assert cli.main([
        "modelhub-train", "--coin", "BTC", "--req-no", "REQ", "--out-dir", str(tmp_path)
    ]) == 1
    result = json.loads(capsys.readouterr().out)
    manifest = json.loads(current.read_text())
    assert result["manifest_updated"] is True
    assert manifest["status"] == "error"
    assert manifest["reason"] == "unexpected_or_invalid_result"
    assert "proposal_file" not in manifest
    assert manifest["automatic_apply"] is False
