"""web 服務 smoke 測試（不開真 socket，直接測處理邏輯）。"""
import html
import json
import re
import time
from io import BytesIO
from email.message import Message
from urllib.parse import parse_qs, urlparse

import pytest

from trustforge import web
from trustforge.schema import COIN_POOL


def _stop_overview_bg_thread_for_test() -> None:
    """測試專用：強制停止目前的首頁總覽背景 thread（若有）並清空
    in-memory 狀態（同 `test_home_overview.py` 慣例，本檔獨立維護一份，
    比照 `test_status_page.py`／本檔既有各自維護自己 module-state-reset
    fixture 的既有風格，不共用單一 helper）。"""
    stop_event = web._overview_bg_stop_event
    if stop_event is not None:
        stop_event.set()
    thread = web._overview_bg_thread
    if thread is not None:
        thread.join(timeout=3.0)
    web._overview_bg_thread = None
    web._overview_bg_stop_event = None
    web._overview_html = None


@pytest.fixture(autouse=True)
def _isolate_home_overview_cache(monkeypatch):
    """Axis C #1：`_render_home_page()` 會透過 `_render_home_overview_cached()`
    讀一次 in-memory 總覽現貨（`_overview_html`），並可能懶啟動背景刷新
    thread（`_ensure_overview_bg_thread_started()`）——本檔多數測試與總覽
    功能無關，強制 `CACHE_BACKEND=json`（`_isolate_connector_cache` 既有
    autouse fixture 已把 `TRUSTFORGE_CACHE_DIR` 指到隔離的 tmp_path），避免
    背景 thread 在有設定真 AWS SSO/憑證的開發機上意外打到真 DynamoDB
    （即使只是讀，也不該讓不相關的測試行為依賴開發者本機的 AWS 設定而變
    得不確定）；並在每個測試前後徹底停掉背景 thread、清空 `_overview_html`
    （比照 `test_status_page.py::_reset_status_module_state` 慣例），避免
    跨測試互相汙染。
    """
    monkeypatch.setenv("CACHE_BACKEND", "json")
    _stop_overview_bg_thread_for_test()
    yield
    _stop_overview_bg_thread_for_test()


def test_do_analyze_returns_report():
    report, evidence, log = web._do_analyze({"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]})
    assert report.coin == "BTC"
    assert report.market_judgment
    assert evidence
    assert log.events


def test_render_report_is_html():
    report, evidence, _ = web._do_analyze({"coin": ["ETH"], "type": ["hypothesis"], "q": ["ETH 盤整"]})
    htmlout = web._render_report(report, evidence)
    assert "市場判斷" in htmlout
    assert "<table>" in htmlout


def test_analyze_json_is_serialisable():
    import dataclasses
    report, evidence, log = web._do_analyze({"coin": ["SOL"], "type": ["multi_source"], "q": ["x"]})
    payload = {
        "report": dataclasses.asdict(report),
        "evidence": [e.to_dict() for e in evidence],
        "execution_log": log.events,
    }
    s = json.dumps(payload, ensure_ascii=False)  # 不可拋例外
    assert "report" in json.loads(s)


def test_bad_coin_rejected():
    import pytest
    with pytest.raises(ValueError):
        web._do_analyze({"coin": ["DOGE"], "type": ["multi_source"], "q": ["x"]})
    assert "BTC" in COIN_POOL


# ---------------------------------------------------------------------------
# P2-4 Demo UI 信任分數面板 驗收測試
# ---------------------------------------------------------------------------

def test_render_report_has_trust_bar_class():
    """_render_report 含 CSS 信任橫條 class（tf-bar-wrap）。"""
    report, evidence, _ = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
    )
    htmlout = web._render_report(report, evidence)
    assert "tf-bar-wrap" in htmlout, "缺少 tf-bar-wrap class"


def test_render_report_has_details():
    """_render_report 含 <details> 可展開 evidence 列表。"""
    report, evidence, _ = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
    )
    htmlout = web._render_report(report, evidence)
    assert "<details>" in htmlout, "缺少 <details> 元素"


