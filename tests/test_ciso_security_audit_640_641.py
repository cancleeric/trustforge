"""CISO adversarial security audit for issue #640 (anomaly baseline) and
#641 (RAG gold set).

These negative tests verify that the security-by-design invariants hold under
hostile inputs.  Every test feeds an attack payload and asserts rejection —
no silent promotion, no cross-tenant leak, no activation path.

#640 — analysis_anomaly_baseline: pure candidate generator with no registry,
       mutable pointer, approval path, activation path, or persistence.
#641 — rag_gold_set: pure builder/evaluator that never stores labels, never
       promotes feedback / repeated answers / retrieval output to Evidence.
"""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

# ── #640 anomaly baseline imports ──────────────────────────────────────────
from trustforge.analysis_anomaly_baseline import (
    AnalysisAnomalyError,
    AnalysisAnomalyPolicy,
    detect_analysis_anomalies,
)
from trustforge.calibration_dataset import _event_anchor, _sha256
from trustforge.learning_event_contract import serialize_learning_event

# Reuse the rich helper fixtures from the existing baseline test suite.
from tests.test_analysis_anomaly_baseline import _event, _manifest, _normal_events, _policy

# ── #641 RAG gold set imports ──────────────────────────────────────────────
from trustforge.learning_event_contract import (
    LearningEventError,
    canonical_integrity_checksum,
    make_learning_event,
)
from trustforge.rag_gold_set import (
    ApprovalStoreSnapshot,
    RagGoldSetError,
    RagGoldSetPolicy,
    ReviewerAuthorityRegistry,
    build_rag_gold_set,
)
from trustforge.rag_gold_set import _event_anchor as _rag_event_anchor

# Reuse the rich helper fixtures from the existing gold-set test suite.
from tests.test_rag_gold_set import (
    T,
    AS_OF,
    sha,
    authority,
    registry,
    evidence,
    retrieval,
    feedback,
    label,
    approval_store,
    approval_store_from_records,
    build,
    inputs,
)


# ═══════════════════════════════════════════════════════════════════════════
# #640 — Anomaly Baseline adversarial security tests
# ═══════════════════════════════════════════════════════════════════════════


