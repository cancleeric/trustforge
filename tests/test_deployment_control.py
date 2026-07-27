from __future__ import annotations

import hashlib
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from trustforge.activation_lock import (
    ActivationLockRecord,
    _set_backend_for_tests,
)
from trustforge.agent.shadow_contracts import canonical_json
from trustforge.authenticated_ledger import LedgerError
from trustforge.deployment_control import (
    ActivationCompletionReceipt,
    DeploymentAuthorization,
    DeploymentControlError,
    DeploymentControlLedger,
)
from trustforge.release_router import ReleaseEndpoint, RoutingPolicy
from trustforge.signed_event_ledger import SignedEventLedger

NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)
AUTH_KEY = b"a" * 32
COMPLETE_KEY = b"c" * 32
CONTROL_KEY = b"l" * 32
OUTCOME_KEY = b"o" * 32


def _public(seed):
    return Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw
    )


class _LockBackend:
    def __init__(self):
        self.records = {}

    def acquire(self, target, owner_id, ttl):
        if target in self.records:
            return False
        self.records[target] = ActivationLockRecord(
            target, owner_id, NOW.timestamp(), NOW.timestamp() + ttl
        )
        return True

    def release(self, target, owner_id):
        if self.records.get(target) and self.records[target].owner_id == owner_id:
            self.records.pop(target)
            return True
        return False

    def get(self, target):
        return self.records.get(target)


def _digest(letter):
    return "sha256:" + letter * 64


def _policy():
    payload = {
        "ratio_basis_points": 500,
        "request_cap": 100,
        "timeout_ms": 500,
        "routing_key_id": "route-1",
        "ramp_id": "ramp-1",
    }
    digest = "sha256:" + hashlib.sha256(
        b"trustforge.routing-policy.v1\x00" + canonical_json(payload)
    ).hexdigest()
    return RoutingPolicy(**payload, policy_digest=digest)


def _control(tmp_path):
    active = ReleaseEndpoint(_digest("a"), "http://127.0.0.1:18081", "manifest-1")
    candidate = ReleaseEndpoint(_digest("b"), "http://127.0.0.1:18082", "manifest-1")
    target = "trustforge-production"
    confirmation = f"PRODUCTION:{target}:{active.release_digest}:{candidate.release_digest}"
    ledger = SignedEventLedger(
        directory=tmp_path / "control-ledger",
        verification_keys={"control-1": _public(CONTROL_KEY)},
        event_permissions={"release-control": frozenset({
            "deployment_initialized", "operator_stop", "activation_prepared",
            "activation_completed", "activation_failed",
        })},
        domain_keys={"release-control": frozenset({"control-1"})},
        signing_key_id="control-1",
        signing_private_key=CONTROL_KEY,
        signing_domain="release-control",
        ledger_role="release-control",
        bootstrap=True,
        coordination_root=tmp_path / "ledger-root",
    )
    outcome_ledger = SignedEventLedger(
        directory=tmp_path / "outcome-ledger",
        verification_keys={"outcome-1": _public(OUTCOME_KEY)},
        event_permissions={"release-router-outcome": frozenset({
            "candidate_reservation", "candidate_result", "router_emergency_stop",
        })},
        domain_keys={"release-router-outcome": frozenset({"outcome-1"})},
        signing_key_id="outcome-1",
        signing_private_key=OUTCOME_KEY,
        signing_domain="release-router-outcome",
        ledger_role="release-router-outcomes",
        bootstrap=True,
        coordination_root=tmp_path / "ledger-root",
    )
    control = DeploymentControlLedger(
        ledger,
        outcome_ledger=outcome_ledger,
        authorization_keys={"auth-1": _public(AUTH_KEY)},
        completion_keys={"complete-1": _public(COMPLETE_KEY)},
        target=target,
        target_confirmation=confirmation,
        active=active,
        candidate=candidate,
        policy=_policy(),
        evidence_bundle_digest=_digest("e"),
        stop_after_errors=2,
        require_distributed_lock=False,
    )
    control.initialize()
    return control


