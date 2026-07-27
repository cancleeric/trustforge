"""`GET /api/asset-context`：資產脈絡查詢端點測試（獨立於 `/api/analyze`）。"""

from __future__ import annotations

import json
from email.message import Message
from io import BytesIO

from trustforge import web


def _envelope(body: str) -> dict:
    return json.loads(body)


def _do_get(path: str) -> tuple[int, str]:
    """比照 `tests/test_json_api.py::_do_get` 既有慣例，端到端呼叫
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


def test_handler_returns_asset_context_for_known_symbol():
    code, body = web._handle_api_asset_context({"symbol": ["ARB"]})
    assert code == 200
    parsed = _envelope(body)
    assert parsed["ok"] is True
    context = parsed["data"]["asset_context"]
    assert context is not None
    assert context["symbol"] == "ARB"
    assert context["settlement_chain"] == "ethereum"
    assert context["gas_token"] == "ETH"
    assert context["dependencies"] == [
        "ethereum_settlement",
        "sequencer",
        "canonical_bridge",
    ]


def test_handler_returns_null_asset_context_for_unknown_symbol():
    # BTC 從 2026-07-27 起已在 fixture 內，改用真正不在資料裡的代號當負向案例。
    code, body = web._handle_api_asset_context({"symbol": ["DOGE"]})
    assert code == 200
    parsed = _envelope(body)
    assert parsed["ok"] is True
    assert parsed["data"]["asset_context"] is None


def test_handler_returns_400_when_symbol_missing():
    code, body = web._handle_api_asset_context({})
    assert code == 400
    parsed = _envelope(body)
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "invalid_request"


def test_handler_symbol_lookup_is_case_insensitive():
    code, body = web._handle_api_asset_context({"symbol": ["arb"]})
    assert code == 200
    parsed = _envelope(body)
    assert parsed["data"]["asset_context"]["symbol"] == "ARB"


def test_do_get_api_asset_context_route():
    code, body = _do_get("/api/asset-context?symbol=ARB")
    assert code == 200
    parsed = _envelope(body)
    assert parsed["ok"] is True
    assert parsed["data"]["asset_context"]["symbol"] == "ARB"


def test_api_asset_context_does_not_require_auth():
    """比照 `/api/analyze` 慣例：無任何認證 header 也能打通。"""
    code, body = _do_get("/api/asset-context?symbol=ARB")
    assert code == 200
