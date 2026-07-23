from dataclasses import replace

import pytest

from trustforge.analysis_quality_event import build_analysis_quality_event
from trustforge.delayed_outcome_labeler import build_delayed_outcome_observation
from trustforge.learning_event_contract import (
    LearningEventError,
    canonical_integrity_checksum,
)
from trustforge.learning_event_store import LearningEventAppendLog


def _analysis():
    evidence_snapshot = [
        {
            "source": f"source-{index}",
            "fetched_at": "2026-06-30T23:59:59.000000Z",
            "content_reference": f"sha256:content-{index}",
            "related_claim": f"claim-{index}",
            "schema_version": "evidence.v1",
            "trust": 0.8,
        }
        for index in range(4)
    ]
    pit = {
        "event_time": "2026-07-01T00:00:00Z",
        "available_time": "2026-07-01T00:00:01Z",
        "as_of_time": "2026-07-01T00:00:01Z",
        "source_available_times": ["2026-06-30T23:59:59Z"],
    }
    provenance = {
        "source": "analysis-flow",
        "collector": "unit-test",
        "observed_at": "2026-07-01T00:00:01Z",
    }
    return build_analysis_quality_event(
        {
            "analysis_id": "an-1",
            "run_id": "run-1",
            "question_id": "question-1",
            "answer_id": "answer-1",
            "evidence_snapshot_id": canonical_integrity_checksum(evidence_snapshot),
            "evidence_snapshot": evidence_snapshot,
            "question": "Will BTC rise?",
            "tenant_id": "tenant-a",
            "coin": "BTC",
            "mode": "formal",
            "question_type": "direction",
            "event_time": "2026-07-01T00:00:00Z",
            "available_time": "2026-07-01T00:00:01Z",
            "as_of_time": "2026-07-01T00:00:01Z",
            "source_available_times": ["2026-06-30T23:59:59Z"],
            "provenance": {"source": "analysis-flow", "collector": "unit-test", "observed_at": "2026-07-01T00:00:01Z"},
            "confidence": {"raw": 0.7, "calibrated": 0.62},
            "decision": {"direction": "bullish", "state": "buy"},
            "evidence_stats": {
                "supporting_count": 3,
                "contrarian_count": 1,
                "evidence_count": 4,
                "average_trust": 0.8,
                "independent_source_count": 3,
                "source_distribution": {"exchange": 2, "news": 2},
            },
            "quality": {"freshness": "ok", "conflict": "low", "missingness": 0.0, "completeness": "complete"},
            "versions": {
                "contract": "analysis-quality.v1",
                "schema": "analysis-quality.v1",
                "kernel": "learning-event.v1",
                "scoring": "score-v1",
                "evidence": "evidence-v1",
                "model": "model-v1",
                "prompt": "prompt-v1",
                "policy": "policy-v1",
                "rule": "rule-v1",
            },
            "stage_metrics": [{"stage": "kernel", "latency_ms": 1, "status": "complete", "attempts": 1, "failure": None}],
            "failure": {"status": "complete", "failed_stage": None, "code": None, "message": None, "retryable": False},
        },
        trusted_tenant_id="tenant-a",
        trusted_pit=pit,
        trusted_provenance=provenance,
    )


def test_delayed_outcome_stays_pending_before_maturity():
    event = build_delayed_outcome_observation(
        _analysis(),
        horizon="T+7",
        as_of_time="2026-07-03T00:00:00Z",
        prices={},
        source_version="fixture-v1",
    )

    assert event.kind == "delayed_outcome"
    assert event.payload["status"] == "pending"
    assert "evidence_id" not in event.payload


def test_delayed_outcome_labels_matured_fixture_and_is_idempotent():
    prices = {
        "2026-07-01": {"close": 100, "available_time": "2026-07-01T01:00:00Z", "source_id": "start"},
        "2026-07-08": {"close": 110, "available_time": "2026-07-08T01:00:00Z", "source_id": "end"},
    }
    event = build_delayed_outcome_observation(
        _analysis(),
        horizon="T+7",
        as_of_time="2026-07-08T01:00:00Z",
        prices=prices,
        source_version="fixture-v1",
    )
    log = LearningEventAppendLog()

    assert event.payload["status"] == "labeled"
    assert event.payload["ground_truth_direction"] == "bullish"
    assert event.payload["outcome_pct"] == pytest.approx(10.0)
    assert log.append(event) == "created"
    assert log.append(event) == "idempotent"


