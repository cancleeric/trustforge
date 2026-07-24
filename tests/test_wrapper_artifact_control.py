"""Controller tests for the wrapper artifact lifecycle (#510).

These tests pin every CISO-flagged invariant:

* transition order (no skipping, no reversing, no self-approval)
* approval must be a typed ``ApprovalRecord`` minted by the controller, not a
  caller-forged object
* triple binding of candidate artifact + config snapshot + rollback target
* ModelHub probe evaluated inline — caller cannot fake "verified"
* sandbox isolation: pointer is not moved until activation
* offline rollback to a known-good previously-approved artifact
"""
from __future__ import annotations

import hashlib
from datetime import timedelta
from copy import copy

import pytest

from trustforge.artifact_registry import (
    InMemoryArtifactRegistry,
    InMemoryRevisionPointerStore,
)
from trustforge.modelhub_readonly_probe import ProbeRequirement
from trustforge.wrapper_artifact_control import (
    ActorPrincipal,
    ApprovalRecord,
    CandidateArtifact,
    DatasetManifest,
    DiagnosticSource,
    ReviewerPrincipal,
    RiskAssessment,
    RollbackEvent,
    SandboxReplayResult,
    WrapperArtifactController,
    WrapperArtifactError,
)


# --------------------------------------------------------------------------- #
# Fixtures and builders
# --------------------------------------------------------------------------- #


# The probe artifact identity is independent of the registry's content-addressed
# candidate artifact_id: it identifies the *ModelHub* artifact that the probe
# is attesting to.  For tests we use a self-consistent fixed identity whose
# sha256(checksum_payload) == artifact_sha256, exactly as the evaluator expects.
_PROBE_PAYLOAD = b"probe-bytes"
_PROBE_SHA256 = hashlib.sha256(_PROBE_PAYLOAD).hexdigest()
_PROBE_ARTIFACT_ID = "modelhub-artifact-1"


def _probe_requirement() -> ProbeRequirement:
    return ProbeRequirement(
        tenant_id="tenant-a",
        product="trustforge",
        model_name="wrapper",
        artifact_id=_PROBE_ARTIFACT_ID,
        artifact_sha256=_PROBE_SHA256,
        provenance_id="prov-1",
    )


def _verified_observation() -> dict:
    return {
        "health_ok": True,
        "capabilities": ["health", "list_models", "get_model_path"],
        "identity": {"tenant_id": "tenant-a", "product": "trustforge"},
        "negative_read_checks": {
            "other_tenant_blocked": True,
            "other_artifact_blocked": True,
        },
        "artifact": {
            "artifact_id": _PROBE_ARTIFACT_ID,
            "sha256": _PROBE_SHA256,
            "checksum_payload": _PROBE_PAYLOAD,
        },
        "provenance": {"id": "prov-1", "verified": True},
    }


def _unverified_observation() -> dict:
    # Only health_ok present — evaluator returns "unverified".
    return {"health_ok": True}


def _disabled_observation() -> dict:
    return {"timeout": True}


def _hash_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@pytest.fixture
def env():
    """A fresh registry, pointer store, controller, and a pre-approved baseline.

    The baseline artifact is activated *outside* the controller (simulating
    that one previously-good wrapper revision is already in production).  The
    controller is then told about it via ``_approved_artifacts`` so it can be
    used as a rollback target.

    For tests that need a fully end-to-end flow, ``env_two_phase`` provides
    two controllers sharing a registry so the first activation's output
    becomes the second's rollback target through the public API.
    """
    registry = InMemoryArtifactRegistry()
    pointers = InMemoryRevisionPointerStore(registry)
    controller = WrapperArtifactController(registry, pointers, pointer_name="wrapper")

    # Seed production baseline.
    baseline_payload = b"baseline-wrapper-v1"
    baseline_record = registry.put(baseline_payload, metadata={"role": "baseline"})
    baseline_config = registry.put(b"baseline-config", metadata={"role": "config-snapshot"})
    pointers.stage("wrapper", baseline_record.artifact_id, actor="bootstrap", now=0.0)
    pointers.activate("wrapper", actor="bootstrap", now=0.0)
    # Make the baseline a valid rollback target.
    controller._approved_artifacts[baseline_record.artifact_id] = baseline_config.artifact_id

    candidate_payload = b"candidate-wrapper-v2"
    candidate_record = registry.put(candidate_payload, metadata={"role": "candidate"})

    return {
        "registry": registry,
        "pointers": pointers,
        "controller": controller,
        "baseline_artifact_id": baseline_record.artifact_id,
        "baseline_payload_sha256": baseline_record.sha256,
        "baseline_config_id": baseline_config.artifact_id,
        "candidate_artifact_id": candidate_record.artifact_id,
        "candidate_payload_sha256": candidate_record.sha256,
    }


