"""`GET /api/peer-metrics`：唯讀 Peer 比較端點測試（獨立於 `/api/analyze`）。"""

from __future__ import annotations

import json
from email.message import Message
from io import BytesIO

from trustforge import web


def _envelope(body: str) -> dict:
    return json.loads(body)


def _do_get(path: str) -> tuple[int, str]:
    """比照 `tests/test_asset_context_api.py::_do_get` 既有慣例，端到端呼叫
    `Handler.do_GET`（不開真 socket），回傳 (status_code, body)。"""
    h = web.Handler.__new__(web.Handler)
    h.client_address = ("127.0.0.1", 12345)
    h.path = path
    h.wfile = BytesIO()
    h.headers = Message()

    captured = []
    h.send_response = lambda code, *a, **k: captured.append(code)
    h.send_header = lambda *a, **k: None
    h.end_headers = lambda: None

    h.do_GET()

    body = h.wfile.getvalue().decode("utf-8")
    return captured[0], body


def test_handler_returns_400_when_asset_missing():
    code, body = web._handle_api_peer_metrics({})
    assert code == 400
    parsed = _envelope(body)
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "invalid_request"


def test_handler_returns_empty_for_unknown_asset():
    code, body = web._handle_api_peer_metrics({"asset": ["asset:unknown"]})
    assert code == 200
    parsed = _envelope(body)
    assert parsed["ok"] is True
    assert parsed["data"]["snapshot"] is None
    assert parsed["data"]["peers"] == []


def test_handler_returns_snapshot_and_peer_comparability_for_arb():
    code, body = web._handle_api_peer_metrics({"asset": ["asset:arb"]})
    assert code == 200
    parsed = _envelope(body)
    assert parsed["ok"] is True
    data = parsed["data"]
    assert data["snapshot"]["asset_id"] == "asset:arb"

    peers_by_id = {peer["snapshot"]["asset_id"]: peer for peer in data["peers"]}
    assert set(peers_by_id) == {"asset:op", "asset:matic"}

    # asset:arb vs asset:op：同 window、metric 齊全 → 可比較
    assert peers_by_id["asset:op"]["comparable"] is True
    assert peers_by_id["asset:op"]["reason"] is None

    # asset:arb vs asset:matic：matic 缺 observed_tps → 不可比較，帶具體 reason
    assert peers_by_id["asset:matic"]["comparable"] is False
    assert peers_by_id["asset:matic"]["reason"] == "observed_tps missing"


def test_do_get_api_peer_metrics_route():
    code, body = _do_get("/api/peer-metrics?asset=asset:arb")
    assert code == 200
    parsed = _envelope(body)
    assert parsed["ok"] is True
    assert parsed["data"]["snapshot"]["asset_id"] == "asset:arb"


def test_api_peer_metrics_does_not_require_auth():
    """比照 `/api/asset-context` 慣例：無任何認證 header 也能打通。"""
    code, body = _do_get("/api/peer-metrics?asset=asset:arb")
    assert code == 200
