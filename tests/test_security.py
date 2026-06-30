"""安全修復測試：token 驗證、per-IP 限流、q 長度上限、例外包裝。"""
from __future__ import annotations

import pytest

from trustforge import web
from trustforge import lambda_handler


# ── helpers ──────────────────────────────────────────────────────────────────

def _qs(coin="BTC", qtype="multi_source", q="test", live="0", token=""):
    """組出 _do_analyze 期望的 qs dict。"""
    d: dict[str, list[str]] = {
        "coin": [coin], "type": [qtype], "q": [q], "live": [live],
    }
    if token:
        d["token"] = [token]
    return d


def _patch_live(monkeypatch, token_value="secret"):
    """把 web module 的 HAS_BEDROCK / LIVE_TOKEN 設成 live 可用狀態。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", True)
    monkeypatch.setattr(web, "LIVE_TOKEN", token_value)


def _make_fake_run(calls: list):
    """回傳 fake run()，記錄 offline 旗標並強制離線執行（不打 AWS）。"""
    import trustforge.pipeline as _pl

    def fake_run(coin, query, qtype, offline=False, data_dir=None):
        calls.append({"offline": offline})
        # 強制離線避免真打 Bedrock
        return _pl.run(coin, query, qtype, offline=True, data_dir=data_dir)

    return fake_run


# ── 1. live 沒帶 token → 走離線 ──────────────────────────────────────────────

def test_live_no_token_stays_offline(monkeypatch):
    """live=1 但未帶 token 參數 → offline=True，不呼叫 Bedrock。"""
    _patch_live(monkeypatch)
    calls: list = []
    monkeypatch.setattr(web, "run", _make_fake_run(calls))

    web._do_analyze(_qs(live="1"))  # 沒有 token key
    assert calls, "run 應被呼叫"
    assert calls[0]["offline"] is True, "沒帶 token 應走離線"


# ── 2. live 帶錯誤 token → 走離線 ────────────────────────────────────────────

def test_live_wrong_token_stays_offline(monkeypatch):
    """live=1 + 錯誤 token → offline=True。"""
    _patch_live(monkeypatch, "secret123")
    calls: list = []
    monkeypatch.setattr(web, "run", _make_fake_run(calls))

    web._do_analyze(_qs(live="1", token="wrongtoken"))
    assert calls[0]["offline"] is True, "錯誤 token 應走離線"


# ── 3. live + 正確 token + env 就緒 → live 路徑 ───────────────────────────────

def test_live_correct_token_calls_live_path(monkeypatch):
    """live=1 + 正確 token + LIVE_TOKEN/HAS_BEDROCK 設好 → offline=False。"""
    _patch_live(monkeypatch, "secret123")
    calls: list = []
    monkeypatch.setattr(web, "run", _make_fake_run(calls))

    web._do_analyze(_qs(live="1", token="secret123"))
    assert calls[0]["offline"] is False, "正確 token 應走 live（offline=False）"


# ── 4. live_token 未設（空字串）→ 即使帶 token 也走離線 ──────────────────────

def test_live_token_env_not_set_stays_offline(monkeypatch):
    """TRUSTFORGE_LIVE_TOKEN 未設時，任何 token 都不能啟用 live。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", True)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")   # 未設
    calls: list = []
    monkeypatch.setattr(web, "run", _make_fake_run(calls))

    web._do_analyze(_qs(live="1", token="anything"))
    assert calls[0]["offline"] is True


# ── 5. q 過長 → ValueError（對應 400）────────────────────────────────────────

def test_q_too_long_raises_value_error():
    """q > 1000 字元 → ValueError 含 '1000' 字樣。"""
    with pytest.raises(ValueError, match="1000"):
        web._do_analyze(_qs(q="A" * 1001))


def test_q_exactly_1000_ok():
    """q = 1000 字元 → 不拋例外，正常走離線。"""
    report, _, _ = web._do_analyze(_qs(q="A" * 1000))
    assert report is not None


