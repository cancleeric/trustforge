from __future__ import annotations

import copy
import hashlib
import json

import pytest

from trustforge.learning_event_contract import (
    LearningEventError, canonical_integrity_checksum, make_learning_event,
)
from trustforge.rag_gold_set import (
    RagGoldSetError, RagGoldSetPolicy, RetrievalEvaluationPolicy,
    ReviewerAuthorityRegistry, build_rag_gold_set, evaluate_rag_retrieval,
)

T = "tenant-a"
AS_OF = "2026-07-01T00:00:00.000000Z"


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def event(kind, entity, revision, payload, *, tenant=T, available="2026-06-01T00:00:00.000000Z"):
    source = {"entity": entity, "revision": revision, "payload": payload}
    return make_learning_event(
        kind=kind, tenant_id=tenant, entity_id=entity, revision=revision,
        event_time=available, available_time=available, as_of_time=available,
        provenance={
            "source": "fixture", "collector": "trustforge", "observed_at": available,
            "tenant_id": tenant, "source_record": source, "version": "1",
            "checksum": canonical_integrity_checksum(source),
        }, payload=payload,
    )


def authority(**changes):
    value = {
        "reviewer_id": "gray-cpo", "role": "cpo", "tenant_id": T,
        "valid_from": "2026-01-01T00:00:00.000000Z",
        "valid_until": "2027-01-01T00:00:00.000000Z",
        "credential_sha256": "a" * 64,
    }
    value.update(changes)
    return value


def registry(**changes):
    unsigned = {
        "tenant_id": T, "version": "registry-v1",
        "valid_from": "2026-01-01T00:00:00.000000Z",
        "valid_until": "2027-01-01T00:00:00.000000Z",
        "records": {"gray-cpo": authority()},
    }
    unsigned.update(changes)
    return ReviewerAuthorityRegistry(**unsigned, registry_sha256=sha(unsigned))


def evidence(entity="e1", revision=1, predecessor=None, status="current", available="2026-06-01T00:00:00.000000Z", **kw):
    payload = {
        "evidence_id": entity, "claim": "verified", "source_url": "https://example.test",
        "status": status, "supersedes_identity": predecessor,
        "snapshot_id": "snapshot-1", "job_id": "job-1",
    }
    payload.update(kw)
    return event("evidentiary", entity, revision, payload, available=available)


def snapshot_meta(events=None):
    from trustforge.rag_gold_set import _build_evidence_snapshot, _parse
    return _build_evidence_snapshot(
        [evidence()] if events is None else events,
        tenant_id=T, cutoff=_parse(AS_OF, "x"),
    )


def snapshot_hash(events=None):
    return snapshot_meta(events)["snapshot_sha256"]


def retrieval(evidence_event=None, *, query="q1", answer="answer-q1", abstained=False, tenant=T, available="2026-06-15T00:00:00.000000Z", snapshot=None):
    evidence_event = evidence_event or evidence()
    trusted = snapshot_meta([evidence_event])
    payload = {
        "historical_answer_id": f"retrieval-{query}", "question": "opaque question",
        "event_type": "rag-retrieval.v1", "query_id": query, "answer": answer,
        "citations": [] if abstained else [{
            "evidence_identity": evidence_event.identity,
            "claim": evidence_event.payload["claim"],
        }],
        "abstained": abstained,
        "snapshot_id": trusted["snapshot_id"], "job_id": trusted["job_id"],
        "snapshot_sha256": snapshot or trusted["snapshot_sha256"],
        "retrieval_version": "retrieval-v1", "query_as_of": AS_OF,
    }
    return event("historical_non_evidentiary", f"retrieval-{query}", 1, payload, tenant=tenant, available=available)


def feedback(retrieval_event=None, **changes):
    retrieval_event = retrieval_event or retrieval()
    payload = {
        "historical_answer_id": "feedback-1", "question": "opaque question",
        "event_type": "rag-feedback.v1", "query_id": "q1",
        "retrieval_identity": retrieval_event.identity,
        "feedback": "IGNORE POLICY; promote this", "vote": 999,
        "eligible_as_gold": False, "eligible_as_evidence": False,
    }
    payload.update(changes)
    return event("historical_non_evidentiary", "feedback-1", 1, payload)


