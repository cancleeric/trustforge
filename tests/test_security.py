"""安全修復測試：token 驗證、per-IP 限流、q 長度上限、例外包裝。"""
from __future__ import annotations

import json
import logging

import pytest

from trustforge import web
from trustforge import lambda_handler


# ── helpers ──────────────────────────────────────────────────────────────────

def _qs(coin="BTC", qtype="multi_source", q="test", live="0", token="", sample=None):
    """組出 _do_analyze 期望的 qs dict。

    世界第一重寫 Phase 2：新增可選 `sample` 參數——預設 `None`（不帶
    `sample` key，落在新版預設檔位「真資料·$0」）；測試若要驗證跟資料
    模式默認值無關的邏輯（如 Bedrock token 驗證本身），可傳 `sample="1"`
    明確走離線示範沙盒，取得確定性的豐富樣本證據，避免被「真連接器全數
    cache miss → 證據太薄 → abstain」這種資料面雜訊污染判斷。
    """
    d: dict[str, list[str]] = {
        "coin": [coin], "type": [qtype], "q": [q], "live": [live],
    }
    if token:
        d["token"] = [token]
    if sample is not None:
        d["sample"] = [sample]
    return d


def _patch_live(monkeypatch, token_value="secret"):
    """把 web module 的 HAS_BEDROCK / LIVE_TOKEN 設成 live 可用狀態。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", True)
    monkeypatch.setattr(web, "LIVE_TOKEN", token_value)


def _make_fake_run(calls: list):
    """回傳 fake run()，記錄完整呼叫參數並強制離線執行（不打 AWS/真連接器）。

    世界第一重寫 Phase 2：預設檔位已從「離線樣本」變成「真資料·$0」
    （`data_mode="live", llm_mode="off"`），`_do_analyze` 對應會用這兩個
    關鍵字呼叫 `run()`（而非舊版單一 `offline` bool）——這裡改記錄完整
    參數組合，讓呼叫端能分辨究竟落在哪個檔位，不是只認 `offline`。
    """
    import trustforge.pipeline as _pl

    def fake_run(coin, query, qtype, offline=False, data_dir=None,
                 data_mode=None, llm_mode=None):
        calls.append({"offline": offline, "data_mode": data_mode, "llm_mode": llm_mode})
        # 強制離線避免真打 Bedrock/真連接器
        return _pl.run(coin, query, qtype, offline=True, data_dir=data_dir)

    return fake_run


# ── 1. live 沒帶 token → 不成立 live，落回新版預設「真資料·$0」 ──────────────

def test_live_no_token_falls_back_to_real_not_offline(monkeypatch):
    """live=1 但未帶 token 參數 → 不成立 live；世界第一重寫 Phase 2 起，
    安全的 fallback 檔位是「真資料·$0」（data_mode=live, llm_mode=off），
    不再是完整離線樣本——一樣不呼叫 Bedrock、一樣 $0，但資料是真連接器
    （credit-safe，且比舊版樣本 fallback 更誠實）。
    """
    _patch_live(monkeypatch)
    calls: list = []
    monkeypatch.setattr(web, "run", _make_fake_run(calls))

    web._do_analyze(_qs(live="1"))  # 沒有 token key
    assert calls, "run 應被呼叫"
    assert calls[0]["data_mode"] == "live" and calls[0]["llm_mode"] == "off", (
        f"沒帶 token 不成立 live，應落回預設真資料·$0 檔，實際 {calls[0]!r}"
    )


# ── 2. live 帶錯誤 token → 不成立 live，落回新版預設「真資料·$0」 ────────────

def test_live_wrong_token_falls_back_to_real_not_offline(monkeypatch):
    """live=1 + 錯誤 token → 不成立 live，落回真資料·$0（見上一測試說明）。"""
    _patch_live(monkeypatch, "secret123")
    calls: list = []
    monkeypatch.setattr(web, "run", _make_fake_run(calls))

    web._do_analyze(_qs(live="1", token="wrongtoken"))
    assert calls[0]["data_mode"] == "live" and calls[0]["llm_mode"] == "off", (
        f"錯誤 token 不成立 live，應落回預設真資料·$0 檔，實際 {calls[0]!r}"
    )


# ── 3. live + 正確 token + env 就緒 → live 路徑 ───────────────────────────────

def test_live_correct_token_calls_live_path(monkeypatch):
    """live=1 + 正確 token + LIVE_TOKEN/HAS_BEDROCK 設好 → offline=False。"""
    _patch_live(monkeypatch, "secret123")
    calls: list = []
    monkeypatch.setattr(web, "run", _make_fake_run(calls))

    web._do_analyze(_qs(live="1", token="secret123"))
    assert calls[0]["offline"] is False, "正確 token 應走 live（offline=False）"


# ── 4. live_token 未設（空字串）→ 即使帶 token 也不成立 live，落回真資料·$0 ──

def test_live_token_env_not_set_falls_back_to_real(monkeypatch):
    """TRUSTFORGE_LIVE_TOKEN 未設時，任何 token 都不能啟用 live，落回真資料·$0
    （見 `test_live_no_token_falls_back_to_real_not_offline` 說明）。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", True)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")   # 未設
    calls: list = []
    monkeypatch.setattr(web, "run", _make_fake_run(calls))

    web._do_analyze(_qs(live="1", token="anything"))
    assert calls[0]["data_mode"] == "live" and calls[0]["llm_mode"] == "off"


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
    web._real_rate_buckets.clear()

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
    """token 正確但未設 BEDROCK_MODEL_ID(HAS_BEDROCK=False)→ 強制離線。

    這條測的是「Bedrock 層本身」的離線降級（跟資料模式預設值無關），刻意帶
    `sample=1` 走離線示範沙盒，確保拿到確定性的豐富樣本證據——世界第一重寫
    Phase 2 起，不帶 `sample=1` 會落在真連接器·$0 預設檔，測試環境的連接器
    快取多半是空的（cache miss），證據可能太薄觸發 abstain，讓 Step3 narrative
    走「不採用 LLM narrative」的確定性模板（不含 `[OFFLINE]` 字樣），
    會讓這條測試量到資料面雜訊而非本來要驗的 Bedrock 層行為。
    """
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "sek")
    report, _, _ = web._do_analyze(
        _qs(live="1", token="sek", sample="1"), client_ip="9.9.9.9"
    )
    assert any("[OFFLINE]" in i for i in report.inferences)