def _build_proposal(env, *, proposal_id="p1", proposer_subject="proposer-1"):
    controller = env["controller"]
    candidate = CandidateArtifact(
        artifact_id=env["candidate_artifact_id"],
        payload_sha256=env["candidate_payload_sha256"],
        dataset_manifest=DatasetManifest(manifest_id="ds-1", sha256=_hash_hex(b"dataset")),
    )
    diagnostic = DiagnosticSource(
        diagnostic_id="diag-1",
        observer="hermes-analysis",
        generated_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    risk = RiskAssessment(
        assessment_id="risk-1", risk_level="medium", evaluator="risk-gate"
    )
    controller.create_proposal(
        proposal_id=proposal_id,
        diagnostic=diagnostic,
        candidate=candidate,
        risk=risk,
        proposer=ActorPrincipal(proposer_subject),
    )
    return candidate, diagnostic, risk


def _attach_sandbox(env, *, proposal_id="p1", runner_subject="runner-1", passed=True):
    env["controller"].attach_sandbox(
        proposal_id=proposal_id,
        sandbox_result=SandboxReplayResult(
            run_id="run-1",
            runner_version="trusted-runner/v1",
            candidate_artifact_id=env["candidate_artifact_id"],
            completed_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            passed=passed,
            replay_sha256=_hash_hex(b"replay"),
        ),
        sandbox_runner=ActorPrincipal(runner_subject),
    )


def _request_approval(
    env,
    *,
    proposal_id="p1",
    reviewer_subject="reviewer-1",
    role="release-manager",
    rollback_target=None,
    config_snapshot=b"cfg-v2",
):
    return env["controller"].request_approval(
        proposal_id=proposal_id,
        reviewer=ReviewerPrincipal(
            subject=reviewer_subject,
            role=role,
            expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            + timedelta(hours=1),
        ),
        config_snapshot=config_snapshot,
        rollback_target_artifact_id=rollback_target or env["baseline_artifact_id"],
        reason="promotion approved",
    )


def _full_setup_to_review(env, **kwargs):
    """Drive a proposal all the way to the ``review`` state and return approval."""
    _build_proposal(env, **{k: v for k, v in kwargs.items() if k in {"proposal_id", "proposer_subject"}})
    _attach_sandbox(env, **{k: v for k, v in kwargs.items() if k in {"proposal_id", "runner_subject", "passed"}})
    return _request_approval(
        env,
        **{k: v for k, v in kwargs.items() if k in {"proposal_id", "reviewer_subject", "role", "rollback_target", "config_snapshot"}},
    )


# --------------------------------------------------------------------------- #
# Provenance + structural creation gates
# --------------------------------------------------------------------------- #


def test_create_proposal_binds_diagnostic_candidate_and_risk(env):
    candidate, diagnostic, risk = _build_proposal(env)
    state = env["controller"].state("p1")
    assert state == "proposal"
    proposal = env["controller"]._require_proposal("p1")
    assert proposal.diagnostic == diagnostic
    assert proposal.candidate == candidate
    assert proposal.risk == risk


def test_create_proposal_rejects_missing_provenance(env):
    import datetime as dt
    registry = env["registry"]
    # Diagnostic missing id -> rejected (provenance gap).
    bad_diag = DiagnosticSource(
        diagnostic_id="",
        observer="hermes-analysis",
        generated_at=dt.datetime.now(dt.timezone.utc),
    )
    candidate = CandidateArtifact(
        artifact_id=env["candidate_artifact_id"],
        payload_sha256=env["candidate_payload_sha256"],
        dataset_manifest=DatasetManifest(manifest_id="ds", sha256=_hash_hex(b"d")),
    )
    risk = RiskAssessment(assessment_id="r", risk_level="low", evaluator="risk-gate")
    with pytest.raises(WrapperArtifactError, match="diagnostic_id"):
        env["controller"].create_proposal(
            proposal_id="p",
            diagnostic=bad_diag,
            candidate=candidate,
            risk=risk,
            proposer=ActorPrincipal("p"),
        )


def test_create_proposal_rejects_naive_generated_at(env):
    import datetime as dt
    bad_diag = DiagnosticSource(
        diagnostic_id="d",
        observer="o",
        generated_at=dt.datetime(2026, 1, 1),  # naive
    )
    candidate = CandidateArtifact(
        artifact_id=env["candidate_artifact_id"],
        payload_sha256=env["candidate_payload_sha256"],
        dataset_manifest=DatasetManifest(manifest_id="ds", sha256=_hash_hex(b"d")),
    )
    risk = RiskAssessment(assessment_id="r", risk_level="low", evaluator="risk-gate")
    with pytest.raises(WrapperArtifactError, match="timezone-aware"):
        env["controller"].create_proposal(
            proposal_id="p",
            diagnostic=bad_diag,
            candidate=candidate,
            risk=risk,
            proposer=ActorPrincipal("p"),
        )


def test_create_proposal_rejects_checksum_version_mismatch(env):
    """Candidate artifact_id must equal sha256:<payload_sha256>."""
    import datetime as dt
    bad_candidate = CandidateArtifact(
        artifact_id="sha256:deadbeef",
        payload_sha256=env["candidate_payload_sha256"],  # mismatches artifact_id
        dataset_manifest=DatasetManifest(manifest_id="ds", sha256=_hash_hex(b"d")),
    )
    diag = DiagnosticSource(
        diagnostic_id="d", observer="o", generated_at=dt.datetime.now(dt.timezone.utc)
    )
    risk = RiskAssessment(assessment_id="r", risk_level="low", evaluator="risk-gate")
    with pytest.raises(WrapperArtifactError, match="sha256:<payload_sha256>"):
        env["controller"].create_proposal(
            proposal_id="p",
            diagnostic=diag,
            candidate=bad_candidate,
            risk=risk,
            proposer=ActorPrincipal("p"),
        )


def test_create_proposal_rejects_unregistered_candidate(env):
    import datetime as dt
    unregistered = CandidateArtifact(
        artifact_id="sha256:" + "0" * 64,
        payload_sha256="0" * 64,
        dataset_manifest=DatasetManifest(manifest_id="ds", sha256=_hash_hex(b"d")),
    )
    diag = DiagnosticSource(
        diagnostic_id="d", observer="o", generated_at=dt.datetime.now(dt.timezone.utc)
    )
    risk = RiskAssessment(assessment_id="r", risk_level="low", evaluator="risk-gate")
    with pytest.raises(WrapperArtifactError, match="not registered"):
        env["controller"].create_proposal(
            proposal_id="p",
            diagnostic=diag,
            candidate=unregistered,
            risk=risk,
            proposer=ActorPrincipal("p"),
        )


def test_create_proposal_rejects_duplicate(env):
    _build_proposal(env)
    with pytest.raises(WrapperArtifactError, match="already exists"):
        _build_proposal(env)


# --------------------------------------------------------------------------- #
# Sandbox isolation
# --------------------------------------------------------------------------- #


def test_sandbox_replay_does_not_move_production_pointer(env):
    pointer_before = env["pointers"].pointer("wrapper")
    history_before = env["pointers"].history("wrapper")
    _build_proposal(env)
    _attach_sandbox(env, passed=True)
    pointer_after = env["pointers"].pointer("wrapper")
    history_after = env["pointers"].history("wrapper")
    # Pointer unchanged and no new pointer events recorded by the sandbox step.
    assert pointer_after.active_artifact_id == pointer_before.active_artifact_id
    assert history_after == history_before


def test_failed_sandbox_blocks_review_advance(env):
    """sandbox_result.passed=False keeps the proposal in sandbox_replay and
    request_approval refuses to advance."""
    _build_proposal(env)
    _attach_sandbox(env, passed=False)
    assert env["controller"].state("p1") == "sandbox_replay"
    with pytest.raises(WrapperArtifactError, match="sandbox replay did not pass"):
        _request_approval(env)


def test_attach_sandbox_rejects_result_for_different_candidate(env):
    _build_proposal(env)
    bogus = SandboxReplayResult(
        run_id="r",
        runner_version="v1",
        candidate_artifact_id="sha256:" + "0" * 64,  # not our candidate
        completed_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        passed=True,
        replay_sha256=_hash_hex(b"r"),
    )
    with pytest.raises(WrapperArtifactError, match="not bound to this proposal"):
        env["controller"].attach_sandbox(
            proposal_id="p1",
            sandbox_result=bogus,
            sandbox_runner=ActorPrincipal("runner-1"),
        )


def test_attach_sandbox_rejects_unknown_proposal(env):
    """Calling attach_sandbox on a non-existent proposal fails before any
    state transition is attempted.  The full matrix of forbidden transitions
    is covered exhaustively in test_wrapper_state_machine.py."""
    bogus = SandboxReplayResult(
        run_id="r",
        runner_version="v1",
        candidate_artifact_id=env["candidate_artifact_id"],
        completed_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        passed=True,
        replay_sha256=_hash_hex(b"r"),
    )
    with pytest.raises(WrapperArtifactError, match="unknown proposal"):
        env["controller"].attach_sandbox(
            proposal_id="never-created",
            sandbox_result=bogus,
            sandbox_runner=ActorPrincipal("runner-1"),
        )


# --------------------------------------------------------------------------- #
# Approval gate — no self-approval, single-use, anti-spoofing
# --------------------------------------------------------------------------- #


def test_request_approval_forbids_self_approval_by_proposer(env):
    _build_proposal(env, proposer_subject="alice")
    _attach_sandbox(env, runner_subject="bob")
    with pytest.raises(WrapperArtifactError, match="same principal as the proposer"):
        _request_approval(env, reviewer_subject="alice")


def test_request_approval_forbids_self_approval_by_runner(env):
    _build_proposal(env, proposer_subject="alice")
    _attach_sandbox(env, runner_subject="bob")
    with pytest.raises(WrapperArtifactError, match="same principal as the sandbox runner"):
        _request_approval(env, reviewer_subject="bob")


def test_request_approval_rejects_expired_reviewer(env):
    import datetime as dt
    _build_proposal(env)
    _attach_sandbox(env)
    expired = ReviewerPrincipal(
        subject="reviewer-x",
        role="release-manager",
        expires_at=dt.datetime.now(dt.timezone.utc) - timedelta(seconds=1),
    )
    with pytest.raises(WrapperArtifactError, match="reviewer principal has expired"):
        env["controller"].request_approval(
            proposal_id="p1",
            reviewer=expired,
            config_snapshot=b"cfg",
            rollback_target_artifact_id=env["baseline_artifact_id"],
            reason="r",
        )


def test_request_approval_rejects_unknown_rollback_target(env):
    """rollback_target must be a previously-approved artifact, not arbitrary."""
    _build_proposal(env)
    _attach_sandbox(env)
    bogus = "sha256:" + "0" * 64
    with pytest.raises(WrapperArtifactError, match="not a previously-approved"):
        _request_approval(env, rollback_target=bogus)


# --------------------------------------------------------------------------- #
# Activation — full positive flow + every negative branch
# --------------------------------------------------------------------------- #


def test_activate_succeeds_with_full_binding_and_verified_probe(env):
    approval = _full_setup_to_review(env)
    activation = env["controller"].activate(
        proposal_id="p1",
        approval=approval,
        probe_observation=_verified_observation(),
        probe_requirement=_probe_requirement(),
        reason="promote v2",
    )
    assert env["controller"].state("p1") == "human_activation"
    assert activation.activated_artifact_id == env["candidate_artifact_id"]
    assert activation.rollback_target_artifact_id == env["baseline_artifact_id"]
    assert activation.modelhub_probe_status == "verified"
    pointer = env["pointers"].pointer("wrapper")
    assert pointer.active_artifact_id == env["candidate_artifact_id"]


def test_activate_rejects_caller_forged_approval_with_known_id(env):
    """A caller that knows a real approval_id cannot reconstruct the record."""
    approval = _full_setup_to_review(env)
    # Forge a record with the same id but a tampered binding_checksum.
    forged = ApprovalRecord(
        approval_id=approval.approval_id,
        reviewer=approval.reviewer,
        proposal_id=approval.proposal_id,
        binding_checksum="0" * 64,  # bogus
        issued_at=approval.issued_at,
    )
    with pytest.raises(WrapperArtifactError, match="does not match journal entry"):
        env["controller"].activate(
            proposal_id="p1",
            approval=forged,
            probe_observation=_verified_observation(),
            probe_requirement=_probe_requirement(),
            reason="r",
        )


def test_activate_rejects_approval_from_different_proposal(env):
    """Two proposals in flight; approval from one cannot activate the other."""
    _build_proposal(env, proposal_id="p1")
    _attach_sandbox(env, proposal_id="p1")
    approval_p1 = _request_approval(env, proposal_id="p1")

    # Second proposal shares registry/pointers but separate controller state.
    _build_proposal(env, proposal_id="p2", proposer_subject="proposer-2")
    _attach_sandbox(env, proposal_id="p2", runner_subject="runner-2")
    approval_p2 = _request_approval(env, proposal_id="p2", reviewer_subject="reviewer-2")

    # Activating p2 with p1's approval must fail.
    with pytest.raises(WrapperArtifactError, match="different proposal"):
        env["controller"].activate(
            proposal_id="p2",
            approval=approval_p1,
            probe_observation=_verified_observation(),
            probe_requirement=_probe_requirement(),
            reason="r",
        )
    # p2's own approval still works — sanity.
    activation = env["controller"].activate(
        proposal_id="p2",
        approval=approval_p2,
        probe_observation=_verified_observation(),
        probe_requirement=_probe_requirement(),
        reason="r",
    )
    assert activation.proposal_id == "p2"


def test_activate_rejects_replay_of_consumed_approval(env):
    approval = _full_setup_to_review(env)
    obs = _verified_observation()
    req = _probe_requirement()
    env["controller"].activate(proposal_id="p1", approval=approval, probe_observation=obs, probe_requirement=req, reason="r")
    # Second use of the same approval id is refused.
    with pytest.raises(WrapperArtifactError, match="already been consumed"):
        env["controller"].activate(proposal_id="p1", approval=approval, probe_observation=obs, probe_requirement=req, reason="r")


def test_activate_rejects_when_state_not_review(env):
    approval = _full_setup_to_review(env)
    # Manually regress the state to simulate an attempt to skip the human gate.
    env["controller"]._require_proposal("p1").state = "sandbox_replay"
    with pytest.raises(WrapperArtifactError, match="requires review state"):
        env["controller"].activate(
            proposal_id="p1",
            approval=approval,
            probe_observation=_verified_observation(),
            probe_requirement=_probe_requirement(),
            reason="r",
        )


def test_activate_rejects_expired_reviewer(env):
    """Reviewer was valid at request_approval time but expired before activate."""
    import datetime as dt
    # Use a controller with a synthetic clock so we can advance time.
    registry = env["registry"]
    pointers = env["pointers"]
    start = dt.datetime(2026, 7, 24, 12, 0, 0, tzinfo=dt.timezone.utc)
    controller = WrapperArtifactController(registry, pointers, clock=start, pointer_name="wrapper")
    # Seed baseline into the new controller.
    controller._approved_artifacts[env["baseline_artifact_id"]] = env["baseline_config_id"]
    env2 = {**env, "controller": controller}

    _build_proposal(env2)
    _attach_sandbox(env2)
    # Build reviewer with expiry relative to the controller's clock, not real now.
    reviewer = ReviewerPrincipal(
        subject="reviewer-1",
        role="release-manager",
        expires_at=start + timedelta(minutes=30),
    )
    approval = controller.request_approval(
        proposal_id="p1",
        reviewer=reviewer,
        config_snapshot=b"cfg-v2",
        rollback_target_artifact_id=env["baseline_artifact_id"],
        reason="promotion approved",
    )
    # Advance clock past reviewer expiry.
    controller._clock = start + timedelta(hours=2)
    with pytest.raises(WrapperArtifactError, match="approval reviewer has expired"):
        controller.activate(
            proposal_id="p1",
            approval=approval,
            probe_observation=_verified_observation(),
            probe_requirement=_probe_requirement(),
            reason="r",
        )


def test_activate_rejects_binding_drift_after_sandbox_replaced(env):
    """If the sandbox_result is mutated after approval, the binding checksum
    mismatches and activation is refused."""
    approval = _full_setup_to_review(env)
    proposal = env["controller"]._require_proposal("p1")
    # Tamper with the recorded sandbox result.  Use dataclasses.replace to
    # simulate an attacker swapping in a different run id.
    from dataclasses import replace as dc_replace
    proposal.sandbox_result = dc_replace(proposal.sandbox_result, run_id="run-evil")
    with pytest.raises(WrapperArtifactError, match="binding checksum does not match"):
        env["controller"].activate(
            proposal_id="p1",
            approval=approval,
            probe_observation=_verified_observation(),
            probe_requirement=_probe_requirement(),
            reason="r",
        )


def test_activate_rejects_modelhub_unverified(env):
    """Caller-supplied observation that evaluates to 'unverified' blocks activation."""
    approval = _full_setup_to_review(env)
    with pytest.raises(WrapperArtifactError, match="ModelHub probe did not verify"):
        env["controller"].activate(
            proposal_id="p1",
            approval=approval,
            probe_observation=_unverified_observation(),
            probe_requirement=_probe_requirement(),
            reason="r",
        )
    # Pointer must not have moved.
    assert env["pointers"].pointer("wrapper").active_artifact_id == env["baseline_artifact_id"]
    assert env["controller"].state("p1") == "review"


def test_activate_rejects_modelhub_disabled(env):
    """A disabled probe (e.g. timeout) must also block activation."""
    approval = _full_setup_to_review(env)
    with pytest.raises(WrapperArtifactError, match="ModelHub probe did not verify"):
        env["controller"].activate(
            proposal_id="p1",
            approval=approval,
            probe_observation=_disabled_observation(),
            probe_requirement=_probe_requirement(),
            reason="r",
        )


def test_activate_rejects_caller_fabricated_verified_dict(env):
    """The headline #510 attack: caller tries ``{"status": "verified"}``.

    Because the controller runs the evaluator inline over the raw observation,
    a bare dict without the required component evidence still evaluates to
    'unverified' (or 'disabled'), and activation is refused.
    """
    approval = _full_setup_to_review(env)
    with pytest.raises(WrapperArtifactError, match="ModelHub probe did not verify"):
        env["controller"].activate(
            proposal_id="p1",
            approval=approval,
            probe_observation={"status": "verified"},  # spoofing attempt
            probe_requirement=_probe_requirement(),
            reason="r",
        )


def test_activate_rejects_when_active_pointer_drifted_from_rollback_target(env):
    """If a concurrent activation moved the pointer, the rollback target no
    longer matches and activation is refused."""
    approval = _full_setup_to_review(env)
    # Simulate concurrent drift: another caller staged a different artifact.
    other_payload = b"other"
    other = env["registry"].put(other_payload, metadata={"role": "other"})
    env["pointers"].stage("wrapper", other.artifact_id, actor="intruder", now=99.0)
    env["pointers"].activate("wrapper", actor="intruder", now=99.0)
    with pytest.raises(WrapperArtifactError, match="active pointer does not match the rollback target"):
        env["controller"].activate(
            proposal_id="p1",
            approval=approval,
            probe_observation=_verified_observation(),
            probe_requirement=_probe_requirement(),
            reason="r",
        )


# --------------------------------------------------------------------------- #
# Monitoring + offline rollback
# --------------------------------------------------------------------------- #


def _activate_for_rollback(env):
    approval = _full_setup_to_review(env)
    return env["controller"].activate(
        proposal_id="p1",
        approval=approval,
        probe_observation=_verified_observation(),
        probe_requirement=_probe_requirement(),
        reason="promote v2",
    )


def test_begin_monitoring_advances_state(env):
    _activate_for_rollback(env)
    assert env["controller"].state("p1") == "human_activation"
    env["controller"].begin_monitoring(proposal_id="p1")
    assert env["controller"].state("p1") == "monitoring"


def test_begin_monitoring_refuses_if_not_activated(env):
    _build_proposal(env)
    with pytest.raises(WrapperArtifactError, match="forbidden wrapper transition"):
        env["controller"].begin_monitoring(proposal_id="p1")


def test_rollback_offline_restores_previous_artifact(env):
    """Rollback after monitoring: pointer returns to baseline, no probe needed."""
    _activate_for_rollback(env)
    env["controller"].begin_monitoring(proposal_id="p1")
    assert env["pointers"].pointer("wrapper").active_artifact_id == env["candidate_artifact_id"]

    event = env["controller"].rollback(
        proposal_id="p1",
        actor=ReviewerPrincipal(
            subject="oncall",
            role="sre",
            expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            + timedelta(hours=1),
        ),
        reason="production incident",
    )
    assert isinstance(event, RollbackEvent)
    assert event.target_artifact_id == env["baseline_artifact_id"]
    assert env["controller"].state("p1") == "rollback"
    # Pointer restored.
    assert env["pointers"].pointer("wrapper").active_artifact_id == env["baseline_artifact_id"]
    # History records the rollback transition.
    actions = [e.action for e in env["pointers"].history("wrapper")]
    assert actions[-1] == "rollback"


def test_rollback_works_without_modelhub_probe(env):
    """The headline offline-rollback property: no observation/requirement needed."""
    _activate_for_rollback(env)
    env["controller"].begin_monitoring(proposal_id="p1")
    # No probe_observation/probe_requirement arguments at all.
    event = env["controller"].rollback(
        proposal_id="p1",
        actor=ReviewerPrincipal(
            subject="oncall",
            role="sre",
            expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            + timedelta(hours=1),
        ),
        reason="modelhub is dark",
    )
    assert event.target_artifact_id == env["baseline_artifact_id"]
    assert env["controller"].state("p1") == "rollback"


def test_rollback_rejects_unauthenticated_actor(env):
    _activate_for_rollback(env)
    env["controller"].begin_monitoring(proposal_id="p1")
    bad_actor = "not-a-reviewer-principal"
    with pytest.raises(WrapperArtifactError, match="actor must be a ReviewerPrincipal"):
        env["controller"].rollback(proposal_id="p1", actor=bad_actor, reason="r")


def test_rollback_rejects_expired_actor(env):
    import datetime as dt
    _activate_for_rollback(env)
    env["controller"].begin_monitoring(proposal_id="p1")
    expired = ReviewerPrincipal(
        subject="oncall",
        role="sre",
        expires_at=dt.datetime.now(dt.timezone.utc) - timedelta(seconds=1),
    )
    with pytest.raises(WrapperArtifactError, match="rollback actor has expired"):
        env["controller"].rollback(proposal_id="p1", actor=expired, reason="r")


def test_rollback_rejects_double_rollback(env):
    _activate_for_rollback(env)
    env["controller"].begin_monitoring(proposal_id="p1")
    reviewer = ReviewerPrincipal(
        subject="oncall",
        role="sre",
        expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc) + timedelta(hours=1),
    )
    env["controller"].rollback(proposal_id="p1", actor=reviewer, reason="r")
    # The state machine itself refuses rollback -> rollback; the dedicated
    # "already rolled back" guard inside the controller is a second line of
    # defense that would fire if the state machine ever changed.
    with pytest.raises(WrapperArtifactError, match="forbidden wrapper transition|already been rolled back"):
        env["controller"].rollback(proposal_id="p1", actor=reviewer, reason="r")