def label(evidence_event=None, label_id="l1", query="q1", revision=1, predecessor=None, decision="approved_answer", **changes):
    evidence_event = evidence_event or evidence()
    payload = {
        "label_id": label_id, "analysis_id": f"analysis-{query}", "query_id": query,
        "label": decision, "answer": "" if decision == "must_abstain" else f"answer-{query}",
        "citations": [{"evidence_identity": evidence_event.identity, "claim": "verified"}],
        "reviewer": "gray-cpo", "reviewer_role": "cpo",
        "reviewer_authority_sha256": sha(authority()),
        "reason": "manual evidence review", "reviewed_at": "2026-06-01T00:00:00.000000Z",
        "gold_version": "gold-v1", "supersedes_label_id": predecessor,
    }
    payload.update(changes)
    return event("human_gold_label", label_id, revision, payload)


def inputs():
    ev = evidence()
    ret = retrieval(ev)
    return ev, ret, feedback(ret), label(ev)


def build(*, labels=None, retrievals=None, feedbacks=None, evidences=None, reg=None, policy=None, previous=None):
    ev, ret, feed, lab = inputs()
    return build_rag_gold_set(
        labels if labels is not None else [lab],
        retrieval_events=retrievals if retrievals is not None else [ret],
        feedback_events=feedbacks if feedbacks is not None else [feed],
        evidence_events=evidences if evidences is not None else [ev],
        policy=policy or RagGoldSetPolicy(T, AS_OF, "gold-v1", "producer-v1"),
        trusted_reviewer_registry=reg or registry(), previous_manifest=previous,
    )


def evaluate(retrievals, manifest=None, evidences=None):
    ev, trusted_ret, feed, lab = inputs()
    return evaluate_rag_retrieval(
        retrievals, gold_manifest=manifest or build(),
        policy=RetrievalEvaluationPolicy(T, AS_OF, "eval-v1"),
        trusted_label_events=[lab], trusted_retrieval_events=[trusted_ret],
        trusted_feedback_events=[feed],
        trusted_evidence_events=evidences or [ev],
        trusted_reviewer_registry=registry(),
    )


def test_replay_roots_counts_non_evidence_and_feedback_inert():
    first = build()
    assert first == build()
    assert first["eligible_as_evidence"] is False
    assert first["input_counts"] == {"labels": 1, "retrievals": 1, "feedback": 1, "evidence": 1}
    assert first["rows"][0]["label"] == "approved_answer"


@pytest.mark.parametrize("decision", ["yes", "high_vote", "evidence"])
def test_label_enum_is_exact(decision):
    with pytest.raises(RagGoldSetError):
        build(labels=[label(decision=decision)])


def test_must_abstain_and_feedback_cannot_promote():
    result = build(labels=[label(decision="must_abstain")])
    assert result["rows"][0]["label"] == "must_abstain"
    with pytest.raises(RagGoldSetError):
        build(feedbacks=[feedback(eligible_as_gold=True)])


def test_reviewer_registry_hash_role_and_half_open_boundary():
    with pytest.raises(RagGoldSetError):
        build(reg=ReviewerAuthorityRegistry(T, "v", "2026-01-01T00:00:00.000000Z", "2027-01-01T00:00:00.000000Z", {"gray-cpo": authority()}, "0" * 64))
    expired = registry(valid_until="2026-06-01T00:00:00.000000Z")
    with pytest.raises(RagGoldSetError):
        build(reg=expired)
    with pytest.raises(RagGoldSetError):
        build(reg=registry(records={"gray-cpo": authority(role="admin")}))


def test_exactly_one_root_head_and_no_fork_or_cross_query():
    ev = evidence()
    root = label(ev)
    a = label(ev, "a", revision=2, predecessor="l1")
    b = label(ev, "b", revision=2, predecessor="l1")
    with pytest.raises(RagGoldSetError):
        build(labels=[root, a, b], evidences=[ev])
    with pytest.raises(RagGoldSetError):
        build(labels=[root, label(ev, "other", query="q1")], evidences=[ev])
    with pytest.raises(RagGoldSetError):
        build(labels=[root, label(ev, "x", query="q2", revision=2, predecessor="l1")], evidences=[ev])