def test_render_report_has_confidence_block():
    """_render_report 含整體信心視覺化區塊（tf-conf-wrap）。"""
    report, evidence, _ = web._do_analyze(
        {"coin": ["ETH"], "type": ["hypothesis"], "q": ["ETH 盤整"]}
    )
    htmlout = web._render_report(report, evidence)
    assert "tf-conf-wrap" in htmlout, "缺少 tf-conf-wrap 信心儀表區塊"


def test_render_report_source_url_link():
    """evidence 有 source_url 時，渲染含 <a href target=_blank rel=noopener>。"""
    from trustforge.schema import Evidence
    ev_with_url = [
        Evidence(
            source="test-src",
            fetched_at="2026-07-01T00:00:00Z",
            content_reference="test content reference",
            related_claim="test claim",
            source_url="https://example.com/article?id=42",
            trust=0.75,
        )
    ]
    report, _, _ = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
    )
    htmlout = web._render_report(report, ev_with_url)
    assert 'href="https://example.com/article?id=42"' in htmlout, "source_url href 缺失"
    assert 'target="_blank"' in htmlout, "target=_blank 缺失"
    assert 'rel="noopener"' in htmlout, "rel=noopener 缺失"


def test_render_report_low_trust_badge():
    """trust < 0.3 的 evidence 應顯示低信任 badge（tf-low class + 低信任文字）。"""
    from trustforge.schema import Evidence
    ev_low = [
        Evidence(
            source="shady-src",
            fetched_at="2026-07-01T00:00:00Z",
            content_reference="suspicious content",
            related_claim="shady claim",
            trust=0.15,  # < 0.3 觸發 badge
        )
    ]
    report, _, _ = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
    )
    htmlout = web._render_report(report, ev_low)
    assert "tf-low" in htmlout, "缺少 tf-low badge class"
    assert "低信任" in htmlout, "缺少 '低信任' 文字"


def test_render_report_high_trust_no_badge():
    """trust >= 0.3 的 evidence 不應出現低信任 badge。"""
    from trustforge.schema import Evidence
    ev_high = [
        Evidence(
            source="reliable-src",
            fetched_at="2026-07-01T00:00:00Z",
            content_reference="reliable content",
            related_claim="solid claim",
            trust=0.85,
        )
    ]
    report, _, _ = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
    )
    htmlout = web._render_report(report, ev_high)
    assert "tf-low" not in htmlout, "trust >= 0.3 不應出現 tf-low badge"


def test_render_report_source_url_xss_escaped():
    """source_url 中的 XSS 特殊字元應被 html.escape（安全防護）。"""
    from trustforge.schema import Evidence
    xss_url = "https://evil.com/?x=<script>alert(1)</script>"
    ev_xss = [
        Evidence(
            source="xss-src",
            fetched_at="2026-07-01T00:00:00Z",
            content_reference="xss ref",
            related_claim="xss",
            source_url=xss_url,
            trust=0.5,
        )
    ]
    report, _, _ = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
    )
    htmlout = web._render_report(report, ev_xss)
    assert "<script>" not in htmlout, "source_url XSS 未被 escape"


def test_render_report_three_sections():
    """_render_report 含事實/推論/結論三段分區。"""
    report, evidence, _ = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
    )
    htmlout = web._render_report(report, evidence)
    assert "事實" in htmlout, "缺少事實區段"
    assert "推論" in htmlout, "缺少推論區段"
    assert "結論" in htmlout, "缺少結論區段"


# ---------------------------------------------------------------------------
# B4 信任分數「可解釋」可視化 驗收測試
# ---------------------------------------------------------------------------

def test_trust_breakdown_shows_components():
    """trust_components 有值時，展開面板應顯示四分項關鍵字（reputation/corroboration 等對應中文）。"""
    from trustforge.schema import Evidence
    ev = [
        Evidence(
            source="test-src",
            fetched_at="2026-07-01T00:00:00Z",
            content_reference="some content",
            related_claim="some claim",
            trust=0.72,
            trust_components={
                "reputation": 0.95,
                "corroboration": 0.75,
                "recency": 1.00,
                "manipulation": 0.00,
            },
        )
    ]
    report, _, _ = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
    )
    htmlout = web._render_report(report, ev)
    assert "信譽" in htmlout, "缺少信譽分項"
    assert "佐證" in htmlout, "缺少佐證分項"
    assert "時效" in htmlout, "缺少時效分項"
    assert "操縱" in htmlout, "缺少操縱分項"
    assert "0.95" in htmlout, "信譽值 0.95 未顯示"
    assert "0.75" in htmlout, "佐證值 0.75 未顯示"


