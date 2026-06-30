"""跨源訊號背離/共識洞察 — T1-T8 驗收測試。

CPO 規格：
  T1 背離（客觀 bullish×2 + 情緒 bearish×2，trust≥0.5）
  T2 共識（客觀 bullish + 情緒 bullish，trust≥0.5）
  T3 情緒 0 筆 → None
  T4 全 neutral → None
  T5 全 trust < 0.5 → None
  T6 兩類各 1 source 仍可下結論
  T7 asdict + json.dumps（None 與有值）不拋
  T8 既有 173 測試全綠（由 pytest 套件統一驗證）

render 測試（在本檔末）：
  - 背離框含 summary 文字 + 橙色
  - XSS summary 被 escape
"""
import dataclasses
import json

import pytest

from trustforge.agent.orchestrator import detect_cross_source_signal
from trustforge.ingestion.base import Document
from trustforge.schema import Report
from trustforge.trust.scoring import Claim, ScoredClaim


# ---------------------------------------------------------------------------
# 輔助工廠
# ---------------------------------------------------------------------------

def _doc(id_: str, kind: str, source: str) -> Document:
    return Document(id=id_, kind=kind, source=source, text="", ts=1.0)


def _sc(id_: str, kind: str, source: str, direction: str, trust: float) -> ScoredClaim:
    doc = _doc(id_, kind, source)
    claim = Claim(id=id_, text=f"claim-{id_}", doc=doc, direction=direction)
    return ScoredClaim(claim=claim, trust=trust)


# ---------------------------------------------------------------------------
# T1：背離（客觀 bullish × 2 + 情緒 bearish × 2，trust ≥ 0.5）
# ---------------------------------------------------------------------------

def test_t1_divergence():
    scored = [
        _sc("obj1", "onchain",    "glassnode",  "bullish", 0.80),
        _sc("obj2", "price",      "binance",    "bullish", 0.75),
        _sc("sen1", "news",       "coindesk",   "bearish", 0.65),
        _sc("sen2", "social",     "twitter",    "bearish", 0.55),
    ]
    result = detect_cross_source_signal(scored)
    assert result is not None, "背離訊號不應為 None"
    assert result["type"] == "divergence", f"期望 divergence，實得 {result['type']}"
    assert result["objective_direction"] == "bullish"
    assert result["sentiment_direction"] == "bearish"
    assert "背離" in result["summary"], "summary 應含「背離」"
    assert "建議交叉驗證" in result["summary"], "summary 應含不代客決策提醒"
    # 嚴禁決策字眼
    for forbidden in ("買", "賣", "進場", "出場", "該買", "該賣"):
        assert forbidden not in result["summary"], f"summary 嚴禁決策字眼「{forbidden}」"
    assert len(result["supporting_claim_ids"]) >= 2, "應有佐證 claim_ids"


# ---------------------------------------------------------------------------
# T2：共識（客觀 bullish + 情緒 bullish）
# ---------------------------------------------------------------------------

def test_t2_consensus():
    scored = [
        _sc("obj1", "regulatory", "sec",        "bullish", 0.85),
        _sc("sen1", "news",       "coindesk",   "bullish", 0.70),
    ]
    result = detect_cross_source_signal(scored)
    assert result is not None, "共識訊號不應為 None"
    assert result["type"] == "consensus", f"期望 consensus，實得 {result['type']}"
    assert result["objective_direction"] == "bullish"
    assert result["sentiment_direction"] == "bullish"
    assert "訊號一致" in result["summary"], "共識 summary 應含「訊號一致」"
    for forbidden in ("買", "賣", "進場", "出場"):
        assert forbidden not in result["summary"], f"summary 嚴禁決策字眼「{forbidden}」"


# ---------------------------------------------------------------------------
# T3：情緒類 0 筆 → None
# ---------------------------------------------------------------------------

def test_t3_no_sentiment_returns_none():
    scored = [
        _sc("obj1", "onchain", "glassnode", "bullish", 0.80),
        _sc("obj2", "price",   "binance",   "bullish", 0.75),
        # 無 news / social
    ]
    assert detect_cross_source_signal(scored) is None, "情緒類 0 筆應回 None"


# ---------------------------------------------------------------------------
# T4：全 neutral → None
# ---------------------------------------------------------------------------

def test_t4_all_neutral_returns_none():
    scored = [
        _sc("obj1", "onchain", "glassnode", "neutral", 0.80),
        _sc("sen1", "news",    "coindesk",  "neutral", 0.70),
    ]
    assert detect_cross_source_signal(scored) is None, "全 neutral 主導應回 None"


# ---------------------------------------------------------------------------
# T5：全 trust < 0.5 → None
# ---------------------------------------------------------------------------

def test_t5_all_low_trust_returns_none():
    scored = [
        _sc("obj1", "onchain", "glassnode", "bullish", 0.3),
        _sc("sen1", "news",    "coindesk",  "bearish", 0.4),
    ]
    assert detect_cross_source_signal(scored) is None, "全 trust < 0.5 應回 None"


