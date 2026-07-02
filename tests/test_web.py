"""web 服務 smoke 測試（不開真 socket，直接測處理邏輯）。"""
import html
import json
import re
import threading
import time
from io import BytesIO
from email.message import Message
from urllib.parse import parse_qs, urlparse

import pytest

from trustforge import web
from trustforge.schema import COIN_POOL


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


# ---------------------------------------------------------------------------
# 世界第一重寫 Phase 3：首頁「多幣總覽」（讀 cache 信任分快照，credit-safe）
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_home_multicoin_cache():
    """`_home_multicoin_cache` 是 module 級 TTL 快取共用狀態，比照
    `test_status_page.py::_reset_status_module_state` 慣例，每個測試前後
    重置，避免前一個測試留下的 30 秒 TTL 快取內容外溢到後一個測試。"""
    web._home_multicoin_cache["expires_at"] = 0.0
    web._home_multicoin_cache["html"] = ""
    web._home_multicoin_refreshing = False
    yield
    web._home_multicoin_cache["expires_at"] = 0.0
    web._home_multicoin_cache["html"] = ""
    web._home_multicoin_refreshing = False


@pytest.fixture
def json_cache_backend(tmp_path, monkeypatch):
    """比照 `test_status_page.py` 慣例：`CACHE_BACKEND=json` + 指向隔離
    tmp_path，確保測試不會意外打真 AWS（開發機可能設有 SSO/AWS profile）。"""
    monkeypatch.setenv("CACHE_BACKEND", "json")
    monkeypatch.setenv("TRUSTFORGE_CACHE_DIR", str(tmp_path))


def test_render_home_page_shows_multicoin_overview_all_coins_empty(json_cache_backend):
    """尚無任何快取寫入（#20「結果持久化」尚未落地的現況）→ 5 幣全部無資料
    時，CEO 決策：整個「多幣總覽」區塊不渲染（避免首頁出現 5 張「尚無資料」
    醜卡），回到 hero+怎麼運作+範例 的乾淨版面。不報錯、不即時算、不打
    連接器。"""
    htmlout = web._render_home_page()
    assert "多幣總覽" not in htmlout
    assert "尚無資料" not in htmlout


def test_get_coin_trust_snapshot_returns_none_when_cache_miss(json_cache_backend):
    assert web._get_coin_trust_snapshot("BTC") is None


def test_get_coin_trust_snapshot_reads_valid_snapshot(json_cache_backend):
    from trustforge.ingestion.cache import JsonCacheBackend, cache_key

    backend = JsonCacheBackend()
    key = cache_key(web._ANALYSIS_SNAPSHOT_SOURCE, "BTC")
    backend.set(key, [{"trust": 0.82, "direction": "偏多"}], fetched_at=1000.0)

    snap = web._get_coin_trust_snapshot("BTC")
    assert snap is not None
    assert snap["trust"] == pytest.approx(0.82)
    assert snap["direction"] == "偏多"


def test_get_coin_trust_snapshot_gracefully_handles_malformed_data(json_cache_backend):
    """快取內容格式不符（缺 trust／非法數字／空 docs）→ 優雅回 None，不拋
    例外，首頁不能因為快取格式漂移就掛掉。"""
    from trustforge.ingestion.cache import JsonCacheBackend, cache_key

    backend = JsonCacheBackend()
    backend.set(
        cache_key(web._ANALYSIS_SNAPSHOT_SOURCE, "ETH"),
        [{"direction": "偏多"}], fetched_at=1000.0,  # 缺 trust
    )
    assert web._get_coin_trust_snapshot("ETH") is None

    backend.set(
        cache_key(web._ANALYSIS_SNAPSHOT_SOURCE, "SOL"),
        [{"trust": "not-a-number"}], fetched_at=1000.0,
    )
    assert web._get_coin_trust_snapshot("SOL") is None

    backend.set(cache_key(web._ANALYSIS_SNAPSHOT_SOURCE, "BNB"), [], fetched_at=1000.0)
    assert web._get_coin_trust_snapshot("BNB") is None