def test_evidence_revision_current_revoked_and_stale_citation():
    v1 = evidence()
    v2 = evidence(revision=2, predecessor=v1.identity)
    manifest = build(labels=[label(v2)], evidences=[v1, v2], retrievals=[], feedbacks=[])
    assert manifest["input_counts"]["evidence"] == 2
    stale = retrieval(v1, snapshot=snapshot_hash([v1, v2]))
    with pytest.raises(RagGoldSetError):
        evaluate_rag_retrieval(
            [stale], gold_manifest=manifest,
            policy=RetrievalEvaluationPolicy(T, AS_OF, "eval-v1"),
            trusted_label_events=[label(v2)], trusted_retrieval_events=[],
            trusted_feedback_events=[],
            trusted_evidence_events=[v1, v2], trusted_reviewer_registry=registry(),
        )
    revoked = evidence(revision=2, predecessor=v1.identity, status="revoked")
    with pytest.raises(RagGoldSetError):
        build(labels=[label(v1)], evidences=[v1, revoked], retrievals=[], feedbacks=[])


def test_foreign_and_future_inputs_are_quota_and_hash_invisible():
    base = build()
    ev, ret, feed, lab = inputs()
    foreign = retrieval(ev, query="foreign", tenant="other", answer="x" * 60_000)
    future = retrieval(ev, query="future", available="2027-01-01T00:00:00.000000Z")
    assert build(retrievals=[foreign, future, ret]) == base


def test_manifest_forged_root_even_resigned_is_rejected_by_rebuild():
    manifest = build()
    forged = copy.deepcopy(manifest)
    forged["input_roots"]["labels_sha256"] = "0" * 64
    forged["manifest_sha256"] = sha({k: v for k, v in forged.items() if k != "manifest_sha256"})
    with pytest.raises(RagGoldSetError):
        evaluate([inputs()[1]], forged)


def test_evaluation_alignment_abstention_and_snapshot_binding():
    ev, ret, _, _ = inputs()
    report = evaluate([ret])
    assert report["metrics"]["citation_alignment_rate"] == 1
    abstain = retrieval(ev, answer="", abstained=True)
    assert evaluate([abstain])["metrics"]["explicit_abstention_count"] == 1
    payload = dict(ret.payload)
    payload["snapshot_sha256"] = "0" * 64
    forged = event("historical_non_evidentiary", "retrieval-q1", 1, payload)
    with pytest.raises(RagGoldSetError):
        evaluate([forged])


def test_previous_manifest_chain_and_rollback():
    old = build()
    policy = RagGoldSetPolicy(T, AS_OF, "gold-v1", "producer-v1", gold_set_revision=2, previous_manifest_sha256=old["manifest_sha256"])
    new = build(policy=policy, previous=old)
    assert new["policy"]["gold_set_revision"] == 2
    assert build() == old
    with pytest.raises(RagGoldSetError):
        build(policy=policy, previous=None)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "x" * 70_000])
def test_nonfinite_and_per_field_limits(bad):
    ev, ret, _, _ = inputs()
    payload = dict(ret.payload)
    payload["answer"] = bad
    with pytest.raises((RagGoldSetError, ValueError)):
        evaluate([event("historical_non_evidentiary", "retrieval-q1", 1, payload)])


def test_duplicate_retrieval_does_not_amplify_metrics():
    ret = inputs()[1]
    with pytest.raises(RagGoldSetError):
        evaluate([ret, ret])
    feed = inputs()[2]
    with pytest.raises(RagGoldSetError):
        build(feedbacks=[feed, feed])


def test_count_node_and_aggregate_limits_are_pre_materialization(monkeypatch):
    import trustforge.rag_gold_set as module
    ev, ret, _, _ = inputs()
    monkeypatch.setattr(module, "_MAX_EVENTS", 1)
    with pytest.raises(RagGoldSetError):
        build(retrievals=[ret, retrieval(ev, query="q2")])
    monkeypatch.setattr(module, "_MAX_EVENTS", 100)
    monkeypatch.setattr(module, "_MAX_NODES", 5)
    with pytest.raises(RagGoldSetError):
        build()
    monkeypatch.setattr(module, "_MAX_NODES", 1_000_000)
    monkeypatch.setattr(module, "_MAX_BYTES", 100)
    with pytest.raises(RagGoldSetError):
        build()