def test_trust_breakdown_manip_red():
    """manipulation > 0 時應以紅色（#cb2431）樣式標示。"""
    from trustforge.schema import Evidence
    ev = [
        Evidence(
            source="shill-src",
            fetched_at="2026-07-01T00:00:00Z",
            content_reference="暴漲 to the moon",
            related_claim="pump claim",
            trust=0.10,
            trust_components={
                "reputation": 0.35,
                "corroboration": 0.00,
                "recency": 0.80,
                "manipulation": 0.80,
            },
        )
    ]
    report, _, _ = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
    )
    htmlout = web._render_report(report, ev)
    # 操縱值 > 0 → 應有 #cb2431 紅色標示
    assert "#cb2431" in htmlout, "manipulation > 0 應出現紅色 #cb2431"
    # 操縱值顯示
    assert "0.80" in htmlout, "manipulation 值 0.80 未顯示"


def test_trust_breakdown_empty_dict_no_crash():
    """trust_components 為空 dict（舊資料）不應崩潰，且靜默略過不輸出分項區塊。"""
    from trustforge.schema import Evidence
    ev = [
        Evidence(
            source="old-src",
            fetched_at="2026-07-01T00:00:00Z",
            content_reference="old content",
            related_claim="old claim",
            trust=0.60,
            trust_components={},   # 空 dict
        )
    ]
    report, _, _ = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
    )
    # 不拋例外即通過
    htmlout = web._render_report(report, ev)
    assert isinstance(htmlout, str), "回傳應為字串"
    # 不應出現信任分析區塊（因為空 dict）
    assert "信任分析" not in htmlout, "空 trust_components 不應顯示信任分析區塊"


def test_trust_breakdown_corroboration_badge():
    """corroboration > 0 顯示「✓ 有獨立來源交叉佐證」；= 0 顯示「— 無交叉佐證」。"""
    from trustforge.schema import Evidence

    report, _, _ = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
    )

    # corroboration > 0
    ev_corr = [
        Evidence(
            source="multi-src",
            fetched_at="2026-07-01T00:00:00Z",
            content_reference="corroborated content",
            related_claim="claim",
            trust=0.80,
            trust_components={
                "reputation": 0.90,
                "corroboration": 0.50,
                "recency": 0.90,
                "manipulation": 0.00,
            },
        )
    ]
    html_corr = web._render_report(report, ev_corr)
    assert "有獨立來源交叉佐證" in html_corr, "corroboration > 0 應顯示交叉佐證文字"

    # corroboration = 0
    ev_no_corr = [
        Evidence(
            source="solo-src",
            fetched_at="2026-07-01T00:00:00Z",
            content_reference="single source content",
            related_claim="claim",
            trust=0.60,
            trust_components={
                "reputation": 0.90,
                "corroboration": 0.00,
                "recency": 0.90,
                "manipulation": 0.00,
            },
        )
    ]
    html_no_corr = web._render_report(report, ev_no_corr)
    assert "無交叉佐證" in html_no_corr, "corroboration = 0 應顯示無交叉佐證文字"


def test_render_comparison_has_trust_bars(monkeypatch):
    """_render_comparison 含信任橫條（tf-bar-wrap）與可展開 details。"""
    from trustforge.ingestion.base import Document

    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return [
            Document(id="d1", kind="price", source="fake-ohlcv", text=f"{coin} price data"),
            Document(id="d2", kind="news", source="fake-news", text=f"{coin} news"),
        ]

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    report_a, evidence_a, report_b, evidence_b, _ = web._do_comparison(
        {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["BTC vs ETH"]},
        client_ip="",
    )
    htmlout = web._render_comparison(report_a, evidence_a, report_b, evidence_b, "BTC vs ETH")
    assert "tf-bar-wrap" in htmlout, "comparison 頁缺少 tf-bar-wrap"
    assert "<details>" in htmlout, "comparison 頁缺少 <details>"
    assert "BTC" in htmlout and "ETH" in htmlout


