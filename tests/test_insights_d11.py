"""D1.1 聰明錢背離洞察 — 單元 + 誠實閘驗收測試。

驗證：
  - 價格跌 + 成交量升 → covered / bullish / 強度 > 0 / 兩個貢獻來源
  - 價格跌但缺成交量 → coverage="insufficient"、summary 含「無法判定」、強度 0
  - 價格未跌（上漲/盤整）→ 不成立、回 None（不硬湊）
  - 缺價格報酬事實 → 回 None
  - detect_insights 聚合回傳 list、序列化（asdict + json）不拋
  - 整輪 pipeline（build_report）報告攜 insights 欄位
"""
from trustforge.agent.orchestrator import build_report
from trustforge.ingestion.base import Document
from trustforge.schema import QuestionType, Report
from trustforge.trust.insights import (
    COVERAGE_INSUFFICIENT,
    detect_insights,
    detect_smart_money_divergence,
)
from trustforge.trust.scoring import Claim, ScoredClaim, TrustedBrief, aggregate, extract_claims, score


def _price_sc(doc_id: str, text: str, direction: str, trust: float = 0.9) -> ScoredClaim:
    doc = Document(id=doc_id, kind="price", source="ohlcv-csv", text=text, ts=1.0,
                   meta={"trading_pair": "BTC/USDT"})
    claim = Claim(id=f"{doc_id}#0", text=text, doc=doc, direction=direction)
    return ScoredClaim(claim=claim, trust=trust)


def _ret(ret_pct: float) -> ScoredClaim:
    return _price_sc("price-BTC-ret",
                     f"BTC 近 14 日收盤從 60000 變動至 58000，報酬 {ret_pct:+.1f}%，呈下跌。",
                     "bearish")


def _vol(vol_pct: float) -> ScoredClaim:
    return _price_sc("price-BTC-volume",
                     f"BTC 近期成交量相對區間初期變化 {vol_pct:+.0f}%。",
                     "bullish" if vol_pct > 0 else "neutral")


def test_d11_covered_bullish_divergence():
    ins = detect_smart_money_divergence([_ret(-3.3), _vol(25)], "BTC")
    assert ins is not None
    assert ins.insight_type == "smart_money_divergence"
    assert ins.coverage != COVERAGE_INSUFFICIENT
    assert ins.direction == "bullish"
    assert ins.strength > 0.0
    assert len(ins.contributions) == 2, "應有價格跌 + 成交量升兩個貢獻來源"
    dirs = {c.direction for c in ins.contributions}
    assert "bearish" in dirs and "bullish" in dirs, "兩貢獻方向應相反（背離本質）"
    assert "無法判定" not in ins.summary
    assert "代理" in (ins.meta.get("proxy_note") or ""), "應誠實說明成交量為鏈上淨流入代理"


def test_d11_price_down_no_volume_insufficient():
    ins = detect_smart_money_divergence([_ret(-3.3)], "BTC")
    assert ins is not None
    assert ins.coverage == COVERAGE_INSUFFICIENT
    assert ins.strength == 0.0
    assert "無法判定" in ins.summary, "樣本不足必須標註無法判定"


def test_d11_volume_down_not_accumulation_insufficient():
    ins = detect_smart_money_divergence([_ret(-3.3), _vol(-10)], "BTC")
    assert ins is not None
    assert ins.coverage == COVERAGE_INSUFFICIENT
    assert ins.strength == 0.0


def test_d11_price_not_down_returns_none():
    # 價格上漲：聰明錢背離（吸籌於下跌中）不成立，不硬湊洞察。
    assert detect_smart_money_divergence([_ret(5.0), _vol(25)], "BTC") is None
    # 價格盤整
    assert detect_smart_money_divergence([_ret(0.0), _vol(25)], "BTC") is None


def test_d11_missing_return_fact_returns_none():
    # 沒有價格報酬事實：缺資料，誠實不出洞察。
    assert detect_smart_money_divergence([_vol(25)], "BTC") is None


def test_d11_aggregation_returns_list_and_serializes():
    insights = detect_insights(
        TrustedBrief(query="q", supporting=[], contrarian=[], confidence=0.0),
        [_ret(-3.3), _vol(25)],
        "BTC", QuestionType.MULTI_SOURCE,
    )
    assert isinstance(insights, list) and len(insights) >= 1
    types = {ins.insight_type for ins in insights}
    assert "smart_money_divergence" in types, "應含聰明錢背離洞察"
    import dataclasses, json
    for ins in insights:
        assert json.dumps(dataclasses.asdict(ins), ensure_ascii=False), "asdict + json 不應拋"


def test_d11_build_report_carries_insights():
    docs = [
        Document(id="price-BTC-ret", kind="price", source="ohlcv-csv",
                 text="BTC 近 14 日收盤從 60000 變動至 58000，報酬 -3.3%，呈下跌。",
                 ts=1.0, meta={"trading_pair": "BTC/USDT"}),
        Document(id="price-BTC-volume", kind="price", source="ohlcv-csv",
                 text="BTC 近期成交量相對區間初期變化 +25%。",
                 ts=1.0, meta={"trading_pair": "BTC/USDT"}),
    ]
    claims = extract_claims(docs)
    scored = score(claims, now=2.0, stance_fn=lambda a, b: "neutral")
    brief = aggregate(scored, query="BTC 分析", coin="BTC")
    report, _ = build_report(
        query="BTC 分析", coin="BTC", qtype=QuestionType.MULTI_SOURCE, brief=brief,
        scored=scored,
    )
    assert isinstance(report, Report)
    assert isinstance(report.insights, list) and len(report.insights) >= 1
    assert report.insights[0].insight_type == "smart_money_divergence"
