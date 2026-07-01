"""Tier2（真實分歧樣本）驗收測試。

CEO 派工規格：
  - ETH multi_source 分析 → cross_source_signal.type == "divergence"（或含「背離」）
    且含 stance_pairs 兩筆方向相反。
  - `stance_fn=None`（未提供）時 detect_cross_source_signal 行為逐字不變
    （回歸鎖，見 test_cross_source_signal.py 既有 T1-T8）。
  - ETH 既有確定性測試（evidence/facts 數量）同步鎖定，避免未來改動悄悄回歸。

真實現象：ETH 現貨 ETF 資金流向因結算時區/資料商方法論不同，不同來源（coindesk /
decrypt）對同一天可能報出方向相反的淨流入/流出——同議題、來源獨立、內容自帶分歧
成因說明，非造假對抗樣本（守 #24 紅線）。
CEO 追加派工（demo 可靠性 #32）：
  - 跨源背離不得依查詢字串措辭而定——ETH 任何合理問法（含中文「以太坊」、
    無空格「ETH現況」）皆須穩定觸發 divergence + 2 筆 stance_pairs。
  - 其他幣（BTC/SOL/BNB/XRP）不得因此修正而誤觸假背離。
"""
from __future__ import annotations

import pytest

from trustforge.agent.orchestrator import (
    _STANCE_PAIR_MIN_TRUST,
    _detect_stance_pairs,
    detect_cross_source_signal,
)
from trustforge.ingestion.base import Document
from trustforge.pipeline import run
from trustforge.schema import QuestionType
from trustforge.trust.scoring import Claim, ScoredClaim


# ---------------------------------------------------------------------------
# 輔助工廠（同 test_cross_source_signal.py 慣例）
# ---------------------------------------------------------------------------

def _doc(id_: str, kind: str, source: str) -> Document:
    return Document(id=id_, kind=kind, source=source, text="", ts=1.0)


def _sc(id_: str, kind: str, source: str, direction: str, trust: float, text: str = "") -> ScoredClaim:
    doc = _doc(id_, kind, source)
    claim = Claim(id=id_, text=text or f"claim-{id_}", doc=doc, direction=direction)
    return ScoredClaim(claim=claim, trust=trust)


def _contradiction_stance_fn(a: str, b: str) -> str:
    """測試用假 stance_fn：固定回 contradiction（模擬快取命中矛盾）。"""
    return "contradiction"


def _neutral_stance_fn(a: str, b: str) -> str:
    return "neutral"


# ---------------------------------------------------------------------------
# 單元測試：_detect_stance_pairs / detect_cross_source_signal 的 stance_pairs 分支
# ---------------------------------------------------------------------------

def test_stance_fn_none_keeps_existing_behavior_unchanged():
    """stance_fn 未提供（預設 None）→ 逐字沿用既有行為，not 新增 stance_pairs。"""
    scored = [
        _sc("obj1", "onchain", "glassnode", "bullish", 0.80),
        _sc("obj2", "price", "binance", "bullish", 0.75),
        _sc("sen1", "news", "coindesk", "bearish", 0.65),
        _sc("sen2", "social", "twitter-a", "bearish", 0.55),
    ]
    result = detect_cross_source_signal(scored)
    assert result is not None
    assert "stance_pairs" not in result, "stance_fn=None 時不應出現 stance_pairs key"


def test_detect_stance_pairs_empty_without_stance_fn():
    """_detect_stance_pairs 在 stance_fn=None 時直接回空 list。"""
    scored = [
        _sc("a", "news", "coindesk", "bullish", 0.44, "同議題方向相反 A"),
        _sc("b", "news", "decrypt", "bearish", 0.44, "同議題方向相反 B"),
    ]
    assert _detect_stance_pairs(scored, None) == []


def test_detect_stance_pairs_finds_contradiction_pair():
    """不同來源 + 方向相反 + stance_fn 判定 contradiction → 配對成立。"""
    scored = [
        _sc("a", "news", "coindesk", "bullish", 0.44, "ETF 淨流入方向 A"),
        _sc("b", "news", "decrypt", "bearish", 0.44, "ETF 淨流出方向 B"),
    ]
    pairs = _detect_stance_pairs(scored, _contradiction_stance_fn)
    assert len(pairs) == 2
    sources = {p["source"] for p in pairs}
    assert sources == {"coindesk", "decrypt"}
    stances = {p["stance"] for p in pairs}
    assert stances == {"bullish", "bearish"}
    for p in pairs:
        assert set(p.keys()) == {"source", "stance", "claim_id", "text"}


def test_detect_stance_pairs_below_min_trust_excluded():
    """trust 低於 _STANCE_PAIR_MIN_TRUST 的主張不進入掃描池。"""
    scored = [
        _sc("a", "news", "coindesk", "bullish", _STANCE_PAIR_MIN_TRUST - 0.01, "A"),
        _sc("b", "news", "decrypt", "bearish", 0.9, "B"),
    ]
    assert _detect_stance_pairs(scored, _contradiction_stance_fn) == []


