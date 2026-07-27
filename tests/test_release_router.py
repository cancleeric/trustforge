from __future__ import annotations

import hashlib
import hmac
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from trustforge.agent.shadow_contracts import canonical_json
from trustforge.authenticated_ledger import AuthenticatedLedger
from trustforge.deployment_control import DeploymentControlLedger
from trustforge.release_router import (
    ReleaseABRouter,
    ReleaseEndpoint,
    RoutingPolicy,
    RoutingSnapshot,
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
            signature = hmac.new(
                b"m" * 32,
                b"trustforge.endpoint-manifest.v1\x00" + canonical_json(unsigned),
                hashlib.sha256,
            ).hexdigest()
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
            manifest_keyring={"manifest-1": b"m" * 32},
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
            manifest_keyring={"manifest-1": b"m" * 32},
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
            manifest_keyring={"manifest-1": b"m" * 32},
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
        ledger = AuthenticatedLedger(
            keyring={"ledger-1": b"l" * 32},
            active_key_id="ledger-1",
            test_directory_override=tmp_path / "ledger",
        )
        target = "production"
        confirmation = f"PRODUCTION:{target}:{a.release_digest}:{b.release_digest}"
        control = DeploymentControlLedger(
            ledger,
            authorization_keys={"auth": b"a" * 32},
            completion_keys={"complete": b"c" * 32},
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
            }
        )
        ledger.append(
            {
                "kind": "activation_completed",
                "transaction_id": "t1",
                "prepared_event_hash": prepared["event_hash"],
                "pointer_active_digest": a.release_digest,
                "observed_manifest_digest": a.release_digest,
                "activation_receipt_digest": "sha256:" + "d" * 64,
                "nonce": "complete-test",
                "actor": "test",
                "at": "2026-07-28T00:00:01Z",
            }
        )
        router = ReleaseABRouter(
            control,
            {"route-2026-07": b"r" * 32},
            pinned_a_fallback=a,
            manifest_keyring={"manifest-1": b"m" * 32},
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
            authorization_keys={"auth": b"a" * 32},
            completion_keys={"complete": b"c" * 32},
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
