from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "verify_production_analysis_report.py"
)
SPEC = importlib.util.spec_from_file_location("analysis_report_canary", SCRIPT)
assert SPEC and SPEC.loader
canary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(canary)


def test_verify_report_waits_for_completed_report(monkeypatch):
    responses = iter(
        [
            {"job_id": "flow-1", "state": "queued"},
            {"job_id": "flow-1", "state": "running", "result": None},
            {
                "job_id": "flow-1",
                "state": "completed",
                "current_stage": "published",
                "result": {"summary": "ok"},
            },
        ]
    )
    monkeypatch.setattr(canary, "_request_json", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(canary.time, "sleep", lambda _seconds: None)

    result = canary.verify_report(
        "http://localhost", timeout_seconds=30, poll_seconds=1
    )

    assert result == {
        "job_id": "flow-1",
        "state": "completed",
        "current_stage": "published",
    }


def test_verify_report_rejects_failed_job(monkeypatch):
    responses = iter(
        [
            {"job_id": "flow-2", "state": "queued"},
            {
                "job_id": "flow-2",
                "state": "failed",
                "error": "worker crashed",
            },
        ]
    )
    monkeypatch.setattr(canary, "_request_json", lambda *_args, **_kwargs: next(responses))

    with pytest.raises(RuntimeError, match="worker crashed"):
        canary.verify_report("http://localhost", timeout_seconds=30)
