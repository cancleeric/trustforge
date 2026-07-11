"""#104：put_dedup_alarm.sh 冒煙測試（無 SNS 時不帶非法 action 且能建出 alarm）。

用一個假的 `aws` 攔截 cloudwatch put-metric-alarm 呼叫、記錄參數，避免真打 AWS：
- 無 SNS：腳本不應傳 `--alarm-actions`（舊版把 Logs ARN 當 action 是非法、會讓
  `set -e` 退出、Alarm 建不出來）；腳本應以 0 結束（建出「純狀態可視」alarm）。
- 有合法 SNS：傳 `--alarm-actions <sns>`，0 結束。
- 有非法 SNS（非 arn:aws:sns:*）：應 `exit 1` 要求修正，絕不假裝成功。
"""
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


def _run(tmp_path: Path, env_extras: dict) -> tuple[int, str, list[str]]:
    log_path = _write_fake_aws(tmp_path)
    env = dict(os.environ)
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["REGION"] = "ap-southeast-2"
    # 清掉可能繼承的 SNS 變數，再套用本測試指定的值
    env.pop("TRUSTFORGE_DEDUP_ALARM_SNS", None)
    env.update(env_extras)
    proc = subprocess.run(
        ["bash", str(_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
    )
    log_file = Path(log_path)
    calls = log_file.read_text().splitlines() if log_file.exists() else []
    return proc.returncode, proc.stdout + proc.stderr, calls


@pytest.mark.skipif(not _SCRIPT.exists(), reason="deploy 腳本不存在")
def test_no_sns_builds_alarm_without_illegal_action(tmp_path):
    rc, out, calls = _run(tmp_path, {})
    assert rc == 0, out
    # 絕不能把 Logs ARN 當 alarm action
    assert "log-group:trustforge" not in out
    # 不該出現 --alarm-actions（純狀態可視）
    assert not any("--alarm-actions" in c for c in calls), f"出現非法 --alarm-actions：{calls}"
    # 應有建表呼叫
    assert any("put-metric-alarm" in c for c in calls)


@pytest.mark.skipif(not _SCRIPT.exists(), reason="deploy 腳本不存在")
def test_valid_sns_builds_alarm_with_action(tmp_path):
    rc, out, calls = _run(
        tmp_path, {"TRUSTFORGE_DEDUP_ALARM_SNS": "arn:aws:sns:ap-southeast-2:123456789012:tf-alerts"}
    )
    assert rc == 0, out
    action_calls = [c for c in calls if "--alarm-actions" in c]
    assert action_calls, "合法 SNS 應帶 --alarm-actions"
    assert "arn:aws:sns:ap-southeast-2:123456789012:tf-alerts" in action_calls[0]


@pytest.mark.skipif(not _SCRIPT.exists(), reason="deploy 腳本不存在")
def test_invalid_sns_exits_nonzero(tmp_path):
    rc, out, calls = _run(
        tmp_path, {"TRUSTFORGE_DEDUP_ALARM_SNS": "arn:aws:logs:ap-southeast-2:123456789012:log-group:trustforge"}
    )
    assert rc != 0, "非法 SNS ARN 應 exit 非 0，不得假裝成功"
    assert "不是合法的 SNS topic ARN" in out
