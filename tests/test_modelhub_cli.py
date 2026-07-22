import json

import pytest

from trustforge import cli


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
