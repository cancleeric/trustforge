#!/usr/bin/env python3
"""GET-only release-level A/B router service for #733.

The service is deliberately outside the application orchestrator and Kernel.
It accepts no request body and never retries a user operation.
"""
from __future__ import annotations

import json
import os
import socket
import socketserver
import stat
from http.server import BaseHTTPRequestHandler

from trustforge.release_router import ReleaseABRouter
from trustforge.release_http_canary import (
    ReleaseHTTPCanaryPolicy,
    validate_analyze_compare_response,
)
from trustforge.release_router_runtime import build_runtime_router

SOCKET_PATH = "/run/trustforge/release-router.sock"
MAX_HEADER_BYTES = 16 * 1024
FORWARDED_REQUEST_HEADERS = frozenset(
    {"accept", "accept-encoding", "authorization", "cookie", "if-none-match"}
)


def build_router() -> ReleaseABRouter:
    """Backward-compatible router-only construction API."""
    return build_runtime_router()


def build_router_with_canary_policy() -> tuple[
    ReleaseABRouter, ReleaseHTTPCanaryPolicy
]:
    """Compose the service router and its fail-closed HTTP canary policy."""
    router = build_runtime_router(response_validator=validate_analyze_compare_response)
    try:
        canary_policy = ReleaseHTTPCanaryPolicy.load(
            budget_keyring=router.cost_budget_keyring
        )
    except FileNotFoundError:
        # K2a's no-provision state must keep the ingress available but make B
        # impossible. K2b owns authenticated, atomic activation provisioning.
        canary_policy = ReleaseHTTPCanaryPolicy.disabled()
    return (
        router,
        canary_policy,
    )


class ReleaseRouterHandler(BaseHTTPRequestHandler):
    router: ReleaseABRouter
    canary_policy: ReleaseHTTPCanaryPolicy

    def do_GET(self) -> None:
        if sum(len(k) + len(v) for k, v in self.headers.items()) > MAX_HEADER_BYTES:
            self.send_error(431, "request headers are too large")
            return
        # The front proxy owns this header: it must strip the client value and
        # inject the authenticated principal. Any legacy/spoofable stable
        # subject is ignored and therefore can only reach A.
        identity = self.canary_policy.authenticated_identity(
            self.connection,
            self.headers.get("X-TrustForge-Trusted-Identity"),
        )
        forwarded = {
            name: value
            for name, value in self.headers.items()
            if name.lower() in FORWARDED_REQUEST_HEADERS
        }
        try:
            try:
                snapshot = self.router.ledger.routing_snapshot()
                subject, expected_head, cost_budget = (
                    self.canary_policy.routing_admission(
                    trusted_identity=identity,
                    path=self.path,
                    snapshot=snapshot,
                    )
                )
            except Exception:
                # An unauthenticated/unreadable snapshot can never authorize B.
                # Let the router independently reach its pinned-A fallback.
                subject, expected_head, cost_budget = None, None, None
            response = self.router.route(
                stable_subject=subject,
                path=self.path,
                request_headers=forwarded,
                expected_control_head=expected_head,
                cost_budget=cost_budget,
            )
        except Exception:
            self.send_error(503, "release router unavailable")
            return
        self.send_response(response.status_code)
        for name, value in response.headers:
            self.send_header(name, value)
        if not any(name.lower() == "content-type" for name, _ in response.headers):
            self.send_header("Content-Type", "application/octet-stream")
        self.send_header("X-TrustForge-Release-Path", response.release)
        self.send_header(
            "X-TrustForge-Failed-Over", "true" if response.failed_over else "false"
        )
        self.end_headers()
        self.wfile.write(response.body)

    def do_POST(self) -> None:
        self.send_error(405, "release canary supports idempotent GET only")

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST

    def log_message(self, format: str, *args: object) -> None:
        # Access logs intentionally omit headers; stable identity is never logged.
        return


def main() -> int:
    try:
        (
            ReleaseRouterHandler.router,
            ReleaseRouterHandler.canary_policy,
        ) = build_router_with_canary_policy()
        if os.path.lexists(SOCKET_PATH):
            if not stat.S_ISSOCK(os.lstat(SOCKET_PATH).st_mode):
                raise RuntimeError("refusing to replace non-socket ingress path")
            os.unlink(SOCKET_PATH)
        server = _BoundedUnixHTTPServer(SOCKET_PATH, ReleaseRouterHandler)
        os.chmod(SOCKET_PATH, 0o660)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "reason": type(exc).__name__,
                    "details_redacted": True,
                }
            )
        )
        return 2
    server.serve_forever()
    return 0


class _BoundedUnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, *args, **kwargs):
        self._slots = __import__("threading").BoundedSemaphore(32)
        super().__init__(*args, **kwargs)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(5)
        return request, address

    def process_request(self, request: socket.socket, client_address) -> None:
        if not self._slots.acquire(blocking=False):
            request.close()
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


if __name__ == "__main__":
    raise SystemExit(main())
