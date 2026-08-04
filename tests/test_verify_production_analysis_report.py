from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import urllib.error

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


def test_verify_report_retries_initial_job_not_found(monkeypatch):
    missing = urllib.error.HTTPError(
        "http://localhost/api/analysis-job", 404, "Not Found", {}, io.BytesIO()
    )
    responses = iter(
        [
            {"job_id": "flow-3", "state": "queued"},
            missing,
            {
                "job_id": "flow-3",
                "state": "completed",
                "current_stage": "published",
                "result": {"summary": "ok"},
            },
        ]
    )

    def request(*_args, **_kwargs):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    sleeps = []
    monkeypatch.setattr(canary, "_request_json", request)
    monkeypatch.setattr(canary.time, "sleep", sleeps.append)

    result = canary.verify_report(
        "http://localhost", timeout_seconds=30, poll_seconds=1
    )

    assert result["job_id"] == "flow-3"
    assert sleeps == [1]