class TestAnomalyBaselineSecurity640:
    """Negative security tests for the anomaly-baseline candidate generator."""

    def test_future_event_injection_is_invisible(self):
        """Events with available_time after query_as_of must not influence the
        report — an attacker cannot smuggle future-looking events to bias the
        baseline."""
        events = _normal_events()
        manifest = _manifest(events)
        baseline = detect_analysis_anomalies(
            events, calibration_manifest=manifest, policy=_policy()
        )
        future = _event(99, "2026-07-16T01:00:00Z")  # after query_as_of
        with_future = detect_analysis_anomalies(
            events + [future], calibration_manifest=manifest, policy=_policy()
        )
        assert baseline["report_sha256"] == with_future["report_sha256"]

    def test_cross_tenant_events_are_isolated(self):
        """Events from a different tenant must be silently filtered out — they
        cannot appear in the cohort, skew distribution stats, or trigger
        findings."""
        events = _normal_events()
        manifest = _manifest(events)
        baseline = detect_analysis_anomalies(
            events, calibration_manifest=manifest, policy=_policy()
        )
        foreign = _event(88, "2026-07-09T01:00:00Z", tenant_id="attacker-tenant")
        with_foreign = detect_analysis_anomalies(
            events + [foreign], calibration_manifest=manifest, policy=_policy()
        )
        assert baseline["report_sha256"] == with_foreign["report_sha256"]

    def test_candidate_diagnostics_cannot_trigger_activation(self):
        """Every emitted candidate diagnostic must be non-evidentiary,
        candidate-only, and devoid of authority / activation / approval
        fields — the baseline generator is structurally incapable of
        promoting candidates."""
        reference = [_event(i, f"2026-07-0{i+1}T01:00:00Z", confidence=.5) for i in range(3)]
        current = [
            _event(i + 3, f"2026-07-{i+9:02d}T01:00:00Z", confidence=.95,
                   evidence=0, distribution={"none": 0})
            for i in range(3)
        ]
        events = reference + current
        result = detect_analysis_anomalies(
            events, calibration_manifest=_manifest(events), policy=_policy()
        )
        assert result["diagnostics"], "expected at least one diagnostic"
        for diag in result["diagnostics"]:
            payload = diag.payload
            assert payload["eligible_as_evidence"] is False
            assert payload["candidate_only"] is True
            assert payload["classification"] == "non_evidentiary_candidate"
            authority_fields = {
                "approve", "approved", "approval", "approval_action",
                "activate", "activated", "activation", "proposal",
                "active_version",
            }
            assert not (authority_fields & set(payload)), (
                f"candidate contains authority field: {authority_fields & set(payload)}"
            )

    def test_manifest_tenant_swap_rejected_by_checksum(self):
        """Swapping tenant_id in the manifest breaks the manifest checksum —
        an attacker cannot re-scope a baseline to a different tenant."""
        events = _normal_events()
        tampered = copy.deepcopy(_manifest(events))
        tampered["policy"]["tenant_id"] = "tenant-b"
        with pytest.raises(AnalysisAnomalyError, match="checksum"):
            detect_analysis_anomalies(
                events, calibration_manifest=tampered, policy=_policy()
            )

    def test_manifest_root_mismatch_rejects_extra_events(self):
        """Injecting events that are not in the manifest's input root must
        fail-closed — you cannot analyse a different dataset than the frozen
        manifest attests."""
        events = _normal_events()
        manifest = _manifest(events)
        extra = _event(77, "2026-07-12T02:00:00Z")
        with pytest.raises(AnalysisAnomalyError, match="root mismatch"):
            detect_analysis_anomalies(
                events + [extra], calibration_manifest=manifest, policy=_policy()
            )

    def test_manifest_fake_digest_resign_rejected_by_root(self):
        """Even if an attacker re-signs the manifest after forging the input
        root, the root itself still mismatches the actual events."""
        events = _normal_events()
        fake = copy.deepcopy(_manifest(events))
        fake["input_roots"]["analysis_sha256"] = "a" * 64
        fake["manifest_sha256"] = _sha256({
            k: v for k, v in fake.items() if k != "manifest_sha256"
        })
        with pytest.raises(AnalysisAnomalyError, match="root mismatch"):
            detect_analysis_anomalies(
                events, calibration_manifest=fake, policy=_policy()
            )

    def test_manifest_older_than_current_window_rejected(self):
        """The calibration manifest's dataset_as_of must not precede the
        current window end — a stale manifest cannot gate a newer analysis."""
        events = _normal_events()
        stale = copy.deepcopy(_manifest(events))
        stale["policy"]["dataset_as_of"] = "2026-07-07T00:00:00.000000Z"
        stale["manifest_sha256"] = _sha256({
            k: v for k, v in stale.items() if k != "manifest_sha256"
        })
        with pytest.raises(AnalysisAnomalyError, match="older than current"):
            detect_analysis_anomalies(
                events, calibration_manifest=stale, policy=_policy()
            )

    def test_no_mutable_state_between_invocations(self):
        """Calling detect twice must yield identical results — there is no
        hidden mutable 'current baseline' pointer that accumulates state."""
        events = _normal_events()
        manifest = _manifest(events)
        first = detect_analysis_anomalies(
            events, calibration_manifest=manifest, policy=_policy()
        )
        second = detect_analysis_anomalies(
            events, calibration_manifest=manifest, policy=_policy()
        )
        assert first["report_sha256"] == second["report_sha256"]

    def test_duplicate_analysis_identity_rejected(self):
        """Two events with the same identity cannot both be processed — an
        attacker cannot duplicate-count to manipulate distribution stats."""
        events = _normal_events()
        with pytest.raises(AnalysisAnomalyError, match="duplicate"):
            detect_analysis_anomalies(
                events + [events[0]],
                calibration_manifest=_manifest(events),
                policy=_policy(),
            )

    def test_non_finite_confidence_rejected_at_event_boundary(self):
        """A NaN/Inf confidence must be rejected before it can poison the
        robust-z computation.  The event constructor itself rejects non-finite
        values — defense in depth prevents malformed events from ever reaching
        the baseline generator."""
        from trustforge.learning_event_contract import LearningEventError
        with pytest.raises(LearningEventError, match="must be finite"):
            _event(50, "2026-07-09T01:00:00Z", confidence=float("nan"))

    def test_policy_window_ordering_enforced(self):
        """Overlapping or non-monotonic time windows must be rejected — an
        attacker cannot collapse reference and current into one window."""
        from dataclasses import replace
        bad_policy = replace(
            _policy(),
            reference_start="2026-07-10T00:00:00Z",  # after current_start
        )
        with pytest.raises(AnalysisAnomalyError, match="windows"):
            bad_policy.canonical()

    def test_non_manifest_cohort_excluded_from_distribution(self):
        """Events not in the manifest's row cohort must be excluded from
        distribution findings but still inspected by pipeline checks — an
        attacker cannot hide partial analyses by excluding them from the
        manifest."""
        # Build events where one is deliberately partial (degraded) but
        # excluded from the manifest rows (it has failure != complete).
        reference = [_event(i, f"2026-07-0{i+1}T01:00:00Z", confidence=.5) for i in range(3)]
        current = [_event(i + 3, f"2026-07-{i+9:02d}T01:00:00Z", confidence=.55) for i in range(3)]
        partial = _event(99, "2026-07-12T02:00:00Z", confidence=.55, partial=True)
        events = reference + current + [partial]
        result = detect_analysis_anomalies(
            events, calibration_manifest=_manifest(events), policy=_policy()
        )
        # The partial event is NOT in manifest rows (failure != complete),
        # but pipeline diagnostics still catch it.
        codes = {f["reason_code"] for f in result["findings"]}
        assert "PIPELINE_FAILURE_OR_PARTIAL" in codes


