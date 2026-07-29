"""HTTP-level contract tests for the Agent OS admin endpoint.

Unlike the dispatcher unit tests, these requests cross the real
``web.Handler`` boundary, including the outer admin authentication gate,
route registration, JSON serialization, and response headers.
"""
from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

from trustforge import web
from trustforge.agos_runtime import AgosRuntime


@pytest.fixture
def agos_http_server(tmp_path: Path):
    runtime = AgosRuntime(data_dir=tmp_path / "agos-http")
    with (
        patch.dict("os.environ", {"TRUSTFORGE_AGOS_ENABLED": "1"}),
        patch.object(web, "ADMIN_TOKEN", "http-e2e-admin-token"),
    ):
        server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        server._agos_runtime = runtime
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


def _get(address, path: str, headers: dict[str, str] | None = None):
    connection = HTTPConnection(*address, timeout=5)
    try:
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_admin_agos_http_requires_outer_x_admin_token(agos_http_server):
    status, headers, body = _get(
        agos_http_server,
        "/api/admin/agos/memories?run_id=http-e2e",
    )

    assert status == 401
    assert headers["Content-Type"].startswith("application/json")
    assert json.loads(body)["error"]["code"] == "unauthorized"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "web.Handler passes bytes to Handler._send for AGOS responses; "
        "production fix is outside this closeout's docs/test-only scope"
    ),
)
def test_admin_agos_http_returns_dispatcher_contract(agos_http_server):
    status, headers, body = _get(
        agos_http_server,
        "/api/admin/agos/memories?run_id=http-e2e",
        {"X-Admin-Token": "http-e2e-admin-token"},
    )

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    payload = json.loads(body)
    assert payload["status"] == "ok"
    assert payload["data"] == {
        "items": [],
        "page": 1,
        "page_size": 50,
        "total": 0,
    }