# ── 6. 非 ValueError 例外 → lambda handler 回 502，不洩露細節 ─────────────────

def test_lambda_handler_502_on_unexpected_exception(monkeypatch):
    """_do_analyze 拋 RuntimeError → handler statusCode=502，body 不含原始錯誤訊息。"""
    def bad_analyze(qs, client_ip=""):
        raise RuntimeError("internal db connection failed: password=hunter2")

    monkeypatch.setattr(web, "_do_analyze", bad_analyze)

    event = {
        "rawPath": "/analyze",
        "queryStringParameters": {"coin": "BTC", "type": "multi_source", "q": "test"},
    }
    resp = lambda_handler.handler(event)
    assert resp["statusCode"] == 502
    # 確認不把 RuntimeError 內容洩漏到 HTTP 回應
    assert "hunter2" not in resp["body"]
    assert "RuntimeError" not in resp["body"]


# ── 7. 非 ValueError 例外 → web handler do_GET 同樣回 502 ────────────────────

def test_web_handler_502_on_unexpected_exception(monkeypatch):
    """web.Handler.do_GET 在 _do_analyze 拋非 ValueError 時回 502。"""
    from io import BytesIO

    def bad_analyze(qs, client_ip=""):
        raise RuntimeError("boom secret key=abc123")

    monkeypatch.setattr(web, "_do_analyze", bad_analyze)

    # 建一個最小化 mock，能讓 Handler._send 運作但不開真 socket
    buf = BytesIO()
    h = web.Handler.__new__(web.Handler)
    h.client_address = ("127.0.0.1", 12345)
    h.path = "/analyze?coin=BTC&type=multi_source&q=test"
    h.wfile = buf

    captured: list[tuple] = []

    def fake_send_response(code):
        captured.append(("status", code))

    def fake_send_header(name, val):
        captured.append(("header", name, val))

    def fake_end_headers():
        pass

    h.send_response = fake_send_response
    h.send_header = fake_send_header
    h.end_headers = fake_end_headers

    h.do_GET()

    status_codes = [tp[1] for tp in captured if tp[0] == "status"]
    assert status_codes == [502]
    body = buf.getvalue().decode("utf-8")
    assert "abc123" not in body
    assert "RuntimeError" not in body


# ── 8. per-IP 限流：同 IP 連發超過 _RATE_MAX 次 live → TooManyRequests ────────

def test_rate_limit_triggers_after_max_requests(monkeypatch):
    """同一 IP 在 60s 窗格內超過 _RATE_MAX 次 live → TooManyRequests。"""
    _patch_live(monkeypatch, "tok")
    calls: list = []
    monkeypatch.setattr(web, "run", _make_fake_run(calls))

    # 清除先前殘留 bucket（避免其他測試干擾）
    web._rate_buckets.clear()

    ip = "10.0.0.99"
    qs = _qs(live="1", token="tok")

    # 前 _RATE_MAX 次應正常
    for _ in range(web._RATE_MAX):
        web._do_analyze(qs, client_ip=ip)

    # 第 _RATE_MAX+1 次應拋 TooManyRequests
    with pytest.raises(web.TooManyRequests):
        web._do_analyze(qs, client_ip=ip)


# ── CEO 親測補充（CPO 指出的覆蓋缺口）──────────────────────────────────────