def test_sample_requests_never_rate_limited():
    """`?sample=1` 離線示範沙盒不消耗 per-IP 限流 bucket（高頻 demo 不會誤觸 429）。

    世界第一重寫 Phase 2：離線示範不再是預設，改成 opt-in（`?sample=1`），
    這條測試對應更新——原測試名「offline_requests_never_rate_limited」驗的
    就是這個離線示範沙盒的限流豁免，語意不變，只是現在要顯式帶 `sample=1`
    才會落在這個檔位（見下一測試：不帶任何參數的新預設「真資料·$0」則
    *應該*被限流，兩者互補）。
    """
    for _ in range(web._RATE_MAX + 10):
        web._do_analyze(_qs(live="0", sample="1"), client_ip="8.8.8.8")  # 不應拋 TooManyRequests


def test_real_default_requests_not_rate_limited_at_normal_volume(monkeypatch):
    """codex HIGH（PR #44）：新預設「真資料·$0」（未帶任何 mode 參數）走自己
    獨立的寬鬆限流（`_check_real_rate_limit`／`_REAL_RATE_MAX`），不是 live
    的緊 `_check_live_rate_limit`（`_RATE_MAX`=5）——real-off 免費、只讀
    cache，緊限流是為了保護 Bedrock 花費，不該套在這條路徑上，否則一般
    使用者正常瀏覽（連跑幾次、比較分析算兩次呼叫）就會被誤 429，反向代理
    後所有使用者共用一個來源 IP 更會整批 429。

    這裡連跑超過 live 門檻（`_RATE_MAX`）次數的正常請求，不應觸發限流。

    用 `_make_fake_run` 強制離線執行 pipeline（不打真連接器/網路）——這裡
    只驗證限流邏輯本身（純看 client_ip/qs），跟 pipeline 實際抓到什麼資料
    無關，避免測試在 CI/沙盒環境因真連接器 cache miss/逾時而變慢或卡住。
    """
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")
    monkeypatch.setattr(web, "run", _make_fake_run([]))
    web._rate_buckets.clear()
    web._real_rate_buckets.clear()
    ip = "8.8.4.4"
    for _ in range(web._RATE_MAX + 10):
        web._do_analyze(_qs(live="0"), client_ip=ip)  # 未帶 sample/real/live → 落在新預設 real
    assert web._rate_buckets == {}, "real 預設路徑不該動用 live 的 _rate_buckets"


def test_real_default_requests_rate_limited_at_flood_volume(monkeypatch):
    """real-off 預設檔位仍需要限流（防真連接器被洪水級高頻打爆），只是門檻
    改成 DoS 洪水級（`_REAL_RATE_MAX`）而非 Bedrock 成本級（`_RATE_MAX`）——
    超過 `_REAL_RATE_MAX` 次才應拋 TooManyRequests（codex HIGH，PR #44）。
    這是「真資料·$0 成為預設」後最重要的防線，不可回歸成無限流。

    用 `_make_fake_run` 強制離線執行 pipeline，理由同上一測試。
    """
    monkeypatch.setattr(web, "HAS_BEDROCK", False)
    monkeypatch.setattr(web, "LIVE_TOKEN", "")
    monkeypatch.setattr(web, "run", _make_fake_run([]))
    web._rate_buckets.clear()
    web._real_rate_buckets.clear()
    ip = "8.8.4.5"
    for _ in range(web._REAL_RATE_MAX):
        web._do_analyze(_qs(live="0"), client_ip=ip)  # 未帶 sample/real/live → 落在新預設 real
    with pytest.raises(web.TooManyRequests):
        web._do_analyze(_qs(live="0"), client_ip=ip)


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
    "java\tscript:alert(1)",         # #11：scheme 中間插 tab
    "java\nscript:alert(1)",         # #11：scheme 中間插 newline
    "java\x00script:alert(1)",       # #11：scheme 中間插 null byte
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


# ── 前後端分離 Phase 3（task #28，harper CISO 安全審 must-have）───────────────
# `TRUSTFORGE_TRUST_PROXY`：config-gated 讀 X-Real-IP/X-Forwarded-For。