def test_render_home_page_shows_snapshot_when_cache_has_data(json_cache_backend):
    """有快取資料的幣種顯示信任分＋方向標籤；其餘沒有資料的幣種仍優雅顯示
    「尚無資料」——同一列可以混合兩種狀態，互不影響。"""
    from trustforge.ingestion.cache import JsonCacheBackend, cache_key

    backend = JsonCacheBackend()
    backend.set(
        cache_key(web._ANALYSIS_SNAPSHOT_SOURCE, "BTC"),
        [{"trust": 0.91, "direction": "偏多"}], fetched_at=1000.0,
    )
    backend.set(
        cache_key(web._ANALYSIS_SNAPSHOT_SOURCE, "ETH"),
        [{"trust": 0.20, "direction": "偏空"}], fetched_at=1000.0,
    )

    htmlout = web._render_home_page()
    assert "0.91" in htmlout
    assert "0.20" in htmlout
    assert "偏多" in htmlout
    assert "偏空" in htmlout
    # 其餘 3 幣（SOL/BNB/XRP）沒有快取資料，仍優雅顯示「尚無資料」
    assert htmlout.count("尚無資料") == 3


def test_home_multicoin_card_link_targets_real_analyze_default():
    """每張卡的 CTA 連到 `/analyze` 真資料·$0 預設檔位（不帶 sample=1），
    跟首頁範例卡刻意不同（範例卡才需要標「示意」，見
    `test_render_home_page_marks_example_as_illustrative`）。"""
    href = web._multicoin_analyze_href("BTC")
    assert href.startswith("/analyze?")
    qs = parse_qs(urlparse(html.unescape(href)).query)
    assert qs["coin"][0] == "BTC"
    assert "sample" not in qs


def test_home_page_multicoin_never_calls_pipeline_or_connectors(json_cache_backend, monkeypatch):
    """credit-safe 鐵律：多幣總覽純讀 cache，絕不觸發 pipeline.run / 真
    Source.fetch()（比照 `test_status_page.py::test_status_route_never_calls_*`
    的樁寫法：一旦被呼叫就斷言失敗）。這裡刻意先寫入 1 幣快照，確保區塊會
    渲染（全空會被 CEO 決策的「整區隱藏」邏輯吃掉，测不到零外呼路徑）。"""
    from trustforge.ingestion.cache import JsonCacheBackend, cache_key

    JsonCacheBackend().set(
        cache_key(web._ANALYSIS_SNAPSHOT_SOURCE, "BTC"),
        [{"trust": 0.5, "direction": "中性"}], fetched_at=1000.0,
    )

    def _boom(*a, **kw):
        raise AssertionError("首頁多幣總覽不該呼叫 pipeline.run()")

    monkeypatch.setattr("trustforge.pipeline.run", _boom)
    monkeypatch.setattr(web, "run", _boom)

    from trustforge.ingestion.base import Source

    def _boom_fetch(self, query, coin=""):
        raise AssertionError(f"首頁多幣總覽不該呼叫真 Source.fetch()（{self})")

    monkeypatch.setattr(Source, "fetch", _boom_fetch, raising=False)

    htmlout = web._render_home_page()
    assert "多幣總覽" in htmlout


def test_home_multicoin_overview_ttl_cached_across_calls(json_cache_backend, monkeypatch):
    """30 秒 TTL 內重複呼叫 `_render_home_multicoin_overview()` 不重打 cache
    backend（比照 `_render_status_page_cached` single-flight 設計）——首頁
    是全站流量最高頁面，避免每個請求都重新逐幣讀 cache。"""
    calls = {"n": 0}
    original = web._get_coin_trust_snapshot

    def _counting(coin, *, backend=None):
        calls["n"] += 1
        return original(coin, backend=backend)

    monkeypatch.setattr(web, "_get_coin_trust_snapshot", _counting)

    web._render_home_multicoin_overview()
    first_call_count = calls["n"]
    assert first_call_count == len(COIN_POOL)

    web._render_home_multicoin_overview()
    assert calls["n"] == first_call_count  # TTL 內第二次呼叫應完全吃快取，不再重讀


class _HangingBackend:
    """模擬 DynamoDB client 沒有明確 timeout 時的「讀阻塞」情境（codex
    HIGH）：`.get()` 完全不會自己逾時，模擬 AWS/憑證/DNS/表降級時 socket
    卡住的最差狀況。刻意遠大於 `_HOME_MULTICOIN_READ_BUDGET_SECONDS`
    （1.0s），確保測試斷言的是「硬預算生效」而不是恰好backend自己夠快。
    """

    def get(self, key, *, consistent_read=False):
        # 這個 sleep 遠大於硬預算，且刻意「正常回傳」而非拋例外——測試斷
        # 言的是呼叫端（ThreadPoolExecutor + `future.result(timeout=...)`）
        # 沒有傻等這裡睡完，不是靠 backend 自己失敗才提早結束。
        time.sleep(5.0)
        return None


