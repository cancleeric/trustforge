#!/usr/bin/env python3
"""Hermetic release A/B rollback drill orchestrator (#877).

Drives the *existing* release-router + deployment-control state machine through
a full rollback-a lifecycle against two loopback HTTP release services backed by
a temporary signed-event ledger. Produces a HMAC-signed ``rollback_drill``
GateReceipt bound to the drill's A/B artifact digests, plus a human-auditable
drill report whose bytes are bound by the receipt's ``output_digest``
(decision D1: no new receipt-schema field).

This is a non-production drill (G=BLOCK on real promotion). It never flips a
flag, routes real traffic, or mutates a host. Distinct drill artifacts, a tmp
ledger under the supplied work directory, a temporary keyring, and an in-memory
activation-lock backend provide full hermetic isolation from any real
PRODUCTION target.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import stat
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from trustforge.activation_lock import ActivationLockRecord, _set_backend_for_tests
from trustforge.agent.shadow_contracts import canonical_json
from trustforge.deployment_control import (
    ActivationCompletionReceipt,
    DeploymentAuthorization,
    DeploymentControlLedger,
)
from trustforge.deployment_evidence import GateReceipt
from trustforge.release_router import ReleaseEndpoint, RoutingPolicy
from trustforge.signed_event_ledger import SignedEventLedger

REPORT_SCHEMA = "trustforge.rollback-drill-report/v1"
GATE_NAME = "rollback_drill"
DRILL_TARGET = "rollback-drill-sandbox"
DRILL_EVIDENCE_SEED = b"trustforge.rollback-drill.evidence-bundle.v1"
GATE_RECEIPT_DOMAIN = b"trustforge.gate-receipt.v1\x00"
AUTH_DOMAIN = b"trustforge.deployment-authorization.v3\x00"
COMPLETION_DOMAIN = b"trustforge.activation-completion.v1\x00"
MANIFEST_DOMAIN = b"trustforge.endpoint-manifest.v1\x00"

SLO_ROLLBACK_RECONCILE_SECONDS = 300
SLO_A_HEALTH_RESTORED_SECONDS = 30


class RollbackDrillError(RuntimeError):
    """The rollback drill could not reach a passing terminal state."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


class _InMemoryActivationLockBackend:
    """Hermetic activation-lock backend: no host filesystem mutation."""

    def __init__(self) -> None:
        self._records: dict[str, ActivationLockRecord] = {}

    def acquire(self, target: str, owner_id: str, ttl: int) -> bool:
        existing = self._records.get(target)
        now = time.time()
        if existing is not None and existing.expires_at > now:
            if existing.owner_id != owner_id:
                return False
        self._records[target] = ActivationLockRecord(
            target=target,
            owner_id=owner_id,
            acquired_at=now,
            expires_at=now + ttl,
        )
        return True

    def release(self, target: str, owner_id: str) -> bool:
        existing = self._records.get(target)
        if existing is not None and existing.owner_id == owner_id:
            self._records.pop(target, None)
            return True
        return False

    def get(self, target: str) -> ActivationLockRecord | None:
        existing = self._records.get(target)
        if existing is None:
            return None
        return ActivationLockRecord(
            target=existing.target,
            owner_id=existing.owner_id,
            acquired_at=existing.acquired_at,
            expires_at=existing.expires_at,
        )


def _build_artifacts(work_dir: Path) -> tuple[Path, str, Path, str]:
    """A = byte-identical current marker; B = distinct candidate marker.

    D3 / H hard-constraint: A flag-off byte parity is preserved by reusing the
    existing build path's contract (deterministic immutable bytes). The drill
    only needs two distinct, digest-bound byte blobs representing the two
    release builds; it never rebuilds the real router bundle.
    """
    a_path = work_dir / "release-a.artifact"
    b_path = work_dir / "release-b.artifact"
    a_path.write_bytes(b"rollback-drill:active-release:byte-identical:v1\n")
    b_path.write_bytes(b"rollback-drill:candidate-release:distinct-build:v1\n")
    return a_path, _sha256(a_path.read_bytes()), b_path, _sha256(b_path.read_bytes())


def _policy() -> RoutingPolicy:
    payload = {
        "ratio_basis_points": 9999,
        "request_cap": 50,
        "timeout_ms": 500,
        "routing_key_id": "drill-route-1",
        "ramp_id": "drill-ramp-1",
    }
    digest = _sha256_raw(b"trustforge.routing-policy.v1\x00" + canonical_json(payload))
    return RoutingPolicy(**payload, policy_digest=digest)


