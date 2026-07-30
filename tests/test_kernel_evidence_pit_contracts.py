"""Security-boundary characterization for issue #502.

These tests deliberately exercise only public contracts.  They must not be
"fixed" by teaching historical answer retrieval to manufacture Evidence.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json

import pytest

from trustforge.analysis_flow import AnalysisFlow
from trustforge.bedrock import BedrockClient
from trustforge.historical_replay import replay_snapshot
from trustforge.ingestion.base import Document
from trustforge_core import KernelClaim, KernelDocument, KernelInput, run_kernel


PIT_EPOCH = datetime(2021, 7, 1, tzinfo=timezone.utc).timestamp()


def _snapshot(document: dict) -> dict:
    return {
        "coin": "BTC",
        "snapshot_at": "2021-07-01T00:00:00Z",
        "snapshot_epoch": PIT_EPOCH,
        "archive_type": "backfilled_archive",
        "sources": [{"source": "government", "documents": [document]}],
    }


def _document(*, published_at: object = "2021-06-30T12:00:00Z") -> dict:
    return {
        "id": "reg-1",
        "kind": "regulatory",
        "text": "BTC regulation update",
        "published_at": published_at,
    }


@pytest.mark.parametrize(
    "hostile_tier",
    [
        "historical_non_evidentiary",
        "evidence",
        "Evidence",
        "primary",
        "verified",
        "historical_evidentiary",
    ],
)
def test_worker_route_keeps_hostile_retrieval_context_out_of_evidence(
    tmp_path, monkeypatch, hostile_tier
):
    snapshot_document = Document(
        id="snapshot-doc",
        kind="regulatory",
        source="snapshot-source",
        text="BTC regulation remains unchanged.",
        url="https://snapshot.test",
        ts=PIT_EPOCH,
        meta={},
    )
    monkeypatch.setattr(
        "trustforge.analysis_flow.collect",
        lambda *args, **kwargs: [snapshot_document],
    )
    flow = AnalysisFlow(tmp_path / f"{hostile_tier}.sqlite3")
    snapshot_id = flow.create_snapshot("BTC")
    job_id = flow.enqueue_job(snapshot_id, "risk", "BTC market risk")
    assert job_id

    hostile = {
        "question_id": "historical-question",
        "coin": "BTC",
        "mode": "risk",
        "question": "prior answer",
        "answer": "HOSTILE MEMORY MUST NOT BECOME EVIDENCE",
        "snapshot_id": "historical-snapshot",
        "job_id": "historical-job",
        "published_at": PIT_EPOCH - 60,
        "source_tier": hostile_tier,
        # Deliberately satisfy both Document-like and Evidence-like shapes.  A
        # future regression that merges retrieval context into source docs can
        # no longer rely on missing fields to keep this payload out.
        "id": "hostile-memory-doc",
        "kind": "regulatory",
        "source": "hostile-memory",
        "text": "HOSTILE MEMORY MUST NOT BECOME EVIDENCE",
        "url": "https://hostile.invalid",
        "ts": PIT_EPOCH - 60,
        "meta": {},
        "fetched_at": "2021-06-30T23:59:00Z",
        "content_reference": "HOSTILE MEMORY MUST NOT BECOME EVIDENCE",
        "related_claim": "BTC market risk",
    }
    monkeypatch.setattr(
        flow,
        "question_context",
        lambda *args, **kwargs: {
            "query": "BTC market risk",
            "matches": [hostile],
            "conversation": [],
            "retrieval": "hostile-test",
        },
    )

    documents_seen_by_claim_extraction: list[Document] = []
    original_extract = BedrockClient.extract_claims_with_llm

    def record_claim_inputs(
        client, documents, *, log=None, mode=None, question=None,
    ):
        documents_seen_by_claim_extraction.extend(documents)
        return original_extract(
            client, documents, log=log, mode=mode, question=question,
        )

    monkeypatch.setattr(BedrockClient, "extract_claims_with_llm", record_claim_inputs)

    flow.start()
    flow.join()
    flow.stop()

    payload = flow.job_status(job_id)["result"]
    assert [document.id for document in documents_seen_by_claim_extraction] == [
        "snapshot-doc"
    ]
    assert payload["retrieval_context"] == [hostile]
    assert {item["source"] for item in payload["evidence"]} == {"snapshot-source"}
    assert all(
        "HOSTILE MEMORY" not in item["content_reference"]
        for item in payload["evidence"]
    )


@pytest.mark.parametrize(
    "published_at",
    [
        None,
        "",
        "not-a-time",
        "2021-06-30T12:00:00",
    ],
)
def test_replay_fails_closed_for_missing_or_unknown_document_time(published_at):
    with pytest.raises((TypeError, ValueError)):
        replay_snapshot(_snapshot(_document(published_at=published_at)), query="test")


def test_replay_rejects_document_one_second_after_pit_boundary():
    with pytest.raises(ValueError, match="future document"):
        replay_snapshot(
            _snapshot(_document(published_at="2021-07-01T00:00:01Z")), query="test"
        )


def test_replay_is_idempotent_for_identical_snapshot_and_query():
    snapshot = _snapshot(_document())
    first = replay_snapshot(snapshot, query="test")
    second = replay_snapshot(snapshot, query="test")

    # A run ID is observability identity, not decision output.  Replaying the
    # same immutable input must reproduce the complete report and evidence,
    # while retaining the same ordered audit-event shape.
    for key in (
        "coin",
        "snapshot_at",
        "snapshot_epoch",
        "archive_type",
        "report",
        "evidence",
    ):
        assert first[key] == second[key]
    first_events = [
        json.loads(line)["tool"] for line in first["execution_log_jsonl"].splitlines()
    ]
    second_events = [
        json.loads(line)["tool"] for line in second["execution_log_jsonl"].splitlines()
    ]
    assert first_events == second_events


def test_kernel_has_exact_deterministic_baseline():
    document = KernelDocument(
        id="price-1",
        kind="price",
        source="fixture-price",
        text="BTC close rose 3%",
        timestamp=PIT_EPOCH,
    )
    claim = KernelClaim(
        id="claim-1",
        text="BTC close rose 3%",
        document=document,
        claim_type="fact",
        direction="bullish",
    )
    kernel_input = KernelInput(
        claims=(claim,), pit_epoch=PIT_EPOCH, coin="BTC", query="BTC outlook"
    )

    first = run_kernel(kernel_input)
    second = run_kernel(kernel_input)

    assert first == second
    expected_scored_claim = {
        "claim": {
            "id": "claim-1",
            "text": "BTC close rose 3%",
            "document": {
                "id": "price-1",
                "kind": "price",
                "source": "fixture-price",
                "text": "BTC close rose 3%",
                "timestamp": PIT_EPOCH,
                "url": "",
                "metadata": (),
            },
            "claim_type": "fact",
            "direction": "bullish",
        },
        "trust": 0.625,
        "components": (
            ("reputation", 0.95),
            ("corroboration", 0.0),
            ("recency", 1.0),
            ("manipulation", 0.0),
        ),
        "reputation_trace": None,
        "manip_flags": (),
        "info_flags": (),
    }
    assert asdict(first) == {
        "trust_score": 0.625,
        "confidence": 0.4188,
        "abstain": True,
        "direction": "中性",
        "reason_codes": ("insufficient_independent_sources",),
        "supporting_count": 1,
        "independent_sources": 1,
        "contract_version": "2.2.0",
        "query": "BTC outlook",
        "scored_claims": (expected_scored_claim,),
        "supporting": (expected_scored_claim,),
        "contrarian": (),
        "decision_state": "abstain",
    }
