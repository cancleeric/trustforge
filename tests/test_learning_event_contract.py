import json

import pytest

from trustforge.learning_event_contract import (
    LearningEvent,
    LearningEventError,
    assert_append_only,
    canonical_identity,
    deserialize_learning_event,
    make_learning_event,
    provenance_checksum,
    serialize_learning_event,
)


TIMES = {
    "event_time": "2026-07-01T08:00:00+08:00",
    "available_time": "2026-07-01T01:00:00Z",
    "as_of_time": "2026-07-01T01:00:00.000000Z",
}


def _provenance(tenant_id="tenant-a", observed_at="2026-07-01T01:00:00.000000Z"):
    source_record = {"record_id": "fixture-1", "sequence": 1}
    return {
        "source": "fixture",
        "collector": "unit-test",
        "observed_at": observed_at,
        "tenant_id": tenant_id,
        "source_record": source_record,
        "version": "fixture.v1",
        "checksum": provenance_checksum(source_record),
    }


def _event(kind, payload, *, tenant_id="tenant-a", entity_id=None, revision=1, identity=None):
    return make_learning_event(
        kind=kind,
        tenant_id=tenant_id,
        entity_id=entity_id or f"{kind}:entity/1",
        revision=revision,
        identity=identity,
        provenance=_provenance(tenant_id),
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
def test_all_five_kinds_round_trip_with_stable_canonical_serializer(kind, payload):
    event = _event(kind, payload)
    encoded = serialize_learning_event(event)
    replayed = deserialize_learning_event(encoded)

    assert replayed == event
    assert serialize_learning_event(replayed) == encoded
    assert event.event_time == "2026-07-01T00:00:00.000000Z"
    assert event.identity == canonical_identity(
        tenant_id=event.tenant_id,
        kind=event.kind,
        entity_id=event.entity_id,
        revision=event.revision,
    )


def test_identity_contains_tenant_kind_entity_revision_and_rejects_caller_spoof():
    first = _event("evidentiary", {"evidence_id": "ev-1", "claim": "c", "source_url": "https://e.test"})
    other_tenant = _event(
        "evidentiary",
        {"evidence_id": "ev-1", "claim": "c", "source_url": "https://e.test"},
        tenant_id="tenant-b",
    )
    assert first.identity != other_tenant.identity
    assert "%2F" in first.identity
    with pytest.raises(LearningEventError, match="canonical identity"):
        _event(
            "evidentiary",
            {"evidence_id": "ev-1", "claim": "c", "source_url": "https://e.test"},
            identity=other_tenant.identity,
        )


@pytest.mark.parametrize(
    "kind,payload",
    [
        ("evidentiary", {"outcome_id": "out-1", "claim": "spoof", "source_url": "https://e.test"}),
        ("historical_non_evidentiary", {"historical_answer_id": "hist-1", "question": "q", "evidence_id": "ev-1"}),
        ("delayed_outcome", {"outcome_id": "out-1", "analysis_id": "an-1", "horizon": "T+1", "status": "labeled", "evidence_id": "ev-1"}),
        ("human_gold_label", {"label_id": "gold-1", "analysis_id": "an-1", "reviewer": "eric", "label": "ok", "outcome_id": "out-1"}),
        ("candidate_diagnostic", {"diagnostic_id": "diag-1", "analysis_id": "an-1", "reason": "r", "activation": "approve"}),
    ],
)
def test_complete_discriminator_matrix_rejects_cross_classification(kind, payload):
    with pytest.raises(LearningEventError):
        _event(kind, payload)


def test_point_in_time_boundaries_and_provenance_binding_fail_closed():
    payload = {"evidence_id": "ev-1", "claim": "c", "source_url": "https://e.test"}
    with pytest.raises(LearningEventError, match="available_time cannot follow"):
        make_learning_event(
            kind="evidentiary",
            tenant_id="tenant-a",
            entity_id="ev-1",
            revision=1,
            provenance=_provenance(),
            payload=payload,
            event_time="2026-07-01T00:00:00Z",
            available_time="2026-07-01T02:00:00Z",
            as_of_time="2026-07-01T01:00:00Z",
        )
    with pytest.raises(LearningEventError, match="observed_at cannot follow"):
        make_learning_event(
            kind="evidentiary",
            tenant_id="tenant-a",
            entity_id="ev-1",
            revision=1,
            provenance=_provenance(observed_at="2026-07-01T02:00:00Z"),
            payload=payload,
            event_time="2026-07-01T00:00:00Z",
            available_time="2026-07-01T01:00:00Z",
            as_of_time="2026-07-01T01:00:00Z",
        )
    with pytest.raises(LearningEventError, match="tenant_id must match"):
        make_learning_event(
            kind="evidentiary",
            tenant_id="tenant-a",
            entity_id="ev-1",
            revision=1,
            provenance=_provenance("tenant-b"),
            payload=payload,
            **TIMES,
        )


def test_checksum_is_over_canonical_source_record_bytes():
    provenance = _provenance()
    provenance["source_record"] = {"sequence": 2}
    with pytest.raises(LearningEventError, match="checksum"):
        make_learning_event(
            kind="evidentiary",
            tenant_id="tenant-a",
            entity_id="ev-1",
            revision=1,
            provenance=provenance,
            payload={"evidence_id": "ev-1", "claim": "c", "source_url": "https://e.test"},
            **TIMES,
        )


def test_deserializer_rejects_legacy_unknown_duplicate_and_non_finite_json():
    event = _event("evidentiary", {"evidence_id": "ev-1", "claim": "c", "source_url": "https://e.test"})
    value = json.loads(serialize_learning_event(event))

    legacy = {key: item for key, item in value.items() if key not in {"tenant_id", "entity_id", "revision"}}
    with pytest.raises(LearningEventError, match="missing learning event fields"):
        deserialize_learning_event(json.dumps(legacy))

    value["unexpected"] = True
    with pytest.raises(LearningEventError, match="unknown learning event fields"):
        deserialize_learning_event(json.dumps(value))

    encoded = serialize_learning_event(event)
    with pytest.raises(LearningEventError, match="JSON is invalid"):
        deserialize_learning_event(encoded[:-1] + ',"kind":"evidentiary"}')
    with pytest.raises(LearningEventError, match="JSON is invalid"):
        deserialize_learning_event(encoded.replace('"revision":1', '"revision":NaN'))


def test_deserializer_rejects_noncanonical_timezone_and_identity_rewrite():
    event = _event("evidentiary", {"evidence_id": "ev-1", "claim": "c", "source_url": "https://e.test"})
    value = json.loads(serialize_learning_event(event))
    value["event_time"] = "2026-07-01T08:00:00+08:00"
    with pytest.raises(LearningEventError, match="canonical UTC"):
        deserialize_learning_event(json.dumps(value))

    value = json.loads(serialize_learning_event(event))
    value["provenance"]["observed_at"] = "2026-07-01T09:00:00+08:00"
    with pytest.raises(LearningEventError, match="provenance.observed_at must use canonical UTC"):
        deserialize_learning_event(json.dumps(value))

    value = json.loads(serialize_learning_event(event))
    value["identity"] = "spoof"
    with pytest.raises(LearningEventError, match="canonical identity"):
        deserialize_learning_event(json.dumps(value))


def test_event_is_deeply_immutable_and_serializer_order_is_stable():
    payload = {
        "evidence_id": "ev-1",
        "claim": "c",
        "source_url": "https://e.test",
        "nested": {"items": [1, 2]},
    }
    event = _event("evidentiary", payload)
    payload["nested"]["items"].append(3)
    assert tuple(event.payload["nested"]["items"]) == (1, 2)
    with pytest.raises(TypeError):
        event.payload["nested"]["new"] = True

    shuffled = json.loads(serialize_learning_event(event))
    raw = json.dumps(dict(reversed(list(shuffled.items()))), ensure_ascii=False)
    assert serialize_learning_event(deserialize_learning_event(raw)) == serialize_learning_event(event)


def test_public_constructor_cannot_bypass_deep_freeze_or_validation():
    factory_event = _event(
        "evidentiary",
        {"evidence_id": "ev-1", "claim": "c", "source_url": "https://e.test"},
    )
    mutable_payload = {"evidence_id": "ev-1", "claim": "c", "source_url": "https://e.test", "nested": []}
    direct = LearningEvent(
        schema_version=factory_event.schema_version,
        kind=factory_event.kind,
        tenant_id=factory_event.tenant_id,
        entity_id=factory_event.entity_id,
        revision=factory_event.revision,
        identity=factory_event.identity,
        event_time=factory_event.event_time,
        available_time=factory_event.available_time,
        as_of_time=factory_event.as_of_time,
        provenance=factory_event.provenance,
        payload=mutable_payload,
    )
    mutable_payload["nested"].append("mutated")
    assert direct.payload["nested"] == ()
    with pytest.raises(LearningEventError, match="canonical identity"):
        LearningEvent(
            schema_version=direct.schema_version,
            kind=direct.kind,
            tenant_id=direct.tenant_id,
            entity_id=direct.entity_id,
            revision=direct.revision,
            identity="spoof",
            event_time=direct.event_time,
            available_time=direct.available_time,
            as_of_time=direct.as_of_time,
            provenance=direct.provenance,
            payload=direct.payload,
        )


def test_append_only_rejects_same_revision_rewrite_and_allows_new_revision():
    original = _event(
        "delayed_outcome",
        {"outcome_id": "out-1", "analysis_id": "an-1", "horizon": "T+1", "status": "pending"},
        entity_id="outcome:an-1:T+1",
    )
    rewritten = _event(
        "delayed_outcome",
        {"outcome_id": "out-1", "analysis_id": "an-1", "horizon": "T+1", "status": "labeled"},
        entity_id="outcome:an-1:T+1",
    )
    revised = _event(
        "delayed_outcome",
        {"outcome_id": "out-1", "analysis_id": "an-1", "horizon": "T+1", "status": "labeled"},
        entity_id="outcome:an-1:T+1",
        revision=2,
    )
    with pytest.raises(LearningEventError, match="immutable"):
        assert_append_only(original, rewritten)
    assert_append_only(original, revised)
