import pytest

from trustforge.learning_event_contract import LearningEventError, make_learning_event, serialize_learning_event
from trustforge.learning_event_store import LearningEventAppendLog, plan_learning_event_migration


TIMES = {
    "event_time": "2026-07-01T00:00:00Z",
    "available_time": "2026-07-01T01:00:00Z",
    "as_of_time": "2026-07-01T01:00:00Z",
}
PROVENANCE = {"source": "fixture", "collector": "storage-test", "observed_at": "2026-07-01T01:00:00Z"}


def _outcome(identity="outcome:an-1:T+1:v1", status="pending"):
    return make_learning_event(
        kind="delayed_outcome",
        identity=identity,
        provenance=PROVENANCE,
        payload={"outcome_id": "out-1", "analysis_id": "an-1", "horizon": "T+1", "status": status},
        **TIMES,
    )


def _evidence(identity="evidence:1"):
    return make_learning_event(
        kind="evidentiary",
        identity=identity,
        provenance=PROVENANCE,
        payload={"evidence_id": "ev-1", "claim": "btc up", "source_url": "https://example.test"},
        **TIMES,
    )


def test_append_log_is_idempotent_and_replay_stable():
    log = LearningEventAppendLog()
    event = _evidence()

    assert log.append(event) == "created"
    assert log.append(event) == "idempotent"
    assert log.replay() == [event]
    assert log.snapshot() == (serialize_learning_event(event),)


def test_append_log_rejects_in_place_rewrite_but_allows_revision_identity():
    log = LearningEventAppendLog()
    original = _outcome(status="pending")
    rewritten = _outcome(status="labeled")
    revision = _outcome(identity="outcome:an-1:T+1:v2", status="labeled")

    assert log.append(original) == "created"
    with pytest.raises(LearningEventError, match="immutable"):
        log.append(rewritten)
    assert log.append(revision) == "created"


def test_migration_plan_dry_run_validates_without_writes_and_replays_duplicates():
    event = serialize_learning_event(_evidence())
    report = plan_learning_event_migration([event, event], dry_run=True)

    assert report["status"] == "ready"
    assert report["dry_run"] is True
    assert report["will_write"] is False
    assert [item["result"] for item in report["results"]] == ["created", "idempotent"]


def test_migration_plan_unknown_schema_fails_closed():
    raw = serialize_learning_event(_evidence()).replace("learning-event.v1", "learning-event.v999")

    report = plan_learning_event_migration([raw], dry_run=True)

    assert report["status"] == "blocked"
    assert report["will_write"] is False
    assert "schema version" in report["reason"]


def test_outcome_cannot_be_migrated_as_evidence():
    raw = serialize_learning_event(_outcome()).replace("delayed_outcome", "evidentiary")

    report = plan_learning_event_migration([raw], dry_run=True)

    assert report["status"] == "blocked"
    assert report["will_write"] is False