# ---------------------------------------------------------------------------
# 世界第一重寫 Phase 1：首頁不再空白 + header 拔 dev artifacts
#
# 背景：`docs/DEV-PLAN-REWRITE.md` Phase 1——判審打開首頁的第一眼不該是黑色
# 空白 `.tf-dashboard` + header 露出 `tf-version`/三檔模式徽號/`cost ledger`
# 這類 dev 內部資訊。這批測試斷言的是「移位」而非「刪除」：dev artifacts
# 從首頁 header 消失，但版號/模式能力/成本摘要仍完整活在 `/status`（見
# `tests/test_status_page.py::test_status_page_shows_version_and_mode_info`
# 等既有斷言，未刪除、未搬動，本檔不重複斷言 /status 內容）。
# ---------------------------------------------------------------------------

def _do_get(path: str) -> tuple[int, str]:
    """比照 `tests/test_status_page.py` 既有慣例，端到端呼叫 `Handler.do_GET`
    （不開真 socket），回傳 (status_code, body)。"""
    h = web.Handler.__new__(web.Handler)
    h.client_address = ("127.0.0.1", 12345)
    h.path = path
    h.wfile = BytesIO()
    h.headers = Message()

    captured = []
    h.send_response = lambda code: captured.append(code)
    h.send_header = lambda name, val: None
    h.end_headers = lambda: None

    h.do_GET()

    body = h.wfile.getvalue().decode("utf-8")
    return captured[0], body


def test_render_home_page_is_non_empty_and_has_hero_keywords():
    """`_render_home_page()` 純函式：非空字串，含一句話定位（hero）文案。"""
    htmlout = web._render_home_page()
    assert htmlout.strip()
    assert "信任提煉" in htmlout
    assert "怎麼運作" in htmlout
    assert "步驟 1/3" in htmlout and "步驟 2/3" in htmlout and "步驟 3/3" in htmlout


def test_render_home_page_has_query_console_cta():
    """Hero CTA 導向左側 Query Console（錨點 `#tf-query-console`）。"""
    htmlout = web._render_home_page()
    assert 'href="#tf-query-console"' in htmlout


def test_render_home_page_example_link_uses_real_analyze_query():
    """「看範例報告」CTA 連到真實可執行的 `/analyze` 查詢（非虛構資料）：
    href 解析出來的 coin/type/q 餵給 `_do_analyze` 要能正常產出報告。"""
    htmlout = web._render_home_page()
    assert "看範例報告" in htmlout

    href = web._example_analyze_href()
    assert href.startswith("/analyze?")
    qs = parse_qs(urlparse(html.unescape(href)).query)
    report, evidence, log = web._do_analyze(qs)
    assert report.coin == qs["coin"][0]
    assert evidence
    assert log.events

    # 未帶 real/live 參數 → 落在既有預設（離線示範，$0），對使用者誠實揭露
    assert "real" not in qs
    assert "live" not in qs


def test_render_home_page_marks_example_as_illustrative():
    """範例卡標「示意用途」而非佯裝即時資料（過渡期文案，#24 誠實原則）。"""
    htmlout = web._render_home_page()
    assert "示意用途" in htmlout


def test_render_home_page_never_calls_get_cache_backend(monkeypatch):
    """`get_cache_backend()`（給排程器／`/status` 用，無 timeout、容錯優先）
    不該被首頁呼叫——首頁總覽讀路徑必須走專用的短 timeout backend
    （`_home_overview_backend()`），兩者刻意分開建構，不可混用（見該函式
    docstring：混用會讓慢/掛掉的 backend 拖住首頁）。"""
    import trustforge.ingestion.cache as cache_mod

    def _boom_backend(*a, **kw):
        raise AssertionError("首頁不該呼叫 get_cache_backend()（應走 _home_overview_backend()）")

    monkeypatch.setattr(cache_mod, "get_cache_backend", _boom_backend)

    htmlout = web._render_home_page()
    assert "信任提煉" in htmlout  # 首頁其餘內容照常渲染