def test_rollback_rejected_before_activation(env):
    """rollback from review/sandbox_replay is structurally forbidden."""
    _build_proposal(env)
    _attach_sandbox(env)
    reviewer = ReviewerPrincipal(
        subject="oncall",
        role="sre",
        expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc) + timedelta(hours=1),
    )
    with pytest.raises(WrapperArtifactError, match="forbidden wrapper transition"):
        env["controller"].rollback(proposal_id="p1", actor=reviewer, reason="r")


def test_rollback_succeeds_directly_from_human_activation(env):
    """Activation-failure path: rollback from ``human_activation`` without
    entering monitoring first."""
    _activate_for_rollback(env)
    # No begin_monitoring call.
    reviewer = ReviewerPrincipal(
        subject="oncall",
        role="sre",
        expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc) + timedelta(hours=1),
    )
    event = env["controller"].rollback(proposal_id="p1", actor=reviewer, reason="activation problem")
    assert env["controller"].state("p1") == "rollback"
    assert event.target_artifact_id == env["baseline_artifact_id"]


# --------------------------------------------------------------------------- #
# Two-phase flow: first activation's output is the next proposal's rollback target
# --------------------------------------------------------------------------- #


def test_two_phase_flow_first_activation_becomes_next_rollback_target(env):
    """After activating v2, v2 is in the approved_artifacts set and can serve
    as the rollback target for a v3 proposal."""
    v2_activation = _activate_for_rollback(env)
    approved = env["controller"].approved_artifacts()
    assert env["candidate_artifact_id"] in approved
    v2_artifact_id = v2_activation.activated_artifact_id

    # Stage a v3 candidate.
    v3_payload = b"candidate-v3"
    v3_record = env["registry"].put(v3_payload, metadata={"role": "candidate"})
    env["candidate_artifact_id"] = v3_record.artifact_id
    env["candidate_payload_sha256"] = v3_record.sha256

    _build_proposal(env, proposal_id="p2", proposer_subject="proposer-2")
    _attach_sandbox(env, proposal_id="p2", runner_subject="runner-2")
    approval = _request_approval(
        env,
        proposal_id="p2",
        reviewer_subject="reviewer-2",
        rollback_target=v2_artifact_id,
    )
    activation = env["controller"].activate(
        proposal_id="p2",
        approval=approval,
        probe_observation=_verified_observation(),
        probe_requirement=_probe_requirement(),
        reason="promote v3",
    )
    # The v3 activation's rollback target is v2 (previously approved).
    assert activation.rollback_target_artifact_id == v2_artifact_id
    env["controller"].begin_monitoring(proposal_id="p2")
    # Active pointer is now v3.
    assert env["pointers"].pointer("wrapper").active_artifact_id == v3_record.artifact_id
    # Roll v3 back to v2 (the previously-approved artifact).
    reviewer = ReviewerPrincipal(
        subject="oncall",
        role="sre",
        expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc) + timedelta(hours=1),
    )
    event = env["controller"].rollback(proposal_id="p2", actor=reviewer, reason="v3 issue")
    # Pointer restored to v2 — fully offline rollback.
    assert event.target_artifact_id == v2_artifact_id
    assert env["pointers"].pointer("wrapper").active_artifact_id == v2_artifact_id


# --------------------------------------------------------------------------- #
# Audit trail
# --------------------------------------------------------------------------- #


def test_activation_and_rollback_events_are_recorded_in_order(env):
    _activate_for_rollback(env)
    env["controller"].begin_monitoring(proposal_id="p1")
    reviewer = ReviewerPrincipal(
        subject="oncall",
        role="sre",
        expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc) + timedelta(hours=1),
    )
    env["controller"].rollback(proposal_id="p1", actor=reviewer, reason="r")
    activations = env["controller"].activation_events()
    rollbacks = env["controller"].rollback_events()
    assert len(activations) == 1
    assert len(rollbacks) == 1
    assert activations[0].activated_artifact_id == env["candidate_artifact_id"]
    assert rollbacks[0].target_artifact_id == env["baseline_artifact_id"]
    # Rollback recorded after activation.
    assert rollbacks[0].at >= activations[0].at