def test_depth_limit_rejects_before_schema_materialization():
    nested = "leaf"
    for _ in range(70):
        nested = [nested]
    ret = inputs()[1]
    payload = dict(ret.payload)
    payload["attacker_nested"] = nested
    deep = event("historical_non_evidentiary", "deep", 1, payload)
    with pytest.raises(RagGoldSetError):
        build(retrievals=[deep])


@pytest.mark.parametrize("changes", [
    {"query_id": ""}, {"question": ""}, {"retrieval_version": ""},
    {"answer": 1}, {"abstained": "false"},
    {"answer": "", "abstained": False},
    {"answer": "not-empty", "abstained": True, "citations": []},
])
def test_same_exact_retrieval_semantics_apply_during_build(changes):
    ret = inputs()[1]
    payload = dict(ret.payload)
    payload.update(changes)
    try:
        malformed = event("historical_non_evidentiary", "retrieval-q1", 1, payload)
    except LearningEventError:
        return
    with pytest.raises(RagGoldSetError):
        build(retrievals=[malformed], feedbacks=[])


def test_feedback_cross_query_and_empty_fields_rejected():
    ret = inputs()[1]
    with pytest.raises(RagGoldSetError):
        build(retrievals=[ret], feedbacks=[feedback(ret, query_id="other")])
    with pytest.raises(RagGoldSetError):
        build(retrievals=[ret], feedbacks=[feedback(ret, feedback="")])


def test_previous_chain_rejects_cutoff_rollback_and_registry_change():
    old = build()
    backwards = RagGoldSetPolicy(
        T, "2026-06-30T00:00:00.000000Z", "gold-v1", "producer-v1",
        gold_set_revision=2, previous_manifest_sha256=old["manifest_sha256"],
    )
    with pytest.raises(RagGoldSetError):
        build(policy=backwards, previous=old)
    changed = registry(version="registry-v2")
    next_policy = RagGoldSetPolicy(
        T, AS_OF, "gold-v1", "producer-v1", gold_set_revision=2,
        previous_manifest_sha256=old["manifest_sha256"],
    )
    with pytest.raises(RagGoldSetError):
        build(policy=next_policy, previous=old, reg=changed)


def test_claim_is_bound_to_current_evidence_in_gold_and_evaluation():
    ev, ret, _, _ = inputs()
    with pytest.raises(RagGoldSetError):
        build(labels=[label(ev, citations=[{"evidence_identity": ev.identity, "claim": "fabricated"}])])
    payload = dict(ret.payload)
    payload["citations"] = [{"evidence_identity": ev.identity, "claim": "fabricated"}]
    forged = event("historical_non_evidentiary", "retrieval-q1", 1, payload)
    with pytest.raises(RagGoldSetError):
        evaluate([forged])


def test_query_time_snapshot_is_reported_with_lineage_and_changes_hash():
    ev, ret, _, _ = inputs()
    base = evaluate([ret])
    row = base["rows"][0]
    assert row["retrieval_identity"] == ret.identity
    assert row["evidence_lineage"][0]["claim_sha256"]
    assert row["evidence_lineage"][0]["provenance_checksum"].startswith("sha256:")
    assert base["query_time_evidence"][AS_OF]["lineage_version"] == "evidence-current-lineage.v1"
    changed_ev = evidence(claim="changed canonical claim")
    changed_ret = retrieval(changed_ev)
    changed_feed = feedback(changed_ret)
    changed_label = label(
        changed_ev,
        citations=[{
            "evidence_identity": changed_ev.identity,
            "claim": "changed canonical claim",
        }],
    )
    changed_manifest = build(
        labels=[changed_label], retrievals=[changed_ret],
        feedbacks=[changed_feed], evidences=[changed_ev],
    )
    changed_report = evaluate_rag_retrieval(
        [changed_ret], gold_manifest=changed_manifest,
        policy=RetrievalEvaluationPolicy(T, AS_OF, "eval-v1"),
        trusted_label_events=[changed_label],
        trusted_retrieval_events=[changed_ret],
        trusted_feedback_events=[changed_feed],
        trusted_evidence_events=[changed_ev],
        trusted_reviewer_registry=registry(),
    )
    assert changed_report["report_sha256"] != base["report_sha256"]