def test_render_home_page_never_touches_backend_directly(monkeypatch):
    """codex HIGH #2（PR #47）：`_render_home_page()`（首頁 request 路徑）
    絕對不能同步呼叫 `_home_overview_backend()`/`cache_get()`——所有 I/O
    只准發生在背景 thread 的 `_overview_bg_refresh_once()` 裡，request 路徑
    只讀 in-memory 的 `_overview_html`。用一個「活著但什麼都不做」的假
    thread 佔住 `_overview_bg_thread` 的位子（讓 `_ensure_overview_bg_thread_
    started()` 判定已有一條在跑而跳過真的啟動），確保這裡從頭到尾保證
    backend 呼叫次數是 0，而不是「機率上通常不會被呼叫到」（P3 ThreadPool
    事故 + Axis C v1/v2 兩輪 codex HIGH 的教訓延續）。"""
    calls = {"n": 0}

    class _FakeBackend:
        def get(self, key, *, consistent_read=False):
            calls["n"] += 1
            return None  # cache-miss

    monkeypatch.setattr(web, "_home_overview_backend", lambda: _FakeBackend())

    import threading as _threading

    placeholder_stop = _threading.Event()
    placeholder_thread = _threading.Thread(
        target=placeholder_stop.wait, name="tf-overview-bg", daemon=True
    )
    placeholder_thread.start()
    web._overview_bg_thread = placeholder_thread
    web._overview_bg_stop_event = placeholder_stop

    htmlout = web._render_home_page()
    assert calls["n"] == 0  # request 路徑零 I/O，一次都不該打到 backend
    assert "多幣信任總覽" not in htmlout  # in-memory 現貨是 None → 優雅缺席
    assert "信任提煉" in htmlout  # 首頁其餘內容照常渲染


def test_render_home_page_shows_overview_when_blob_present(monkeypatch):
    """寫入者（`fetch_scheduler.py --snapshot`）預先組好的總覽 blob 存在時，
    背景刷新讀到後寫入 `_overview_html`，首頁把它原樣嵌入頁面。

    codex HIGH #2 之後 `_render_home_page()` 不再同步讀 backend，改為手動
    呼叫一次 `_overview_bg_refresh_once()`（模擬背景 thread 跑過一輪）餵好
    in-memory 現貨，再驗證首頁讀路徑正確把它顯示出來。"""
    fake_html = '<div class="tf-overview-card">BTC 假卡片（測試樁）</div>'

    class _FakeBackend:
        def get(self, key, *, consistent_read=False):
            return {"docs": [{"html": fake_html}], "fetched_at": time.time()}

    monkeypatch.setattr(web, "_home_overview_backend", lambda: _FakeBackend())

    web._overview_bg_refresh_once()
    assert web._overview_html == fake_html

    htmlout = web._render_home_page()
    assert "多幣信任總覽" in htmlout
    assert fake_html in htmlout


def test_render_home_page_omits_overview_when_backend_raises(monkeypatch):
    """讀失敗（含短 timeout 逾時、backend 例外）→ 不顯總覽，首頁其餘內容
    照常渲染、不崩、不把例外往外拋（P3 鐵律：首頁永不因為 backend 故障而
    壞掉或被拖住）。

    codex HIGH #2 之後例外只會發生在背景 thread 的
    `_overview_bg_refresh_once()` 裡（被吞掉、`_overview_html` 設回
    `None`），先手動跑一輪真正驗證例外處理路徑，避免變成「反正
    `_render_home_page()` 本來就不會碰 backend，所以無論如何都會斷言成功」
    的假覆蓋。"""
    class _BoomBackend:
        def get(self, key, *, consistent_read=False):
            raise TimeoutError("simulated backend timeout")

    monkeypatch.setattr(web, "_home_overview_backend", lambda: _BoomBackend())

    web._overview_bg_refresh_once()
    assert web._overview_html is None  # 例外被吞掉、現貨清空，不是保留舊值

    htmlout = web._render_home_page()
    assert "多幣信任總覽" not in htmlout
    assert "信任提煉" in htmlout


