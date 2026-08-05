from __future__ import annotations

import pytest

from trustforge.training_trigger import (
    exit_code,
    parse_req_no_map,
    run_training_trigger,
)


def test_live_trigger_is_fail_closed_without_double_enable(monkeypatch, tmp_path):
    calls = []

    def submitter(*args, **kwargs):
        calls.append((args, kwargs))
        return {"coin": args[0], "status": "candidate"}

    monkeypatch.delenv("TRUSTFORGE_TRAINING_TRIGGER_ENABLED", raising=False)

    report = run_training_trigger(
        provider="sagemaker",
        coins=("BTC", "ETH"),
        training_dir=tmp_path,
        out_dir=tmp_path / "out",
        dry_run=False,
        enable_live=True,
        sagemaker_submitter=submitter,
    )

    assert calls == []
    assert report["status"] == "blocked"
    assert report["automatic_apply"] is False
    assert report["requires_human_approval"] is True
    assert report["summary"] == {"blocked": 2}


def test_sagemaker_dry_run_batches_all_requested_coins(tmp_path):
    calls = []

    def submitter(coin, **kwargs):
        calls.append((coin, kwargs))
        return {
            "coin": coin,
            "status": "dry_run",
            "automatic_apply": False,
            "requires_human_approval": True,
        }

    report = run_training_trigger(
        provider="sagemaker",
        coins=("BTC", "ETH"),
        training_dir=tmp_path / "training",
        out_dir=tmp_path / "out",
        dry_run=True,
        sagemaker_submitter=submitter,
    )

    assert [coin for coin, _ in calls] == ["BTC", "ETH"]
    assert all(call[1]["dry_run"] is True for call in calls)
    assert report["status"] == "ok"
    assert report["summary"] == {"dry_run": 2}


def test_missing_governance_flags_are_normalized_to_safe_defaults(tmp_path):
    def submitter(coin, **kwargs):
        return {
            "coin": coin,
            "status": "dry_run",
        }

    report = run_training_trigger(
        provider="modelhub",
        coins=("BTC",),
        training_dir=tmp_path / "training",
        out_dir=tmp_path / "out",
        dry_run=True,
        modelhub_submitter=submitter,
    )

    assert report["status"] == "ok"
    assert report["summary"] == {"dry_run": 1}
    assert report["results"][0]["automatic_apply"] is False
    assert report["results"][0]["requires_human_approval"] is True


def test_modelhub_live_requires_req_no_per_coin(monkeypatch, tmp_path):
    calls = []

    def submitter(*args, **kwargs):
        calls.append((args, kwargs))
        return {
            "coin": args[0],
            "status": "candidate",
            "automatic_apply": False,
            "requires_human_approval": True,
        }

    monkeypatch.setenv("TRUSTFORGE_TRAINING_TRIGGER_ENABLED", "1")

    report = run_training_trigger(
        provider="modelhub",
        coins=("BTC", "ETH"),
        training_dir=tmp_path / "training",
        out_dir=tmp_path / "out",
        dry_run=False,
        enable_live=True,
        req_no_map={"BTC": "REQ-BTC"},
        modelhub_submitter=submitter,
    )

    assert [call[0][0] for call in calls] == ["BTC"]
    assert calls[0][1]["req_no"] == "REQ-BTC"
    assert report["summary"] == {"candidate": 1, "blocked": 1}
    assert report["results"][1]["coin"] == "ETH"
    assert report["results"][1]["reason"] == "missing ModelHub req_no for live trigger"


def test_governance_violation_is_forced_to_error(tmp_path):
    def submitter(coin, **kwargs):
        return {
            "coin": coin,
            "status": "candidate",
            "automatic_apply": True,
            "requires_human_approval": False,
        }

    report = run_training_trigger(
        provider="sagemaker",
        coins=("BTC",),
        training_dir=tmp_path / "training",
        out_dir=tmp_path / "out",
        dry_run=True,
        sagemaker_submitter=submitter,
    )

    assert report["status"] == "error"
    assert report["summary"] == {"error": 1}
    assert report["results"][0]["automatic_apply"] is False
    assert report["results"][0]["requires_human_approval"] is True
    assert "manual approval governance" in report["results"][0]["reason"]


def test_parse_req_no_map_validates_coin_and_shape():
    assert parse_req_no_map(["BTC=REQ-1"]) == {"BTC": "REQ-1"}
    with pytest.raises(ValueError):
        parse_req_no_map(["BTC"])
    with pytest.raises(ValueError):
        parse_req_no_map(["DOGE=REQ"])


@pytest.mark.parametrize(
    ("status", "code"),
    [("ok", 0), ("error", 1), ("blocked", 2), ("no_action", 2)],
)
def test_exit_code(status, code):
    assert exit_code({"status": status}) == code
