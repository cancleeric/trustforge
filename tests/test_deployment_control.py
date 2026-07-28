from __future__ import annotations

import errno
import hashlib
import json
import os
import threading
from dataclasses import asdict, replace
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
    return (
        Ed25519PrivateKey.from_private_bytes(seed)
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
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
    digest = (
        "sha256:"
        + hashlib.sha256(
            b"trustforge.routing-policy.v1\x00" + canonical_json(payload)
        ).hexdigest()
    )
    return RoutingPolicy(**payload, policy_digest=digest)


def _control(tmp_path, *, clock=lambda: NOW):
    active = ReleaseEndpoint(_digest("a"), "http://127.0.0.1:18081", "manifest-1")
    candidate = ReleaseEndpoint(_digest("b"), "http://127.0.0.1:18082", "manifest-1")
    target = "trustforge-production"
    confirmation = (
        f"PRODUCTION:{target}:{active.release_digest}:{candidate.release_digest}"
    )
    ledger = SignedEventLedger(
        directory=tmp_path / "ledger-root" / "control",
        verification_keys={"control-1": _public(CONTROL_KEY)},
        event_permissions={
            "release-control": frozenset(
                {
                    "deployment_initialized",
                    "operator_stop",
                    "activation_prepared",
                    "activation_completed",
                    "activation_failed",
                }
            )
        },
        domain_keys={"release-control": frozenset({"control-1"})},
        signing_key_id="control-1",
        signing_private_key=CONTROL_KEY,
        signing_domain="release-control",
        ledger_role="release-control",
        bootstrap=True,
        coordination_root=tmp_path / "ledger-root",
    )
    outcome_ledger = SignedEventLedger(
        directory=tmp_path / "ledger-root" / "router-outcomes",
        verification_keys={"outcome-1": _public(OUTCOME_KEY)},
        event_permissions={
            "release-router-outcome": frozenset(
                {
                    "candidate_reservation",
                    "candidate_result",
                    "router_emergency_stop",
                }
            )
        },
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
        clock=clock,
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
        "expected_control_head": control._records()[-1]["event_hash"],
        "expected_sequence": len(control._records()) + 1,
        "actor": "ceo",
        "issued_at": (NOW - timedelta(minutes=1)).isoformat(),
        "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
        "nonce": nonce,
        "key_id": "auth-1",
        "receipt_version": "trustforge.deployment-authorization/v3",
    }
    signature = (
        Ed25519PrivateKey.from_private_bytes(AUTH_KEY)
        .sign(
            b"trustforge.deployment-authorization.v3\x00" + canonical_json(unsigned),
        )
        .hex()
    )
    return DeploymentAuthorization(**unsigned, signature=signature)


def _completion(control, prepared, action, nonce, *, pointer=None, status="completed"):
    unsigned = {
        "transaction_id": prepared["event"]["transaction_id"],
        "action": action,
        "target": control.target,
        "prepared_event_hash": prepared["event_hash"],
        "active_artifact_digest": control.active.release_digest,
        "candidate_artifact_digest": control.candidate.release_digest,
        "pointer_active_digest": pointer
        or (
            control.candidate.release_digest
            if action == "promote"
            else control.active.release_digest
        ),
        "observed_manifest_digest": pointer
        or (
            control.candidate.release_digest
            if action == "promote"
            else control.active.release_digest
        ),
        "status": status,
        "verified_at": NOW.isoformat(),
        "actor": "release-operator",
        "nonce": nonce,
        "key_id": "complete-1",
        "receipt_version": "trustforge.activation-completion/v1",
    }
    signature = (
        Ed25519PrivateKey.from_private_bytes(COMPLETE_KEY)
        .sign(
            b"trustforge.activation-completion.v1\x00" + canonical_json(unsigned),
        )
        .hex()
    )
    return ActivationCompletionReceipt(**unsigned, signature=signature)


@pytest.fixture(autouse=True)
def activation_backend():
    backend = _LockBackend()
    _set_backend_for_tests(backend)
    yield
    _set_backend_for_tests(None)


def test_prepared_is_not_active_and_completed_receipt_reconciles_pointer(tmp_path):
    control = _control(tmp_path)
    prepared = control.prepare(
        "start", _authorization(control, "start", "auth-start"), now=NOW
    )
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
    prepared = control.prepare(
        "start", _authorization(control, "start", "auth-start"), now=NOW
    )
    forged = _completion(
        control,
        prepared,
        "start",
        "complete-start",
        pointer=control.candidate.release_digest,
    )
    with pytest.raises(DeploymentControlError, match="binding"):
        control.complete(forged, now=NOW)
    assert control.routing_snapshot().activation_status == "prepared"


def test_failed_activation_is_distinct_and_observed_known_pointer_is_accepted(tmp_path):
    control = _control(tmp_path)
    prepared = control.prepare(
        "start", _authorization(control, "start", "auth-start"), now=NOW
    )
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
    prepared = control.prepare(
        "start", _authorization(control, "start", "auth-start"), now=NOW
    )
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
        _completion(control, prepared, "start", f"complete-start-{suffix}"),
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
    control.prepare("stop", _authorization(control, "stop", "auth-stop-epoch"), now=NOW)
    _start_canary(control, "epoch-2")
    restarted = _control(tmp_path)
    state = restarted.routing_snapshot()
    assert state.phase == "canary"
    assert state.candidate_requests == 0
    assert state.consecutive_errors == 0
    epochs = {
        record["event"]["canary_epoch"] for record in restarted.outcome_ledger.read()
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


def test_expired_authorization_cannot_be_consumed_with_backdated_event_time(tmp_path):
    control = _control(tmp_path)
    receipt = _authorization(control, "start", "expired-backdate")
    backdated = replace(
        receipt,
        issued_at=(NOW - timedelta(minutes=10)).isoformat(),
        expires_at=(NOW - timedelta(minutes=1)).isoformat(),
        signature="",
    )
    signature = (
        Ed25519PrivateKey.from_private_bytes(AUTH_KEY)
        .sign(
            b"trustforge.deployment-authorization.v3\x00"
            + canonical_json(backdated.unsigned())
        )
        .hex()
    )
    with pytest.raises(DeploymentControlError, match="not current"):
        control.prepare(
            "start",
            replace(backdated, signature=signature),
            now=NOW - timedelta(minutes=5),
        )


def test_direct_backdated_append_cannot_revive_expired_authorization(tmp_path):
    control = _control(tmp_path)
    receipt = _authorization(control, "start", "direct-expired")
    receipt = replace(
        receipt,
        issued_at=(NOW - timedelta(minutes=10)).isoformat(),
        expires_at=(NOW - timedelta(minutes=1)).isoformat(),
        signature="",
    )
    receipt = replace(
        receipt,
        signature=Ed25519PrivateKey.from_private_bytes(AUTH_KEY)
        .sign(
            b"trustforge.deployment-authorization.v3\x00"
            + canonical_json(receipt.unsigned())
        )
        .hex(),
    )
    transaction = hashlib.sha256(
        b"trustforge.activation-transaction.v1\x00"
        + canonical_json(
            {
                "ledger_id": receipt.ledger_id,
                "action": receipt.action,
                "nonce": receipt.nonce,
            }
        )
    ).hexdigest()
    control.ledger.append(
        {
            "kind": "activation_prepared",
            "transaction_id": transaction,
            "action": "start",
            "desired_phase": "canary",
            "nonce": receipt.nonce,
            "actor": receipt.actor,
            "owner_id": f"deployment-control:{transaction}",
            "evidence_bundle_digest": control.evidence_bundle_digest,
            "active_artifact_digest": control.active.release_digest,
            "candidate_artifact_digest": control.candidate.release_digest,
            "routing_policy_digest": control.policy.policy_digest,
            "at": (NOW - timedelta(minutes=5)).isoformat(),
            "authorization_receipt": asdict(receipt),
        }
    )
    with pytest.raises(DeploymentControlError, match="stale"):
        control.routing_snapshot()


def test_router_snapshot_hot_path_is_read_only(tmp_path):
    control = _control(tmp_path)
    _start_canary(control, "read-only-hot-path")
    before = {
        path.name: (path.stat().st_mtime_ns, path.stat().st_size)
        for path in control.ledger.directory.iterdir()
    }
    for _ in range(20):
        assert control.routing_snapshot().phase == "canary"
    after = {
        path.name: (path.stat().st_mtime_ns, path.stat().st_size)
        for path in control.ledger.directory.iterdir()
    }
    assert after == before
    assert "monotonic-time-floor" not in after


def test_naive_clock_is_rejected_before_timezone_conversion(tmp_path):
    with pytest.raises(DeploymentControlError, match="timezone aware"):
        _control(tmp_path, clock=lambda: NOW.replace(tzinfo=None))


def test_unsigned_checkpoint_time_mutation_self_heals_from_terminal_history(tmp_path):
    control = _control(tmp_path)
    _start_canary(control, "checkpoint-time-mutation")
    payload = json.loads(control._checkpoint_path.read_text())
    payload["floor_at"] = (NOW + timedelta(days=1)).isoformat()
    control._checkpoint_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    )
    os.chmod(control._checkpoint_path, 0o600)
    assert control.routing_snapshot().candidate_blocked is False
    assert json.loads(control._checkpoint_path.read_text())["floor_at"] == (
        NOW.isoformat()
    )


def test_read_only_router_cannot_heal_corrupt_checkpoint(tmp_path):
    control = _control(tmp_path)
    _start_canary(control, "router-no-heal")
    damaged = b'{"corrupt":true}\n'
    control._checkpoint_path.write_bytes(damaged)
    os.chmod(control._checkpoint_path, 0o600)
    control.ledger._private_key = None
    assert control.routing_snapshot().candidate_blocked is True
    assert control._checkpoint_path.read_bytes() == damaged


def test_operator_rebuilds_missing_checkpoint_from_signed_terminal_history(tmp_path):
    control = _control(tmp_path)
    _start_canary(control, "operator-checkpoint-rebuild")
    terminal = control._records()[-1]
    control._checkpoint_path.unlink()

    rebuilt = control.rebuild_checkpoint()

    assert rebuilt == json.loads(control._checkpoint_path.read_text())
    assert rebuilt["control_sequence"] == terminal["sequence"]
    assert rebuilt["control_head"] == terminal["event_hash"]
    control.ledger._private_key = None
    with pytest.raises(DeploymentControlError, match="operator signing identity"):
        control.rebuild_checkpoint()


def test_checkpoint_rebuild_rejects_signed_but_semantically_invalid_terminal(tmp_path):
    control = _control(tmp_path)
    _start_canary(control, "invalid-terminal-rebuild")
    control.ledger.append(
        {
            "kind": "operator_stop",
            "at": NOW.isoformat(),
            "checkpoint_floor_at": NOW.isoformat(),
        }
    )
    control._checkpoint_path.unlink()

    with pytest.raises(DeploymentControlError, match="authorization is absent"):
        control.rebuild_checkpoint()
    assert not control._checkpoint_path.exists()


def test_checkpoint_rebuild_rejects_terminal_with_invalid_monotonic_floor(tmp_path):
    control = _control(tmp_path)
    _start_canary(control, "invalid-floor-rebuild")
    authorization = _authorization(control, "stop", "invalid-floor-stop")
    control.ledger.append(
        {
            "kind": "operator_stop",
            "nonce": authorization.nonce,
            "actor": authorization.actor,
            "at": NOW.isoformat(),
            "checkpoint_floor_at": (NOW + timedelta(seconds=1)).isoformat(),
            "authorization_receipt": asdict(authorization),
        }
    )
    control._checkpoint_path.unlink()

    with pytest.raises(DeploymentControlError, match="checkpoint floor is invalid"):
        control.rebuild_checkpoint()
    assert not control._checkpoint_path.exists()


def test_checkpoint_rollback_and_tamper_fail_terminal_head_challenge(tmp_path):
    control = _control(tmp_path)
    _start_canary(control, "checkpoint-first")
    old = control._checkpoint_path.read_bytes()
    control.prepare("stop", _authorization(control, "stop", "checkpoint-stop"), now=NOW)
    control._checkpoint_path.write_bytes(old)
    os.chmod(control._checkpoint_path, 0o600)
    assert control.routing_snapshot().candidate_blocked is False
    damaged = bytearray(old)
    damaged[-3] ^= 1
    control._checkpoint_path.write_bytes(damaged)
    os.chmod(control._checkpoint_path, 0o600)
    assert control.routing_snapshot().candidate_blocked is False
    assert (
        json.loads(control._checkpoint_path.read_text())["control_head"]
        == (control._records()[-1]["event_hash"])
    )


def test_concurrent_checkpoint_readers_never_observe_partial_file(tmp_path):
    control = _control(tmp_path)
    _start_canary(control, "checkpoint-concurrency")
    records = control._records()
    terminal = records[-1]
    errors = []

    def writer():
        for _ in range(30):
            control._write_checkpoint(terminal_record=terminal)

    def reader():
        for _ in range(100):
            try:
                control.routing_snapshot()
            except Exception as exc:  # asserted below
                errors.append(exc)

    workers = [threading.Thread(target=writer)] + [
        threading.Thread(target=reader) for _ in range(3)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(5)
        assert not worker.is_alive()
    assert errors == []


def test_clock_rollback_blocks_b_and_start_but_status_remains_available(tmp_path):
    observed = [NOW]
    control = _control(tmp_path, clock=lambda: observed[0])
    _start_canary(control, "clock-checkpoint")
    observed[0] = NOW - timedelta(minutes=2)
    restarted = _control(tmp_path, clock=lambda: observed[0])
    assert restarted.routing_snapshot().phase == "canary"
    assert restarted.routing_snapshot().candidate_blocked is True
    receipt = _authorization(restarted, "start", "clock-rollback-start")
    with pytest.raises(DeploymentControlError, match="stale|clock rolled back"):
        restarted.prepare("start", receipt, now=observed[0])
    stop_receipt = _authorization(restarted, "stop", "clock-rollback-stop")
    stop_receipt = replace(
        stop_receipt,
        issued_at=(NOW - timedelta(minutes=3)).isoformat(),
        signature="",
    )
    stop_receipt = replace(
        stop_receipt,
        signature=Ed25519PrivateKey.from_private_bytes(AUTH_KEY)
        .sign(
            b"trustforge.deployment-authorization.v3\x00"
            + canonical_json(stop_receipt.unsigned())
        )
        .hex(),
    )
    restarted.prepare("stop", stop_receipt, now=observed[0])
    assert restarted.routing_snapshot().phase == "stopped"
    assert restarted.routing_snapshot().candidate_blocked is True


def test_rollback_completion_older_than_authorization_preserves_signed_floor(
    tmp_path,
):
    control = _control(tmp_path)
    _start_canary(control, "floor-before-rollback")
    control.prepare("stop", _authorization(control, "stop", "floor-stop"), now=NOW)
    prior_floor = control._records()[-1]["event"]["checkpoint_floor_at"]
    prepared = control.prepare(
        "rollback-a",
        _authorization(control, "rollback-a", "floor-rollback"),
        now=NOW,
    )
    completion = _completion(control, prepared, "rollback-a", "floor-rollback-complete")
    completion = replace(
        completion,
        verified_at=(NOW - timedelta(minutes=5)).isoformat(),
        signature="",
    )
    completion = replace(
        completion,
        signature=Ed25519PrivateKey.from_private_bytes(COMPLETE_KEY)
        .sign(
            b"trustforge.activation-completion.v1\x00"
            + canonical_json(completion.unsigned())
        )
        .hex(),
    )
    control.complete(completion, now=NOW)
    assert control._records()[-1]["event"]["checkpoint_floor_at"] == prior_floor
    assert (
        json.loads(control._checkpoint_path.read_text())["control_head"]
        == (control._records()[-1]["event_hash"])
    )


def test_projection_rejects_failed_receipt_as_completed_event(tmp_path):
    control = _control(tmp_path)
    prepared = control.prepare(
        "start", _authorization(control, "start", "failed-kind"), now=NOW
    )
    receipt = _completion(
        control,
        prepared,
        "start",
        "failed-kind-complete",
        status="failed",
    )
    control.ledger.append(
        {
            "kind": "activation_completed",
            "transaction_id": receipt.transaction_id,
            "action": receipt.action,
            "prepared_event_hash": receipt.prepared_event_hash,
            "pointer_active_digest": receipt.pointer_active_digest,
            "observed_manifest_digest": receipt.observed_manifest_digest,
            "activation_receipt_digest": "sha256:"
            + hashlib.sha256(
                canonical_json(receipt.unsigned() | {"signature": receipt.signature})
            ).hexdigest(),
            "nonce": receipt.nonce,
            "actor": receipt.actor,
            "at": NOW.isoformat(),
            "checkpoint_floor_at": NOW.isoformat(),
            "completion_receipt": asdict(receipt),
        }
    )
    with pytest.raises(DeploymentControlError, match="semantics mismatch"):
        control.routing_snapshot()


def test_projection_rejects_tampered_prepared_semantic_metadata(tmp_path):
    control = _control(tmp_path)
    receipt = _authorization(control, "start", "tampered-metadata")
    transaction = hashlib.sha256(
        b"trustforge.activation-transaction.v1\x00"
        + canonical_json(
            {
                "ledger_id": receipt.ledger_id,
                "action": receipt.action,
                "nonce": receipt.nonce,
            }
        )
    ).hexdigest()
    control.ledger.append(
        {
            "kind": "activation_prepared",
            "transaction_id": transaction,
            "action": "start",
            "desired_phase": "promoted",
            "nonce": receipt.nonce,
            "actor": receipt.actor,
            "owner_id": f"deployment-control:{transaction}",
            "evidence_bundle_digest": control.evidence_bundle_digest,
            "active_artifact_digest": control.active.release_digest,
            "candidate_artifact_digest": control.candidate.release_digest,
            "routing_policy_digest": control.policy.policy_digest,
            "at": NOW.isoformat(),
            "authorization_receipt": asdict(receipt),
        }
    )
    with pytest.raises(DeploymentControlError, match="semantics mismatch"):
        control.routing_snapshot()


def test_emergency_stop_retries_outcome_contention(tmp_path, monkeypatch):
    control = _control(tmp_path)
    _start_canary(control, "contention")
    original = control.outcome_ledger.append
    attempts = 0

    def contend(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 4:
            raise LedgerError("simulated contention")
        return original(*args, **kwargs)

    monkeypatch.setattr(control.outcome_ledger, "append", contend)
    control.emergency_stop(
        ledger_id=control.routing_snapshot().ledger_id,
        reason="candidate_outcome_unrecordable",
    )
    assert attempts == 4
    assert control.routing_snapshot().phase == "stopped"


def test_restart_allows_a_second_epoch_scoped_emergency_stop(tmp_path):
    control = _control(tmp_path)
    _start_canary(control, "emergency-1")
    control.emergency_stop(
        ledger_id=control.routing_snapshot().ledger_id,
        reason="candidate_outcome_unrecordable",
    )
    prepared = control.prepare(
        "rollback-a",
        _authorization(control, "rollback-a", "rollback-after-emergency"),
        now=NOW,
    )
    control.complete(
        _completion(control, prepared, "rollback-a", "rollback-complete"),
        now=NOW,
    )
    _start_canary(control, "emergency-2")
    control.emergency_stop(
        ledger_id=control.routing_snapshot().ledger_id,
        reason="candidate_outcome_unrecordable",
    )
    emergency = [
        item["event"]
        for item in control.outcome_ledger.read()
        if item["event"]["kind"] == "router_emergency_stop"
    ]
    assert len(emergency) == 2
    assert emergency[0]["nonce"] != emergency[1]["nonce"]


def test_candidate_connection_rechecks_stop_before_network_start(tmp_path):
    control = _control(tmp_path)
    _start_canary(control, "concurrent-stop")
    state = control.routing_snapshot()
    reservation_id = "d" * 32
    control.reserve_candidate(
        expected_head=state.ledger_head,
        reservation_id=reservation_id,
    )
    control.prepare(
        "stop",
        _authorization(control, "stop", "stop-before-network"),
        now=NOW,
    )
    network_started = False
    with pytest.raises(LedgerError, match="stale"):
        with control.candidate_connection(
            endpoint=control.candidate,
            reservation_id=reservation_id,
            connect_timeout=0.1,
        ):
            network_started = True
    assert network_started is False


def test_connect_handoff_orders_stop_and_does_not_lock_hanging_response(
    tmp_path, monkeypatch
):
    control = _control(tmp_path)
    _start_canary(control, "hanging-b")
    state = control.routing_snapshot()
    reservation_id = "e" * 32
    control.reserve_candidate(
        expected_head=state.ledger_head, reservation_id=reservation_id
    )
    epoch = control._canary_epoch(control._records())
    assert epoch is not None
    connect_entered = threading.Event()
    allow_connect = threading.Event()
    connected = threading.Event()
    release_hang = threading.Event()
    stop_done = threading.Event()

    class BoundedSocket:
        def setblocking(self, _enabled):
            return None

        def connect_ex(self, _address):
            connect_entered.set()
            assert allow_connect.wait(1)
            return 0

        def settimeout(self, _timeout):
            return None

        def close(self):
            return None

    monkeypatch.setattr(
        "trustforge.deployment_control.socket.socket",
        lambda *_args: BoundedSocket(),
    )

    def hanging_candidate():
        with control.candidate_connection(
            endpoint=control.candidate,
            reservation_id=reservation_id,
            connect_timeout=0.1,
        ):
            connected.set()
            release_hang.wait(2)

    def stop():
        control.prepare(
            "stop",
            _authorization(control, "stop", "stop-hanging-b"),
            now=NOW,
        )
        stop_done.set()

    worker = threading.Thread(target=hanging_candidate)
    worker.start()
    assert connect_entered.wait(1)
    stopper = threading.Thread(target=stop)
    stopper.start()
    assert not stop_done.wait(0.05)
    allow_connect.set()
    assert connected.wait(1)
    assert stop_done.wait(0.5)
    assert worker.is_alive()
    assert control.ledger.epoch_stopped(
        ledger_id=control._records()[0]["ledger_id"], canary_epoch=epoch
    )
    release_hang.set()
    worker.join(1)
    stopper.join(1)
    assert not worker.is_alive()
    assert not stopper.is_alive()


def test_candidate_connect_uses_hard_nonblocking_select_deadline(tmp_path, monkeypatch):
    control = _control(tmp_path)
    _start_canary(control, "connect-deadline")
    state = control.routing_snapshot()
    reservation_id = "f" * 32
    control.reserve_candidate(
        expected_head=state.ledger_head, reservation_id=reservation_id
    )

    class StalledSocket:
        def setblocking(self, _enabled):
            return None

        def connect_ex(self, _address):
            return errno.EINPROGRESS

        def close(self):
            return None

    monkeypatch.setattr(
        "trustforge.deployment_control.socket.socket",
        lambda *_args: StalledSocket(),
    )
    observed_timeout = []

    def deadline_select(_read, _write, _errors, timeout):
        observed_timeout.append(timeout)
        return [], [], []

    monkeypatch.setattr("trustforge.deployment_control.select.select", deadline_select)
    with pytest.raises(TimeoutError, match="deadline"):
        with control.candidate_connection(
            endpoint=control.candidate,
            reservation_id=reservation_id,
            connect_timeout=0.2,
        ):
            pytest.fail("stalled connect cannot yield")
    assert observed_timeout == [0.2]


def test_latch_first_stop_crash_is_idempotently_reconciled(tmp_path, monkeypatch):
    control = _control(tmp_path)
    _start_canary(control, "latch-crash")
    receipt = _authorization(control, "stop", "latch-crash-stop")
    original = control.ledger.append
    failed = False

    def crash_once(event, **kwargs):
        nonlocal failed
        if event.get("kind") == "operator_stop" and not failed:
            failed = True
            raise OSError("simulated terminal publication crash")
        return original(event, **kwargs)

    monkeypatch.setattr(control.ledger, "append", crash_once)
    with pytest.raises(OSError, match="publication"):
        control.prepare("stop", receipt, now=NOW)
    assert control.routing_snapshot().phase == "stopped"
    result = control.prepare("stop", receipt, now=NOW)
    assert result["event"]["kind"] == "operator_stop"
    assert control._checkpoint_path.exists()
    assert control.routing_snapshot().candidate_blocked is False


def test_checkpoint_publication_failure_never_wedges_emergency_control(
    tmp_path, monkeypatch
):
    control = _control(tmp_path)
    prepared = control.prepare(
        "start",
        _authorization(control, "start", "checkpoint-publish-fail"),
        now=NOW,
    )
    monkeypatch.setattr(
        control,
        "_write_checkpoint",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    control.complete(
        _completion(control, prepared, "start", "checkpoint-fail-complete"),
        now=NOW,
    )
    state = control.routing_snapshot()
    assert state.phase == "canary"
    assert state.candidate_blocked is True
    control.prepare(
        "stop",
        _authorization(control, "stop", "checkpoint-fail-stop"),
        now=NOW,
    )
    assert control.routing_snapshot().phase == "stopped"
    rollback = control.prepare(
        "rollback-a",
        _authorization(control, "rollback-a", "checkpoint-fail-rollback"),
        now=NOW,
    )
    control.complete(
        _completion(control, rollback, "rollback-a", "checkpoint-fail-reconcile"),
        now=NOW,
    )
    assert control.routing_snapshot().phase == "disabled"