def test_detect_stance_pairs_same_source_excluded():
    """同來源不算跨源矛盾，即使方向相反 + stance_fn 判 contradiction。"""
    scored = [
        _sc("a", "news", "coindesk", "bullish", 0.6, "A"),
        _sc("b", "news", "coindesk", "bearish", 0.6, "B"),
    ]
    assert _detect_stance_pairs(scored, _contradiction_stance_fn) == []


def test_detect_stance_pairs_same_direction_excluded():
    """方向相同不算矛盾（即使 stance_fn 誤判也不觸發，方向閘先擋）。"""
    scored = [
        _sc("a", "news", "coindesk", "bullish", 0.6, "A"),
        _sc("b", "news", "decrypt", "bullish", 0.6, "B"),
    ]
    assert _detect_stance_pairs(scored, _contradiction_stance_fn) == []


def test_detect_stance_pairs_neutral_stance_fn_excluded():
    """stance_fn 回 neutral（非 contradiction）→ 不成立配對。"""
    scored = [
        _sc("a", "news", "coindesk", "bullish", 0.6, "A"),
        _sc("b", "news", "decrypt", "bearish", 0.6, "B"),
    ]
    assert _detect_stance_pairs(scored, _neutral_stance_fn) == []


def test_cross_source_signal_stance_pairs_fallback_when_aggregate_inconclusive():
    """聚合層級（客觀/情緒）判不出結論（此例情緒類 0 筆）時，
    仍可靠 stance_pairs 備援產出 divergence 訊號（覆蓋 T3 的 None 分支）。
    """
    scored = [
        _sc("obj1", "onchain", "glassnode", "bullish", 0.80, "客觀 A"),
        _sc("sen1", "news", "coindesk", "bullish", 0.44, "ETF 淨流入 A"),
        _sc("sen2", "news", "decrypt", "bearish", 0.44, "ETF 淨流出 B"),
    ]
    # sen1/sen2 trust < 0.5 → 聚合層級「情緒類 0 筆（trust>=0.5）」→ 原行為回 None
    baseline = detect_cross_source_signal(scored)
    assert baseline is None, "回歸鎖：不給 stance_fn 時，此 fixture 應仍回 None"

    result = detect_cross_source_signal(scored, stance_fn=_contradiction_stance_fn)
    assert result is not None
    assert result["type"] == "divergence"
    assert "背離" in result["summary"]
    assert len(result["stance_pairs"]) == 2
    assert {p["stance"] for p in result["stance_pairs"]} == {"bullish", "bearish"}
    forbidden = ("買", "賣", "進場", "出場")
    for word in forbidden:
        assert word not in result["summary"], f"summary 嚴禁決策字眼「{word}」"


def test_cross_source_signal_merges_stance_pairs_into_aggregate_result():
    """聚合層級已能判定 divergence 時，stance_pairs（若有）以選填 key 附加，
    不覆蓋既有 objective_direction/sentiment_direction/summary 語意。
    """
    scored = [
        _sc("obj1", "onchain", "glassnode", "bullish", 0.80, "客觀 A"),
        _sc("obj2", "price", "binance", "bullish", 0.75, "客觀 B"),
        _sc("sen1", "news", "coindesk", "bearish", 0.70, "ETF 淨流出 A"),
        _sc("sen2", "social", "twitter-a", "bullish", 0.50, "ETF 淨流入 B"),
    ]
    result = detect_cross_source_signal(scored, stance_fn=_contradiction_stance_fn)
    assert result["type"] == "divergence"
    assert result["objective_direction"] == "bullish"
    assert result["sentiment_direction"] == "bearish"
    assert "stance_pairs" in result
    assert len(result["stance_pairs"]) == 2


# ---------------------------------------------------------------------------
# 整合測試：ETH multi_source 真實分歧樣本（demo/sample_data/news.json +
# stance_cache.json），全離線，不打真 AWS。
# ---------------------------------------------------------------------------

def test_eth_multi_source_shows_real_divergence_with_stance_pairs():
    """ETH multi_source 分析：ETF 資金流向真實分歧樣本應在 cross_source_signal
    浮現，含 stance_pairs 兩筆方向相反的主張。
    """
    report, evidence, log = run("ETH", "ETH 現況", QuestionType.MULTI_SOURCE, offline=True)

    sig = report.cross_source_signal
    assert sig is not None, "ETH 應偵測到跨源訊號（真實 ETF 分歧樣本）"
    assert sig["type"] == "divergence" or "背離" in sig["summary"]

    assert "stance_pairs" in sig
    pairs = sig["stance_pairs"]
    assert len(pairs) == 2
    stances = {p["stance"] for p in pairs}
    assert stances == {"bullish", "bearish"}, f"應為方向相反兩筆，實得 {stances}"
    sources = {p["source"] for p in pairs}
    assert sources == {"coindesk", "decrypt"}
    claim_ids = {p["claim_id"] for p in pairs}
    assert claim_ids == {"news-eth-etf-inflow#0", "news-eth-etf-outflow#0"}

    # 守 HOYA 不代客決策：summary 不得含決策字眼
    for word in ("買", "賣", "進場", "出場"):
        assert word not in sig["summary"]