# ═══════════════════════════════════════════════════════════════════════════
# #641 — RAG Gold Set adversarial security tests
# ═══════════════════════════════════════════════════════════════════════════


class TestRagGoldSetSecurity641:
    """Negative security tests for the RAG gold-set builder and evaluator."""

    def test_historical_answer_cannot_be_promoted_to_gold(self):
        """Feedback events are structurally barred from becoming gold — the
        eligible_as_gold field is forced False by the schema and cannot be
        overridden."""
        with pytest.raises(RagGoldSetError, match="feedback cannot be gold"):
            build(feedbacks=[feedback(eligible_as_gold=True)])

    def test_historical_answer_cannot_become_evidence(self):
        """Feedback cannot carry eligible_as_evidence=True — historical
        observations are permanently non-evidentiary."""
        with pytest.raises(RagGoldSetError, match="feedback cannot be.*Evidence"):
            build(feedbacks=[feedback(eligible_as_evidence=True)])

    def test_reviewer_forgery_wrong_id_rejected(self):
        """Only 'gray-cpo' is the accepted reviewer — an attacker claiming
        to be a different reviewer is rejected at resolve time."""
        forged_label = label(reviewer="attacker", reviewer_role="cpo")
        with pytest.raises(RagGoldSetError):
            build(labels=[forged_label])

    def test_reviewer_forgery_wrong_role_rejected(self):
        """The reviewer's role must be exactly 'cpo' — escalating to 'admin'
        or any other role is rejected."""
        with pytest.raises(RagGoldSetError):
            build(reg=registry(records={"gray-cpo": authority(role="admin")}))

    def test_reviewer_authority_hash_mismatch_rejected(self):
        """The label's reviewer_authority_sha256 must match the trusted
        registry — an attacker cannot self-assert authority."""
        ev = evidence()
        wrong_hash = label(
            reviewer_authority_sha256="b" * 64,  # does not match registry
        )
        with pytest.raises(RagGoldSetError, match="authority hash"):
            build(labels=[wrong_hash])

    def test_reviewer_authority_expired_rejected(self):
        """An authority record whose valid_until is before the review time
        must be rejected — expired credentials cannot approve gold."""
        expired = registry(valid_until="2026-05-31T00:00:00.000000Z")
        with pytest.raises(RagGoldSetError):
            build(reg=expired)

    def test_reviewer_registry_checksum_tamper_rejected(self):
        """Modifying the registry contents but keeping the old checksum must
        fail — the registry is caller-trusted and tamper-evident."""
        tampered = ReviewerAuthorityRegistry(
            T, "registry-v1",
            "2026-01-01T00:00:00.000000Z", "2027-01-01T00:00:00.000000Z",
            {"gray-cpo": authority(role="superadmin")},
            "0" * 64,  # wrong checksum
        )
        with pytest.raises(RagGoldSetError, match="checksum"):
            build(reg=tampered)

    def test_malicious_feedback_does_not_pollute_gold_rows(self):
        """Feedback with a huge vote or injection payload must not appear in
        any gold row — feedback is structurally non-evidentiary and inert."""
        result = build(feedbacks=[feedback(vote=99999, feedback="INJECT; DROP TABLE")])
        for row in result["rows"]:
            assert "feedback" not in row
            assert row["reviewer_id"] == "gray-cpo"

    def test_unapproved_label_rejected(self):
        """A label without a matching independent approval record in the
        trusted approval store must be rejected — no self-approval."""
        ev, ret, feed, lab = inputs()
        empty_store = approval_store_from_records([])  # no approvals
        with pytest.raises(RagGoldSetError, match="approval"):
            build_rag_gold_set(
                [lab],
                retrieval_events=[ret],
                feedback_events=[feed],
                evidence_events=[ev],
                policy=RagGoldSetPolicy(T, AS_OF, "gold-v1", "producer-v1"),
                trusted_reviewer_registry=registry(),
                trusted_approval_store=empty_store,
            )

    def test_approval_store_duplicate_id_rejected(self):
        """Two approvals with the same approval_id must be rejected —
        one-time-use tokens cannot be replayed."""
        ev, ret, feed, lab = inputs()
        from trustforge.rag_gold_set import _sha256 as _rag_sha256
        base_approval = {
            "approval_id": "approval-l1",
            "label_identity": lab.identity,
            "label_event_sha256": _rag_sha256(_rag_event_anchor(lab)),
            "label_id": "l1", "query_id": "q1",
            "decision": "approved_answer", "reason": "manual evidence review",
            "reviewer_id": "gray-cpo",
            "reviewed_at": "2026-06-01T00:00:00.000000Z", "tenant_id": T,
        }
        dup = [base_approval, dict(base_approval)]
        with pytest.raises(RagGoldSetError, match="duplicate"):
            build(labels=[lab], approvals=approval_store_from_records(dup))

    def test_approval_store_extra_scoped_label_rejected(self):
        """The approval store must not contain extra scoped label approvals
        beyond what the gold set contains — no hidden shadow approvals."""
        ev, ret, feed, lab = inputs()
        from trustforge.rag_gold_set import _sha256 as _rag_sha256
        phantom_approval = {
            "approval_id": "approval-phantom",
            "label_identity": "label-that-does-not-exist",
            "label_event_sha256": "0" * 64,
            "label_id": "phantom", "query_id": "phantom-q",
            "decision": "approved_answer", "reason": "ghost",
            "reviewer_id": "gray-cpo",
            "reviewed_at": "2026-06-01T00:00:00.000000Z", "tenant_id": T,
        }
        store = approval_store_from_records(
            [
                {
                    "approval_id": "approval-l1",
                    "label_identity": lab.identity,
                    "label_event_sha256": _rag_sha256(_rag_event_anchor(lab)),
                    "label_id": "l1", "query_id": "q1",
                    "decision": "approved_answer", "reason": "manual evidence review",
                    "reviewer_id": "gray-cpo",
                    "reviewed_at": "2026-06-01T00:00:00.000000Z", "tenant_id": T,
                },
                phantom_approval,
            ]
        )
        with pytest.raises(RagGoldSetError, match="extra scoped label"):
            build(labels=[lab], approvals=store)

    def test_gold_manifest_eligible_as_evidence_always_false(self):
        """The gold manifest is structurally barred from becoming Evidence —
        eligible_as_evidence is hardcoded False and cannot be overridden."""
        result = build()
        assert result["eligible_as_evidence"] is False
        assert result["classification"] == "human_reviewed_non_evidentiary_gold"

    def test_must_abstain_cannot_carry_answer(self):
        """A must_abstain label with an answer or citations must be rejected —
        abstention means 'no evidence to answer', not 'secret answer'."""
        ev = evidence()
        bad_abstain = label(
            ev, decision="must_abstain",
            answer="secret answer",  # abstain must not carry an answer
        )
        with pytest.raises(RagGoldSetError, match="must_abstain.*answer"):
            build(labels=[bad_abstain], evidences=[ev])

    def test_approved_answer_requires_citation(self):
        """An approved_answer label with zero citations must be rejected —
        gold answers require at least one evidence-backed citation."""
        ev = evidence()
        no_cite = label(ev, citations=[])
        with pytest.raises(RagGoldSetError, match="at least one evidence"):
            build(labels=[no_cite], evidences=[ev])

    def test_citation_absent_from_evidence_snapshot_rejected(self):
        """A citation referencing an evidence identity not in the trusted
        evidence snapshot must be rejected — no phantom citations."""
        ev = evidence()
        phantom_cite = label(
            ev,
            citations=[{"evidence_identity": "nonexistent", "claim": "forged"}],
        )
        with pytest.raises(RagGoldSetError, match="absent from trusted evidence"):
            build(labels=[phantom_cite], evidences=[ev])

    def test_citation_claim_mismatch_rejected(self):
        """A citation whose claim text doesn't match the current Evidence
        must be rejected — no claim substitution attacks."""
        ev = evidence()
        mismatched = label(
            ev,
            citations=[{"evidence_identity": ev.identity, "claim": "WRONG CLAIM"}],
        )
        with pytest.raises(RagGoldSetError, match="claim does not match"):
            build(labels=[mismatched], evidences=[ev])

    def test_feedback_cross_query_lineage_rejected(self):
        """Feedback cannot reference a retrieval from a different query —
        no cross-contamination of query lineages."""
        ret = inputs()[1]
        with pytest.raises(RagGoldSetError, match="cross query"):
            build(retrievals=[ret], feedbacks=[feedback(ret, query_id="other")])

    def test_feedback_dangling_retrieval_rejected(self):
        """Feedback referencing a retrieval identity that doesn't exist must
        be rejected — no phantom feedback."""
        from tests.test_rag_gold_set import event
        bad_payload = dict(feedback().payload)
        bad_payload["retrieval_identity"] = "does-not-exist"
        bad_payload["historical_answer_id"] = "feedback-phantom"
        bad = event(
            "historical_non_evidentiary", "feedback-phantom", 1,
            bad_payload,
        )
        with pytest.raises(RagGoldSetError, match="dangling"):
            build(feedbacks=[bad])

    def test_gold_manifest_checksum_tamper_rejected(self):
        """Tampering with any field in a built manifest breaks the checksum —
        the manifest is tamper-evident end-to-end."""
        manifest = build()
        forged = copy.deepcopy(manifest)
        forged["rows"][0]["answer"] = "tampered answer"
        forged["manifest_sha256"] = sha(
            {k: v for k, v in forged.items() if k != "manifest_sha256"}
        )
        from trustforge.rag_gold_set import evaluate_rag_retrieval, RetrievalEvaluationPolicy
        ev, ret, feed, lab = inputs()
        with pytest.raises(RagGoldSetError):
            evaluate_rag_retrieval(
                [ret], gold_manifest=forged,
                policy=RetrievalEvaluationPolicy(T, AS_OF, "eval-v1"),
                trusted_label_events=[lab], trusted_retrieval_events=[ret],
                trusted_feedback_events=[feed], trusted_evidence_events=[ev],
                trusted_reviewer_registry=registry(),
                trusted_approval_store=approval_store([lab]),
            )

    def test_revoked_evidence_cannot_be_cited_in_gold(self):
        """A gold label citing a revoked evidence identity must be rejected —
        only 'current' evidence backs gold answers."""
        v1 = evidence()
        revoked = evidence(revision=2, predecessor=v1.identity, status="revoked")
        # Build with revoked evidence as the only source → v1 is superseded,
        # revoked is the head but status=revoked → no current evidence.
        with pytest.raises(RagGoldSetError):
            build(
                labels=[label(revoked, citations=[{
                    "evidence_identity": revoked.identity, "claim": "verified",
                }])],
                evidences=[v1, revoked], retrievals=[], feedbacks=[],
            )

    def test_foreign_tenant_inputs_are_invisible(self):
        """Retrieval/feedback/label events from a foreign tenant must not
        appear in the manifest — strict tenant isolation."""
        base = build()
        ev, ret, feed, lab = inputs()
        foreign_retrieval = retrieval(ev, query="foreign", tenant="attacker")
        foreign_future = retrieval(
            ev, query="future", available="2027-01-01T00:00:00.000000Z"
        )
        # Adding foreign/future retrievals must not change the manifest.
        assert build(retrievals=[foreign_retrieval, foreign_future, ret]) == base

    def test_supersession_revision_gap_rejected(self):
        """Gold label supersession revisions must be continuous — an attacker
        cannot skip intermediate reviews by jumping from revision 1 to 3."""
        ev = evidence()
        root = label(ev)
        # Revision 3 with predecessor root (rev 1) → gap (expected 2).
        gap = label(ev, "gap", revision=3, predecessor="l1")
        with pytest.raises(RagGoldSetError, match="revisions must be continuous"):
            build(labels=[root, gap], evidences=[ev])
