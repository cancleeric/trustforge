from __future__ import annotations

import hashlib
import json
import os
import runpy
import stat
import subprocess
import sys
from contextlib import nullcontext
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from trustforge.activation_lock import _set_backend_for_tests
from trustforge.agent.shadow_contracts import canonical_json
from trustforge.asset_intrinsic_promotion import (
    IntrinsicPromotionDecision,
    IntrinsicPromotionReceipt,
    load_intrinsic_promotion_policy,
    policy_digest,
    policy_to_dict,
)
import trustforge.asset_intrinsic_promotion_receipt as receipt_module
from trustforge.asset_intrinsic_promotion_dataset import DATASET_SCHEMA_VERSION
from trustforge.asset_intrinsic_promotion_receipt import (
    EVENT_KIND,
    FAILURE_EVENT_KIND,
    SIGNER_DOMAIN,
    FailureReason,
    ReleaseBinding,
    produce_failure_receipt,
    produce_signed_receipt,
)
from trustforge.deployment_control import (
    DeploymentAuthorization,
    DeploymentControlError,
)
from trustforge.release_router import ReleaseABRouter, RoutedResponse
from trustforge.release_manifest import ReleaseManifest
from trustforge.secure_keyring import (
    SecureKeyringError,
    read_private_keyring,
    read_public_keyring,
)
from trustforge.signed_event_ledger import SignedEventLedger
from trustforge.verified_receipt_release_gate import (
    CEO_AUTHORIZATION_DOMAIN,
    CEO_AUTHORIZATION_VERSION,
    VerifiedReceiptCEOAuthorization,
    VerifiedReceiptReleaseGate,
)

NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)
PIT = "2026-07-27T23:00:00Z"
CEO_KEY = b"z" * 32
RECEIPT_KEY = b"r" * 32
AUDIT_KEY = b"u" * 32
GIT_SHA = "a" * 40
RELEASE_MANIFEST_DIGEST = "sha256:" + "9" * 64


def _public(seed: bytes) -> bytes:
    return (
        Ed25519PrivateKey.from_private_bytes(seed)
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
    )


def _receipt_ledger(tmp_path) -> SignedEventLedger:
    return SignedEventLedger(
        directory=tmp_path / "ledger-root" / "intrinsic-promotion-receipts",
        verification_keys={"receipt-1": _public(RECEIPT_KEY)},
        event_permissions={
            SIGNER_DOMAIN: frozenset({EVENT_KIND, FAILURE_EVENT_KIND})
        },
        domain_keys={SIGNER_DOMAIN: frozenset({"receipt-1"})},
        signing_key_id="receipt-1",
        signing_private_key=RECEIPT_KEY,
        signing_domain=SIGNER_DOMAIN,
        ledger_role="intrinsic-promotion-receipts",
        bootstrap=True,
        coordination_root=tmp_path / "ledger-root",
    )


def _audit_ledger(tmp_path) -> SignedEventLedger:
    return SignedEventLedger(
        directory=tmp_path / "ledger-root" / "release-gate-audit",
        verification_keys={"audit-1": _public(AUDIT_KEY)},
        event_permissions={
            "verified-receipt-release-gate": frozenset(
                {
                    "verified_receipt_canary_intent",
                    "verified_receipt_canary_outcome",
                }
            )
        },
        domain_keys={"verified-receipt-release-gate": frozenset({"audit-1"})},
        signing_key_id="audit-1",
        signing_private_key=AUDIT_KEY,
        signing_domain="verified-receipt-release-gate",
        ledger_role="verified-receipt-release-gate",
        bootstrap=True,
        coordination_root=tmp_path / "ledger-root",
    )


def _dataset(active: str, candidate: str, *, pit_cutoff: str = PIT) -> dict:
    body = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "pit_cutoff": pit_cutoff,
        "provenance": {
            "release_identity": {
                "active_artifact_digest": active,
                "candidate_artifact_digest": candidate,
            }
        },
        "observations": [],
    }
    return {
        **body,
        "dataset_digest": "sha256:"
        + hashlib.sha256(
            b"trustforge.intrinsic-promotion-dataset.v1\x00"
            + canonical_json(body)
        ).hexdigest(),
    }


def _pass_receipt(
    tmp_path, control, monkeypatch, *, generated_at=NOW, pit_cutoff=PIT
):
    ledger = _receipt_ledger(tmp_path)
    dataset = _dataset(
        control.active.release_digest,
        control.candidate.release_digest,
        pit_cutoff=pit_cutoff,
    )
    policy = load_intrinsic_promotion_policy()

    def passing(policy_value, observations, *, benchmark_manifest_digest, **_):
        return IntrinsicPromotionReceipt(
            receipt_domain_version="trustforge.intrinsic-promotion-receipt/v1",
            policy_digest=policy_digest(policy_value),
            observation_root_digest="sha256:" + "f" * 64,
            benchmark_manifest_digest=benchmark_manifest_digest,
            evaluated_at=generated_at.isoformat(),
            policy=policy_to_dict(policy_value),
            decision=IntrinsicPromotionDecision.PASS,
            reasons=(),
            calibration_claim="test-only pass",
            counts={"observations": len(observations)},
        )

    monkeypatch.setattr(receipt_module, "evaluate_promotion", passing)
    event = produce_signed_receipt(
        ledger=ledger,
        release=ReleaseBinding(
            git_sha=GIT_SHA,
            active_artifact_digest=control.active.release_digest,
            shadow_candidate_artifact_digest=control.candidate.release_digest,
            artifact_digest=control.candidate.release_digest,
            release_id="release:candidate@1",
        ),
        pit_cutoff=pit_cutoff,
        policy=policy,
        benchmark_manifest_digest="sha256:" + "b" * 64,
        dataset_loader=lambda: dataset,
        now=lambda: generated_at,
    )
    assert event["decision"] == "pass", (
        event["failure_stage"],
        event["failure_reason"],
    )
    return ledger, event


