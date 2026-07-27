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
from trustforge.release_router_runtime import build_runtime_router

SOCKET_PATH = "/run/trustforge/release-router.sock"
MAX_SUBJECT_BYTES = 256
MAX_HEADER_BYTES = 16 * 1024
FORWARDED_REQUEST_HEADERS = frozenset(
    {"accept", "accept-encoding", "authorization", "cookie", "if-none-match"}
)


def build_router() -> ReleaseABRouter:
    return build_runtime_router()


class ReleaseRouterHandler(BaseHTTPRequestHandler):
    router: ReleaseABRouter

    def do_GET(self) -> None:
        if self.headers.get("X-TrustForge-Stable-Subject") is not None:
            self.send_error(400, "untrusted identity header is forbidden")
            return
        if sum(len(k) + len(v) for k, v in self.headers.items()) > MAX_HEADER_BYTES:
            self.send_error(431, "request headers are too large")
            return
        subject = self.headers.get("X-TrustForge-Trusted-Subject")
        if subject is not None and len(subject.encode("utf-8")) > MAX_SUBJECT_BYTES:
            self.send_error(400, "stable subject is too large")
            return
        forwarded = {
            name: value
            for name, value in self.headers.items()
            if name.lower() in FORWARDED_REQUEST_HEADERS
        }
        try:
            response = self.router.route(
                stable_subject=subject,
                path=self.path,
                request_headers=forwarded,
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
        ReleaseRouterHandler.router = build_router()
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