def test_trust_proxy_default_off_ignores_forwarded_headers():
    """TRUST_PROXY 預設關 → 無論 header 帶什麼，一律回傳直連 IP（行為逐字不變）。"""
    assert web.TRUST_PROXY is False, "預設必須是關，cutover 前不可誤開"
    headers = {"X-Real-IP": "1.2.3.4", "X-Forwarded-For": "5.6.7.8, 9.9.9.9"}
    assert web._resolve_client_ip("10.0.0.1", headers) == "10.0.0.1"


def test_trust_proxy_default_off_with_no_headers():
    """TRUST_PROXY 關、無任何反代 header → 仍回傳直連 IP（現況/直連部署）。"""
    assert web._resolve_client_ip("203.0.113.9", {}) == "203.0.113.9"


def test_trust_proxy_on_prefers_x_real_ip(monkeypatch):
    """TRUST_PROXY 開 → 優先信任 X-Real-IP（nginx 固定寫死 $remote_addr）。"""
    monkeypatch.setattr(web, "TRUST_PROXY", True)
    headers = {"X-Real-IP": "1.2.3.4", "X-Forwarded-For": "5.6.7.8, 9.9.9.9"}
    assert web._resolve_client_ip("127.0.0.1", headers) == "1.2.3.4"


def test_trust_proxy_on_falls_back_to_x_forwarded_for_first_hop(monkeypatch):
    """TRUST_PROXY 開、無 X-Real-IP → 退回 X-Forwarded-For，取最左（最原始）一段。"""
    monkeypatch.setattr(web, "TRUST_PROXY", True)
    headers = {"X-Forwarded-For": "5.6.7.8, 9.9.9.9"}
    assert web._resolve_client_ip("127.0.0.1", headers) == "5.6.7.8"


def test_trust_proxy_on_no_headers_falls_back_to_direct_ip(monkeypatch):
    """TRUST_PROXY 開但兩個 header 都沒帶（例如非 nginx 直打）→ 退回直連 IP。"""
    monkeypatch.setattr(web, "TRUST_PROXY", True)
    assert web._resolve_client_ip("127.0.0.1", {}) == "127.0.0.1"


def test_trust_proxy_env_var_gating(monkeypatch):
    """環境變數解析：只有明確 truthy 值才視為開啟，其餘（含未設/空字串/其他字串）視為關。"""
    import importlib

    for truthy in ("1", "true", "True", "YES", "on"):
        monkeypatch.setenv("TRUSTFORGE_TRUST_PROXY", truthy)
        importlib.reload(web)
        assert web.TRUST_PROXY is True, f"{truthy!r} 應解析為開啟"

    for falsy in ("0", "false", "no", "", "garbage"):
        monkeypatch.setenv("TRUSTFORGE_TRUST_PROXY", falsy)
        importlib.reload(web)
        assert web.TRUST_PROXY is False, f"{falsy!r} 應解析為關閉"

    monkeypatch.delenv("TRUSTFORGE_TRUST_PROXY", raising=False)
    importlib.reload(web)
    assert web.TRUST_PROXY is False, "未設定時預設必須是關"


def test_do_get_uses_resolved_client_ip_for_rate_limit(monkeypatch):
    """do_GET 讀取 client_ip 時走 `_resolve_client_ip`，TRUST_PROXY 開時吃 X-Real-IP。

    用 `/api/status`（`_check_status_rate_limit`）驗證：同一個偽造 X-Real-IP
    的請求，即使每次直連 IP（client_address）都不同，仍會被視為同一個
    per-IP bucket（因為 X-Real-IP 相同）——證明限流真的 keyed 在解析後的 IP。
    """
    monkeypatch.setattr(web, "TRUST_PROXY", True)
    web._status_rate_buckets.clear() if hasattr(web, "_status_rate_buckets") else None

    seen_ips: list[str] = []
    orig_handle = web._handle_api_status

    def fake_handle_api_status(client_ip):
        seen_ips.append(client_ip)
        return 200, "{}"

    monkeypatch.setattr(web, "_handle_api_status", fake_handle_api_status)

    h = web.Handler.__new__(web.Handler)
    h.client_address = ("10.9.9.1", 1)
    h.path = "/api/status"
    h.headers = {"X-Real-IP": "42.42.42.42"}

    from io import BytesIO
    h.wfile = BytesIO()
    h.send_response = lambda code: None
    h.send_header = lambda name, val: None
    h.end_headers = lambda: None

    h.client_address = ("10.9.9.2", 1)  # 換一個直連 IP，證明不是用這個 keyed
    h.do_GET()

    assert seen_ips == ["42.42.42.42"], "應該用 X-Real-IP 而非直連 client_address keyed"


# ── CSP_MODE：legacy（預設，byte-identical）／react（cutover 後）───────────────


def test_csp_mode_default_is_legacy():
    assert web.CSP_MODE == "legacy", "預設必須是 legacy，cutover 前不可誤切"


def test_send_legacy_csp_byte_identical(monkeypatch):
    """CSP_MODE=legacy（預設）時，`_send` 送出的 CSP header 必須與 cutover 前逐字相同。"""
    from io import BytesIO

    monkeypatch.setattr(web, "CSP_MODE", "legacy")
    h = web.Handler.__new__(web.Handler)
    h.wfile = BytesIO()
    captured: list[tuple] = []
    h.send_response = lambda code: captured.append(("status", code))
    h.send_header = lambda name, val: captured.append(("header", name, val))
    h.end_headers = lambda: None

    h._send(200, "ok")

    headers = {name: val for tp in captured if tp[0] == "header" for (_, name, val) in [tp]}
    assert headers["Content-Security-Policy"] == (
        "default-src 'none'; "
        "style-src 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com"
    )
    assert "X-Frame-Options" not in headers, "legacy 模式不應新增 X-Frame-Options"
    assert "Referrer-Policy" not in headers, "legacy 模式不應新增 Referrer-Policy"


