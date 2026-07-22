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
def test_cli_status_exit_mapping(monkeypatch, capsys, status, code):
    monkeypatch.setattr(
        "trustforge.modelhub_submit.submit_calibrator_training",
        lambda coin, **kwargs: {"coin": coin, "status": status},
    )
    assert cli.main(["modelhub-train", "--coin", "BTC", "--dry-run"]) == code
    assert json.loads(capsys.readouterr().out)["status"] == status


def test_cli_all_isolates_coin_failures(monkeypatch, capsys):
    calls = []

    def submit(coin, **kwargs):
        calls.append(coin)
        if coin == "ETH":
            raise RuntimeError("isolated")
        return {"coin": coin, "status": "dry_run"}

    monkeypatch.setattr("trustforge.modelhub_submit.submit_calibrator_training", submit)
    assert cli.main(["modelhub-train", "--all", "--dry-run"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert len(calls) == len(cli.COIN_POOL)
    assert any(item["coin"] == "ETH" and item["status"] == "error" for item in output)
