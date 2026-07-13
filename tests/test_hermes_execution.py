"""Hermes agent observability contract tests."""
from __future__ import annotations

import json

from trustforge.execlog import ExecutionLog, HERMES_NODES


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
