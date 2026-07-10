"""issue #1（CISO High 第二部分）：EC2 live token 在明文 HTTP 下的技術性強制
拒絕。涵蓋兩層防護：

1. `web._live_token_over_insecure_transport`（純函式）：`TRUST_PROXY=1` 時
   信任 nginx 忠實轉發的 `X-Forwarded-Proto`，判斷帶 token 的請求是否已
   可信地確認經明文 HTTP 送達。
2. `web.main()` 啟動期靜態檢查：live token 有設定但 `TRUST_PROXY` 未開
   （無法確認有 TLS 反代保護）→ 拒絕啟動（fail-closed），除非明確設
   `TRUSTFORGE_ALLOW_INSECURE_LIVE_TOKEN=1` opt-out。

`Handler.do_GET` 端到端呼叫慣例比照 `tests/test_admin_api.py::_request`
（`Handler.__new__` 不開真 socket）。
"""
from __future__ import annotations

from email.message import Message
from io import BytesIO

import pytest

from trustforge import web


# ── `_live_token_over_insecure_transport` 純函式 ────────────────────────────


def test_no_token_never_flagged_regardless_of_transport(monkeypatch):
    monkeypatch.setattr(web, "TRUST_PROXY", True)
    qs = {"token": [""]}
    headers = {"X-Forwarded-Proto": "http"}
    assert web._live_token_over_insecure_transport(qs, headers) is False


def test_trust_proxy_off_never_flagged_even_with_http_header(monkeypatch):
    """沒有可信賴反代時不能拿 X-Forwarded-Proto 當證據（可能是使用者偽造）。"""
    monkeypatch.setattr(web, "TRUST_PROXY", False)
    qs = {"token": ["tok"]}
    headers = {"X-Forwarded-Proto": "http"}
    assert web._live_token_over_insecure_transport(qs, headers) is False


def test_trust_proxy_on_headers_none_not_flagged(monkeypatch):
    monkeypatch.setattr(web, "TRUST_PROXY", True)
    qs = {"token": ["tok"]}
    assert web._live_token_over_insecure_transport(qs, None) is False


def test_trust_proxy_on_missing_forwarded_proto_not_flagged(monkeypatch):
    """反代沒帶這個 header（理論上不該發生）→ 無明確證據，保守不判定不安全。"""
    monkeypatch.setattr(web, "TRUST_PROXY", True)
    qs = {"token": ["tok"]}
    assert web._live_token_over_insecure_transport(qs, {}) is False


def test_trust_proxy_on_http_proto_flagged(monkeypatch):
    monkeypatch.setattr(web, "TRUST_PROXY", True)
    qs = {"token": ["tok"]}
    headers = {"X-Forwarded-Proto": "http"}
    assert web._live_token_over_insecure_transport(qs, headers) is True


def test_trust_proxy_on_https_proto_not_flagged(monkeypatch):
    monkeypatch.setattr(web, "TRUST_PROXY", True)
    qs = {"token": ["tok"]}
    headers = {"X-Forwarded-Proto": "https"}
    assert web._live_token_over_insecure_transport(qs, headers) is False


def test_proto_comparison_case_insensitive(monkeypatch):
    monkeypatch.setattr(web, "TRUST_PROXY", True)
    qs = {"token": ["tok"]}
    assert web._live_token_over_insecure_transport(
        qs, {"X-Forwarded-Proto": "HTTPS"}
    ) is False
    assert web._live_token_over_insecure_transport(
        qs, {"X-Forwarded-Proto": "HTTP"}
    ) is True


# ── `Handler.do_GET` 端到端：不安全連線帶 token 的請求應被 403 拒絕 ──────────


def _request(path: str, *, headers: dict | None = None, ip: str = "203.0.113.1"):
    h = web.Handler.__new__(web.Handler)
    h.client_address = (ip, 12345)
    h.path = path
    h.wfile = BytesIO()
    msg = Message()
    for name, val in (headers or {}).items():
        msg[name] = val
    h.headers = msg

    captured: list[int] = []
    h.send_response = lambda code: captured.append(code)
    h.send_header = lambda name, val: None
    h.end_headers = lambda: None

    h.do_GET()
    return captured[0], h.wfile.getvalue().decode("utf-8")