# ---------------------------------------------------------------------------
# T6：兩類各只有 1 個 source，仍可下結論
# ---------------------------------------------------------------------------

def test_t6_one_source_each_can_conclude():
    scored = [
        _sc("obj1", "price",  "binance",  "bearish", 0.90),
        _sc("sen1", "social", "reddit",   "bullish", 0.60),
    ]
    result = detect_cross_source_signal(scored)
    # 兩類各 1 source，合計 2 → 滿足「source 合計 ≥ 2」
    assert result is not None, "兩類各 1 source 應可下結論"
    assert result["type"] == "divergence"


# ---------------------------------------------------------------------------
# T7：asdict + json.dumps（signal=None 與有值）不拋
# ---------------------------------------------------------------------------

def _make_report(cross_signal=None) -> Report:
    return Report(
        coin="BTC",
        question_type="multi_source",
        question="test",
        market_judgment="偏空",
        facts=[],
        inferences=[],
        key_basis=[],
        confidence=0.6,
        limits=[],
        could_flip=[],
        contrarian=[],
        generated_at="2026-07-01T00:00:00Z",
        cross_source_signal=cross_signal,
    )


def test_t7_asdict_json_none():
    """cross_source_signal=None 的 Report asdict+json.dumps 不拋。"""
    r = _make_report(cross_signal=None)
    d = dataclasses.asdict(r)
    s = json.dumps(d, ensure_ascii=False)
    parsed = json.loads(s)
    assert parsed["cross_source_signal"] is None


def test_t7_asdict_json_with_value():
    """cross_source_signal 有值的 Report asdict+json.dumps 不拋，且值被正確序列化。"""
    sig = {
        "type": "divergence",
        "objective_direction": "bullish",
        "sentiment_direction": "bearish",
        "summary": "客觀數據偏多、情緒類偏空，呈背離，建議交叉驗證、留意轉折。",
        "supporting_claim_ids": ["obj1", "sen1"],
    }
    r = _make_report(cross_signal=sig)
    d = dataclasses.asdict(r)
    s = json.dumps(d, ensure_ascii=False)
    parsed = json.loads(s)
    assert parsed["cross_source_signal"]["type"] == "divergence"
    assert "背離" in parsed["cross_source_signal"]["summary"]


# ---------------------------------------------------------------------------
# render 測試：背離框含 summary + 橙色；XSS summary 被 escape
# ---------------------------------------------------------------------------

def test_render_divergence_has_orange_and_summary():
    """背離訊號應渲染橙色框（#d9832a）並包含 summary 文字。"""
    from trustforge import web

    sig = {
        "type": "divergence",
        "objective_direction": "bullish",
        "sentiment_direction": "bearish",
        "summary": "客觀數據偏多、情緒類偏空，呈背離，建議交叉驗證、留意轉折。",
        "supporting_claim_ids": ["c1", "c2"],
    }
    report = _make_report(cross_signal=sig)
    ev: list = []
    htmlout = web._render_report(report, ev)
    assert "#d9832a" in htmlout, "背離框應含橙色 #d9832a"
    assert "背離" in htmlout, "背離框應含『背離』文字"
    assert "呈背離" in htmlout, "背離 summary 文字應出現在 HTML"


def test_render_xss_summary_escaped():
    """cross_source_signal summary 中的 XSS 字串應被 html.escape。"""
    from trustforge import web

    xss_summary = "<script>alert('xss')</script>偏多偏空背離"
    sig = {
        "type": "divergence",
        "objective_direction": "bullish",
        "sentiment_direction": "bearish",
        "summary": xss_summary,
        "supporting_claim_ids": [],
    }
    report = _make_report(cross_signal=sig)
    ev: list = []
    htmlout = web._render_report(report, ev)
    assert "<script>" not in htmlout, "XSS <script> 未被 escape"
    assert "&lt;script&gt;" in htmlout, "XSS 應被轉成 &lt;script&gt;"


def test_render_consensus_has_blue():
    """共識訊號應渲染藍色框（#1f6feb）。"""
    from trustforge import web

    sig = {
        "type": "consensus",
        "objective_direction": "bullish",
        "sentiment_direction": "bullish",
        "summary": "客觀與情緒同向偏多，訊號一致。",
        "supporting_claim_ids": ["c1"],
    }
    report = _make_report(cross_signal=sig)
    htmlout = web._render_report(report, [])
    assert "#1f6feb" in htmlout, "共識框應含藍色 #1f6feb"
    assert "共識" in htmlout, "共識框應含『共識』文字"


def test_render_no_signal_no_cross_section():
    """cross_source_signal=None 時，HTML 中不應出現跨源訊號區塊。"""
    from trustforge import web

    report = _make_report(cross_signal=None)
    htmlout = web._render_report(report, [])
    assert "跨源訊號" not in htmlout, "signal=None 時不應出現跨源訊號區塊"