def _operator_authorization(
    control, *, now: datetime = NOW, nonce: str = "operator-start"
) -> DeploymentAuthorization:
    helpers = runpy.run_path("tests/test_deployment_control.py")
    original = helpers["_authorization"](control, "start", nonce)
    unsigned = {
        **original.unsigned(),
        "actor": "release-operator",
        "key_id": "auth-1",
        "issued_at": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
    }
    signature = (
        Ed25519PrivateKey.from_private_bytes(helpers["AUTH_KEY"])
        .sign(
            b"trustforge.deployment-authorization.v3\x00"
            + canonical_json(unsigned)
        )
        .hex()
    )
    return DeploymentAuthorization(**unsigned, signature=signature)


def _ceo_authorization(
    control,
    event,
    event_hash,
    *,
    actor="ceo",
    nonce="ceo-start-1",
    now: datetime = NOW,
    release_manifest_digest: str = RELEASE_MANIFEST_DIGEST,
):
    records = control._records()
    unsigned = {
        "action": "start-canary",
        "receipt_event_hash": event_hash,
        "git_sha": event["git_sha"],
        "artifact_digest": event["artifact_digest"],
        "release_manifest_digest": release_manifest_digest,
        "policy_digest": event["policy_digest"],
        "dataset_digest": event["dataset_digest"],
        "pit_cutoff": event["pit_cutoff"],
        "expected_control_head": records[-1]["event_hash"],
        "expected_sequence": len(records) + 1,
        "active_artifact_digest": control.active.release_digest,
        "candidate_artifact_digest": control.candidate.release_digest,
        "actor": actor,
        "issued_at": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "nonce": nonce,
        "key_id": "ceo-1",
        "authorization_version": CEO_AUTHORIZATION_VERSION,
    }
    signature = (
        Ed25519PrivateKey.from_private_bytes(CEO_KEY)
        .sign(CEO_AUTHORIZATION_DOMAIN + canonical_json(unsigned))
        .hex()
    )
    return VerifiedReceiptCEOAuthorization(**unsigned, signature=signature)


def _setup(tmp_path, monkeypatch):
    helpers = runpy.run_path("tests/test_deployment_control.py")
    _set_backend_for_tests(helpers["_LockBackend"]())
    control = helpers["_control"](tmp_path, clock=lambda: NOW)
    ledger, event = _pass_receipt(tmp_path, control, monkeypatch)
    record = ledger.read()[-1]
    gate = VerifiedReceiptReleaseGate(
        receipt_ledger=ledger,
        audit_ledger=_audit_ledger(tmp_path),
        deployment_control=control,
        ceo_keys={"ceo-1": _public(CEO_KEY)},
        expected_git_sha=GIT_SHA,
        expected_policy_digest=event["policy_digest"],
        expected_dataset_digest=event["dataset_digest"],
        expected_release_manifest_digest=RELEASE_MANIFEST_DIGEST,
    )
    return control, ledger, event, record, gate


@pytest.fixture(autouse=True)
def reset_activation_backend():
    yield
    _set_backend_for_tests(None)