def test_correct_token_but_no_model_id_stays_offline(monkeypatch):
    """token 正確但未設 BEDROCK_MODEL_ID(HAS_BEDROCK=False)→ 強制離線。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "sek")
    report, _, _ = web._do_analyze(_qs(live="1", token="sek"), client_ip="9.9.9.9")
    assert any("[OFFLINE]" in i for i in report.inferences)


def test_offline_requests_never_rate_limited():
    """離線請求不消耗 per-IP 限流 bucket(高頻 demo 不會誤觸 429)。"""
    for _ in range(web._RATE_MAX + 10):
        web._do_analyze(_qs(live="0"), client_ip="8.8.8.8")  # 不應拋 TooManyRequests


# ── 9. _safe_href XSS scheme 驗證 ────────────────────────────────────────────

@pytest.mark.parametrize("bad_url", [
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
    "JaVaScRiPt:alert(1)",          # 大小寫混用
    " javascript:alert(1)",          # 前導空白
    "\tjavascript:alert(1)",         # 前導 tab
    "",                              # 空字串
    "relative/path",                 # 相對路徑（無 scheme）
    "//evil.com/xss",                # protocol-relative
])
def test_safe_href_blocks_dangerous_scheme(bad_url):
    """_safe_href 對非 http/https URL 不輸出 <a>，且輸出不含原始危險 scheme。"""
    out = web._safe_href(bad_url)
    assert "<a " not in out, f"不應產生 <a>：{bad_url!r} → {out!r}"
    # javascript: / data: 等危險 scheme 不得出現在輸出（只允許 escape 後的文字）
    assert 'href=' not in out, f"不應含 href：{bad_url!r} → {out!r}"


@pytest.mark.parametrize("good_url", [
    "http://example.com/article?id=42",
    "https://example.com/page",
    "HTTPS://EXAMPLE.COM/CAPS",      # 大寫 scheme（urlparse 保留大小寫，我們 lower 比較）
    "http://sub.domain.com:8080/path?a=1&b=2",
])
def test_safe_href_allows_http_https(good_url):
    """_safe_href 對 http/https URL 輸出含 href 的 <a> 連結。"""
    out = web._safe_href(good_url)
    assert "<a " in out, f"應產生 <a>：{good_url!r} → {out!r}"
    assert 'href=' in out, f"應含 href：{good_url!r} → {out!r}"
    assert 'target="_blank"' in out
    assert 'rel="noopener"' in out


def test_safe_href_escape_preserved_in_link():
    """http/https URL 含 HTML 特殊字元時，escape 仍保留（不產生 XSS）。"""
    url = 'https://evil.com/?x=<script>alert(1)</script>'
    out = web._safe_href(url)
    assert "<script>" not in out, "URL 中的 <script> 應被 escape"
    assert "<a " in out, "有效 https URL 應仍輸出連結"


def test_safe_href_escape_preserved_in_plain_text():
    """非 http/https URL 含 HTML 特殊字元時，escape 仍保留（純文字輸出也安全）。"""
    url = 'javascript:<img src=x onerror=alert(1)>'
    out = web._safe_href(url)
    assert "<img" not in out, "危險 HTML 應被 escape"
    assert "<a " not in out


def test_render_evidence_list_blocks_javascript_href():
    """_render_evidence_list：source_url=javascript:alert(1) → 不產生可點擊 <a>。"""
    from trustforge.schema import Evidence
    ev = [
        Evidence(
            source="xss-src",
            fetched_at="2026-07-01T00:00:00Z",
            content_reference="ref",
            related_claim="claim",
            source_url="javascript:alert(1)",
            trust=0.5,
        )
    ]
    report, _, _ = web._do_analyze({"coin": ["BTC"], "type": ["multi_source"], "q": ["t"]})
    out = web._render_report(report, ev)
    assert 'href="javascript:' not in out, "javascript: 不可出現在 href"
    assert "href='javascript:" not in out, "javascript: 不可出現在 href（單引號）"


def test_render_evidence_list_blocks_data_uri():
    """_render_evidence_list：source_url=data:text/html,... → 不產生可點擊 <a>。"""
    from trustforge.schema import Evidence
    ev = [
        Evidence(
            source="data-src",
            fetched_at="2026-07-01T00:00:00Z",
            content_reference="ref",
            related_claim="claim",
            source_url="data:text/html,<script>alert(1)</script>",
            trust=0.5,
        )
    ]
    report, _, _ = web._do_analyze({"coin": ["BTC"], "type": ["multi_source"], "q": ["t"]})
    out = web._render_report(report, ev)
    assert 'href="data:' not in out, "data: URI 不可出現在 href"
