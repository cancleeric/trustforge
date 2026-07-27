from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from trustforge.agent.shadow_contracts import canonical_json
from trustforge.deployment_control import DeploymentControlLedger
from trustforge.release_router import (
    ReleaseABRouter,
    ReleaseEndpoint,
    RoutingPolicy,
    RoutingSnapshot,
)
from trustforge.signed_event_ledger import SignedEventLedger


_MANIFEST_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"m" * 32)
_MANIFEST_PUBLIC_KEY = _MANIFEST_PRIVATE_KEY.public_key().public_bytes(
    Encoding.Raw, PublicFormat.Raw
)


class _Handler(BaseHTTPRequestHandler):
    marker = b"?"
    fail = False
    artifact_digest = ""
    origin = ""

    def do_GET(self):
        if self.path == "/.well-known/trustforge-release-manifest":
            unsigned = {
                "schema": "trustforge.endpoint-manifest/v1",
                "artifact_digest": self.artifact_digest,
                "origin": self.origin,
                "key_id": "manifest-1",
            }
            signature = _MANIFEST_PRIVATE_KEY.sign(
                b"trustforge.endpoint-manifest.v1\x00" + canonical_json(unsigned)
            ).hex()
            body = json.dumps({**unsigned, "signature": signature}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(503 if self.fail else 200)
        self.end_headers()
        self.wfile.write(self.marker)

    def log_message(self, format, *args):
        return


def _server(marker: bytes, artifact_digest: str):
    handler = type(
        f"Handler{marker!r}",
        (_Handler,),
        {"marker": marker, "artifact_digest": artifact_digest},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    handler.origin = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, handler


def _policy(ratio=9999):
    payload = {
        "ratio_basis_points": ratio,
        "request_cap": 50,
        "timeout_ms": 500,
        "routing_key_id": "route-2026-07",
        "ramp_id": "ramp-1",
    }
    digest = "sha256:" + hashlib.sha256(
        b"trustforge.routing-policy.v1\x00" + canonical_json(payload)
    ).hexdigest()
    return RoutingPolicy(**payload, policy_digest=digest)


class _Ledger:
    def __init__(self, a, b, *, stop_after=1):
        self.a, self.b = a, b
        self.phase = "canary"
        self.requests = 0
        self.errors = 0
        self.head = "sha256:" + "0" * 64
        self.stop_after = stop_after
        self.subjects = []
        self.reservations = set()
        self.emergency = False

    def routing_snapshot(self):
        return RoutingSnapshot(
            ledger_id="ledger-prod-1",
            phase="stopped" if self.emergency else self.phase,
            desired_phase="canary",
            activation_status="completed",
            active=self.a,
            candidate=self.b,
            policy=_policy(),
            candidate_requests=self.requests,
            consecutive_errors=self.errors,
            stop_after_errors=self.stop_after,
            ledger_head=self.head,
        )

    def reserve_candidate(self, *, expected_head, reservation_id):
        assert expected_head == self.head
        self.reservations.add(reservation_id)
        self.requests += 1
        self.head = "sha256:" + hashlib.sha256(
            json.dumps(["reserve", self.requests]).encode()
        ).hexdigest()
        return self.routing_snapshot()

    def record_candidate_result(
        self, *, expected_head, reservation_id, ok, status_code, latency_ms, error_kind
    ):
        assert expected_head == self.head
        assert reservation_id in self.reservations
        self.errors = 0 if ok else self.errors + 1
        if self.errors >= self.stop_after:
            self.phase = "stopped"
        self.head = "sha256:" + hashlib.sha256(
            json.dumps([self.requests, self.errors]).encode()
        ).hexdigest()
        return self.routing_snapshot()

    def emergency_stop(self, *, ledger_id, reason):
        assert ledger_id == "ledger-prod-1"
        self.emergency = True


def test_real_separate_http_releases_route_limited_b_without_core_import():
    a_server, _ = _server(b"A", "sha256:" + "a" * 64)
    b_server, _ = _server(b"B", "sha256:" + "b" * 64)
    try:
        a = ReleaseEndpoint("sha256:" + "a" * 64, f"http://127.0.0.1:{a_server.server_port}", "manifest-1")
        b = ReleaseEndpoint("sha256:" + "b" * 64, f"http://127.0.0.1:{b_server.server_port}", "manifest-1")
        ledger = _Ledger(a, b)
        router = ReleaseABRouter(
            ledger, {"route-2026-07": b"r" * 32}, pinned_a_fallback=a,
            manifest_keyring={"manifest-1": _MANIFEST_PUBLIC_KEY},
        )
        response = router.route(stable_subject="stable-user")
        assert response.release == "B"
        assert response.body == b"B"
        assert ledger.requests == 1
        assert "trustforge_core" not in __import__(
            "inspect"
        ).getsource(__import__("trustforge.release_router", fromlist=["*"]))
    finally:
        a_server.shutdown()
        b_server.shutdown()


def test_real_b_failure_fails_over_a_and_durable_stop_prevents_next_b():
    a_server, _ = _server(b"A", "sha256:" + "a" * 64)
    b_server, b_handler = _server(b"B", "sha256:" + "b" * 64)
    b_handler.fail = True
    try:
        a = ReleaseEndpoint("sha256:" + "a" * 64, f"http://127.0.0.1:{a_server.server_port}", "manifest-1")
        b = ReleaseEndpoint("sha256:" + "b" * 64, f"http://127.0.0.1:{b_server.server_port}", "manifest-1")
        ledger = _Ledger(a, b)
        router = ReleaseABRouter(
            ledger, {"route-2026-07": b"r" * 32}, pinned_a_fallback=a,
            manifest_keyring={"manifest-1": _MANIFEST_PUBLIC_KEY},
        )
        failed = router.route(stable_subject="stable-user")
        assert failed.release == "A"
        assert failed.failed_over is True
        assert ledger.phase == "stopped"
        b_handler.fail = False
        next_response = router.route(stable_subject="stable-user")
        assert next_response.release == "A"
        assert ledger.requests == 1
    finally:
        a_server.shutdown()
        b_server.shutdown()


def test_missing_or_corrupt_ledger_routes_pinned_a():
    a_server, _ = _server(b"A", "sha256:" + "a" * 64)
    try:
        a = ReleaseEndpoint("sha256:" + "a" * 64, f"http://127.0.0.1:{a_server.server_port}", "manifest-1")

        class Broken:
            def routing_snapshot(self):
                raise ValueError("tampered")

        router = ReleaseABRouter(
            Broken(), {"route-2026-07": b"r" * 32}, pinned_a_fallback=a,
            manifest_keyring={"manifest-1": _MANIFEST_PUBLIC_KEY},
        )
        response = router.route(stable_subject="stable-user")
        assert response.release == "A"
        assert response.failed_over is True
    finally:
        a_server.shutdown()


def test_real_authenticated_control_restart_concurrency_cap_and_auto_stop(tmp_path):
    a_server, _ = _server(b"A", "sha256:" + "a" * 64)
    b_server, b_handler = _server(b"B", "sha256:" + "b" * 64)
    try:
        a = ReleaseEndpoint(
            "sha256:" + "a" * 64,
            f"http://127.0.0.1:{a_server.server_port}",
            "manifest-1",
        )
        b = ReleaseEndpoint(
            "sha256:" + "b" * 64,
            f"http://127.0.0.1:{b_server.server_port}",
            "manifest-1",
        )
        policy = _policy()
        object.__setattr__(policy, "request_cap", 6)
        policy_payload = {
            "ratio_basis_points": policy.ratio_basis_points,
            "request_cap": 6,
            "timeout_ms": policy.timeout_ms,
            "routing_key_id": policy.routing_key_id,
            "ramp_id": policy.ramp_id,
        }
        object.__setattr__(
            policy,
            "policy_digest",
            "sha256:" + hashlib.sha256(
                b"trustforge.routing-policy.v1\x00" + canonical_json(policy_payload)
            ).hexdigest(),
        )
        control_seed = b"l" * 32
        outcome_seed = b"o" * 32
        ledger = SignedEventLedger(
            directory=tmp_path / "control-ledger",
            verification_keys={"control-1": Ed25519PrivateKey.from_private_bytes(
                control_seed
            ).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)},
            event_permissions={"release-control": frozenset({
                "deployment_initialized", "operator_stop", "activation_prepared",
                "activation_completed", "activation_failed",
            })},
            domain_keys={"release-control": frozenset({"control-1"})},
            signing_key_id="control-1",
            signing_private_key=control_seed,
            signing_domain="release-control",
            ledger_role="release-control",
            bootstrap=True,
            coordination_root=tmp_path / "ledger-root",
        )
        outcome_ledger = SignedEventLedger(
            directory=tmp_path / "outcome-ledger",
            verification_keys={"outcome-1": Ed25519PrivateKey.from_private_bytes(
                outcome_seed
            ).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)},
            event_permissions={"release-router-outcome": frozenset({
                "candidate_reservation", "candidate_result", "router_emergency_stop",
            })},
            domain_keys={"release-router-outcome": frozenset({"outcome-1"})},
            signing_key_id="outcome-1",
            signing_private_key=outcome_seed,
            signing_domain="release-router-outcome",
            ledger_role="release-router-outcomes",
            bootstrap=True,
            coordination_root=tmp_path / "ledger-root",
        )
        target = "production"
        confirmation = f"PRODUCTION:{target}:{a.release_digest}:{b.release_digest}"
        control = DeploymentControlLedger(
            ledger,
            outcome_ledger=outcome_ledger,
            authorization_keys={"auth": Ed25519PrivateKey.from_private_bytes(
                b"a" * 32
            ).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)},
            completion_keys={"complete": Ed25519PrivateKey.from_private_bytes(
                b"c" * 32
            ).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)},
            target=target,
            target_confirmation=confirmation,
            active=a,
            candidate=b,
            policy=policy,
            evidence_bundle_digest="sha256:" + "e" * 64,
            stop_after_errors=1,
            require_distributed_lock=False,
        )
        control.initialize()
        now = datetime(2026, 7, 28, tzinfo=timezone.utc)
        auth_unsigned = {
            "action": "start",
            "target": target,
            "target_confirmation": confirmation,
            "ledger_id": control.routing_snapshot().ledger_id,
            "active_artifact_digest": a.release_digest,
            "candidate_artifact_digest": b.release_digest,
            "evidence_bundle_digest": "sha256:" + "e" * 64,
            "routing_policy_digest": policy.policy_digest,
            "routing_key_id": policy.routing_key_id,
            "actor": "test",
            "issued_at": (now - timedelta(minutes=1)).isoformat(),
            "expires_at": (now + timedelta(minutes=5)).isoformat(),
            "nonce": "prepare-test",
            "key_id": "auth",
            "receipt_version": "trustforge.deployment-authorization/v2",
        }
        auth_receipt = {
            **auth_unsigned,
            "signature": Ed25519PrivateKey.from_private_bytes(b"a" * 32).sign(
                b"trustforge.deployment-authorization.v2\x00"
                + canonical_json(auth_unsigned)
            ).hex(),
        }
        prepared = ledger.append(
            {
                "kind": "activation_prepared",
                "transaction_id": "t1",
                "action": "start",
                "desired_phase": "canary",
                "nonce": "prepare-test",
                "actor": "test",
                "owner_id": "test",
                "evidence_bundle_digest": "sha256:" + "e" * 64,
                "active_artifact_digest": a.release_digest,
                "candidate_artifact_digest": b.release_digest,
                "routing_policy_digest": policy.policy_digest,
                "at": "2026-07-28T00:00:00Z",
                "authorization_receipt": auth_receipt,
            }
        )
        completion_unsigned = {
            "transaction_id": "t1",
            "action": "start",
            "target": target,
            "prepared_event_hash": prepared["event_hash"],
            "active_artifact_digest": a.release_digest,
            "candidate_artifact_digest": b.release_digest,
            "pointer_active_digest": a.release_digest,
            "observed_manifest_digest": a.release_digest,
            "status": "completed",
            "verified_at": now.isoformat(),
            "actor": "test",
            "nonce": "complete-test",
            "key_id": "complete",
            "receipt_version": "trustforge.activation-completion/v1",
        }
        completion_receipt = {
            **completion_unsigned,
            "signature": Ed25519PrivateKey.from_private_bytes(b"c" * 32).sign(
                b"trustforge.activation-completion.v1\x00"
                + canonical_json(completion_unsigned)
            ).hex(),
        }
        ledger.append(
            {
                "kind": "activation_completed",
                "transaction_id": "t1",
                "prepared_event_hash": prepared["event_hash"],
                "pointer_active_digest": a.release_digest,
                "observed_manifest_digest": a.release_digest,
                "activation_receipt_digest": "sha256:" + hashlib.sha256(
                    canonical_json(completion_receipt)
                ).hexdigest(),
                "nonce": "complete-test",
                "actor": "test",
                "at": "2026-07-28T00:00:01Z",
                "completion_receipt": completion_receipt,
            }
        )
        router = ReleaseABRouter(
            control,
            {"route-2026-07": b"r" * 32},
            pinned_a_fallback=a,
            manifest_keyring={"manifest-1": _MANIFEST_PUBLIC_KEY},
        )
        with ThreadPoolExecutor(max_workers=12) as pool:
            responses = list(
                pool.map(
                    lambda index: router.route(stable_subject=f"subject-{index}"),
                    range(5),
                )
            )
        restarted = DeploymentControlLedger(
            ledger,
            outcome_ledger=outcome_ledger,
            authorization_keys=control.authorization_keys,
            completion_keys=control.completion_keys,
            target=target,
            target_confirmation=confirmation,
            active=a,
            candidate=b,
            policy=policy,
            evidence_bundle_digest="sha256:" + "e" * 64,
            stop_after_errors=1,
            require_distributed_lock=False,
        )
        assert 1 <= restarted.routing_snapshot().candidate_requests <= 5
        assert sum(response.release == "B" for response in responses) <= 5
        while restarted.routing_snapshot().candidate_requests < 5:
            router.route(
                stable_subject=(
                    f"fill-{restarted.routing_snapshot().candidate_requests}"
                )
            )
        assert restarted.routing_snapshot().candidate_requests == 5
        b_handler.fail = True
        failed = router.route(stable_subject="failure-subject")
        assert failed.release == "A"
        assert failed.failed_over is True
        assert restarted.routing_snapshot().phase == "stopped"
        assert all(
            router.route(stable_subject=f"after-{index}").release == "A"
            for index in range(3)
        )
    finally:
        a_server.shutdown()
        b_server.shutdown()
