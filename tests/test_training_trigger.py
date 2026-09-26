from __future__ import annotations

import json

import pytest

from trustforge.training_trigger import (
    exit_code,
    main,
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


def test_modelhub_real_dry_run_path_preserves_governance_defaults(tmp_path):
    # Post-#1468 the trigger never imports web-owned submitters implicitly;
    # the regression requirement from #1452 is the REAL ModelHub dry-run path
    # (not a mock), injected explicitly here.
    from trustforge.modelhub_submit import submit_calibrator_training

    report = run_training_trigger(
        provider="modelhub",
        coins=("BTC",),
        out_dir=tmp_path / "modelhub",
        dry_run=True,
        modelhub_submitter=submit_calibrator_training,
    )

    assert report["status"] == "ok"
    assert report["summary"] == {"dry_run": 1}
    result = report["results"][0]
    assert result["coin"] == "BTC"
    assert result["status"] == "dry_run"
    assert result["automatic_apply"] is False
    assert result["requires_human_approval"] is True


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


def test_missing_submitter_fails_closed_without_importing_web_modules(tmp_path):
    """#1468: agent-layer trigger must not fall back to importing web-owned
    submitter modules; without an injected submitter every coin is blocked."""
    report = run_training_trigger(
        provider="sagemaker",
        coins=("BTC", "ETH"),
        training_dir=tmp_path / "training",
        out_dir=tmp_path / "out",
        dry_run=True,
    )

    assert report["status"] == "no_action"
    assert report["enabled"] is False
    assert report["summary"] == {"blocked": 2}
    assert all(
        result["status"] == "blocked"
        and "no sagemaker submitter configured" in result["reason"]
        for result in report["results"]
    )
    assert report["automatic_apply"] is False
    assert report["requires_human_approval"] is True


def test_missing_modelhub_submitter_fails_closed(tmp_path):
    report = run_training_trigger(
        provider="modelhub",
        coins=("BTC",),
        training_dir=tmp_path / "training",
        out_dir=tmp_path / "out",
        dry_run=True,
    )

    assert report["status"] == "no_action"
    assert report["summary"] == {"blocked": 1}
    assert "no modelhub submitter configured" in report["reason"]


def test_main_threads_composition_root_submitters(tmp_path, capsys):
    """The CLI entrypoint must forward submitters injected by the composition
    root (scripts/run_training_trigger.py) so cron behavior is unchanged."""
    calls = []

    def submitter(coin, **kwargs):
        calls.append((coin, kwargs))
        return {
            "coin": coin,
            "status": "dry_run",
            "automatic_apply": False,
            "requires_human_approval": True,
        }

    code = main(
        ["--provider", "sagemaker", "--coin", "BTC", "--training-dir", str(tmp_path)],
        sagemaker_submitter=submitter,
    )

    assert code == 0
    assert [coin for coin, _ in calls] == ["BTC"]
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "ok"
    assert report["summary"] == {"dry_run": 1}


def test_composition_root_script_smoke(tmp_path):
    """codex-review MINOR: execute scripts/run_training_trigger.py itself so a
    broken import/wiring in the composition root is caught, not just main()."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    # The shared dev venv is an editable install of the MAIN checkout; force
    # the worktree src so this smoke test exercises the code under test.
    env["PYTHONPATH"] = str(root / "src")
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "run_training_trigger.py"),
            "--provider", "sagemaker",
            "--coin", "BTC",
            "--training-dir", str(tmp_path / "no-such-data"),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    # Missing training data yields an error result (exit 1) — what matters is
    # the script wired the real submitter and emitted a governed JSON report.
    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    assert report["provider"] == "sagemaker"
    assert report["automatic_apply"] is False
    assert report["requires_human_approval"] is True
    assert report["results"][0]["coin"] == "BTC"
