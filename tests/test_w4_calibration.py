"""W4：校準信心 + abstain（棄權）驗收測試。

CEO 派工規格：
  - `trust.scoring.aggregate()` 新增 `calibrated_confidence`（硬編分位數映射表，
    確定性、免 LLM）；`confidence` 裸值保留、不砍。
  - `agent.orchestrator.build_report()` 用校準後信心取代武斷單一 0.5 硬門檻，
    改為三態：
      calibrated < 0.35 或 supporting < 2（證據不足）→ abstain：中性措辭，
      不給方向性字眼。
      0.35 <= calibrated < 0.5 → 仍出結論，標「低信心」。
      calibrated >= 0.5 → 正常（既有行為逐字不變）。
  - 0.5 錨點不刪，只從唯一硬門檻降為三態分界之一；`support_threshold=0.50`
    等既有呼叫端逐字不變。

誠實聲明（比照 `trust.scoring._calibrate_confidence` docstring）：這是簡化版
分位數校準，不是嚴謹 conformal prediction，沒有 coverage 保證。
"""
from __future__ import annotations

from trustforge.agent.orchestrator import build_report
from trustforge.bedrock import BedrockClient
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import Document
from trustforge.schema import QuestionType
from trustforge.trust.scoring import (
    Claim,
    ScoredClaim,
    TrustedBrief,
    _calibrate_confidence,
    aggregate,
    extract_claims,
    score,
)

# 不得出現在 abstain 措辭裡的方向性字眼（守「不代客決策」鐵律）。
_DIRECTIONAL_WORDS = ("偏多", "偏空", "看漲", "看跌", "上漲", "下跌")


def _doc(id_: str, kind: str, source: str, text: str = "", ts: float = 1.0) -> Document:
    return Document(id=id_, kind=kind, source=source, text=text, ts=ts)


def _sc(id_: str, kind: str, source: str, trust: float, text: str = "", direction: str = "neutral") -> ScoredClaim:
    doc = _doc(id_, kind, source, text=text)
    claim = Claim(id=id_, text=text or f"claim-{id_}", doc=doc, direction=direction)
    return ScoredClaim(claim=claim, trust=trust)


def _brief(supporting, contrarian, confidence: float, calibrated_confidence: float, query: str = "分析 BTC") -> TrustedBrief:
    return TrustedBrief(
        query=query,
        supporting=supporting,
        contrarian=contrarian,
        confidence=confidence,
        calibrated_confidence=calibrated_confidence,
    )


# ---------------------------------------------------------------------------
# 1. `_calibrate_confidence` 純函式：固定校準表 + 三組信心(0.3/0.4/0.6)
# ---------------------------------------------------------------------------

def test_calibrate_confidence_0_3_lands_below_abstain_threshold():
    """裸信心 0.3 → 校準後應落入 abstain 區間（< 0.35）。"""
    calibrated = _calibrate_confidence(0.3)
    assert calibrated < 0.35, f"預期 0.3 校準後 < 0.35（abstain），實得 {calibrated}"


def test_calibrate_confidence_0_4_lands_in_low_confidence_band():
    """裸信心 0.4 → 校準後應落入低信心區間 [0.35, 0.5)。"""
    calibrated = _calibrate_confidence(0.4)
    assert 0.35 <= calibrated < 0.5, f"預期 0.4 校準後落在 [0.35, 0.5)（低信心），實得 {calibrated}"


def test_calibrate_confidence_0_6_lands_in_normal_band():
    """裸信心 0.6 → 校準後應落入正常區間（>= 0.5）。"""
    calibrated = _calibrate_confidence(0.6)
    assert calibrated >= 0.5, f"預期 0.6 校準後 >= 0.5（正常），實得 {calibrated}"


def test_calibrate_confidence_fixed_table_exact_values():
    """固定校準表回歸鎖：釘住表上明確錨點的精確輸出，未來改表需明確更新此測試。"""
    assert _calibrate_confidence(0.0) == 0.0
    assert _calibrate_confidence(0.3) == 0.20
    assert _calibrate_confidence(0.4) == 0.40
    assert _calibrate_confidence(0.55) == 0.55
    assert _calibrate_confidence(1.0) == 1.0


def test_calibrate_confidence_monotonic_non_decreasing():
    """確定性、免 LLM：校準函式必須是單調不減（分位數映射的基本要求）。"""
    xs = [i / 100 for i in range(0, 101)]
    ys = [_calibrate_confidence(x) for x in xs]
    for a, b in zip(ys, ys[1:]):
        assert b >= a, "校準表插值不應出現非單調（違反分位數映射基本假設）"


def test_calibrate_confidence_clamps_out_of_range_input():
    """輸入超出 [0, 1]（防禦性，理論上不該發生）時 clamp 到邊界，不 crash。"""
    assert _calibrate_confidence(-1.0) == 0.0
    assert _calibrate_confidence(2.0) == 1.0


def test_calibrate_confidence_deterministic_same_input_same_output():
    """確定性：同輸入呼叫多次結果逐字相同（免 LLM、無隨機性）。"""
    results = {_calibrate_confidence(0.437) for _ in range(5)}
    assert len(results) == 1


# ---------------------------------------------------------------------------
# 2. `aggregate()` 附上 `calibrated_confidence`，`confidence` 裸值不砍
# ---------------------------------------------------------------------------

