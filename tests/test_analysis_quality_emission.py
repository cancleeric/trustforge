import pytest

from trustforge.analysis_quality_emission import (
    AnalysisQualityConflictError,
    AnalysisQualityEmissionError,
    emit_analysis_quality_event,
)
from trustforge.learning_event_contract import LearningEventError
from trustforge.learning_event_store import LearningEventAppendLog
from tests.test_analysis_quality_event import snapshot, trusted_pit, trusted_provenance


def emit(value, sink):
    return emit_analysis_quality_event(
        value,
        trusted_tenant_id="tenant-a",
        trusted_pit=trusted_pit(value),
        trusted_provenance=trusted_provenance(value),
        sink=sink,
    )


def test_first_emit_and_identical_redelivery_are_exactly_once():
    sink = LearningEventAppendLog()

    first = emit(snapshot(), sink)
    retry = emit(snapshot(), sink)

    assert first.status == "created"
    assert retry.status == "idempotent"
    assert first.identity == retry.identity
    assert len(sink.replay()) == 1


def test_same_identity_content_drift_fails_closed():
    sink = LearningEventAppendLog()
    emit(snapshot(), sink)
    drift = snapshot()
    drift["confidence"]["raw"] = 0.8

    with pytest.raises(LearningEventError, match="immutable"):
        emit(drift, sink)


@pytest.mark.parametrize(
    ("status", "error"),
    [
        ("conflict", AnalysisQualityConflictError),
        ("error", AnalysisQualityEmissionError),
        ("unknown", AnalysisQualityEmissionError),
    ],
)
def test_non_success_sink_status_never_reports_success(status, error):
    class Sink:
        def append(self, event):
            return status

    with pytest.raises(error):
        emit(snapshot(), Sink())


def test_append_exception_propagates_without_false_success():
    failure = RuntimeError("disk unavailable")

    class Sink:
        def append(self, event):
            raise failure

    with pytest.raises(RuntimeError) as raised:
        emit(snapshot(), Sink())
    assert raised.value is failure
