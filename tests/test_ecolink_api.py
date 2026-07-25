"""`GET /api/eco-link`：唯讀 EcoLink 影響路徑端點測試（獨立於 `/api/analyze`）。"""

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
    code, body = web._handle_api_eco_link({})
    assert code == 400
    parsed = _envelope(body)
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "invalid_request"


def test_handler_returns_impact_paths_for_arb():
    # asset:arb 的 stylus 升級事件 -> asset:eth，依賴邊有兩條（confidence
    # 0.85 / 0.55），皆 >= 預設門檻 0.4，取信心較高的那條。
    code, body = web._handle_api_eco_link({"asset": ["asset:arb"]})
    assert code == 200
    parsed = _envelope(body)
    assert parsed["ok"] is True
    data = parsed["data"]
    assert data["illustrative"] is True
    assert data["verdict"] == "possible_relation"
    assert "可能相關" in data["message"] or "possible_relation" == data["verdict"]
    assert "導致" not in data["message"]
    assert "因此" not in data["message"]
    assert len(data["impact_paths"]) == 1
    path = data["impact_paths"][0]
    assert path["path"] == ["asset:arb", "asset:eth"]
    assert path["confidence"] == 0.85


def test_handler_returns_insufficient_data_when_no_path_meets_confidence():
    # asset:op 的 bedrock 升級事件 -> asset:eth，唯一的依賴邊 confidence
    # 0.35 低於預設門檻 0.4 → 被濾除，repository 回空結果。
    code, body = web._handle_api_eco_link({"asset": ["asset:op"]})
    assert code == 200
    parsed = _envelope(body)
    assert parsed["ok"] is True
    data = parsed["data"]
    assert data["illustrative"] is True
    assert data["verdict"] == "insufficient_data"
    assert data["message"] == "資料不足，無法判定"
    assert data["impact_paths"] == []


def test_handler_returns_insufficient_data_for_unknown_asset():
    code, body = web._handle_api_eco_link({"asset": ["asset:unknown"]})
    assert code == 200
    parsed = _envelope(body)
    assert parsed["data"]["verdict"] == "insufficient_data"
    assert parsed["data"]["impact_paths"] == []


def test_do_get_api_eco_link_route():
    code, body = _do_get("/api/eco-link?asset=asset:arb")
    assert code == 200
    parsed = _envelope(body)
    assert parsed["ok"] is True
    assert parsed["data"]["verdict"] == "possible_relation"


def test_api_eco_link_does_not_require_auth():
    """比照 `/api/asset-context` 慣例：無任何認證 header 也能打通。"""
    code, body = _do_get("/api/eco-link?asset=asset:arb")
    assert code == 200
