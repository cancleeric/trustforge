"""Generic execution event log tests (#410)."""
from __future__ import annotations

import json
from pathlib import Path

from trustforge.execution_event_log import (
    REDACTED,
    ExecutionEventLog,
    ExecutionStepRecord,
    redact_secrets,
    record_to_dict,
)
from trustforge.execlog import ExecutionLog


def test_generic_event_log_serializes_legacy_jsonl_shape():
    log = ExecutionEventLog(
        run_id="run-1",
        started_at="2026-07-22T00:00:00Z",
        budget_sec=900,
    )

    log.append(
        ts="2026-07-22T00:00:01Z",
        elapsed_sec=1.234,
        tool="provider.invoke",
        params={"provider": "fake"},
        summary="ran provider",
        step=ExecutionStepRecord(step_id="provider", label="Provider", order=1),
    )

    line = json.loads(log.to_jsonl())
    assert line == {
        "ts": "2026-07-22T00:00:01Z",
        "elapsed_sec": 1.23,
        "tool": "provider.invoke",
        "params": {"provider": "fake"},
        "summary": "ran provider",
    }


def test_execution_log_keeps_manifest_and_jsonl_compatibility():
    log = ExecutionLog(now_fn=lambda: 1000.0, run_id="hermes-compat")
    log.record("provider.resolve", params={"provider": "null"})

    assert log.manifest()["run_id"] == "hermes-compat"
    assert log.manifest()["agent"] == "hermes"
    lines = [json.loads(line) for line in log.to_jsonl().splitlines()]
    assert set(lines[0]) == {"ts", "elapsed_sec", "tool", "params", "summary"}
    assert lines[-1]["params"]["provider"] == "null"
    assert lines[-1]["params"]["hermes"]["agent"] == "hermes"
    assert log.events == lines


def test_secret_redaction_is_recursive_and_key_based():
    value = {
        "api_key": "abc",
        "nested": {
            "Authorization": "Bearer token",
            "items": [{"password": "pw"}, {"safe": "ok"}],
        },
        "safe": "visible",
    }

    assert redact_secrets(value) == {
        "api_key": REDACTED,
        "nested": {
            "Authorization": REDACTED,
            "items": [{"password": REDACTED}, {"safe": "ok"}],
        },
        "safe": "visible",
    }


def test_execution_log_redacts_secrets_before_jsonl_output():
    log = ExecutionLog(now_fn=lambda: 1000.0, run_id="hermes-redact")
    log.record(
        "provider.resolve",
        params={"api_key": "abc", "nested": {"token": "secret", "safe": "ok"}},
    )

    event = json.loads(log.to_jsonl().splitlines()[-1])

    assert event["params"]["api_key"] == REDACTED
    assert event["params"]["nested"]["token"] == REDACTED
    assert event["params"]["nested"]["safe"] == "ok"


def test_generic_run_record_manifest_is_json_compatible():
    log = ExecutionEventLog(
        run_id="run-2",
        started_at="2026-07-22T00:00:00Z",
        budget_sec=60,
    )
    log.append(
        ts="2026-07-22T00:00:01Z",
        elapsed_sec=1,
        tool="step",
        step=ExecutionStepRecord(step_id="s1", status="completed"),
    )

    assert record_to_dict(log.manifest()) == {
        "run_id": "run-2",
        "started_at": "2026-07-22T00:00:00Z",
        "elapsed_sec": 1.0,
        "budget_sec": 60,
        "steps": [{"step_id": "s1", "label": "", "order": 0, "status": "completed"}],
    }


def test_generic_execution_event_log_has_no_hermes_node_names():
    source = Path("src/trustforge/execution_event_log.py").read_text(encoding="utf-8").lower()

    assert "hermes" not in source
    assert "source_ingestion" not in source
    assert "trust_reasoning" not in source
