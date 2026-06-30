"""web 服務 smoke 測試（不開真 socket，直接測處理邏輯）。"""
import json

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