def test_verified_pass_and_two_person_authorization_only_prepare_canary(
    tmp_path, monkeypatch
):
    control, _, event, record, gate = _setup(tmp_path, monkeypatch)
    prepared = gate.start_canary(
        ceo_authorization=_ceo_authorization(
            control, event, record["event_hash"]
        ),
        operator_authorization=_operator_authorization(control),
        now=NOW,
    )
    assert prepared["event"]["desired_phase"] == "canary"
    assert control.routing_snapshot().phase == "disabled"
    assert control.active.release_digest == event["active_artifact_digest"]
    audit = [item["event"] for item in gate.audit_ledger.read()]
    assert audit[0]["from_phase"] == "disabled"
    assert audit[0]["to_phase"] == "canary"
    assert audit[0]["rollback_artifact_digest"] == control.active.release_digest
    assert audit[0]["receipt_event_hash"] == record["event_hash"]
    assert audit[1]["status"] == "prepared"
    assert audit[1]["control_event_hash"] == prepared["event_hash"]
    with pytest.raises(DeploymentControlError):
        gate.start_canary(
            ceo_authorization=_ceo_authorization(
                control, event, record["event_hash"]
            ),
            operator_authorization=_operator_authorization(control),
            now=NOW,
        )
    helpers = runpy.run_path("tests/test_deployment_control.py")
    completion = helpers["_completion"](
        control, prepared, "start", "complete-start"
    )
    control.complete(completion, now=NOW)
    gate.reconcile_audit()
    assert control.routing_snapshot().phase == "canary"
    assert gate.audit_ledger.read()[-1]["event"]["status"] == "completed"
    router = ReleaseABRouter(
        control,
        {control.policy.routing_key_id: b"r" * 32},
        pinned_a_fallback=control.active,
        manifest_keyring={},
    )
    snapshot = router.ledger.routing_snapshot()
    selected = {
        router._candidate_selected(snapshot, f"asset:{index}")
        for index in range(500)
    }
    assert selected == {False, True}
    assert snapshot.active == control.active
    monkeypatch.setattr(
        control,
        "candidate_connection",
        lambda **kwargs: nullcontext(object()),
    )
    monkeypatch.setattr(
        router,
        "_request",
        lambda endpoint, path, *, release, failed_over, request_headers: (
            RoutedResponse(release.encode(), 200, release, failed_over)
        ),
    )
    b_subject = next(
        f"asset:{index}"
        for index in range(500)
        if router._candidate_selected(snapshot, f"asset:{index}")
    )
    monkeypatch.setattr(
        router,
        "_request_connected_candidate",
        lambda *args, **kwargs: RoutedResponse(b"B", 200, "B", False),
    )
    assert router.route(stable_subject=b_subject).release == "B"
    monkeypatch.setattr(
        router,
        "_request_connected_candidate",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError()),
    )
    failed_over = router.route(stable_subject=b_subject)
    assert failed_over.release == "A"
    assert failed_over.failed_over is True
    rollback = control.prepare(
        "rollback-a",
        helpers["_authorization"](control, "rollback-a", "rollback-a"),
        now=NOW,
    )
    control.complete(
        helpers["_completion"](
            control, rollback, "rollback-a", "rollback-a-complete"
        ),
        now=NOW,
    )
    rolled_back = router.route(stable_subject=b_subject)
    assert rolled_back.release == "A"
    assert control.routing_snapshot().phase == "disabled"
    assert control.routing_snapshot().active == control.active
    with pytest.raises(DeploymentControlError):
        gate.start_canary(
            ceo_authorization=_ceo_authorization(
                control, event, record["event_hash"]
            ),
            operator_authorization=_operator_authorization(control),
            now=NOW,
        )


@pytest.mark.parametrize(
    "change",
    [
        {"receipt_event_hash": "0" * 64},
        {"dataset_digest": "sha256:" + "0" * 64},
        {"release_manifest_digest": "sha256:" + "0" * 64},
        {"expected_sequence": 999},
        {"actor": "release-operator"},
    ],
)
def test_ceo_binding_and_actor_separation_fail_closed(tmp_path, monkeypatch, change):
    control, _, event, record, gate = _setup(tmp_path, monkeypatch)
    authorization = _ceo_authorization(control, event, record["event_hash"])
    tampered = replace(authorization, **change)
    with pytest.raises(DeploymentControlError):
        gate.start_canary(
            ceo_authorization=tampered,
            operator_authorization=_operator_authorization(control),
            now=NOW,
        )
    assert len(control._records()) == 1


def test_audit_replay_rejects_duplicate_prepared_outcome(tmp_path, monkeypatch):
    control, _, event, record, gate = _setup(tmp_path, monkeypatch)
    gate.start_canary(
        ceo_authorization=_ceo_authorization(
            control, event, record["event_hash"]
        ),
        operator_authorization=_operator_authorization(control),
        now=NOW,
    )
    duplicate = dict(gate.audit_ledger.read()[-1]["event"])
    gate.audit_ledger.append(duplicate)
    with pytest.raises(DeploymentControlError, match="transition"):
        gate.verify_audit()