def test_mobile_media_query_forces_table_horizontal_scroll():
    """375px 可讀性驗收（codex #MEDIUM）：base table 是 `width:100%`，若
    mobile media query 只給 container `overflow-x:auto` 而不限制 table
    `min-width`，table 會被壓縮換行、不會真的橫向捲動。這裡不依賴瀏覽器
    引擎（本專案零外部 runtime 依賴，未含 Playwright/Selenium），改用靜態
    CSS 斷言驗證：480px 斷點內 `.tf-section table` 必須有一個明顯大於
    375px 視窗寬度的 min-width（迫使 `width:100%` 溢出容器），且外層
    `.tf-section` 仍保留 `overflow-x:auto` 讓溢出內容變成可橫向捲動而非
    被壓縮換行。實機 375px viewport 的 scrollWidth/clientWidth 對稿由
    CEO Chrome 親驗（既有驗收流程）。
    """
    _, css = _do_get("/")
    media_start = css.index("@media (max-width:480px)")
    media_end = css.index("</style>", media_start)
    media_block = css[media_start:media_end]

    assert "overflow-x:auto" in css  # .tf-section 既有橫捲容器
    match = re.search(r"\.tf-section table\{min-width:(\d+)px\}", media_block)
    assert match is not None, "mobile 斷點內找不到 .tf-section table 的 min-width 規則"
    min_width_px = int(match.group(1))
    assert min_width_px >= 600, "min-width 太小不足以在 375px 視窗強制溢出橫捲"


def test_do_get_home_route_returns_200_non_empty_body():
    code, body = _do_get("/")
    assert code == 200
    assert body.strip()
    assert "信任提煉" in body
    assert 'class="tf-dashboard"' in body


def test_do_get_home_route_header_has_no_dev_artifacts():
    """老闆複驗後調整：首頁 header 允許保留一個小字/muted 版號（靠版號確認
    部署是否成功），但三檔模式徽號／`cost ledger $` 這些才是真正的雜訊，
    仍移到 `/status`（見下方 `_do_get("/status")` 對照測試），不是刪除。"""
    _, body = _do_get("/")
    assert 'class="tf-hdr-version"' in body
    assert web.VERSION in body
    assert 'class="tf-mode-badge' not in body
    assert "cost ledger" not in body
    assert "未設 BEDROCK_MODEL_ID" not in body
    assert "離線示範資料" in body  # 範例卡的透明標示屬於本次新增內容，非殘留舊 badge


def test_do_get_home_route_header_keeps_minimal_status_link():
    """header 不是整個拔光——仍保留一個極簡連到 `/status` 的連結（移位，非刪除）。"""
    _, body = _do_get("/")
    assert 'href="/status"' in body
    assert 'class="tf-logo"' in body


def test_do_get_home_route_never_calls_pipeline_run(monkeypatch):
    """credit-safe 鐵律：首頁必須是純靜態渲染，不觸發 pipeline/Bedrock。"""
    def _boom(*a, **kw):
        raise AssertionError("首頁 / 不該呼叫 pipeline.run()")

    monkeypatch.setattr("trustforge.pipeline.run", _boom)
    monkeypatch.setattr(web, "run", _boom)

    code, _ = _do_get("/")
    assert code == 200


def test_do_get_home_route_never_calls_real_source_fetch(monkeypatch):
    """credit-safe 鐵律：首頁不該觸發任何真 `Source.fetch()`（零連接器外呼）。"""
    from trustforge.ingestion.base import Source

    def _boom(self, query, coin=""):
        raise AssertionError(f"首頁 / 不該呼叫真 Source.fetch()（{self}）")

    monkeypatch.setattr(Source, "fetch", _boom, raising=False)
    code, _ = _do_get("/")
    assert code == 200


def test_render_page_default_header_unchanged_for_non_home_routes():
    """回歸鎖：`render_page()` 預設（`minimal_header=False`，`/costs`／
    `/status`／`/analyze` 結果頁沿用）行為完全不變——版號/三檔模式徽號/
    cost ledger 連結仍在，既有測試（`tests/test_web_dark_theme.py`、
    `tests/test_credit_safe_modes.py`）的斷言零回歸。"""
    htmlout = web.render_page("")
    assert 'class="tf-version"' in htmlout
    assert 'class="tf-mode-badge' in htmlout
    assert "cost ledger" in htmlout


def test_do_get_costs_route_header_unchanged():
    """`/costs` 不受本次首頁重寫影響，header 仍是完整版（非首頁範疇）。"""
    _, body = _do_get("/costs")
    assert 'class="tf-version"' in body
    assert "cost ledger" in body
