import pytest

from trustforge.analysis_quality_event import build_analysis_quality_event
from trustforge.learning_event_contract import LearningEventError, serialize_learning_event
from trustforge.learning_event_store import LearningEventAppendLog


def _snapshot():
    return {
        "analysis_id": "an-1",
        "tenant_id": "tenant-a",
        "coin": "BTC",
        "mode": "formal",
        "question_type": "direction",
        "event_time": "2026-07-01T00:00:00Z",
        "available_time": "2026-07-01T00:00:01Z",
        "as_of_time": "2026-07-01T00:00:00Z",
        "source_available_times": ["2026-06-30T23:59:59Z"],
        "provenance": {"source": "analysis-flow", "collector": "unit-test", "observed_at": "2026-07-01T00:00:01Z"},
        "confidence": {"raw": 0.7, "calibrated": 0.62},
        "decision": {"direction": "bullish", "abstain": False},
        "evidence_stats": {"supporting": 3, "contrarian": 1, "missingness": 0.0},
        "quality": {"freshness": "ok", "conflict": "low", "completeness": "complete"},
        "versions": {"kernel": "learning-event.v1", "scoring": "test"},
        "stage_metrics": [{"stage": "kernel", "latency_ms": 12, "failure": None, "retry": 0}],
    }


def test_analysis_quality_event_has_unique_id_and_no_outcome_mutation_surface():
    event = build_analysis_quality_event(_snapshot())

    assert event.kind == "historical_non_evidentiary"
    assert event.identity == "analysis-quality:tenant-a:an-1"
    assert event.payload["event_type"] == "analysis-quality.v1"
    assert "outcome_id" not in serialize_learning_event(event)


def test_analysis_quality_retry_is_idempotent_in_append_log():
    event = build_analysis_quality_event(_snapshot())
    retry = build_analysis_quality_event(_snapshot())
    log = LearningEventAppendLog()

    assert log.append(event) == "created"
    assert log.append(retry) == "idempotent"


def test_analysis_quality_rejects_missing_version_or_provenance():
    missing_version = _snapshot()
    missing_version.pop("versions")
    missing_provenance = _snapshot()
    missing_provenance["provenance"] = {"collector": "unit-test", "observed_at": "2026-07-01T00:00:01Z"}

    with pytest.raises(LearningEventError, match="versions"):
        build_analysis_quality_event(missing_version)
    with pytest.raises(LearningEventError, match="provenance.source"):
        build_analysis_quality_event(missing_provenance)


def test_analysis_quality_rejects_future_data_and_cross_tenant_rewrite():
    future = _snapshot()
    future["source_available_times"] = ["2026-07-02T00:00:00Z"]
    tenant_b = _snapshot()
    tenant_b["tenant_id"] = "tenant-b"

    with pytest.raises(LearningEventError, match="future source data"):
        build_analysis_quality_event(future)
    assert build_analysis_quality_event(tenant_b).identity == "analysis-quality:tenant-b:an-1"


def test_partial_failure_and_retry_metadata_are_explicit():
    snapshot = _snapshot()
    snapshot["failure"] = {"stage": "retrieval", "reason": "timeout"}
    snapshot["retry"] = {"attempt": 2, "dedupe_key": "an-1"}

    event = build_analysis_quality_event(snapshot)

    assert event.payload["failure"] == {"stage": "retrieval", "reason": "timeout"}
    assert event.payload["retry"] == {"attempt": 2, "dedupe_key": "an-1"}


def test_analysis_quality_cannot_carry_outcome_or_gold_label_identity():
    outcome = _snapshot()
    outcome["outcome_id"] = "out-1"

    with pytest.raises(LearningEventError, match="outcome or gold label"):
        build_analysis_quality_event(outcome)