def _authorization(control, action, nonce):
    snapshot = control.routing_snapshot()
    unsigned = {
        "action": action,
        "target": control.target,
        "target_confirmation": control.target_confirmation,
        "ledger_id": snapshot.ledger_id,
        "active_artifact_digest": control.active.release_digest,
        "candidate_artifact_digest": control.candidate.release_digest,
        "evidence_bundle_digest": control.evidence_bundle_digest,
        "routing_policy_digest": control.policy.policy_digest,
        "routing_key_id": control.policy.routing_key_id,
        "actor": "ceo",
        "issued_at": (NOW - timedelta(minutes=1)).isoformat(),
        "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
        "nonce": nonce,
        "key_id": "auth-1",
        "receipt_version": "trustforge.deployment-authorization/v2",
    }
    signature = Ed25519PrivateKey.from_private_bytes(AUTH_KEY).sign(
        b"trustforge.deployment-authorization.v2\x00" + canonical_json(unsigned),
    ).hex()
    return DeploymentAuthorization(**unsigned, signature=signature)


def _completion(control, prepared, action, nonce, *, pointer=None, status="completed"):
    unsigned = {
        "transaction_id": prepared["event"]["transaction_id"],
        "action": action,
        "target": control.target,
        "prepared_event_hash": prepared["event_hash"],
        "active_artifact_digest": control.active.release_digest,
        "candidate_artifact_digest": control.candidate.release_digest,
        "pointer_active_digest": pointer or (
            control.candidate.release_digest if action == "promote"
            else control.active.release_digest
        ),
        "observed_manifest_digest": pointer or (
            control.candidate.release_digest if action == "promote"
            else control.active.release_digest
        ),
        "status": status,
        "verified_at": NOW.isoformat(),
        "actor": "release-operator",
        "nonce": nonce,
        "key_id": "complete-1",
        "receipt_version": "trustforge.activation-completion/v1",
    }
    signature = Ed25519PrivateKey.from_private_bytes(COMPLETE_KEY).sign(
        b"trustforge.activation-completion.v1\x00" + canonical_json(unsigned),
    ).hex()
    return ActivationCompletionReceipt(**unsigned, signature=signature)


@pytest.fixture(autouse=True)
def activation_backend():
    backend = _LockBackend()
    _set_backend_for_tests(backend)
    yield
    _set_backend_for_tests(None)


def test_prepared_is_not_active_and_completed_receipt_reconciles_pointer(tmp_path):
    control = _control(tmp_path)
    prepared = control.prepare("start", _authorization(control, "start", "auth-start"), now=NOW)
    state = control.routing_snapshot()
    assert state.phase == "disabled"
    assert state.desired_phase == "canary"
    assert state.activation_status == "prepared"
    restarted = _control(tmp_path)
    assert restarted.routing_snapshot().phase == "disabled"
    restarted.complete(
        _completion(restarted, prepared, "start", "complete-start"), now=NOW
    )
    assert restarted.routing_snapshot().phase == "canary"


def test_forged_pointer_completion_is_rejected_and_lock_remains_for_retry(tmp_path):
    control = _control(tmp_path)
    prepared = control.prepare("start", _authorization(control, "start", "auth-start"), now=NOW)
    forged = _completion(
        control, prepared, "start", "complete-start", pointer=control.candidate.release_digest
    )
    with pytest.raises(DeploymentControlError, match="binding"):
        control.complete(forged, now=NOW)
    assert control.routing_snapshot().activation_status == "prepared"


def test_failed_activation_is_distinct_and_observed_known_pointer_is_accepted(tmp_path):
    control = _control(tmp_path)
    prepared = control.prepare("start", _authorization(control, "start", "auth-start"), now=NOW)
    failed = _completion(
        control,
        prepared,
        "start",
        "complete-failed",
        pointer=control.candidate.release_digest,
        status="failed",
    )
    control.complete(failed, now=NOW)
    state = control.routing_snapshot()
    assert state.phase == "recovery_required"
    assert state.activation_status == "failed"


def test_authorization_binds_evidence_policy_target_ledger_and_key(tmp_path):
    control = _control(tmp_path)
    valid = _authorization(control, "start", "auth-start")
    for field, value in (
        ("evidence_bundle_digest", _digest("f")),
        ("routing_key_id", "wrong"),
        ("target_confirmation", "PRODUCTION:wrong"),
        ("ledger_id", "0" * 32),
    ):
        with pytest.raises(DeploymentControlError):
            control.prepare("start", replace(valid, **{field: value}), now=NOW)


