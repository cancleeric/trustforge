"""#104 smoke tests for deploy/put_dedup_alarm.sh."""

from __future__ import annotations

import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest


_DEPLOY_DIR = Path(__file__).resolve().parents[1] / "deploy"
_SCRIPT = _DEPLOY_DIR / "put_dedup_alarm.sh"


def _write_fake_aws(tmp_path: Path) -> str:
    fake = tmp_path / "aws"
    log = tmp_path / "aws.log"
    fake.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            echo "$*" >> {log}
            exit 0
            """
        )
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(log)


def _run(tmp_path: Path, env_extras: dict[str, str]) -> tuple[int, str, list[str]]:
    log_path = _write_fake_aws(tmp_path)
    env = dict(os.environ)
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["REGION"] = "ap-southeast-2"
    env["TRUSTFORGE_DEDUP_LOG_GROUP"] = "/aws/apprunner/trustforge/application"
    env.pop("TRUSTFORGE_DEDUP_ALARM_SNS", None)
    env.update(env_extras)

    proc = subprocess.run(
        ["bash", str(_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    log_file = Path(log_path)
    calls = log_file.read_text().splitlines() if log_file.exists() else []
    return proc.returncode, proc.stdout + proc.stderr, calls


@pytest.mark.skipif(not _SCRIPT.exists(), reason="deploy script is missing")
def test_no_sns_builds_filter_and_alarms_without_actions(tmp_path: Path) -> None:
    rc, out, calls = _run(tmp_path, {})

    assert rc == 0, out
    assert not any("--alarm-actions" in call for call in calls), calls
    assert not any("--ok-actions" in call for call in calls), calls
    assert any("logs put-metric-filter" in call for call in calls), calls
    assert sum("cloudwatch put-metric-alarm" in call for call in calls) == 2
    assert any('"ALERT: TrustForge dedup"' in call for call in calls), calls
    assert any("DedupFailOpenRecentFailures" in call for call in calls), calls
    assert any("DedupFailOpenAlertLogCount" in call for call in calls), calls


@pytest.mark.skipif(not _SCRIPT.exists(), reason="deploy script is missing")
def test_blank_sns_builds_alarms_without_actions(tmp_path: Path) -> None:
    rc, out, calls = _run(tmp_path, {"TRUSTFORGE_DEDUP_ALARM_SNS": ""})

    assert rc == 0, out
    assert not any("--alarm-actions" in call for call in calls), calls
    assert not any("--ok-actions" in call for call in calls), calls
    assert sum("cloudwatch put-metric-alarm" in call for call in calls) == 2


@pytest.mark.skipif(not _SCRIPT.exists(), reason="deploy script is missing")
def test_valid_sns_adds_actions_to_both_alarms(tmp_path: Path) -> None:
    sns = "arn:aws:sns:ap-southeast-2:123456789012:trustforge-alerts"
    rc, out, calls = _run(tmp_path, {"TRUSTFORGE_DEDUP_ALARM_SNS": sns})

    assert rc == 0, out
    alarm_calls = [call for call in calls if "cloudwatch put-metric-alarm" in call]
    assert len(alarm_calls) == 2
    assert all(f"--alarm-actions {sns}" in call for call in alarm_calls)
    assert all(f"--ok-actions {sns}" in call for call in alarm_calls)


@pytest.mark.skipif(not _SCRIPT.exists(), reason="deploy script is missing")
def test_invalid_sns_exits_nonzero_before_aws_calls(tmp_path: Path) -> None:
    rc, out, calls = _run(
        tmp_path,
        {
            "TRUSTFORGE_DEDUP_ALARM_SNS": (
                "arn:aws:logs:ap-southeast-2:123456789012:log-group:trustforge"
            )
        },
    )

    assert rc != 0
    assert "not a valid SNS topic ARN" in out
    assert calls == []