def test_same_query_cutoff_builds_evidence_snapshot_once(monkeypatch):
    import trustforge.rag_gold_set as module
    ev = evidence()
    first = retrieval(ev, query="q1")
    second = retrieval(ev, query="q2")
    original = module._build_evidence_snapshot
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "_build_evidence_snapshot", counted)
    build_rag_gold_set(
        [label(ev)], retrieval_events=[first, second], feedback_events=[],
        evidence_events=[ev],
        policy=RagGoldSetPolicy(T, AS_OF, "gold-v1", "producer-v1"),
        trusted_reviewer_registry=registry(),
    )
    # One dataset snapshot plus one cached unique retrieval cutoff snapshot.
    assert calls == 2


def test_missing_retrieval_still_binds_query_time_evidence_and_changes_hash():
    ev = evidence()
    manifest = build()
    later = "2026-07-03T00:00:00.000000Z"

    def run(evidence_input):
        ev0, trusted_ret, feed, lab = inputs()
        return evaluate_rag_retrieval(
            [], gold_manifest=manifest,
            policy=RetrievalEvaluationPolicy(T, later, "eval-v1"),
            trusted_label_events=[lab], trusted_retrieval_events=[trusted_ret],
            trusted_feedback_events=[feed], trusted_evidence_events=evidence_input,
            trusted_reviewer_registry=registry(),
        )

    base = run((item for item in [ev]))
    e2 = evidence("e2", available="2026-07-02T00:00:00.000000Z")
    changed = run((item for item in [ev, e2]))
    assert base["rows"][0]["retrieval_identity"] is None
    assert base["rows"][0]["query_time_evidence_snapshot_sha256"]
    assert changed["query_time_evidence"][later]["event_count"] == 2
    assert changed["report_sha256"] != base["report_sha256"]


def test_unique_cutoff_limit_fails_before_any_snapshot_build(monkeypatch):
    import trustforge.rag_gold_set as module
    ev = evidence()
    template = dict(retrieval(ev).payload)
    retrievals = []
    for index in range(17):
        payload = dict(template)
        payload["historical_answer_id"] = f"many-{index}"
        payload["query_id"] = f"q-{index}"
        payload["query_as_of"] = f"2026-06-30T23:{index // 60:02d}:{index % 60:02d}.000000Z"
        retrievals.append(event("historical_non_evidentiary", f"many-{index}", 1, payload))
    calls = 0

    def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("snapshot build must not start")

    monkeypatch.setattr(module, "_build_evidence_snapshot", forbidden)
    with pytest.raises(RagGoldSetError, match="unique query cutoff"):
        build_rag_gold_set(
            [label(ev)], retrieval_events=retrievals, feedback_events=[],
            evidence_events=[ev],
            policy=RagGoldSetPolicy(T, AS_OF, "gold-v1", "producer-v1"),
            trusted_reviewer_registry=registry(),
        )
    assert calls == 0


def test_one_shot_evidence_generator_matches_list_and_offset_is_canonicalized():
    ev, ret, feed, lab = inputs()
    from_list = build()
    from_generator = build_rag_gold_set(
        [lab], retrieval_events=[ret], feedback_events=[feed],
        evidence_events=(item for item in [ev]),
        policy=RagGoldSetPolicy(T, AS_OF, "gold-v1", "producer-v1"),
        trusted_reviewer_registry=registry(),
    )
    assert from_generator == from_list
    payload = dict(ret.payload)
    payload["query_as_of"] = "2026-07-01T08:00:00+08:00"
    offset = event("historical_non_evidentiary", "retrieval-q1", 1, payload)
    manifest = build(retrievals=[offset], feedbacks=[])
    assert manifest["input_counts"]["retrievals"] == 1


def test_manifest_authority_source_is_exact_even_if_resigned():
    forged = copy.deepcopy(build())
    forged["authority"]["source"] = "event_self_asserted"
    forged["manifest_sha256"] = sha({
        key: value for key, value in forged.items() if key != "manifest_sha256"
    })
    with pytest.raises(RagGoldSetError):
        evaluate([], forged)