def test_send_react_csp_and_extra_headers(monkeypatch):
    """CSP_MODE=react 時，`_send` 套用 harper 新指令集 + X-Frame-Options/Referrer-Policy。"""
    from io import BytesIO

    monkeypatch.setattr(web, "CSP_MODE", "react")
    h = web.Handler.__new__(web.Handler)
    h.wfile = BytesIO()
    captured: list[tuple] = []
    h.send_response = lambda code: captured.append(("status", code))
    h.send_header = lambda name, val: captured.append(("header", name, val))
    h.end_headers = lambda: None

    h._send(200, "ok")

    headers = {name: val for tp in captured if tp[0] == "header" for (_, name, val) in [tp]}
    csp = headers["Content-Security-Policy"]
    for directive in (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "connect-src 'self'",
        "img-src 'self' data:",
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'none'",
        "form-action 'self'",
    ):
        assert directive in csp, f"react CSP 缺少指令：{directive}"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_lambda_handler_csp_mode_legacy_default(monkeypatch):
    monkeypatch.setattr(web, "CSP_MODE", "legacy")
    resp = lambda_handler._resp(200, "ok", "text/plain")
    assert resp["headers"]["Content-Security-Policy"] == web._CSP_LEGACY
    assert "X-Frame-Options" not in resp["headers"]


def test_lambda_handler_csp_mode_react(monkeypatch):
    monkeypatch.setattr(web, "CSP_MODE", "react")
    resp = lambda_handler._resp(200, "ok", "text/plain")
    assert resp["headers"]["Content-Security-Policy"] == web._CSP_REACT
    assert resp["headers"]["X-Frame-Options"] == "DENY"
    assert resp["headers"]["Referrer-Policy"] == "strict-origin-when-cross-origin"


# ── main() 綁定收斂：TRUST_PROXY 開時強制 127.0.0.1 ─────────────────────────


def test_main_forces_127_bind_when_trust_proxy_on(monkeypatch):
    """TRUST_PROXY=1 但 TRUSTFORGE_BIND_HOST 設別的值 → main() 強制收斂成 127.0.0.1。"""
    monkeypatch.setattr(web, "TRUST_PROXY", True)
    monkeypatch.setenv("TRUSTFORGE_BIND_HOST", "0.0.0.0")

    captured_addr = {}

    class FakeServer:
        def __init__(self, addr, handler_cls):
            captured_addr["addr"] = addr

        def serve_forever(self):
            pass

    monkeypatch.setattr(web, "ThreadingHTTPServer", FakeServer)
    web.main()

    assert captured_addr["addr"][0] == "127.0.0.1", (
        "TRUST_PROXY 開啟時必須強制綁 127.0.0.1，不可信任 TRUSTFORGE_BIND_HOST 亂設"
    )


def test_main_respects_bind_host_when_trust_proxy_off(monkeypatch):
    """TRUST_PROXY 關（預設）→ 沿用 TRUSTFORGE_BIND_HOST（預設 0.0.0.0，直連部署現況）。"""
    monkeypatch.setattr(web, "TRUST_PROXY", False)
    monkeypatch.delenv("TRUSTFORGE_BIND_HOST", raising=False)

    captured_addr = {}

    class FakeServer:
        def __init__(self, addr, handler_cls):
            captured_addr["addr"] = addr

        def serve_forever(self):
            pass

    monkeypatch.setattr(web, "ThreadingHTTPServer", FakeServer)
    web.main()

    assert captured_addr["addr"][0] == "0.0.0.0"


# ── CISO hardening R2（#2a）：live token 改優先讀 X-Live-Token header ────────
# query `?token=` 版本保留一版 deprecation 相容（不斷線 + log warning），見
# `web._apply_live_token_header`（`Handler.do_GET` 解析 qs 後立刻呼叫的唯一
# 收斂點）。


def test_apply_live_token_header_prefers_header_over_query(caplog):
    """header 有值 → 覆寫 qs["token"]，即使 query 也帶了不同的 token 且被覆寫掉，
    仍然生效的是 header 版本。"""
    qs = {"token": ["old-query-token"]}
    with caplog.at_level(logging.WARNING):
        web._apply_live_token_header(qs, {"X-Live-Token": "new-header-token"})
    assert qs["token"] == ["new-header-token"]


def test_apply_live_token_header_header_and_query_both_present_still_warns(caplog):
    """codex/harper 雙審（PR #99 MEDIUM）：header 跟 query 同時帶 token（過渡期
    最常見情境），header 覆寫生效，但 query token 已經進了 URL／access log／
    Referer，外洩風險跟 header 有沒有生效無關——仍必須 log deprecation
    warning 才能在遷移期追蹤到誰還沒把舊參數拔掉；warning 內容不含 token 值。"""
    qs = {"token": ["old-query-token"]}
    with caplog.at_level(logging.WARNING):
        web._apply_live_token_header(qs, {"X-Live-Token": "new-header-token"})
    assert qs["token"] == ["new-header-token"], "header 仍應優先生效"
    warnings = [r.message for r in caplog.records if "已棄用" in r.message]
    assert warnings, f"header/query 同時帶時仍應 log deprecation warning，實際 {caplog.records!r}"
    assert all(
        "old-query-token" not in msg and "new-header-token" not in msg
        for msg in warnings
    ), "warning 訊息不應包含任何 token 值"