def test_prepare_failure_crash_recovery_and_receipt_race_are_honest(
    tmp_path, monkeypatch
):
    control, _, event, record, gate = _setup(tmp_path / "failure", monkeypatch)
    monkeypatch.setattr(
        "trustforge.deployment_control.acquire_activation_lock",
        lambda *args, **kwargs: False,
    )
    with pytest.raises(DeploymentControlError, match="lock"):
        gate.start_canary(
            ceo_authorization=_ceo_authorization(
                control, event, record["event_hash"]
            ),
            operator_authorization=_operator_authorization(control),
            now=NOW,
        )
    assert gate.audit_ledger.read()[-1]["event"]["status"] == "not_prepared"
    assert not control.activation_transaction(
        gate.audit_ledger.read()[0]["event"]["transaction_id"]
    )

    monkeypatch.undo()
    with pytest.raises(DeploymentControlError, match="CEO authorization"):
        gate.start_canary(
            ceo_authorization=_ceo_authorization(
                control, event, record["event_hash"]
            ),
            operator_authorization=_operator_authorization(
                control, nonce="operator-start-fresh-1"
            ),
            now=NOW,
        )
    with pytest.raises(DeploymentControlError, match="operator authorization"):
        gate.start_canary(
            ceo_authorization=_ceo_authorization(
                control,
                event,
                record["event_hash"],
                nonce="ceo-start-fresh-1",
            ),
            operator_authorization=_operator_authorization(control),
            now=NOW,
        )
    fresh = gate.start_canary(
        ceo_authorization=_ceo_authorization(
            control,
            event,
            record["event_hash"],
            nonce="ceo-start-fresh",
        ),
        operator_authorization=_operator_authorization(
            control, nonce="operator-start-fresh"
        ),
        now=NOW,
    )
    assert fresh["event"]["kind"] == "activation_prepared"

    crash_control, _, crash_event, crash_record, crash_gate = _setup(
        tmp_path / "crash", monkeypatch
    )
    original_reconcile = crash_gate.reconcile_audit
    calls = 0

    def crash_after_prepare():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("crash after control append")
        return original_reconcile()

    monkeypatch.setattr(crash_gate, "reconcile_audit", crash_after_prepare)
    with pytest.raises(RuntimeError, match="crash after"):
        crash_gate.start_canary(
            ceo_authorization=_ceo_authorization(
                crash_control, crash_event, crash_record["event_hash"]
            ),
            operator_authorization=_operator_authorization(crash_control),
            now=NOW,
        )
    monkeypatch.setattr(crash_gate, "reconcile_audit", original_reconcile)
    restarted_control = runpy.run_path("tests/test_deployment_control.py")[
        "_control"
    ](tmp_path / "crash", clock=lambda: NOW)
    restarted_gate = VerifiedReceiptReleaseGate(
        receipt_ledger=_receipt_ledger(tmp_path / "crash"),
        audit_ledger=_audit_ledger(tmp_path / "crash"),
        deployment_control=restarted_control,
        ceo_keys={"ceo-1": _public(CEO_KEY)},
        expected_git_sha=GIT_SHA,
        expected_policy_digest=crash_event["policy_digest"],
        expected_dataset_digest=crash_event["dataset_digest"],
        expected_release_manifest_digest=RELEASE_MANIFEST_DIGEST,
    )
    recovered = restarted_gate.reconcile_audit()
    assert recovered[-1]["event"]["status"] == "prepared"

    audit_control, _, audit_event, audit_record, audit_gate = _setup(
        tmp_path / "audit-unavailable", monkeypatch
    )
    original_append_outcome = audit_gate._append_outcome

    def unavailable(*args, **kwargs):
        raise OSError("audit storage unavailable")

    monkeypatch.setattr(audit_gate, "_append_outcome", unavailable)
    with pytest.raises(OSError, match="audit storage"):
        audit_gate.start_canary(
            ceo_authorization=_ceo_authorization(
                audit_control, audit_event, audit_record["event_hash"]
            ),
            operator_authorization=_operator_authorization(audit_control),
            now=NOW,
        )
    monkeypatch.setattr(audit_gate, "_append_outcome", original_append_outcome)
    reconciled = audit_gate.reconcile_audit()
    assert reconciled[-1]["event"]["status"] == "prepared"

    race_control, race_ledger, race_event, race_record, race_gate = _setup(
        tmp_path / "race", monkeypatch
    )
    original_append = race_gate.audit_ledger.append

    def append_then_block(value, **kwargs):
        result = original_append(value, **kwargs)
        if value["kind"] == "verified_receipt_canary_intent":
            produce_failure_receipt(
                ledger=race_ledger,
                pit_cutoff=PIT,
                stage="policy",
                reason=FailureReason.POLICY_INVALID,
                now=lambda: NOW,
            )
        return result

    monkeypatch.setattr(race_gate.audit_ledger, "append", append_then_block)
    with pytest.raises(DeploymentControlError, match="not eligible"):
        race_gate.start_canary(
            ceo_authorization=_ceo_authorization(
                race_control, race_event, race_record["event_hash"]
            ),
            operator_authorization=_operator_authorization(race_control),
            now=NOW,
        )
    assert len(race_control._records()) == 1
    assert race_gate.audit_ledger.read()[-1]["event"]["status"] == "not_prepared"