def test_production_prepare_rejects_local_json_activation_lock(tmp_path, monkeypatch):
    control = _control(tmp_path)
    control.require_distributed_lock = True
    monkeypatch.setenv("TRUSTFORGE_ACTIVATION_LOCK_BACKEND", "json")
    with pytest.raises(DeploymentControlError, match="distributed"):
        control.prepare(
            "start", _authorization(control, "start", "auth-start"), now=NOW
        )


def test_candidate_results_atomically_auto_stop_and_do_not_log_subject(tmp_path):
    control = _control(tmp_path)
    prepared = control.prepare("start", _authorization(control, "start", "auth-start"), now=NOW)
    control.complete(_completion(control, prepared, "start", "complete-start"), now=NOW)
    first = control.routing_snapshot()
    reserved = control.reserve_candidate(
        expected_head=first.ledger_head, reservation_id="1" * 32
    )
    control.record_candidate_result(
        expected_head=reserved.ledger_head,
        reservation_id="1" * 32,
        ok=False,
        status_code=503,
        latency_ms=10,
        error_kind="candidate_http_or_transport_error",
    )
    second = control.routing_snapshot()
    assert second.phase == "canary"
    reserved2 = control.reserve_candidate(
        expected_head=second.ledger_head, reservation_id="2" * 32
    )
    stopped = control.record_candidate_result(
        expected_head=reserved2.ledger_head,
        reservation_id="2" * 32,
        ok=False,
        status_code=503,
        latency_ms=11,
        error_kind="candidate_http_or_transport_error",
    )
    assert stopped.phase == "stopped"
    serialized = canonical_json([item["event"] for item in control.ledger.read()])
    assert b"subject" not in serialized


def _start_canary(control, suffix):
    prepared = control.prepare(
        "start", _authorization(control, "start", f"auth-start-{suffix}"), now=NOW
    )
    control.complete(
        _completion(
            control, prepared, "start", f"complete-start-{suffix}"
        ),
        now=NOW,
    )


def test_stop_wins_coordination_lock_before_reservation_deterministically(tmp_path):
    control = _control(tmp_path)
    _start_canary(control, "race")
    before = control.routing_snapshot()
    attempted = threading.Event()
    result = {}

    def reserve():
        attempted.set()
        try:
            control.reserve_candidate(
                expected_head=before.ledger_head, reservation_id="a" * 32
            )
        except Exception as exc:  # asserted below
            result["error"] = exc

    with control.ledger.coordination_lock():
        worker = threading.Thread(target=reserve)
        worker.start()
        assert attempted.wait(1)
        control._prepare_locked(
            "stop", _authorization(control, "stop", "auth-stop-race"), now=NOW
        )
    worker.join(2)
    assert not worker.is_alive()
    assert isinstance(result.get("error"), LedgerError)
    assert control.routing_snapshot().candidate_requests == 0
    assert control.routing_snapshot().phase == "stopped"


def test_restart_canary_epoch_excludes_old_ramp_outcomes(tmp_path):
    control = _control(tmp_path)
    _start_canary(control, "epoch-1")
    first = control.routing_snapshot()
    reserved = control.reserve_candidate(
        expected_head=first.ledger_head, reservation_id="b" * 32
    )
    control.record_candidate_result(
        expected_head=reserved.ledger_head,
        reservation_id="b" * 32,
        ok=False,
        status_code=503,
        latency_ms=1,
        error_kind="candidate_http_or_transport_error",
    )
    control.prepare(
        "stop", _authorization(control, "stop", "auth-stop-epoch"), now=NOW
    )
    _start_canary(control, "epoch-2")
    restarted = _control(tmp_path)
    state = restarted.routing_snapshot()
    assert state.phase == "canary"
    assert state.candidate_requests == 0
    assert state.consecutive_errors == 0
    epochs = {
        record["event"]["canary_epoch"]
        for record in restarted.outcome_ledger.read()
    }
    assert len(epochs) == 1
    assert next(iter(epochs)) != restarted._canary_epoch(restarted._records())


@pytest.mark.parametrize("key_role", ["authorization_keys", "completion_keys"])
def test_projection_reverifies_persisted_independent_receipts(tmp_path, key_role):
    control = _control(tmp_path)
    _start_canary(control, key_role)
    setattr(control, key_role, {"wrong": b"x" * 32})
    with pytest.raises(DeploymentControlError, match="signature"):
        control.routing_snapshot()