def test_apply_live_token_header_header_present_query_empty_no_warning(caplog):
    """header 有值、query 完全沒帶 token（非「同時帶」的情境）→ 沒有 query token
    可外洩，不需要 warn。"""
    qs = {}
    with caplog.at_level(logging.WARNING):
        web._apply_live_token_header(qs, {"X-Live-Token": "new-header-token"})
    assert qs["token"] == ["new-header-token"]
    assert not any("已棄用" in r.message for r in caplog.records)


def test_apply_live_token_header_falls_back_to_query_with_warning(caplog):
    """header 沒帶、query 帶 token → 照常運作（qs 不變）但 log deprecation warning。"""
    qs = {"token": ["legacy-token"]}
    with caplog.at_level(logging.WARNING):
        web._apply_live_token_header(qs, {})
    assert qs["token"] == ["legacy-token"], "沒有 header 時應保留 query token 相容運作"
    assert any(
        "query token 已棄用" in r.message and "X-Live-Token" in r.message
        for r in caplog.records
    ), f"應 log deprecation warning，實際 {caplog.records!r}"


def test_apply_live_token_header_no_token_anywhere_no_warning(caplog):
    """header 跟 query 都沒帶 token → qs 不變，也不 log warning（沒東西可棄用）。"""
    qs = {}
    with caplog.at_level(logging.WARNING):
        web._apply_live_token_header(qs, {})
    assert qs == {}
    assert not any("已棄用" in r.message for r in caplog.records)


def test_apply_live_token_header_handles_missing_headers_object():
    """headers 是 None（極端防呆）不應炸掉，維持既有 query fallback 行為。"""
    qs = {"token": ["legacy-token"]}
    web._apply_live_token_header(qs, None)
    assert qs["token"] == ["legacy-token"]


def test_apply_live_token_header_query_has_token_explicit_true_warns_even_if_blank(caplog):
    """codex/harper 終審（PR #99 LOW）：`query_has_token=True` 明確告知「query
    帶了 token 這個 key」時，即使 qs 裡對應的值是空字串（`?token=` 或裸
    `?token` 解析出來的樣子），仍要 log deprecation warning——不能只看
    truthiness。模擬呼叫端已經用 keep_blank_values=True 判斷過的情境。"""
    qs = {"token": [""]}
    with caplog.at_level(logging.WARNING):
        web._apply_live_token_header(qs, {}, query_has_token=True)
    warnings = [r.message for r in caplog.records if "已棄用" in r.message]
    assert warnings, "query_has_token=True 時即使值為空也應 log deprecation warning"


def test_apply_live_token_header_qs_has_blank_token_key_warns_via_key_presence(caplog):
    """未顯式傳 query_has_token（向後相容既有呼叫）時，退回用 `"token" in qs`
    判斷（key 存在即算，不看 truthiness）——涵蓋呼叫端自己用
    keep_blank_values=True 手動建構出 `qs = {"token": [""]}` 的情境。"""
    qs = {"token": [""]}
    with caplog.at_level(logging.WARNING):
        web._apply_live_token_header(qs, {})
    warnings = [r.message for r in caplog.records if "已棄用" in r.message]
    assert warnings, "qs 裡有 token key（即使值空）就該 warn，不能只看 truthiness 漏報"


def test_do_get_api_analyze_query_token_equals_blank_still_warns(monkeypatch, caplog):
    """端到端（PR #99 LOW 三案例之一）：`?token=`（裸等號、空值）——`parse_qs`
    預設 `keep_blank_values=False` 會把這個 key 整個丟掉，若只看 `qs` 內容
    會漏報；`Handler.do_GET` 需另外用 keep_blank_values=True 偵測到這個 key
    存在，一樣要 log deprecation warning（migration 追蹤不能漏掉這種請求）。
    """
    captured: dict = {}

    def fake_handle_api_analyze(qs, client_ip):
        captured["token"] = qs.get("token", [""])[0]
        return 200, "{}"

    monkeypatch.setattr(web, "_handle_api_analyze", fake_handle_api_analyze)

    from io import BytesIO

    h = web.Handler.__new__(web.Handler)
    h.client_address = ("10.9.9.1", 1)
    h.path = "/api/analyze?token="
    h.headers = {}
    h.wfile = BytesIO()
    h.send_response = lambda code: None
    h.send_header = lambda name, val: None
    h.end_headers = lambda: None

    with caplog.at_level(logging.WARNING):
        h.do_GET()

    assert captured["token"] == ""
    warnings = [r.message for r in caplog.records if "已棄用" in r.message]
    assert warnings, "?token=（空值）也應觸發 deprecation warning，不能因為值是空字串就漏報"


