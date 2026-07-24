from __future__ import annotations

import subprocess
from pathlib import Path

from scripts import pytest_benchmark_inventory as inventory


def test_benchmark_report_records_counts_and_preserves_failures(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def fake_run(command, cwd, text, stdout, stderr, check):
        calls.append(list(command))
        name = " ".join(command)
        if "--collect-only" in command:
            return subprocess.CompletedProcess(command, 0, "tests/test_a.py::test_one\n", "")
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "pytest 9.1.1\n", "")
        if "measured-2-fails" in name:
            return subprocess.CompletedProcess(
                command,
                1,
                "1 failed, 2 passed, 1 skipped in 1.25s\n0.50s call tests/test_a.py::test_one\n",
                "",
            )
        return subprocess.CompletedProcess(
            command,
            0,
            "3 passed in 1.00s\n0.40s call tests/test_a.py::test_one\n",
            "",
        )

    monkeypatch.setattr(inventory.subprocess, "run", fake_run)
    monkeypatch.setattr(inventory.time, "perf_counter", iter([0, 1, 1, 2, 2, 3, 3, 4, 4, 5]).__next__)

    exit_code, report = inventory.build_report(
        tmp_path,
        ["measured-2-fails"],
        measured_runs=2,
    )

    assert exit_code == 1
    assert report["collection"]["collected_count"] == 1
    assert report["measured_wall_seconds"] == {"median": 1, "min": 1, "max": 1}
    assert report["measured_runs"][1]["counts"]["failed"] == 1
    assert report["measured_runs"][1]["counts"]["passed"] == 2
    assert report["measured_runs"][1]["counts"]["skipped"] == 1
    assert report["measured_runs"][1]["slowest"][0]["nodeid"] == "tests/test_a.py::test_one"
    assert any("--durations=50" in call for call in calls)


def test_redact_removes_paths_and_secret_like_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    text = f"{tmp_path}/repo/tests/test_a.py\nAPI_TOKEN=abc\nregular line"

    redacted = inventory._redact(text, tmp_path / "repo")

    assert str(tmp_path) not in redacted
    assert "API_TOKEN" not in redacted
    assert "<repo>" in redacted
    assert "regular line" in redacted


def test_collect_args_force_single_quiet_nodeid_output():
    assert inventory._collect_args(["tests/test_a.py", "--no-cov", "-q"]) == [
        "tests/test_a.py",
        "--no-cov",
        "--collect-only",
        "-q",
    ]


def test_main_writes_reports_and_returns_pytest_status(monkeypatch, tmp_path):
    report = {
        "created_at": "2026-07-22T00:00:00+00:00",
        "python_version": "3.11.0",
        "pytest_version": "pytest 9.1.1",
        "pytest_command": ["python", "-m", "pytest"],
        "collection": {"collected_count": 1, "returncode": 0},
        "warmup": {"returncode": 0},
        "measured_wall_seconds": {"median": 1.0, "min": 1.0, "max": 1.0},
        "measured_runs": [{"name": "measured-1", "returncode": 5, "wall_seconds": 1, "counts": {}, "slowest": []}],
    }
    monkeypatch.setattr(inventory, "build_report", lambda repo_root, pytest_args, measured_runs: (5, report))
    monkeypatch.setattr(inventory, "_markdown", lambda payload: "# report\n")

    exit_code = inventory.main(["--output-dir", str(tmp_path), "--measured-runs", "1", "--", "-q"])

    assert exit_code == 5
    assert len(list(Path(tmp_path).glob("*.json"))) == 1
    assert len(list(Path(tmp_path).glob("*.md"))) == 1