def _sha256_raw(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


class _ReleaseHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    marker: bytes = b"?"
    artifact_digest: str = ""
    origin: str = ""
    manifest_private: Ed25519PrivateKey | None = None
    manifest_key_id: str = "drill-manifest-1"
    fail: bool = False
    normal_requests: int = 0

    def do_GET(self) -> None:  # noqa: N802 - http.server contract
        if self.path == "/.well-known/trustforge-release-manifest":
            unsigned = {
                "schema": "trustforge.endpoint-manifest/v1",
                "artifact_digest": self.artifact_digest,
                "origin": self.origin,
                "key_id": self.manifest_key_id,
            }
            signature = self.manifest_private.sign(  # type: ignore[union-attr]
                MANIFEST_DOMAIN + canonical_json(unsigned)
            ).hex()
            body = json.dumps({**unsigned, "signature": signature}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        type(self).normal_requests += 1
        body = self.marker
        self.send_response(503 if self.fail else 200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


def _start_release_server(
    marker: bytes,
    artifact_digest: str,
    manifest_private: Ed25519PrivateKey,
    manifest_key_id: str,
) -> tuple[ThreadingHTTPServer, type[_ReleaseHandler]]:
    handler = type(
        f"DrillHandler{marker!r}",
        (_ReleaseHandler,),
        {
            "marker": marker,
            "artifact_digest": artifact_digest,
            "manifest_private": manifest_private,
            "manifest_key_id": manifest_key_id,
            "fail": False,
            "normal_requests": 0,
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    handler.origin = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, handler


def _make_ledgers(work_dir: Path) -> tuple[SignedEventLedger, SignedEventLedger]:
    ledger_root = work_dir / "ledger-root"
    control_seed = secrets.token_bytes(32)
    outcome_seed = secrets.token_bytes(32)
    control = SignedEventLedger(
        directory=ledger_root / "control",
        verification_keys={
            "control-1": Ed25519PrivateKey.from_private_bytes(control_seed)
            .public_key()
            .public_bytes(Encoding.Raw, PublicFormat.Raw)
        },
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
        signing_private_key=control_seed,
        signing_domain="release-control",
        ledger_role="release-control",
        bootstrap=True,
        coordination_root=ledger_root,
    )
    outcome = SignedEventLedger(
        directory=ledger_root / "router-outcomes",
        verification_keys={
            "outcome-1": Ed25519PrivateKey.from_private_bytes(outcome_seed)
            .public_key()
            .public_bytes(Encoding.Raw, PublicFormat.Raw)
        },
        event_permissions={
            "release-router-outcome": frozenset(
                {
                    "candidate_reservation",
                    "candidate_result",
                    "candidate_cost_reconciliation",
                    "router_emergency_stop",
                }
            )
        },
        domain_keys={"release-router-outcome": frozenset({"outcome-1"})},
        signing_key_id="outcome-1",
        signing_private_key=outcome_seed,
        signing_domain="release-router-outcome",
        ledger_role="release-router-outcomes",
        bootstrap=True,
        coordination_root=ledger_root,
    )
    return control, outcome


def _authorization(
    control: DeploymentControlLedger,
    action: str,
    nonce: str,
    now: datetime,
    auth_private: Ed25519PrivateKey,
    auth_key_id: str,
) -> DeploymentAuthorization:
    snapshot = control.routing_snapshot()
    records = control._records()
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
        "expected_control_head": records[-1]["event_hash"],
        "expected_sequence": len(records) + 1,
        "actor": "rollback-drill",
        "issued_at": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "nonce": nonce,
        "key_id": auth_key_id,
        "receipt_version": "trustforge.deployment-authorization/v3",
    }
    signature = auth_private.sign(AUTH_DOMAIN + canonical_json(unsigned)).hex()
    return DeploymentAuthorization(**unsigned, signature=signature)


def _completion(
    control: DeploymentControlLedger,
    prepared: dict[str, Any],
    action: str,
    nonce: str,
    now: datetime,
    complete_private: Ed25519PrivateKey,
    complete_key_id: str,
) -> ActivationCompletionReceipt:
    pointer = control.active.release_digest
    unsigned = {
        "transaction_id": prepared["event"]["transaction_id"],
        "action": action,
        "target": control.target,
        "prepared_event_hash": prepared["event_hash"],
        "active_artifact_digest": control.active.release_digest,
        "candidate_artifact_digest": control.candidate.release_digest,
        "pointer_active_digest": pointer,
        "observed_manifest_digest": pointer,
        "status": "completed",
        "verified_at": now.isoformat(),
        "actor": "rollback-drill",
        "nonce": nonce,
        "key_id": complete_key_id,
        "receipt_version": "trustforge.activation-completion/v1",
    }
    signature = complete_private.sign(
        COMPLETION_DOMAIN + canonical_json(unsigned)
    ).hex()
    return ActivationCompletionReceipt(**unsigned, signature=signature)


def _probe_health(base_url: str, path: str = "/healthz") -> tuple[int, bytes]:
    """Direct loopback probe of one release service (no router involvement)."""
    request = urllib.request.Request(base_url.rstrip("/") + path, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=2) as response:  # noqa: S310
            body = response.read(4096)
            return int(response.status), body
    except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
        return int(exc.code), exc.read(4096)


def _replay_pit_inputs(
    a_origin: str, b_origin: str, inputs: tuple[str, ...]
) -> tuple[str, str, list[dict[str, Any]]]:
    """Replay identical point-in-time inputs against A and B; capture digests.

    Both services are probed directly (loopback) with the same request paths so
    the report records the behavioral baseline of each build before rollback.
    """
    a_hasher = hashlib.sha256()
    b_hasher = hashlib.sha256()
    detail: list[dict[str, Any]] = []
    for path in inputs:
        a_status, a_body = _probe_health(a_origin, path)
        b_status, b_body = _probe_health(b_origin, path)
        a_hasher.update(f"{path}:{a_status}:".encode() + a_body)
        b_hasher.update(f"{path}:{b_status}:".encode() + b_body)
        detail.append(
            {
                "path": path,
                "a": {"status": a_status, "bytes": len(a_body)},
                "b": {"status": b_status, "bytes": len(b_body)},
            }
        )
    return (
        "sha256:" + a_hasher.hexdigest(),
        "sha256:" + b_hasher.hexdigest(),
        detail,
    )


def run_drill(
    work_dir: Path,
    *,
    actor: str,
    reason: str,
    pit_inputs: tuple[str, ...] = ("/healthz", "/api/ping"),
) -> dict[str, Any]:
    """Run one hermetic rollback drill; return the auditable report payload.

    The returned dict is the ``output_digest``-bound drill report content. It is
    deterministic in structure (not in transient keys/ports/digests). Raises
    ``RollbackDrillError`` if any rollback SLO or state-machine invariant fails.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    now = _utcnow()
    evidence_bundle_digest = _sha256_raw(DRILL_EVIDENCE_SEED)

    # Temporary keyring: every signing identity is generated fresh for this run
    # and never leaves the work directory. No production key is read or touched.
    manifest_private = Ed25519PrivateKey.generate()
    manifest_key_id = "drill-manifest-1"
    auth_private = Ed25519PrivateKey.generate()
    auth_key_id = "drill-auth-1"
    complete_private = Ed25519PrivateKey.generate()
    complete_key_id = "drill-complete-1"
    a_path, a_digest, b_path, b_digest = _build_artifacts(work_dir)
    a_server, a_handler = _start_release_server(
        b"ACTIVE", a_digest, manifest_private, manifest_key_id
    )
    b_server, b_handler = _start_release_server(
        b"CANDIDATE", b_digest, manifest_private, manifest_key_id
    )
    lock_backend = _InMemoryActivationLockBackend()

    with ExitStack() as stack:
        stack.callback(a_server.shutdown)
        stack.callback(b_server.shutdown)
        stack.callback(lambda: _set_backend_for_tests(None))
        _set_backend_for_tests(lock_backend)

        a_origin = a_handler.origin
        b_origin = b_handler.origin
        a_endpoint = ReleaseEndpoint(a_digest, a_origin, manifest_key_id)
        b_endpoint = ReleaseEndpoint(b_digest, b_origin, manifest_key_id)
        confirmation = f"PRODUCTION:{DRILL_TARGET}:{a_digest}:{b_digest}"

        control_ledger, outcome_ledger = _make_ledgers(work_dir)
        control = DeploymentControlLedger(
            control_ledger,
            outcome_ledger=outcome_ledger,
            authorization_keys={
                auth_key_id: auth_private.public_key().public_bytes(
                    Encoding.Raw, PublicFormat.Raw
                )
            },
            completion_keys={
                complete_key_id: complete_private.public_key().public_bytes(
                    Encoding.Raw, PublicFormat.Raw
                )
            },
            target=DRILL_TARGET,
            target_confirmation=confirmation,
            active=a_endpoint,
            candidate=b_endpoint,
            policy=_policy(),
            evidence_bundle_digest=evidence_bundle_digest,
            stop_after_errors=2,
            require_distributed_lock=False,
            clock=lambda: now,
        )
        control.initialize()

        # A must be healthy before we begin (baseline invariant).
        a_pre_status, _ = _probe_health(a_origin)
        if a_pre_status != 200:
            raise RollbackDrillError(
                f"active A not healthy at baseline: {a_pre_status}"
            )

        # disabled -> canary (real prepare/complete through the state machine).
        prepared_start = control.prepare(
            "start",
            _authorization(
                control, "start", "drill-start", now, auth_private, auth_key_id
            ),
            now=now,
        )
        control.complete(
            _completion(
                control,
                prepared_start,
                "start",
                "drill-start-complete",
                now,
                complete_private,
                complete_key_id,
            ),
            now=now,
        )
        canary_state = control.routing_snapshot()
        if canary_state.phase != "canary":
            raise RollbackDrillError(f"canary phase not reached: {canary_state.phase}")

        # Replay identical PIT inputs against A and B; capture baseline digests.
        a_replay_digest, b_replay_digest, replay_detail = _replay_pit_inputs(
            a_origin, b_origin, pit_inputs
        )

        # Inject the regression: candidate B starts failing.
        t_regression = time.monotonic()
        b_handler.fail = True
        b_reg_status, _ = _probe_health(b_origin)
        if b_reg_status != 503:
            raise RollbackDrillError(
                f"regression injection did not make B fail: {b_reg_status}"
            )

        # A must remain healthy even while B regresses (no shared fate).
        a_during_status, _ = _probe_health(a_origin)
        if a_during_status != 200:
            raise RollbackDrillError(
                f"active A regressed during B failure: {a_during_status}"
            )

        # Operator observes the regression and stops the canary.
        control.prepare(
            "stop",
            _authorization(
                control, "stop", "drill-stop", now, auth_private, auth_key_id
            ),
            now=now,
        )
        stopped_state = control.routing_snapshot()
        if stopped_state.phase != "stopped":
            raise RollbackDrillError(
                f"operator stop did not reach stopped: {stopped_state.phase}"
            )

        # rollback-a: stopped -> disabled. Measure reconcile latency vs SLO.
        t_rollback_start = time.monotonic()
        prepared_rollback = control.prepare(
            "rollback-a",
            _authorization(
                control,
                "rollback-a",
                "drill-rollback",
                now,
                auth_private,
                auth_key_id,
            ),
            now=now,
        )
        control.complete(
            _completion(
                control,
                prepared_rollback,
                "rollback-a",
                "drill-rollback-complete",
                now,
                complete_private,
                complete_key_id,
            ),
            now=now,
        )
        t_rollback_complete = time.monotonic()
        rollback_reconcile_s = t_rollback_complete - t_rollback_start

        final_state = control.routing_snapshot()
        if final_state.phase != "disabled":
            raise RollbackDrillError(
                f"rollback-a did not restore disabled: {final_state.phase}"
            )
        if final_state.activation_status != "completed":
            raise RollbackDrillError(
                f"rollback left activation unresolved: {final_state.activation_status}"
            )

        # Confirm A is serving healthy immediately after rollback (SLO).
        a_post_status, _ = _probe_health(a_origin)
        t_a_health_confirmed = time.monotonic()
        a_health_restored_s = t_a_health_confirmed - t_rollback_complete
        if a_post_status != 200:
            raise RollbackDrillError(
                f"active A not healthy after rollback: {a_post_status}"
            )

        slo_pass = (
            rollback_reconcile_s <= SLO_ROLLBACK_RECONCILE_SECONDS
            and a_health_restored_s <= SLO_A_HEALTH_RESTORED_SECONDS
        )

        control_records = control._records()
        observation_ref = (
            f"control-ledger:{final_state.ledger_id}"
            f"@head:{control_records[-1]['event_hash']}"
        )

        report: dict[str, Any] = {
            "schema": REPORT_SCHEMA,
            "gate": GATE_NAME,
            "actor": actor,
            "reason": reason,
            "drill_target": DRILL_TARGET,
            "target_confirmation_kind": "PRODUCTION",
            "from_phase": "canary",
            "to_phase": final_state.phase,
            "transition": "canary->stopped->disabled",
            "active_artifact_digest": a_digest,
            "candidate_artifact_digest": b_digest,
            "active_artifact_path": str(a_path),
            "candidate_artifact_path": str(b_path),
            "evidence_bundle_digest": evidence_bundle_digest,
            "routing_policy_digest": control.policy.policy_digest,
            "config": {
                "stop_after_errors": control.stop_after_errors,
                "require_distributed_lock": False,
                "lock_backend": "in-memory-hermetic",
                "ratio_basis_points": control.policy.ratio_basis_points,
                "request_cap": control.policy.request_cap,
                "timeout_ms": control.policy.timeout_ms,
                "ramp_id": control.policy.ramp_id,
            },
            "slo": {
                "rollback_reconcile_seconds": {
                    "budget": SLO_ROLLBACK_RECONCILE_SECONDS,
                    "observed": round(rollback_reconcile_s, 6),
                },
                "a_health_restored_seconds": {
                    "budget": SLO_A_HEALTH_RESTORED_SECONDS,
                    "observed": round(a_health_restored_s, 6),
                },
            },
            "latency": {
                "regression_injected_at_monotonic": round(t_regression, 6),
                "rollback_prepare_start_monotonic": round(t_rollback_start, 6),
                "rollback_complete_monotonic": round(t_rollback_complete, 6),
                "a_health_confirmed_monotonic": round(t_a_health_confirmed, 6),
            },
            "replay": {
                "inputs": list(pit_inputs),
                "active_output_digest": a_replay_digest,
                "candidate_output_digest": b_replay_digest,
                "detail": replay_detail,
            },
            "health": {
                "active_before": a_pre_status,
                "active_during_regression": a_during_status,
                "active_after_rollback": a_post_status,
                "candidate_after_injection": b_reg_status,
            },
            "observation_ref": observation_ref,
            "control_ledger_id": final_state.ledger_id,
            "control_event_count": len(control_records),
            "outcome_event_count": len(outcome_ledger.read()),
            "slo_pass": slo_pass,
            "result": "pass" if slo_pass else "fail",
            "executed_at": now.isoformat(),
        }
        if not slo_pass:
            raise RollbackDrillError(
                f"rollback SLO failed: reconcile={rollback_reconcile_s}s "
                f"a_health={a_health_restored_s}s"
            )
        return report


def _sign_gate_receipt(unsigned: dict[str, Any], gate_key: bytes) -> str:
    if len(gate_key) < 32:
        raise RollbackDrillError("gate key must be at least 32 bytes")
    return hmac.new(
        gate_key, GATE_RECEIPT_DOMAIN + canonical_json(unsigned), hashlib.sha256
    ).hexdigest()


def build_gate_receipt(
    report: dict[str, Any],
    *,
    report_bytes: bytes,
    gate_key: bytes,
    gate_key_id: str,
    command_digest: str,
    now: datetime,
) -> tuple[GateReceipt, dict[str, Any]]:
    """Construct the HMAC-signed rollback_drill GateReceipt.

    Per D1 the drill report (actor/reason/from-to/digests/config/SLO/latency/
    replay/observation-ref) is bound via the receipt's ``output_digest``, which
    is the sha256 of the exact report file bytes. No GateReceipt schema field
    is added or altered.
    """
    output_digest = "sha256:" + hashlib.sha256(report_bytes).hexdigest()
    expires_at = now + timedelta(hours=1)
    unsigned = {
        "gate": GATE_NAME,
        "active_artifact_digest": report["active_artifact_digest"],
        "candidate_artifact_digest": report["candidate_artifact_digest"],
        "command_digest": command_digest,
        "output_digest": output_digest,
        "result": "pass",
        "provider_calls": 0,
        "cost_usd": 0.0,
        "executed_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "key_id": gate_key_id,
        "nonce": secrets.token_hex(16),
        "receipt_version": "trustforge.executable-gate-receipt/v1",
    }
    signature = _sign_gate_receipt(unsigned, gate_key)
    receipt = GateReceipt(**unsigned, signature=signature)
    return receipt, unsigned


def _write_protected_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    fd = os.open(
        str(path),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.write(fd, json.dumps(payload, sort_keys=True).encode() + b"\n")
        os.fsync(fd)
    finally:
        os.close(fd)
    info = os.lstat(path)
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise RollbackDrillError("receipt file ownership or permissions unsafe")


def write_drill_artifacts(
    work_dir: Path,
    *,
    report: dict[str, Any],
    gate_key: bytes,
    gate_key_id: str,
    now: datetime,
) -> tuple[Path, Path]:
    """Persist the drill report and the commit-bound GateReceipt.

    The report file bytes are the canonical form bound by the receipt's
    ``output_digest``. A verifier re-reads the report file, hashes it, and
    compares to ``receipt.output_digest``.
    """
    report_path = work_dir / "rollback_drill_report.json"
    receipt_path = work_dir / "rollback_drill_receipt.json"
    command_descriptor = {
        "orchestrator": "scripts/run_rollback_drill.py",
        "schema": REPORT_SCHEMA,
        "drill_target": report["drill_target"],
    }
    command_digest = _sha256_raw(canonical_json(command_descriptor))
    report_bytes = canonical_json(report) + b"\n"
    report_path.write_bytes(report_bytes)
    receipt, _ = build_gate_receipt(
        report,
        report_bytes=report_bytes,
        gate_key=gate_key,
        gate_key_id=gate_key_id,
        command_digest=command_digest,
        now=now,
    )
    _write_protected_json(receipt_path, asdict(receipt))
    return report_path, receipt_path


def run(
    work_dir: Path,
    *,
    actor: str,
    reason: str,
    gate_key: bytes | None = None,
    gate_key_id: str = "drill-gate-1",
) -> dict[str, Any]:
    """Run the drill and persist report + receipt; return a summary dict.

    Generates a fresh gate key if none is supplied so the receipt is
    self-contained and verifiable only by callers given the key bytes.
    """
    if gate_key is None:
        gate_key = secrets.token_bytes(32)
    report = run_drill(work_dir, actor=actor, reason=reason)
    report_path, receipt_path = write_drill_artifacts(
        work_dir,
        report=report,
        gate_key=gate_key,
        gate_key_id=gate_key_id,
        now=_utcnow(),
    )
    report_bytes = Path(report_path).read_bytes()
    receipt = json.loads(Path(receipt_path).read_text())
    output_digest = "sha256:" + hashlib.sha256(report_bytes).hexdigest()
    return {
        "report_path": str(report_path),
        "receipt_path": str(receipt_path),
        "result": report["result"],
        "active_artifact_digest": report["active_artifact_digest"],
        "candidate_artifact_digest": report["candidate_artifact_digest"],
        "rollback_reconcile_seconds": report["slo"]["rollback_reconcile_seconds"][
            "observed"
        ],
        "a_health_restored_seconds": report["slo"]["a_health_restored_seconds"][
            "observed"
        ],
        "slo_pass": report["slo_pass"],
        "gate_key_hex": gate_key.hex(),
        "gate_key_id": gate_key_id,
        "receipt_output_digest": output_digest,
        "receipt_output_digest_match": receipt.get("output_digest") == output_digest,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a hermetic release A/B rollback drill (#877)."
    )
    parser.add_argument(
        "--work-dir", type=Path, required=True, help="tmp drill workspace"
    )
    parser.add_argument("--actor", default="rollback-drill-operator")
    parser.add_argument(
        "--reason",
        default="periodic rollback readiness verification (#877)",
    )
    parser.add_argument(
        "--gate-key-hex",
        help="HMAC gate key (>=32 bytes, hex). Generated if omitted.",
    )
    parser.add_argument("--gate-key-id", default="drill-gate-1")
    parser.add_argument(
        "--summary",
        type=Path,
        help="write a machine-readable summary JSON to this path",
    )
    args = parser.parse_args()

    gate_key = (
        bytes.fromhex(args.gate_key_hex)
        if args.gate_key_hex
        else secrets.token_bytes(32)
    )
    if len(gate_key) < 32:
        parser.error("gate key must be at least 32 bytes")

    summary = run(
        args.work_dir,
        actor=args.actor,
        reason=args.reason,
        gate_key=gate_key,
        gate_key_id=args.gate_key_id,
    )
    print(
        f"rollback_drill={summary['result']} "
        f"reconcile={summary['rollback_reconcile_seconds']}s "
        f"a_health={summary['a_health_restored_seconds']}s "
        f"slo_pass={summary['slo_pass']}"
    )
    print(f"report={summary['report_path']}")
    print(f"receipt={summary['receipt_path']}")
    if args.summary is not None:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, sort_keys=True) + "\n")
    return 0 if summary["slo_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