def test_do_get_api_analyze_bare_token_no_equals_still_warns(monkeypatch, caplog):
    """端到端（PR #99 LOW 三案例之二）：裸 `?token`（連 `=` 都沒有）——同樣會被
    `parse_qs` 預設丟棄，一樣要 log deprecation warning。"""
    captured: dict = {}

    def fake_handle_api_analyze(qs, client_ip):
        captured["token"] = qs.get("token", [""])[0]
        return 200, "{}"

    monkeypatch.setattr(web, "_handle_api_analyze", fake_handle_api_analyze)

    from io import BytesIO

    h = web.Handler.__new__(web.Handler)
    h.client_address = ("10.9.9.1", 1)
    h.path = "/api/analyze?token"
    h.headers = {}
    h.wfile = BytesIO()
    h.send_response = lambda code: None
    h.send_header = lambda name, val: None
    h.end_headers = lambda: None

    with caplog.at_level(logging.WARNING):
        h.do_GET()

    assert captured["token"] == ""
    warnings = [r.message for r in caplog.records if "已棄用" in r.message]
    assert warnings, "裸 ?token（無 =）也應觸發 deprecation warning，不能因為 parse_qs 預設丟棄就漏報"


def test_do_get_api_analyze_empty_query_valid_header_no_warning(monkeypatch, caplog):
    """端到端（PR #99 LOW 三案例之三）：完全沒有 query token（連 key 都沒有），
    只帶合法的 X-Live-Token header → header 正常生效，且不該有 deprecation
    warning（沒有任何舊參數可棄用，不能誤報）。"""
    captured: dict = {}

    def fake_handle_api_analyze(qs, client_ip):
        captured["token"] = qs.get("token", [""])[0]
        return 200, "{}"

    monkeypatch.setattr(web, "_handle_api_analyze", fake_handle_api_analyze)

    from io import BytesIO

    h = web.Handler.__new__(web.Handler)
    h.client_address = ("10.9.9.1", 1)
    h.path = "/api/analyze"
    h.headers = {"X-Live-Token": "valid-header-token"}
    h.wfile = BytesIO()
    h.send_response = lambda code: None
    h.send_header = lambda name, val: None
    h.end_headers = lambda: None

    with caplog.at_level(logging.WARNING):
        h.do_GET()

    assert captured["token"] == "valid-header-token"
    assert not any("已棄用" in r.message for r in caplog.records), (
        "完全沒有 query token、只有 header 時不該誤報 deprecation warning"
    )


def test_lambda_handler_query_token_blank_still_warns(monkeypatch, caplog):
    """端到端（Lambda 版）：queryStringParameters 帶 `{"token": ""}`（`?token=`
    或裸 `?token`，API Gateway/Function URL 都會正規化成空字串），raw_qs 本來
    就保留這個 key，一樣要 log deprecation warning。"""
    monkeypatch.setattr(lambda_handler.web, "HAS_BEDROCK", False)

    event = {
        "rawPath": "/healthz",
        "queryStringParameters": {"token": ""},
        "headers": {},
        "requestContext": {"http": {"sourceIp": "1.2.3.4"}},
    }
    with caplog.at_level(logging.WARNING):
        lambda_handler.handler(event)

    warnings = [r.message for r in caplog.records if "已棄用" in r.message]
    assert warnings, "Lambda 版 query token 為空值時也應 log deprecation warning"