def test_missing_mature_price_is_unavailable_not_evidence():
    prices = {"2026-07-01": {"close": 100, "available_time": "2026-07-01T01:00:00Z", "source_id": "start"}}

    event = build_delayed_outcome_observation(
        _analysis(),
        horizon="T+7",
        as_of_time="2026-07-08T01:00:00Z",
        prices=prices,
        source_version="fixture-v1",
    )

    assert event.payload["status"] == "unavailable"
    assert event.kind == "delayed_outcome"


def test_future_available_target_price_is_unavailable_at_cutoff():
    prices = {
        "2026-07-01": {"close": 100, "available_time": "2026-07-01T01:00:00Z", "source_id": "start"},
        "2026-07-08": {"close": 110, "available_time": "2026-07-09T00:00:00Z", "source_id": "future-end"},
    }

    event = build_delayed_outcome_observation(
        _analysis(),
        horizon="T+7",
        as_of_time="2026-07-08T01:00:00Z",
        prices=prices,
        source_version="fixture-v1",
    )

    assert event.payload["status"] == "unavailable"
    assert "source_lineage" not in event.payload


def test_future_available_start_price_is_unavailable_at_cutoff():
    prices = {
        "2026-07-01": {"close": 100, "available_time": "2026-07-08T02:00:00Z", "source_id": "future-start"},
        "2026-07-08": {"close": 110, "available_time": "2026-07-08T01:00:00Z", "source_id": "end"},
    }

    event = build_delayed_outcome_observation(
        _analysis(),
        horizon="T+7",
        as_of_time="2026-07-08T01:00:00Z",
        prices=prices,
        source_version="fixture-v1",
    )

    assert event.payload["status"] == "unavailable"
    assert "source_lineage" not in event.payload


def test_price_available_exactly_at_cutoff_can_label():
    prices = {
        "2026-07-01": {"close": 100, "available_time": "2026-07-01T01:00:00Z", "source_id": "start"},
        "2026-07-08": {"close": 110, "available_time": "2026-07-08T01:00:00Z", "source_id": "end"},
    }

    event = build_delayed_outcome_observation(
        _analysis(),
        horizon="T+7",
        as_of_time="2026-07-08T01:00:00Z",
        prices=prices,
        source_version="fixture-v1",
    )

    assert event.payload["status"] == "labeled"
    assert event.payload["source_lineage"]["end_available_time"] == "2026-07-08T01:00:00Z"


def test_source_revision_creates_new_observation_identity_without_rewrite():
    prices = {
        "2026-07-01": {"close": 100, "available_time": "2026-07-01T01:00:00Z", "source_id": "start"},
        "2026-07-02": {"close": 90, "available_time": "2026-07-02T01:00:00Z", "source_id": "end"},
    }

    original = build_delayed_outcome_observation(
        _analysis(),
        horizon="T+1",
        as_of_time="2026-07-02T01:00:00Z",
        prices=prices,
        source_version="fixture-v1",
    )
    revised = build_delayed_outcome_observation(
        _analysis(),
        horizon="T+1",
        as_of_time="2026-07-02T01:00:00Z",
        prices=prices,
        source_version="fixture-v2",
        revision=2,
    )

    assert original.identity.endswith("/v1")
    assert revised.identity.endswith("/v2")
    assert original.identity != revised.identity


def test_delayed_outcome_rejects_non_analysis_source_and_unknown_horizon():
    with pytest.raises(LearningEventError, match="analysis-quality"):
        build_delayed_outcome_observation(
            replace(
                _analysis(),
                payload={
                    "historical_answer_id": "not-analysis",
                    "question": "fixture",
                    "event_type": "not-analysis-quality",
                },
            ),
            horizon="T+1",
            as_of_time="2026-07-02T01:00:00Z",
            prices={},
            source_version="fixture-v1",
        )
    with pytest.raises(LearningEventError, match="unsupported"):
        build_delayed_outcome_observation(
            _analysis(),
            horizon="T+3",
            as_of_time="2026-07-02T01:00:00Z",
            prices={},
            source_version="fixture-v1",
        )
