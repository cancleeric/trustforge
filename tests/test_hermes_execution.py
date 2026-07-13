"""Hermes agent observability contract tests."""
from __future__ import annotations

import json

from trustforge.execlog import ExecutionLog, HERMES_NODES
from trustforge.ingestion.base import OfflineSampleSource, execution_log_context, collect


def test_hermes_events_have_stable_run_and_node_context():
    log = ExecutionLog(now_fn=lambda: 1000.0, run_id="hermes-test-run")
    log.record("ingestion.collect", params={"coin": "BTC"})
    log.record("pipeline.step1.start")
    log.record("bedrock.complete", params={"step": 1})
    log.record("pipeline.step2.start")
    log.record("evidence.build")
    log.record("report.done")

    hermes = [event["params"]["hermes"] for event in log.events]
    assert {item["run_id"] for item in hermes} == {"hermes-test-run"}
    assert {item["agent"] for item in hermes} == {"hermes"}
    assert [item["node_id"] for item in hermes[1:]] == [
        "source_ingestion", "claim_extraction", "claim_extraction",
        "trust_reasoning", "evidence_assembly", "report_delivery",
    ]
    assert hermes[-1]["status"] == "completed"


def test_hermes_manifest_and_jsonl_are_auditable():
    log = ExecutionLog(now_fn=lambda: 1000.0, run_id="hermes-test-run")
    manifest = log.manifest()

    assert manifest["agent"] == "hermes"
    assert manifest["run_id"] == "hermes-test-run"
    assert manifest["budget_sec"] == 900
    assert manifest["nodes"] == [
        {"id": node_id, "label": label, "order": order}
        for node_id, label, order in HERMES_NODES
    ]
    event = json.loads(log.to_jsonl())
    assert event["params"]["hermes"]["node_id"] == "source_ingestion"


def test_each_source_boundary_records_safe_latency_and_outcome():
    log = ExecutionLog(run_id="hermes-source-run")
    with execution_log_context(log):
        collect(
            "分析 BTC", coin="BTC", offline=True,
            sources=[OfflineSampleSource("regulatory", "government-announcements")],
        )

    events = [event for event in log.events if event["tool"] == "ingestion.source"]
    assert [event["params"]["source"] for event in events] == [
        "official-ohlcv", "government-announcements",
    ]
    for event in events:
        params = event["params"]
        assert params["outcome"] == "ok"
        assert params["duration_ms"] >= 0
        assert params["document_count"] >= 0
        assert params["hermes"]["node_id"] == "source_ingestion"
