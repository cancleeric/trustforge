from __future__ import annotations

import json
from pathlib import Path
import subprocess

from scripts import run_multi_angle_release_gate as gate


def test_timeout_is_structured_and_nonzero(monkeypatch, capsys):
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            Path(kwargs["env"]["TRUSTFORGE_AWS_GUARD_MARKER"]).write_text(
                "blocked", encoding="utf-8"
            )
            return subprocess.CompletedProcess(
                command, 1, "", "AWS access blocked by multi-angle release gate"
            )
        raise subprocess.TimeoutExpired(
            command, kwargs["timeout"], output=b"partial", stderr=b"deadline"
        )

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    assert gate.main() == 124
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "timeout"
    assert result["exit_code"] == 124
    assert result["timeout_seconds"] == gate.TIMEOUT_SECONDS
    assert result["aws_guard_negative_control"] is True
    assert result["aws_enabled"] is False
    assert result["pytest_stdout"] == "partial"
    assert result["pytest_stderr"] == "deadline"