def test_do_get_rejects_live_token_over_plain_http(monkeypatch):
    """issue #134 起 live token 只認 `X-Live-Token` header（query `?token=`
    已不再生效），這裡改用 header 帶 token 觸發明文 HTTP 拒絕檢查。"""
    monkeypatch.setattr(web, "TRUST_PROXY", True)
    status, body = _request(
        "/analyze?live=1&coin=BTC&q=x",
        headers={"X-Forwarded-Proto": "http", "X-Live-Token": "secret"},
    )
    assert status == 403
    assert "secret" not in body  # token 本身不可回顯


def test_do_get_allows_live_token_over_https(monkeypatch):
    """走 HTTPS 時不觸發這道新的拒絕（後續照舊走既有 token 驗證/限流邏輯，
    不在本測試斷言範圍——這裡只確認不是被 403 擋掉）。"""
    monkeypatch.setattr(web, "TRUST_PROXY", True)
    status, _body = _request(
        "/analyze?live=1&coin=BTC&q=x",
        headers={"X-Forwarded-Proto": "https", "X-Live-Token": "secret"},
    )
    assert status != 403


def test_do_get_no_token_never_triggers_403_for_this_guard(monkeypatch):
    monkeypatch.setattr(web, "TRUST_PROXY", True)
    status, _body = _request(
        "/healthz",
        headers={"X-Forwarded-Proto": "http"},
    )
    assert status == 200


# ── `main()` 啟動期靜態檢查 ──────────────────────────────────────────────────


class _FakeServer:
    def __init__(self, addr, handler_cls):
        pass

    def serve_forever(self):
        pass


def test_main_refuses_to_start_when_live_token_set_and_trust_proxy_off(monkeypatch):
    monkeypatch.setattr(web, "TRUST_PROXY", False)
    monkeypatch.setattr(web, "_LIVE_TOKEN_BOOTSTRAP_RESOLVED", "some-live-token")
    monkeypatch.delenv("TRUSTFORGE_ALLOW_INSECURE_LIVE_TOKEN", raising=False)
    monkeypatch.setattr(web, "ThreadingHTTPServer", _FakeServer)

    with pytest.raises(SystemExit):
        web.main()


def test_main_starts_when_live_token_set_and_trust_proxy_on(monkeypatch):
    monkeypatch.setattr(web, "TRUST_PROXY", True)
    monkeypatch.setenv("TRUSTFORGE_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(web, "_LIVE_TOKEN_BOOTSTRAP_RESOLVED", "some-live-token")
    monkeypatch.delenv("TRUSTFORGE_ALLOW_INSECURE_LIVE_TOKEN", raising=False)
    monkeypatch.setattr(web, "ThreadingHTTPServer", _FakeServer)

    web.main()  # 不應該 raise


def test_main_starts_with_explicit_insecure_opt_out(monkeypatch):
    monkeypatch.setattr(web, "TRUST_PROXY", False)
    monkeypatch.delenv("TRUSTFORGE_BIND_HOST", raising=False)
    monkeypatch.setattr(web, "_LIVE_TOKEN_BOOTSTRAP_RESOLVED", "some-live-token")
    monkeypatch.setenv("TRUSTFORGE_ALLOW_INSECURE_LIVE_TOKEN", "1")
    monkeypatch.setattr(web, "ThreadingHTTPServer", _FakeServer)

    web.main()  # 明確 opt-out，不應該 raise


def test_main_starts_when_no_live_token_configured(monkeypatch):
    monkeypatch.setattr(web, "TRUST_PROXY", False)
    monkeypatch.delenv("TRUSTFORGE_BIND_HOST", raising=False)
    monkeypatch.setattr(web, "_LIVE_TOKEN_BOOTSTRAP_RESOLVED", "")
    monkeypatch.setattr(web, "ThreadingHTTPServer", _FakeServer)

    web.main()  # 沒設 live token，跟這道檢查無關，不應該 raise