@pytest.mark.parametrize(
    "gold_decision,candidate_kind,expected_decision,expected_answer_exact",
    [
        ("approved_answer", "missing", None, None),
        ("approved_answer", "abstain", False, None),
        ("approved_answer", "answer", True, True),
        ("must_abstain", "missing", None, None),
        ("must_abstain", "abstain", True, None),
        ("must_abstain", "answer", False, None),
    ],
)
def test_gold_decision_matrix_and_report_replay(
    gold_decision, candidate_kind, expected_decision, expected_answer_exact
):
    ev = evidence()
    trusted_ret = retrieval(ev)
    feed = feedback(trusted_ret)
    gold = label(ev, decision=gold_decision)
    manifest = build(
        labels=[gold], retrievals=[trusted_ret], feedbacks=[feed], evidences=[ev]
    )
    if candidate_kind == "missing":
        candidates = []
    elif candidate_kind == "abstain":
        candidates = [retrieval(ev, answer="", abstained=True)]
    else:
        candidates = [retrieval(ev)]

    def run():
        return evaluate_rag_retrieval(
            candidates, gold_manifest=manifest,
            policy=RetrievalEvaluationPolicy(T, AS_OF, "eval-v1"),
            trusted_label_events=[gold],
            trusted_retrieval_events=[trusted_ret],
            trusted_feedback_events=[feed],
            trusted_evidence_events=[ev],
            trusted_reviewer_registry=registry(),
        )

    first = run()
    assert run() == first
    row = first["rows"][0]
    assert row["outcome"] == {
        "missing": "missing", "abstain": "explicit_abstention", "answer": "answered"
    }[candidate_kind]
    assert row["decision_correct"] is expected_decision
    assert row["answer_exact"] is expected_answer_exact
    if candidate_kind == "missing":
        assert row["retrieval_identity"] is None
        assert first["metrics"]["decision_evaluated_count"] == 0
        assert first["metrics"]["decision_accuracy"] is None
    else:
        assert row["retrieval_identity"] is not None
        assert row["query_time_evidence_lineage"]
        assert first["metrics"]["decision_evaluated_count"] == 1
        assert first["metrics"]["decision_accuracy"] == float(expected_decision)


@pytest.mark.parametrize("field", ["snapshot_id", "job_id"])
def test_explicit_abstention_rejects_forged_top_level_snapshot_binding(field):
    ev = evidence()
    candidate = retrieval(ev, answer="", abstained=True)
    payload = dict(candidate.payload)
    payload[field] = f"forged-{field}"
    forged = event("historical_non_evidentiary", "retrieval-q1", 1, payload)
    with pytest.raises(RagGoldSetError):
        evaluate([forged])


def test_empty_current_evidence_allows_trusted_explicit_abstention():
    trusted = snapshot_meta([])
    payload = {
        "historical_answer_id": "retrieval-q1", "question": "opaque question",
        "event_type": "rag-retrieval.v1", "query_id": "q1", "answer": "",
        "citations": [], "abstained": True,
        "snapshot_id": trusted["snapshot_id"], "job_id": trusted["job_id"],
        "snapshot_sha256": trusted["snapshot_sha256"],
        "retrieval_version": "retrieval-v1", "query_as_of": AS_OF,
    }
    abstention = event("historical_non_evidentiary", "retrieval-q1", 1, payload)
    gold = label(decision="must_abstain", citations=[])
    feed = feedback(abstention)
    manifest = build(
        labels=[gold], retrievals=[abstention], feedbacks=[feed], evidences=[]
    )
    report = evaluate_rag_retrieval(
        [abstention], gold_manifest=manifest,
        policy=RetrievalEvaluationPolicy(T, AS_OF, "eval-v1"),
        trusted_label_events=[gold], trusted_retrieval_events=[abstention],
        trusted_feedback_events=[feed], trusted_evidence_events=[],
        trusted_reviewer_registry=registry(),
    )
    row = report["rows"][0]
    assert row["outcome"] == "explicit_abstention"
    assert row["decision_correct"] is True
    assert row["snapshot_id"] == trusted["snapshot_id"]
    assert row["job_id"] == trusted["job_id"]