def test_aggregate_sets_calibrated_confidence_consistent_with_raw():
    docs = [
        _doc("a", "onchain", "glassnode", "大額 BTC 轉入交易所造成賣壓，價格下跌。"),
        _doc("b", "social", "x-anon", "BTC 翻倍 to the moon 穩賺！"),
    ]
    brief = aggregate(score(extract_claims(docs), now=1.0), query="BTC 賣壓")
    assert 0.0 <= brief.confidence <= 1.0
    assert 0.0 <= brief.calibrated_confidence <= 1.0
    # calibrated_confidence 必須是 confidence 經 _calibrate_confidence() 算出來的，
    # 不是另外一套邏輯（回歸鎖：兩者不可各自漂移）。
    assert brief.calibrated_confidence == _calibrate_confidence(brief.confidence)


def test_aggregate_no_supporting_confidence_and_calibrated_both_zero():
    """既有行為：無 supporting 時 confidence=0.0；calibrated 亦應為 0.0（校準表 (0,0) 錨點）。"""
    docs = [_doc("a", "social", "x-anon", "BTC 翻倍 to the moon 穩賺快上車！")]
    brief = aggregate(score(extract_claims(docs), now=1.0), query="無關查詢字串")
    if not brief.supporting:
        assert brief.confidence == 0.0
        assert brief.calibrated_confidence == 0.0


# ---------------------------------------------------------------------------
# 3. `build_report` 三態 abstain（agent/orchestrator.py）
# ---------------------------------------------------------------------------

def _run_report(brief, qtype=QuestionType.MULTI_SOURCE, query="分析 BTC"):
    return build_report(
        query=query, coin="BTC", qtype=qtype, brief=brief,
        client=BedrockClient(offline=True),
        log=ExecutionLog(now_fn=lambda: 1000.0),
        now_fn=lambda: 1000.0,
    )


def test_abstain_when_calibrated_confidence_below_threshold():
    """calibrated < 0.35 → abstain：中性「資料不足」措辭，不給方向詞。"""
    supporting = [
        _sc("s1", "price", "binance", 0.55, "BTC 上漲 站穩關鍵位。"),
        _sc("s2", "onchain", "glassnode", 0.52, "鏈上資金 流入。"),
    ]
    brief = _brief(supporting, contrarian=[], confidence=0.3, calibrated_confidence=0.20)
    report, _evidence = _run_report(brief)

    assert report.direction == "不明"
    assert "不足" in report.market_judgment
    for w in _DIRECTIONAL_WORDS:
        assert w not in report.market_judgment, f"abstain 措辭不應含方向詞「{w}」：{report.market_judgment}"


def test_abstain_forced_when_supporting_has_only_one_claim():
    """supporting 只 1 筆 → 強制 abstain，即使 calibrated_confidence 很高
    （證據不足鐵則優先於信心數值本身）。"""
    supporting = [_sc("s1", "price", "binance", 0.90, "BTC 上漲 站穩關鍵位。")]
    brief = _brief(supporting, contrarian=[], confidence=0.9, calibrated_confidence=0.85)
    report, _evidence = _run_report(brief)

    assert report.direction == "不明"
    assert "不足" in report.market_judgment
    for w in _DIRECTIONAL_WORDS:
        assert w not in report.market_judgment, f"abstain 措辭不應含方向詞「{w}」：{report.market_judgment}"


def test_low_confidence_state_still_gives_conclusion_but_marked():
    """0.35 <= calibrated < 0.5 → 仍出結論（有方向），但標「低信心」。"""
    supporting = [
        _sc("s1", "price", "binance", 0.55, "BTC 上漲 站穩關鍵位。"),
        _sc("s2", "onchain", "glassnode", 0.52, "鏈上資金 流入。"),
    ]
    brief = _brief(supporting, contrarian=[], confidence=0.4, calibrated_confidence=0.40)
    report, _evidence = _run_report(brief)

    assert report.direction == "偏多"
    assert "低信心" in report.market_judgment
    assert "不足以判斷" not in report.market_judgment


def test_normal_state_unmarked_and_unchanged():
    """calibrated >= 0.5 → 正常，不含 abstain/低信心標記（既有行為逐字不變）。"""
    supporting = [
        _sc("s1", "price", "binance", 0.80, "BTC 上漲 站穩關鍵位。"),
        _sc("s2", "onchain", "glassnode", 0.75, "鏈上資金 流入。"),
    ]
    brief = _brief(supporting, contrarian=[], confidence=0.7, calibrated_confidence=0.70)
    report, _evidence = _run_report(brief)

    assert report.direction == "偏多"
    assert "低信心" not in report.market_judgment
    assert "不足以判斷" not in report.market_judgment


def test_confidence_field_still_reports_raw_value_not_calibrated():
    """`Report.confidence` 沿用既有語意（裸值），回歸鎖：不得被 W4 悄悄換成校準值。"""
    supporting = [
        _sc("s1", "price", "binance", 0.55, "BTC 上漲 站穩關鍵位。"),
        _sc("s2", "onchain", "glassnode", 0.52, "鏈上資金 流入。"),
    ]
    brief = _brief(supporting, contrarian=[], confidence=0.4, calibrated_confidence=0.40)
    report, _evidence = _run_report(brief)
    assert report.confidence == 0.4
