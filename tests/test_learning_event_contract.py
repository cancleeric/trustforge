import pytest

from trustforge.learning_event_contract import (
    LearningEventError,
    assert_append_only,
    deserialize_learning_event,
    make_learning_event,
    serialize_learning_event,
)


TIMES = {
    "event_time": "2026-07-01T00:00:00Z",
    "available_time": "2026-07-01T01:00:00Z",
    "as_of_time": "2026-07-01T01:00:00Z",
}
PROVENANCE = {"source": "fixture", "collector": "unit-test", "observed_at": "2026-07-01T01:00:00Z"}


def _event(kind, payload, identity=None):
    return make_learning_event(
        kind=kind,
        identity=identity or f"{kind}:1",
        provenance=PROVENANCE,
        payload=payload,
        **TIMES,
    )


@pytest.mark.parametrize(
    "kind,payload",
    [
        ("evidentiary", {"evidence_id": "ev-1", "claim": "btc up", "source_url": "https://example.test"}),
        ("historical_non_evidentiary", {"historical_answer_id": "hist-1", "question": "what happened"}),
        ("delayed_outcome", {"outcome_id": "out-1", "analysis_id": "an-1", "horizon": "T+7", "status": "pending"}),
        ("human_gold_label", {"label_id": "gold-1", "analysis_id": "an-1", "reviewer": "eric", "label": "good"}),
        ("candidate_diagnostic", {"diagnostic_id": "diag-1", "analysis_id": "an-1", "reason": "source drift"}),
    ],
)
def test_learning_event_kinds_round_trip_with_stable_serializer(kind, payload):
    event = _event(kind, payload)
    encoded = serialize_learning_event(event)

    assert deserialize_learning_event(encoded) == event
    assert serialize_learning_event(deserialize_learning_event(encoded)) == encoded


def test_unknown_schema_version_fails_closed():
    event = _event("evidentiary", {"evidence_id": "ev-1", "claim": "btc up", "source_url": "https://e.test"})
    encoded = serialize_learning_event(event).replace("learning-event.v1", "learning-event.v999")

    with pytest.raises(LearningEventError, match="schema version"):
        deserialize_learning_event(encoded)


@pytest.mark.parametrize(
    "kind,payload",
    [
        ("evidentiary", {"outcome_id": "out-1", "claim": "spoof", "source_url": "https://e.test"}),
        (
            "historical_non_evidentiary",
            {"historical_answer_id": "hist-1", "question": "q", "evidence_id": "ev-1"},
        ),
        ("delayed_outcome", {"outcome_id": "out-1", "analysis_id": "an-1", "horizon": "T+1", "status": "labeled", "evidence_id": "ev-1"}),
        ("human_gold_label", {"label_id": "gold-1", "analysis_id": "an-1", "reviewer": "eric", "label": "ok", "outcome_id": "out-1"}),
        ("candidate_diagnostic", {"diagnostic_id": "diag-1", "analysis_id": "an-1", "reason": "r", "activation": "approve"}),
    ],
)
def test_cross_classification_spoofing_is_rejected(kind, payload):
    with pytest.raises(LearningEventError):
        _event(kind, payload)


def test_identity_provenance_and_time_are_required_and_point_in_time_safe():
    with pytest.raises(LearningEventError, match="identity"):
        make_learning_event(
            kind="evidentiary",
            identity=" ",
            provenance=PROVENANCE,
            payload={"evidence_id": "ev-1", "claim": "btc up", "source_url": "https://e.test"},
            **TIMES,
        )
    with pytest.raises(LearningEventError, match="provenance.source"):
        make_learning_event(
            kind="evidentiary",
            identity="ev:1",
            provenance={"collector": "unit-test", "observed_at": "2026-07-01T01:00:00Z"},
            payload={"evidence_id": "ev-1", "claim": "btc up", "source_url": "https://e.test"},
            **TIMES,
        )
    with pytest.raises(LearningEventError, match="available_time"):
        make_learning_event(
            kind="evidentiary",
            identity="ev:1",
            provenance=PROVENANCE,
            payload={"evidence_id": "ev-1", "claim": "btc up", "source_url": "https://e.test"},
            event_time="2026-07-01T00:00:00Z",
            available_time="2026-06-30T23:59:59Z",
            as_of_time="2026-07-01T01:00:00Z",
        )


def test_append_only_contract_rejects_in_place_rewrite_but_allows_revision_identity():
    original = _event(
        "delayed_outcome",
        {"outcome_id": "out-1", "analysis_id": "an-1", "horizon": "T+1", "status": "pending"},
        identity="outcome:an-1:T+1:v1",
    )
    rewritten = _event(
        "delayed_outcome",
        {"outcome_id": "out-1", "analysis_id": "an-1", "horizon": "T+1", "status": "labeled"},
        identity="outcome:an-1:T+1:v1",
    )
    revised = _event(
        "delayed_outcome",
        {"outcome_id": "out-1", "analysis_id": "an-1", "horizon": "T+1", "status": "labeled"},
        identity="outcome:an-1:T+1:v2",
    )

    with pytest.raises(LearningEventError, match="immutable"):
        assert_append_only(original, rewritten)
    assert_append_only(original, revised)
