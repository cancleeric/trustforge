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
from trustforge.historical_replay import replay_snapshot
from trustforge.schema import Evidence
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


def test_historical_answer_is_not_structurally_bindable_as_evidence(tmp_path):
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    flow.register_question("BTC", "risk", "BTC market risk", enqueue=False)

    [historical] = flow.question_context("BTC", "risk", "BTC market risk")["matches"]

    assert historical["source_tier"] == "historical_non_evidentiary"
    with pytest.raises(TypeError):
        Evidence(**historical)


def test_historical_non_evidentiary_replay_document_is_rejected_before_evidence():
    with pytest.raises(ValueError, match="historical_non_evidentiary"):
        replay_snapshot(
            _snapshot(
                {
                    "id": "prior-answer",
                    "kind": "historical_non_evidentiary",
                    "text": "Prior generated answer must not become evidence",
                    "published_at": "2021-06-30T12:00:00Z",
                }
            ),
            query="BTC outlook",
        )


@pytest.mark.parametrize(
    "hostile_tier",
    ["evidence", "Evidence", "primary", "verified", "historical_evidentiary"],
)
def test_relabeling_historical_answer_cannot_escalate_its_classification(
    tmp_path, hostile_tier
):
    flow = AnalysisFlow(tmp_path / f"{hostile_tier}.sqlite3")
    flow.register_question("BTC", "risk", "BTC market risk", enqueue=False)
    [historical] = flow.question_context("BTC", "risk", "BTC market risk")["matches"]
    hostile = {**historical, "source_tier": hostile_tier}

    with pytest.raises(TypeError):
        Evidence(**hostile)


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
        "scored_claims": (asdict(first.scored_claims[0]),),
        "supporting": (asdict(first.supporting[0]),),
        "contrarian": (),
        "decision_state": "abstain",
    }