def test_mode_extra_params_never_leaks_token_into_self_link(monkeypatch):
    """codex/harper 終審（PR #99 HIGH，推翻先前「延後修」裁決）：`_mode_extra_params()`
    先前會把 `qs["token"]`（不論來源是 header 正規化後寫回、還是舊 query）原樣
    塞進自我連結（如 `/analyze.json?...&token=...`）回吐給前端——header-only
    客戶端的 token 因此也會被本函式重新暴露進 URL／HTML／瀏覽器歷史／access
    log／Referer，是本 PR 把 token 從 query 移到 header 之後、在輸出端重新
    打開的洩漏面，不是既有問題，必須本 PR 修。自我連結一律只留 `live=1`
    這個不敏感的模式開關，不含任何憑證；live 模式重放改由客戶端下次請求
    自行重帶 `X-Live-Token` header。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", True)
    monkeypatch.setattr(web, "LIVE_TOKEN", "secret-token-value")

    qs = {"live": ["1"], "token": ["secret-token-value"]}
    extra = web._mode_extra_params(qs)

    assert extra == {"live": "1"}, (
        f"自我連結不應含 token：_mode_extra_params 只該回傳 {{'live': '1'}}，實際 {extra!r}"
    )
    assert "token" not in extra
    href = web._analyze_json_href("BTC", "single", "q", extra)
    assert "secret-token-value" not in href, f"自我連結 href 不應含 token 值，實際 {href!r}"
    assert "token=" not in href, f"自我連結 href 不應含 token 參數本身，實際 {href!r}"


def test_do_get_api_analyze_prefers_header_token_over_query(monkeypatch):
    """端到端：`/api/analyze` 走 `Handler.do_GET`，X-Live-Token header 蓋過
    query `?token=`（同一個請求兩者都帶，header 生效）。"""
    captured: dict = {}

    def fake_handle_api_analyze(qs, client_ip):
        captured["token"] = qs.get("token", [""])[0]
        return 200, "{}"

    monkeypatch.setattr(web, "_handle_api_analyze", fake_handle_api_analyze)

    h = web.Handler.__new__(web.Handler)
    h.client_address = ("10.9.9.1", 1)
    h.path = "/api/analyze?token=old-query-token"
    h.headers = {"X-Live-Token": "new-header-token"}

    from io import BytesIO

    h.wfile = BytesIO()
    h.send_response = lambda code: None
    h.send_header = lambda name, val: None
    h.end_headers = lambda: None

    h.do_GET()

    assert captured["token"] == "new-header-token"


def test_do_get_api_analyze_query_token_still_works_with_warning(monkeypatch, caplog):
    """端到端：舊式 `?token=` 沒帶 header 時仍照常運作（7/13 工作坊 demo 腳本
    不斷線），但 log deprecation warning。"""
    captured: dict = {}

    def fake_handle_api_analyze(qs, client_ip):
        captured["token"] = qs.get("token", [""])[0]
        return 200, "{}"

    monkeypatch.setattr(web, "_handle_api_analyze", fake_handle_api_analyze)

    h = web.Handler.__new__(web.Handler)
    h.client_address = ("10.9.9.1", 1)
    h.path = "/api/analyze?token=old-query-token"
    h.headers = {}

    from io import BytesIO

    h.wfile = BytesIO()
    h.send_response = lambda code: None
    h.send_header = lambda name, val: None
    h.end_headers = lambda: None

    with caplog.at_level(logging.WARNING):
        h.do_GET()

    assert captured["token"] == "old-query-token", "沒帶 header 時舊式 query token 仍要能用"
    assert any(
        "query token 已棄用" in r.message for r in caplog.records
    ), f"應 log deprecation warning，實際 {caplog.records!r}"


def test_lambda_handler_prefers_x_live_token_header_over_query(monkeypatch):
    """Lambda Function URL 入口跟 EC2 `web.py` 共用同一顆
    `_apply_live_token_header`：header 優先於 query（大小寫皆可，Function
    URL 事件的 header key 一般已是小寫）。"""
    captured: dict = {}

    # 直接觀察 `_do_analyze` 收到的 qs，不需要跑完整 pipeline；用可預期例外
    # 提早結束（`lambda_handler` 對 `TooManyRequests` 有現成的 429 分支）。
    def fake_do_analyze(qs, *a, **k):
        captured["token"] = qs.get("token", [""])[0]
        raise web.TooManyRequests("stop-here")

    monkeypatch.setattr(web, "_do_analyze", fake_do_analyze)

    event = {
        "rawPath": "/analyze",
        "requestContext": {"http": {"sourceIp": "9.9.9.9"}},
        "queryStringParameters": {"live": "1", "token": "old-query-token"},
        "headers": {"x-live-token": "new-header-token"},
    }
    lambda_handler.handler(event)

    assert captured["token"] == "new-header-token"



# ── #93（harper CISO PR #92 審查附帶條件 #1）：dedup fail-open 告警 ─────────
# `web.py` 裡 SSR `/analyze`／`/analyze.json` 路由準備 dedup coin_key／
# dedup key 失敗時 fail-open（放行不套用 dedup），先前只有
# `logging.exception` 默默記一筆。這裡驗證：未達頻率門檻只記一般
# `WARNING`；達到門檻（`_DEDUP_PREP_FAILURE_ALERT_THRESHOLD` 次／
# `_DEDUP_PREP_FAILURE_WINDOW_SEC` 秒滑動視窗內）才升級成 `ALERT:`
# 前綴的 `ERROR` log，且 `/api/status` 的 `dedup` 欄位如實反映
# `degraded` 狀態，供監控端使用。

def _call_ssr_analyze_for_dedup_alert_test(path_and_query: str, client_ip: str = "10.9.9.9"):
    """比照本檔既有 `do_GET` fake handler 慣例，打一次 SSR `/analyze.json`。"""
    from io import BytesIO

    buf = BytesIO()
    h = web.Handler.__new__(web.Handler)
    h.client_address = (client_ip, 12345)
    h.path = path_and_query
    h.headers = {}
    h.wfile = buf

    captured_status: list[int] = []
    h.send_response = lambda code: captured_status.append(code)
    h.send_header = lambda name, val: None
    h.end_headers = lambda: None

    h.do_GET()

    status = captured_status[-1] if captured_status else None
    return status, buf.getvalue().decode("utf-8")


def test_dedup_prep_failure_below_threshold_only_warning_no_alert(monkeypatch, caplog):
    """未達 `_DEDUP_PREP_FAILURE_ALERT_THRESHOLD` 次的 dedup coin_key 準備
    失敗，只該記一般 `WARNING`，不該觸發 `ALERT:` 前綴的 `ERROR` 升級 log
    ——避免偶發、單次的 fail-open 就洗版告警。"""
    web._dedup_prep_failure_timestamps.clear()
    web._dedup_prep_failure_last_alert_ts = 0.0

    def _boom(*a, **kw):
        raise RuntimeError("dedup coin_key boom")

    monkeypatch.setattr(web, "_analyze_dedup_coin_key", _boom)
    try:
        with caplog.at_level(logging.WARNING):
            for _ in range(web._DEDUP_PREP_FAILURE_ALERT_THRESHOLD - 1):
                code, _body = _call_ssr_analyze_for_dedup_alert_test(
                    "/analyze.json?coin=BTC&type=multi_source&q=dedup-alert-below&sample=1"
                )
                assert code == 200, "coin_key 準備失敗應 fail-open 放行，不影響請求本身成功"

        alert_errors = [
            r for r in caplog.records
            if r.levelno == logging.ERROR and r.message.startswith("ALERT: TrustForge dedup")
        ]
        assert not alert_errors, f"未達門檻不該觸發 ALERT，實際 {caplog.records!r}"

        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "dedup coin_key 準備失敗" in r.message
        ]
        assert len(warnings) == web._DEDUP_PREP_FAILURE_ALERT_THRESHOLD - 1

        health = web._dedup_prep_failure_health()
        assert health["degraded"] is False
        assert health["recent_failures"] == web._DEDUP_PREP_FAILURE_ALERT_THRESHOLD - 1
    finally:
        web._dedup_prep_failure_timestamps.clear()
        web._dedup_prep_failure_last_alert_ts = 0.0


def test_dedup_prep_failure_reaches_threshold_triggers_alert_and_status_degraded(monkeypatch, caplog):
    """連續達到 `_DEDUP_PREP_FAILURE_ALERT_THRESHOLD` 次 dedup coin_key
    準備失敗 → 升級成 `ALERT:` 前綴的 `ERROR` log，且 `/api/status` 的
    `dedup` 欄位顯示 `degraded: true`（監控端可見，不需要 grep server
    log）。"""
    web._dedup_prep_failure_timestamps.clear()
    web._dedup_prep_failure_last_alert_ts = 0.0

    def _boom(*a, **kw):
        raise RuntimeError("dedup coin_key boom")

    monkeypatch.setattr(web, "_analyze_dedup_coin_key", _boom)
    try:
        with caplog.at_level(logging.WARNING):
            for _ in range(web._DEDUP_PREP_FAILURE_ALERT_THRESHOLD):
                code, _body = _call_ssr_analyze_for_dedup_alert_test(
                    "/analyze.json?coin=BTC&type=multi_source&q=dedup-alert-reach&sample=1"
                )
                assert code == 200

        alert_errors = [
            r for r in caplog.records
            if r.levelno == logging.ERROR and r.message.startswith("ALERT: TrustForge dedup")
        ]
        assert alert_errors, f"達門檻應觸發 ALERT 前綴 ERROR log，實際 {caplog.records!r}"

        health = web._dedup_prep_failure_health()
        assert health["degraded"] is True
        assert health["recent_failures"] >= web._DEDUP_PREP_FAILURE_ALERT_THRESHOLD

        code, body = web._handle_api_status(client_ip="")
        assert code == 200
        payload = json.loads(body)
        assert payload["data"]["dedup"]["degraded"] is True
    finally:
        web._dedup_prep_failure_timestamps.clear()
        web._dedup_prep_failure_last_alert_ts = 0.0


def test_dedup_prep_failure_alert_has_cooldown_avoids_log_flood_while_degraded_persists(
    monkeypatch, caplog
):
    """CTO 複審（接手 #93）補充：若 dedup 準備失敗是持續性的（例如真的回歸，
    往後每個請求都失敗），原始設計會讓「達門檻後的每一次」後續失敗都再噴一行
    `ALERT:` `ERROR`——高流量下等於每個請求都寫一行 ERROR，把 server log
    洗版、稀釋掉真正需要人工介入的訊號。這裡驗證：同一輪 degraded 期間，
    達門檻後再持續失敗，不該重複觸發 ALERT（有冷卻期），但 `/api/status`
    的 `degraded` 仍要如實反映當下真實狀態（冷卻期只節流 log，不節流
    健康狀態本身）。"""
    web._dedup_prep_failure_timestamps.clear()
    web._dedup_prep_failure_last_alert_ts = 0.0

    def _boom(*a, **kw):
        raise RuntimeError("dedup coin_key boom")

    monkeypatch.setattr(web, "_analyze_dedup_coin_key", _boom)
    try:
        with caplog.at_level(logging.WARNING):
            # 先打到門檻，觸發第一次 ALERT。
            for _ in range(web._DEDUP_PREP_FAILURE_ALERT_THRESHOLD):
                code, _body = _call_ssr_analyze_for_dedup_alert_test(
                    "/analyze.json?coin=BTC&type=multi_source&q=dedup-alert-cooldown-a&sample=1"
                )
                assert code == 200

            first_round_alerts = [
                r for r in caplog.records
                if r.levelno == logging.ERROR and r.message.startswith("ALERT: TrustForge dedup")
            ]
            assert len(first_round_alerts) == 1, (
                f"達門檻那一刻應恰好觸發一次 ALERT，實際 {first_round_alerts!r}"
            )

            # degraded 持續期間（冷卻期內）再打幾次失敗，不該再重複 ALERT。
            for _ in range(3):
                code, _body = _call_ssr_analyze_for_dedup_alert_test(
                    "/analyze.json?coin=BTC&type=multi_source&q=dedup-alert-cooldown-b&sample=1"
                )
                assert code == 200

        all_alerts = [
            r for r in caplog.records
            if r.levelno == logging.ERROR and r.message.startswith("ALERT: TrustForge dedup")
        ]
        assert len(all_alerts) == 1, (
            f"冷卻期內持續失敗不該重複噴 ALERT（避免洗版），實際 {all_alerts!r}"
        )

        # 但 /api/status 的 degraded 仍要如實反映當下真實狀態，不受冷卻期影響。
        health = web._dedup_prep_failure_health()
        assert health["degraded"] is True
    finally:
        web._dedup_prep_failure_timestamps.clear()
        web._dedup_prep_failure_last_alert_ts = 0.0
