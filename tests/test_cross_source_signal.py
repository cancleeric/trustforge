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


# ---------------------------------------------------------------------------
# issue #21（CISO-LOW）：sentiment_source_count 透明化欄位
# 純展示用，驗證只加欄位、不動既有分數/方向計算（T1/T2/T6 既有斷言不變）。
# ---------------------------------------------------------------------------

def test_sentiment_source_count_single_source():
    """情緒類只有 1 個獨立來源（單一 social 源）時，count 應為 1，供 UI
    顯示「單一來源主導」徽章。"""
    scored = [
        _sc("obj1", "price",  "binance",  "bearish", 0.90),
        _sc("sen1", "social", "reddit",   "bullish", 0.60),
    ]
    result = detect_cross_source_signal(scored)
    assert result is not None
    assert result["sentiment_source_count"] == 1
    # 加欄位不動既有計算：type/方向不受影響
    assert result["type"] == "divergence"
    assert result["sentiment_direction"] == "bullish"


def test_sentiment_source_count_multiple_sources():
    """情緒類有 2 個以上獨立來源（news + social）時，count >= 2，不應顯示徽章。"""
    scored = [
        _sc("obj1", "onchain",    "glassnode",  "bullish", 0.80),
        _sc("obj2", "price",      "binance",    "bullish", 0.75),
        _sc("sen1", "news",       "coindesk",   "bearish", 0.65),
        _sc("sen2", "social",     "twitter",    "bearish", 0.55),
    ]
    result = detect_cross_source_signal(scored)
    assert result is not None
    assert result["sentiment_source_count"] == 2
    # 加欄位不動既有計算：沿用 T1 的既有斷言
    assert result["type"] == "divergence"
    assert result["sentiment_direction"] == "bearish"


def test_sentiment_source_count_two_social_sources_same_direction():
    """情緒類 2 筆不同來源的 social claim（同方向）時，count == 2。"""
    scored = [
        _sc("obj1", "regulatory", "sec",      "bullish", 0.85),
        _sc("sen1", "social",     "reddit",   "bullish", 0.70),
        _sc("sen2", "social",     "twitter",  "bullish", 0.65),
    ]
    result = detect_cross_source_signal(scored)
    assert result is not None
    assert result["sentiment_source_count"] == 2
    assert result["type"] == "consensus"


def test_sentiment_source_count_absent_in_stance_pair_fallback():
    """`_stance_pair_signal()` 備援分支（聚合層級任一類 0 筆/neutral，但有
    stance_pairs）不附加 sentiment_source_count——該分支本就保證 stance_pairs
    涉及 >=2 個獨立來源，不需要標記單一來源主導。"""
    scored = [
        _sc("obj1", "onchain", "glassnode", "neutral", 0.80),
        _sc("sen1", "news",    "coindesk",  "bearish", 0.70),
        _sc("sen2", "social",  "twitter",   "bullish", 0.65),
    ]

    def stance_fn(_a: str, _b: str) -> str:
        return "contradiction"

    result = detect_cross_source_signal(scored, stance_fn=stance_fn)
    assert result is not None
    assert "sentiment_source_count" not in result


def test_sentiment_source_count_includes_stance_pair_low_trust_source():
    """CEO 退修必修 1（dev-manager 實測重現）：`sentiment_source_count` 若
    只算 trust>=0.5 聚合投票用的 `sent_sources`，會跟 `_detect_stance_pairs`
    （門檻 0.35，比 0.5 寬）抓到的矛盾配對脫鉤——一筆 trust 落在
    [0.35, 0.5) 的情緒來源不會進聚合投票，但只要跟另一筆情緒來源方向相反
    且 stance_fn 判矛盾，仍會被抓進 `stance_pairs`（非空時一律附加進
    result，collision 分支的 summary 甚至具名列出這些來源）。若計數只看
    `sent_sources`，會出現「count=1 顯示『單一來源主導』徽章，但
    summary/stance_pairs 明明列出 2 個矛盾來源」的自相矛盾。本測試重現該
    場景並驗證修復：count 改算 `sent_sources | stance_pairs 來源` 聯集後應
    為 2，徽章不應顯示。"""
    scored = [
        _sc("obj1", "price",  "binance",  "bearish", 0.90),
        # 唯一達 trust>=0.5、進聚合投票決定 sent_dir 的情緒來源
        _sc("sen1", "social", "socialA",  "bearish", 0.60),
        # 未達 0.5（不進 sent_sources），但達 0.35，仍被 _detect_stance_pairs
        # 抓進矛盾配對（跟 sen1 方向相反、來源不同）
        _sc("sen2", "social", "socialB",  "bullish", 0.40),
    ]

    def stance_fn(_a: str, _b: str) -> str:
        return "contradiction"

    result = detect_cross_source_signal(scored, stance_fn=stance_fn)
    assert result is not None
    # 聚合層級 obj_dir == sent_dir == bearish（同向），但 stance_pairs 抓到
    # 矛盾 → collision，type 仍是 divergence
    assert result["type"] == "divergence"
    assert "stance_pairs" in result
    stance_pair_sources = {p["source"] for p in result["stance_pairs"]}
    assert stance_pair_sources == {"socialA", "socialB"}
    # 修復核心斷言：徽章宣稱必須跟 stance_pairs 引用的來源集合一致 → 2，
    # 不是只算 sent_sources 的 1（回歸前的 bug 值）
    assert result["sentiment_source_count"] == 2


def test_sentiment_source_count_normalizes_alias_casing_and_whitespace():
    """CEO 退修必修 2（codex 指出 `_normalize_source_key` 只做
    `strip().casefold()`，不解 publisher 別名，如 `coindesk` vs
    `coindesk.com` 仍是 2 個不同來源——別名 canonicalization 追蹤於
    follow-up issue #72，本輪不做）。本測試只驗證「本輪範圍內」該有的行
    為：同一 publisher 純大小寫/空白變體（`"CoinDesk"` vs `" coindesk "`）
    必須正規化收斂成同一個獨立來源，count==1 且訊號仍正常產生（不因正規
    化而被過濾掉）。"""
    scored = [
        _sc("obj1", "price", "binance",    "bullish", 0.90),
        _sc("sen1", "news",  "CoinDesk",   "bullish", 0.70),
        _sc("sen2", "news",  " coindesk ", "bullish", 0.65),
    ]
    result = detect_cross_source_signal(scored)
    assert result is not None
    assert result["type"] == "consensus"
    assert result["sentiment_source_count"] == 1


def test_render_no_signal_no_cross_section():
    """cross_source_signal=None 時，HTML 中不應出現跨源訊號區塊。"""
    from trustforge import web

    report = _make_report(cross_signal=None)
    htmlout = web._render_report(report, [])
    assert "跨源訊號" not in htmlout, "signal=None 時不應出現跨源訊號區塊"
