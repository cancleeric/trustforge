#!/usr/bin/env python3
"""GET-only release-level A/B router service for #733.

The service is deliberately outside the application orchestrator and Kernel.
It accepts no request body and never retries a user operation.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from scripts.deployment_readiness import _build_control
from trustforge.release_router import ReleaseABRouter

LISTEN_ADDRESS = ("127.0.0.1", 8090)
MAX_SUBJECT_BYTES = 256


def build_router() -> ReleaseABRouter:
    control, routing_keys, manifest_keys = _build_control(
        require_preflight=False,
        verify_retained_a=False,
        verify_retained_b=False,
    )
    state = control.routing_snapshot()
    return ReleaseABRouter(
        control,
        routing_keys,
        pinned_a_fallback=state.active,
        manifest_keyring=manifest_keys,
    )


class ReleaseRouterHandler(BaseHTTPRequestHandler):
    router: ReleaseABRouter

    def do_GET(self) -> None:
        subject = self.headers.get("X-TrustForge-Stable-Subject")
        if subject is not None and len(subject.encode("utf-8")) > MAX_SUBJECT_BYTES:
            self.send_error(400, "stable subject is too large")
            return
        try:
            response = self.router.route(stable_subject=subject, path=self.path)
        except Exception:
            self.send_error(503, "release router unavailable")
            return
        self.send_response(response.status_code)
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
        server = ThreadingHTTPServer(LISTEN_ADDRESS, ReleaseRouterHandler)
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


if __name__ == "__main__":
    raise SystemExit(main())