def test_eth_multi_source_evidence_facts_count_pinned():
    """ETH 既有確定性測試同步更新：納入 Tier2 兩筆新樣本後的 evidence/facts
    數量鎖定（回歸鎖，避免未來改動悄悄改變證據/事實輸出規模）。

    數量由 6/12 調整為 7/13（demo 可靠性 #32 追加的 coin-filter 主導修正）：
    `aggregate()` 改為讓「明確提及該幣」的主張不再受 query 文字措辭影響去留，
    使一筆先前因「ETH 現況」查詢字面篩選而被排除的 ETH 客觀事實
    （objective kind）穩定納入 `brief.supporting`，讓 facts/evidence 各多 1 筆
    ——這是修復查詢脆弱性後的預期結果，非回歸。
    """
    report, evidence, log = run("ETH", "ETH 現況", QuestionType.MULTI_SOURCE, offline=True)

    assert len(report.facts) == 7
    assert len(evidence) == 13

    sources = {e.source for e in evidence}
    assert {"coindesk", "decrypt"} <= sources, "兩則 ETF 分歧樣本來源應都出現在證據清單"

    refs = [e.content_reference for e in evidence]
    assert any("淨流入" in r for r in refs), "應含 ETF 淨流入證據"
    assert any("淨流出" in r for r in refs), "應含 ETF 淨流出證據"


# ---------------------------------------------------------------------------
# demo 可靠性 #32 追加：跨源背離不得依查詢字串措辭而定
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "query",
    [
        "ETH 現況",           # 原本已可（有空格、英文）
        "分析 ETH 市場",
        "ETH staking",
        "ETH ETF 資金流",
        "評估 ETH 是否值得買進",
        "以太坊分析",           # CEO 回報：中文幣名、無 ETH token → 原本 None
        "ETH現況",             # CEO 回報：無空格 → 原本 None
        "分析以太坊",
        "分析ETH市場",         # 無空格中英混排
        "以太坊",              # 純中文幣名、無任何動詞
        "ETH",                # 純幣代碼
    ],
)
def test_eth_divergence_stable_across_query_wording(query: str):
    """coin-filter 主導修正：ETH 的跨源背離（含 stance_pairs）不得因查詢字串
    措辭（中/英文、有無空格）而忽有忽無——只要 coin=ETH，結果必須一致。
    """
    report, evidence, log = run("ETH", query, QuestionType.MULTI_SOURCE, offline=True)
    sig = report.cross_source_signal
    assert sig is not None, f"query={query!r} 應觸發跨源訊號，實得 None"
    assert sig["type"] == "divergence" or "背離" in sig["summary"]
    assert "stance_pairs" in sig
    pairs = sig["stance_pairs"]
    assert len(pairs) == 2, f"query={query!r} 應為 2 筆 stance_pairs，實得 {len(pairs)}"
    stances = {p["stance"] for p in pairs}
    assert stances == {"bullish", "bearish"}
    claim_ids = {p["claim_id"] for p in pairs}
    assert claim_ids == {"news-eth-etf-inflow#0", "news-eth-etf-outflow#0"}


@pytest.mark.parametrize(
    ("coin", "query"),
    [
        ("BTC", "BTC 現況"),
        ("BTC", "比特幣分析"),
        ("BTC", "BTC現況"),
        ("BTC", "分析BTC市場"),
        ("BTC", "BTC ETF 資金流"),
        ("SOL", "SOL 現況"),
        ("SOL", "索拉納分析"),
        ("SOL", "SOL現況"),
        ("SOL", "分析SOL市場"),
        ("BNB", "BNB 現況"),
        ("BNB", "幣安幣分析"),
        ("BNB", "BNB現況"),
        ("XRP", "XRP 現況"),
        ("XRP", "瑞波幣分析"),
        ("XRP", "XRP現況"),
    ],
)
def test_other_coins_no_false_divergence_after_coin_filter_fix(coin: str, query: str):
    """coin-filter 主導修正不得讓 BTC/SOL/BNB/XRP 誤觸假背離——這些幣目前沒有
    真實分歧樣本／stance_cache 矛盾配對，修正後仍應維持 cross_source_signal
    為 None（或至少不含 stance_pairs），不可因排序調整而意外浮現假訊號。
    """
    report, evidence, log = run(coin, query, QuestionType.MULTI_SOURCE, offline=True)
    sig = report.cross_source_signal
    if sig is not None:
        assert "stance_pairs" not in sig or not sig["stance_pairs"], (
            f"{coin} query={query!r} 不應出現 stance_pairs 假背離，實得 {sig}"
        )
