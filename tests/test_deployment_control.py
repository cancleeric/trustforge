from __future__ import annotations

import hashlib
import hmac
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from trustforge.activation_lock import (
    ActivationLockRecord,
    _set_backend_for_tests,
)
from trustforge.agent.shadow_contracts import canonical_json
from trustforge.authenticated_ledger import AuthenticatedLedger
from trustforge.deployment_control import (
    ActivationCompletionReceipt,
    DeploymentAuthorization,
    DeploymentControlError,
    DeploymentControlLedger,
)
from trustforge.release_router import ReleaseEndpoint, RoutingPolicy

NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)
AUTH_KEY = b"a" * 32
COMPLETE_KEY = b"c" * 32


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
    ledger = AuthenticatedLedger(
        keyring={"ledger-1": b"l" * 32},
        active_key_id="ledger-1",
        test_directory_override=tmp_path / "ledger",
    )
    control = DeploymentControlLedger(
        ledger,
        authorization_keys={"auth-1": AUTH_KEY},
        completion_keys={"complete-1": COMPLETE_KEY},
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
    signature = hmac.new(
        AUTH_KEY,
        b"trustforge.deployment-authorization.v2\x00" + canonical_json(unsigned),
        hashlib.sha256,
    ).hexdigest()
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
    signature = hmac.new(
        COMPLETE_KEY,
        b"trustforge.activation-completion.v1\x00" + canonical_json(unsigned),
        hashlib.sha256,
    ).hexdigest()
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