def test_latest_block_missing_stale_future_and_pit_lag_fail_closed(
    tmp_path, monkeypatch
):
    control, ledger, event, record, gate = _setup(tmp_path, monkeypatch)
    ceo = _ceo_authorization(control, event, record["event_hash"])
    produce_failure_receipt(
        ledger=ledger,
        pit_cutoff=PIT,
        stage="policy",
        reason=FailureReason.POLICY_INVALID,
        now=lambda: NOW,
    )
    with pytest.raises(DeploymentControlError):
        gate.start_canary(
            ceo_authorization=ceo,
            operator_authorization=_operator_authorization(control),
            now=NOW,
        )

    for offset in (
        timedelta(minutes=91),
        timedelta(seconds=-31),
    ):
        isolated = tmp_path / str(abs(offset.total_seconds()))
        other_control, _, other_event, other_record, other_gate = _setup(
            isolated, monkeypatch
        )
        with pytest.raises(DeploymentControlError):
            other_gate.start_canary(
                ceo_authorization=_ceo_authorization(
                    other_control, other_event, other_record["event_hash"]
                ),
                operator_authorization=_operator_authorization(other_control),
                now=NOW + offset,
            )

    helpers = runpy.run_path("tests/test_deployment_control.py")
    lag_control = helpers["_control"](tmp_path / "pit-lag", clock=lambda: NOW)
    lag_pit = (NOW - timedelta(minutes=91)).isoformat().replace("+00:00", "Z")
    lag_ledger, lag_event = _pass_receipt(
        tmp_path / "pit-lag",
        lag_control,
        monkeypatch,
        pit_cutoff=lag_pit,
    )
    lag_record = lag_ledger.read()[-1]
    lag_gate = VerifiedReceiptReleaseGate(
        receipt_ledger=lag_ledger,
        audit_ledger=_audit_ledger(tmp_path / "pit-lag"),
        deployment_control=lag_control,
        ceo_keys={"ceo-1": _public(CEO_KEY)},
        expected_git_sha=GIT_SHA,
        expected_policy_digest=lag_event["policy_digest"],
        expected_dataset_digest=lag_event["dataset_digest"],
        expected_release_manifest_digest=RELEASE_MANIFEST_DIGEST,
    )
    with pytest.raises(DeploymentControlError):
        lag_gate.start_canary(
            ceo_authorization=_ceo_authorization(
                lag_control, lag_event, lag_record["event_hash"]
            ),
            operator_authorization=_operator_authorization(lag_control),
            now=NOW,
        )

    missing_control = helpers["_control"](
        tmp_path / "missing", clock=lambda: NOW
    )
    missing_gate = VerifiedReceiptReleaseGate(
        receipt_ledger=_receipt_ledger(tmp_path / "missing"),
        audit_ledger=_audit_ledger(tmp_path / "missing"),
        deployment_control=missing_control,
        ceo_keys={"ceo-1": _public(CEO_KEY)},
        expected_git_sha=GIT_SHA,
        expected_policy_digest=event["policy_digest"],
        expected_dataset_digest=event["dataset_digest"],
        expected_release_manifest_digest=RELEASE_MANIFEST_DIGEST,
    )
    with pytest.raises(DeploymentControlError, match="missing"):
        missing_gate._latest_pass(NOW)


def test_gate_rejects_shared_lock_with_mismatched_coordination_root(
    tmp_path, monkeypatch
):
    control, receipt, event, _, gate = _setup(tmp_path, monkeypatch)
    gate.audit_ledger.coordination_root = tmp_path / "other-root"
    with pytest.raises(DeploymentControlError, match="coordination root"):
        VerifiedReceiptReleaseGate(
            receipt_ledger=receipt,
            audit_ledger=gate.audit_ledger,
            deployment_control=control,
            ceo_keys={"ceo-1": _public(CEO_KEY)},
            expected_git_sha=GIT_SHA,
            expected_policy_digest=event["policy_digest"],
            expected_dataset_digest=event["dataset_digest"],
            expected_release_manifest_digest=RELEASE_MANIFEST_DIGEST,
        )