class _RaisingBackend:
    """模擬 backend 立即拋錯（如 AccessDenied／ValidationException）。"""

    def get(self, key, *, consistent_read=False):
        raise RuntimeError("模擬 DynamoDB 立即失敗")


def test_home_page_responds_within_budget_when_backend_hangs(json_cache_backend, monkeypatch):
    """codex HIGH：多幣總覽的 cache 讀不能拖垮首頁核心。backend 完全不回應
    （模擬沒有 timeout 上限的 DynamoDB client 卡住）時，首頁仍須在硬預算
    （`_HOME_MULTICOIN_READ_BUDGET_SECONDS`）內回應、渲染無總覽區塊、不是
    500，且不能傻等 backend 那個 5 秒的 `time.sleep`。"""
    monkeypatch.setattr(web, "_home_overview_cache_backend", lambda: _HangingBackend())

    start = time.monotonic()
    htmlout = web._render_home_page()
    elapsed = time.monotonic() - start

    assert elapsed < web._HOME_MULTICOIN_READ_BUDGET_SECONDS + 1.0, (
        f"首頁被 hang 住的 backend 拖慢，耗時 {elapsed:.2f}s"
    )
    assert "多幣總覽" not in htmlout
    assert "尚無資料" not in htmlout


def test_do_get_home_route_responds_within_budget_when_backend_hangs(
    json_cache_backend, monkeypatch
):
    """比照上一則，但走真正的 `_do_get('/')` HTTP handler 路徑，確認整條
    請求鏈（含 header/hero/其餘首頁內容）不因總覽 hang 住而回 500 或卡死。
    """
    monkeypatch.setattr(web, "_home_overview_cache_backend", lambda: _HangingBackend())

    start = time.monotonic()
    code, body = _do_get("/")
    elapsed = time.monotonic() - start

    assert code == 200
    assert elapsed < web._HOME_MULTICOIN_READ_BUDGET_SECONDS + 1.0
    assert "信任提煉" in body  # 首頁其餘內容照常渲染，不是空白/錯誤頁


def test_home_page_gracefully_hides_overview_when_backend_raises(
    json_cache_backend, monkeypatch
):
    """backend 立即拋錯（如 AccessDenied）時，`cache_get()` 會 fallback 讀
    本地 JsonCacheBackend（既有行為），這裡本地也沒資料 → 依然優雅整區
    隱藏，不報 500、不洩漏例外字串到首頁。"""
    monkeypatch.setattr(web, "_home_overview_cache_backend", lambda: _RaisingBackend())

    htmlout = web._render_home_page()
    assert "多幣總覽" not in htmlout
    assert "RuntimeError" not in htmlout
    assert "Traceback" not in htmlout


def test_concurrent_home_page_requests_not_all_blocked_by_hanging_backend(
    json_cache_backend, monkeypatch
):
    """codex HIGH 核心情境：TTL miss + 慢 backend 時，多個併發首頁請求
    不能全部卡在同一顆鎖上排隊（會變成「首頁全站不可用」）。這裡開
    8 條執行緒同時打 `_render_home_page()`，斷言**每一條**都在硬預算附近
    內完成，而不是像鎖序列化那樣越晚開始的執行緒等越久（例如第 8 條要
    等前 7 條各自 hang 完才輪到）。
    """
    monkeypatch.setattr(web, "_home_overview_cache_backend", lambda: _HangingBackend())

    n_threads = 8
    elapsed_times: list[float] = []
    lock = threading.Lock()

    def _worker():
        start = time.monotonic()
        web._render_home_page()
        elapsed = time.monotonic() - start
        with lock:
            elapsed_times.append(elapsed)

    threads = [threading.Thread(target=_worker) for _ in range(n_threads)]
    overall_start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
    overall_elapsed = time.monotonic() - overall_start

    assert len(elapsed_times) == n_threads, "有執行緒沒在 10 秒內完成，代表被鎖序列化卡死"
    # 若序列化排隊（每條都等前面的 hang 完），8 條會接近 8 * 5s = 40s+；
    # 這裡斷言遠低於此，證明沒有互相卡隊。
    assert overall_elapsed < 8.0, f"併發首頁請求疑似互相卡隊，總耗時 {overall_elapsed:.2f}s"


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