def test_provision_and_runtime_cli_end_to_end(tmp_path, monkeypatch):
    helpers = runpy.run_path("tests/test_deployment_control.py")
    current = datetime.now(timezone.utc).replace(microsecond=0)
    _set_backend_for_tests(helpers["_LockBackend"]())
    control = helpers["_control"](tmp_path, clock=lambda: current)
    pit = (current - timedelta(minutes=60)).isoformat().replace("+00:00", "Z")
    receipt_ledger, event = _pass_receipt(
        tmp_path,
        control,
        monkeypatch,
        generated_at=current,
        pit_cutoff=pit,
    )
    receipt_record = receipt_ledger.read()[-1]
    (tmp_path / "ledger-root").chmod(0o750)

    def private_keyring(path, key_id, seed):
        path.write_text(
            json.dumps(
                {
                    "key_id": key_id,
                    "private_key": seed.hex(),
                    "verification_keys": {key_id: _public(seed).hex()},
                }
            )
        )
        path.chmod(0o600)

    def public_keyring(path, key_id, seed):
        path.write_text(
            json.dumps({"verification_keys": {key_id: _public(seed).hex()}})
        )
        path.chmod(0o644)

    audit_private = tmp_path / "audit-private.json"
    private_keyring(audit_private, "audit-1", AUDIT_KEY)
    env = {
        **os.environ,
        "PYTHONPATH": str(Path("src").resolve()),
        "TRUSTFORGE_HOME": str(tmp_path),
    }
    provision = subprocess.run(
        [
            sys.executable,
            "scripts/provision_verified_release_gate_audit.py",
            "--ledger-root",
            str(tmp_path / "ledger-root"),
            "--bootstrap-keyring",
            str(audit_private),
            "--apply",
            "--test-owner-current-user",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert provision.returncode == 0, provision.stderr
    audit_directory = tmp_path / "ledger-root" / "verified-release-gate-audit"
    assert stat.S_IMODE(audit_directory.stat().st_mode) == 0o700
    assert audit_directory.stat().st_gid == os.getegid()
    for child in audit_directory.iterdir():
        assert stat.S_IMODE(child.stat().st_mode) == 0o600
        assert child.stat().st_gid == os.getegid()
    assert not list(
        (tmp_path / "ledger-root").glob(".verified-release-bootstrap-*")
    )

    manifest = ReleaseManifest(
        artifact_digest=control.candidate.release_digest,
        git_sha=GIT_SHA,
        app_version="1.0.0",
        kernel_contract_version="1",
        kernel_resolution_version="1",
        core_content_hash="sha256:" + "c" * 64,
        config_snapshot_identity="sha256:" + "d" * 64,
        build_timestamp=current.isoformat(),
        build_host="release-builder",
    )
    manifest_path = tmp_path / "release-manifest.json"
    manifest_path.write_text(manifest.to_json())
    manifest_path.chmod(0o644)
    manifest_digest = "sha256:" + hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    config = {
        "target": control.target,
        "target_confirmation": control.target_confirmation,
        "active": asdict(control.active),
        "candidate": asdict(control.candidate),
        "routing_policy": asdict(control.policy),
        "evidence_bundle_digest": control.evidence_bundle_digest,
        "stop_after_errors": control.stop_after_errors,
        "require_distributed_lock": False,
        "expected_git_sha": GIT_SHA,
        "expected_policy_digest": event["policy_digest"],
        "expected_dataset_digest": event["dataset_digest"],
        "release_manifest_digest": manifest_digest,
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    config_path.chmod(0o644)
    key_paths = {}
    for name, key_id, seed in (
        ("receipt", "receipt-1", RECEIPT_KEY),
        ("ceo", "ceo-1", CEO_KEY),
        ("operator", "auth-1", helpers["AUTH_KEY"]),
        ("completion", "complete-1", helpers["COMPLETE_KEY"]),
        ("outcome", "outcome-1", helpers["OUTCOME_KEY"]),
    ):
        path = tmp_path / f"{name}-public.json"
        public_keyring(path, key_id, seed)
        key_paths[name] = path
    control_private = tmp_path / "control-private.json"
    private_keyring(control_private, "control-1", helpers["CONTROL_KEY"])
    control_public = tmp_path / "control-public.json"
    public_keyring(control_public, "control-1", helpers["CONTROL_KEY"])
    audit_public = tmp_path / "audit-public.json"
    public_keyring(audit_public, "audit-1", AUDIT_KEY)
    operator = _operator_authorization(control, now=current)
    ceo = _ceo_authorization(
        control,
        event,
        receipt_record["event_hash"],
        now=current,
        release_manifest_digest=manifest_digest,
    )
    operator_path = tmp_path / "operator.json"
    operator_path.write_text(json.dumps(asdict(operator)))
    operator_path.chmod(0o644)
    ceo_path = tmp_path / "ceo.json"
    ceo_path.write_text(json.dumps(asdict(ceo)))
    ceo_path.chmod(0o644)
    base = [
        sys.executable,
        "scripts/run_verified_receipt_release_gate.py",
        "--ledger-root",
        str(tmp_path / "ledger-root"),
        "--config",
        str(config_path),
        "--release-manifest",
        str(manifest_path),
        "--receipt-public-keyring",
        str(key_paths["receipt"]),
        "--ceo-public-keyring",
        str(key_paths["ceo"]),
        "--operator-public-keyring",
        str(key_paths["operator"]),
        "--completion-public-keyring",
        str(key_paths["completion"]),
        "--outcome-public-keyring",
        str(key_paths["outcome"]),
        "--test-owner-current-user",
    ]
    absent_root = tmp_path / "unprovisioned"
    absent_root.mkdir(mode=0o750)
    absent_lock = absent_root / "coordination.lock"
    absent_lock.touch(mode=0o600)
    unprovisioned = subprocess.run(
        [
            *base,
            "--ledger-root",
            str(absent_root),
            "--control-public-keyring",
            str(control_public),
            "--audit-public-keyring",
            str(audit_public),
            "verify-audit",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert unprovisioned.returncode != 0
    assert AUDIT_KEY.hex() not in unprovisioned.stderr
    verify_public = [
        "--control-public-keyring",
        str(control_public),
        "--audit-public-keyring",
        str(audit_public),
        "verify-audit",
    ]
    manifest_path.chmod(0o666)
    unsafe_manifest = subprocess.run(
        [*base, *verify_public], capture_output=True, text=True, env=env
    )
    assert unsafe_manifest.returncode != 0
    manifest_path.chmod(0o644)
    manifest_link = tmp_path / "manifest-link.json"
    manifest_link.symlink_to(manifest_path)
    linked_base = list(base)
    linked_base[linked_base.index(str(manifest_path))] = str(manifest_link)
    linked_manifest = subprocess.run(
        [*linked_base, *verify_public],
        capture_output=True,
        text=True,
        env=env,
    )
    assert linked_manifest.returncode != 0
    mismatched_manifest = tmp_path / "mismatched-manifest.json"
    mismatched = json.loads(manifest_path.read_text())
    mismatched["app_version"] = "9.9.9"
    mismatched_manifest.write_text(json.dumps(mismatched))
    mismatched_manifest.chmod(0o644)
    mismatched_base = list(base)
    mismatched_base[mismatched_base.index(str(manifest_path))] = str(
        mismatched_manifest
    )
    mismatch_result = subprocess.run(
        [*mismatched_base, *verify_public],
        capture_output=True,
        text=True,
        env=env,
    )
    assert mismatch_result.returncode != 0
    run = subprocess.run(
        [
            *base,
            "--control-keyring",
            str(control_private),
            "--audit-keyring",
            str(audit_private),
            "run",
            "--operator-authorization",
            str(operator_path),
            "--ceo-authorization",
            str(ceo_path),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["status"] == "prepared"
    reconcile = subprocess.run(
        [
            *base,
            "--control-public-keyring",
            str(control_public),
            "--audit-keyring",
            str(audit_private),
            "reconcile",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert json.loads(reconcile.stdout)
    verify = subprocess.run(
        [
            *base,
            "--control-public-keyring",
            str(control_public),
            "--audit-public-keyring",
            str(audit_public),
            "verify-audit",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert json.loads(verify.stdout)
    rejected = subprocess.run(
        [
            *base,
            "--control-public-keyring",
            str(control_private),
            "--audit-public-keyring",
            str(audit_private),
            "verify-audit",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert rejected.returncode != 0
    assert AUDIT_KEY.hex() not in rejected.stderr


def test_production_audit_bootstrap_runs_as_operator_identity(monkeypatch):
    provision = runpy.run_path(
        "scripts/provision_verified_release_gate_audit.py"
    )
    state = {"euid": 0, "observed": None, "order": []}

    class FakeLedger:
        def __init__(self, **kwargs):
            state["observed"] = state["euid"]

    globals_ = provision["_bootstrap_worker"].__globals__
    monkeypatch.setitem(globals_, "SignedEventLedger", FakeLedger)
    monkeypatch.setitem(
        globals_,
        "_identity",
        lambda args: {
            "root_uid": 0,
            "group": "release",
            "owner_uid": 1234,
            "lock_mode": 0o660,
            "directory_mode": 0o750,
            "file_mode": 0o640,
        },
    )
    monkeypatch.setattr(
        provision["grp"],
        "getgrnam",
        lambda name: type("Group", (), {"gr_gid": 5678})(),
    )
    monkeypatch.setattr(
        provision["os"], "geteuid", lambda: state["euid"]
    )
    monkeypatch.setattr(
        provision["os"],
        "setgroups",
        lambda value: state["order"].append(("groups", value)),
    )
    monkeypatch.setattr(
        provision["os"],
        "setgid",
        lambda value: state["order"].append(("gid", value)),
    )
    monkeypatch.setattr(
        provision["os"],
        "setuid",
        lambda value: (
            state["order"].append(("uid", value)),
            state.update(euid=value),
        ),
    )
    read_fd, write_fd = os.pipe()
    os.write(
        write_fd,
        json.dumps(
            {
                "key_id": "audit-1",
                "private_key": AUDIT_KEY.hex(),
                "verification_keys": {
                    "audit-1": _public(AUDIT_KEY).hex()
                },
            }
        ).encode(),
    )
    os.close(write_fd)
    args = type(
        "Args",
        (),
        {
            "test_owner_current_user": False,
            "bootstrap_fd": read_fd,
            "ledger_root": Path("/ledger"),
            "test_worker_failure": False,
            "test_worker_hang": False,
        },
    )()
    provision["_bootstrap_worker"](args)
    assert state["observed"] == 1234
    assert state["order"] == [
        ("groups", [5678]),
        ("gid", 5678),
        ("uid", 1234),
    ]
    assert state["euid"] == 1234


def test_provision_worker_failure_keeps_parent_identity_and_cleans_up(
    tmp_path, monkeypatch
):
    # The real subprocess failure path is exercised with a malformed credential.
    root = tmp_path / "ledger-root"
    root.mkdir(mode=0o750)
    (root / "coordination.lock").touch(mode=0o600)
    credential = tmp_path / "private.json"
    credential.write_text(
        json.dumps(
            {
                "key_id": "audit-1",
                "private_key": AUDIT_KEY.hex(),
                "verification_keys": {
                    "audit-1": _public(AUDIT_KEY).hex()
                },
            }
        )
    )
    credential.chmod(0o600)
    before = os.geteuid()
    result = subprocess.run(
        [
            sys.executable,
            "scripts/provision_verified_release_gate_audit.py",
            "--ledger-root",
            str(root),
            "--bootstrap-keyring",
            str(credential),
            "--apply",
            "--test-owner-current-user",
            "--test-worker-failure",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path("src").resolve())},
    )
    assert result.returncode != 0
    assert os.geteuid() == before
    assert not (root / "verified-release-gate-audit").exists()
    assert AUDIT_KEY.hex() not in " ".join(result.args)
    assert AUDIT_KEY.hex() not in result.stderr
    assert not list(root.glob(".verified-release-bootstrap-*"))


def test_near_limit_pipe_credential_real_subprocess_does_not_hang(tmp_path):
    root = tmp_path / "ledger-root"
    root.mkdir(mode=0o750)
    (root / "coordination.lock").touch(mode=0o600)
    verification_keys = {
        f"key-{index:03d}": bytes([index % 256]) * 32
        for index in range(240)
    }
    verification_keys["audit-1"] = _public(AUDIT_KEY)
    credential = tmp_path / "near-limit-private.json"
    credential.write_text(
        json.dumps(
            {
                "key_id": "audit-1",
                "private_key": AUDIT_KEY.hex(),
                "verification_keys": {
                    key: value.hex()
                    for key, value in verification_keys.items()
                },
            }
        )
    )
    credential.chmod(0o600)
    assert 16_384 < credential.stat().st_size <= 32_768
    result = subprocess.run(
        [
            sys.executable,
            "scripts/provision_verified_release_gate_audit.py",
            "--ledger-root",
            str(root),
            "--bootstrap-keyring",
            str(credential),
            "--apply",
            "--test-owner-current-user",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path("src").resolve())},
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_bootstrap_worker_timeout_cleans_target_and_preserves_parent(tmp_path):
    root = tmp_path / "ledger-root"
    root.mkdir(mode=0o750)
    (root / "coordination.lock").touch(mode=0o600)
    credential = tmp_path / "private.json"
    credential.write_text(
        json.dumps(
            {
                "key_id": "audit-1",
                "private_key": AUDIT_KEY.hex(),
                "verification_keys": {
                    "audit-1": _public(AUDIT_KEY).hex()
                },
            }
        )
    )
    credential.chmod(0o600)
    before = os.geteuid()
    result = subprocess.run(
        [
            sys.executable,
            "scripts/provision_verified_release_gate_audit.py",
            "--ledger-root",
            str(root),
            "--bootstrap-keyring",
            str(credential),
            "--apply",
            "--test-owner-current-user",
            "--test-worker-hang",
            "--worker-timeout",
            "0.1",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path("src").resolve())},
        timeout=5,
    )
    assert result.returncode != 0
    assert os.geteuid() == before
    assert not (root / "verified-release-gate-audit").exists()


@pytest.mark.parametrize(
    "payload, message",
    [
        (b"", "empty"),
        (b"{", "invalid JSON"),
        (b"x" * 32_769, "maximum size"),
    ],
)
def test_bootstrap_pipe_rejects_eof_malformed_and_oversize(
    tmp_path, payload, message
):
    provision = runpy.run_path(
        "scripts/provision_verified_release_gate_audit.py"
    )
    path = tmp_path / "pipe-payload"
    path.write_bytes(payload)
    descriptor = os.open(path, os.O_RDONLY)
    with pytest.raises(SecureKeyringError, match=message):
        provision["_read_pipe_credential"](descriptor)


def test_secure_keyring_contract_rejects_mode_symlink_mismatch_and_private_public(
    tmp_path, monkeypatch
):
    seed = b"s" * 32
    public = _public(seed)
    private = tmp_path / "private.json"
    private.write_text(
        json.dumps(
            {
                "key_id": "key-1",
                "private_key": seed.hex(),
                "verification_keys": {"key-1": public.hex()},
            }
        )
    )
    private.chmod(0o600)
    assert read_private_keyring(private)[0] == "key-1"
    with pytest.raises(SecureKeyringError):
        read_public_keyring(private)

    unsafe = tmp_path / "unsafe.json"
    unsafe.write_text("{}")
    unsafe.chmod(0o666)
    with pytest.raises(SecureKeyringError):
        read_private_keyring(unsafe)
    symlink = tmp_path / "link.json"
    symlink.symlink_to(private)
    with pytest.raises(SecureKeyringError):
        read_private_keyring(symlink)

    mismatch = tmp_path / "mismatch.json"
    mismatch.write_text(
        json.dumps(
            {
                "key_id": "key-1",
                "private_key": seed.hex(),
                "verification_keys": {"key-1": _public(b"x" * 32).hex()},
            }
        )
    )
    mismatch.chmod(0o600)
    with pytest.raises(SecureKeyringError) as caught:
        read_private_keyring(mismatch)
    assert seed.hex() not in str(caught.value)

    for name, raw in (
        ("malformed", b"{"),
        ("truncated", b'{"verification_keys":'),
        ("oversize", b"x" * 32_769),
    ):
        path = tmp_path / f"{name}.json"
        path.write_bytes(raw)
        path.chmod(0o600)
        with pytest.raises(SecureKeyringError):
            read_private_keyring(path)

    replaced = tmp_path / "replaced.json"
    replaced.write_bytes(private.read_bytes())
    replaced.chmod(0o600)
    original_read = os.read
    changed = False

    def change_during_read(descriptor, count):
        nonlocal changed
        value = original_read(descriptor, count)
        if not changed:
            changed = True
            replaced.write_bytes(replaced.read_bytes() + b" ")
        return value

    monkeypatch.setattr(os, "read", change_during_read)
    with pytest.raises(SecureKeyringError, match="changed during read"):
        read_private_keyring(replaced)
