"""D1.5 假設驗證題型結構化正反方 — 驗收測試。

驗收：
  - qtype=HYPOTHESIS → report.hypothesis_ledger 非空，pro/con 索引綁定 evidence，
    confidence_limit 明講「不宣稱預測力」。
  - 非 HYPOTHESIS 題型 → hypothesis_ledger 為 None。
  - 證據不足（abstain）時，正反方對照線仍進 limits（不過度宣稱）。
  - 報告 inferences / limits 含「假設驗證正反方對照」文字。
"""
from trustforge.agent.orchestrator import build_report
from trustforge.ingestion.base import Document
from trustforge.schema import QuestionType
from trustforge.trust.scoring import Claim, ScoredClaim, TrustedBrief


def _sc(doc_id: str, text: str, kind: str, source: str, direction: str, trust: float) -> ScoredClaim:
    doc = Document(id=doc_id, kind=kind, source=source, text=text, ts=1.0)
    claim = Claim(id=f"{doc_id}#0", text=text, doc=doc, direction=direction)
    return ScoredClaim(claim=claim, trust=trust)


def _hypothesis_report(supporting, contrarian, qtype=QuestionType.HYPOTHESIS):
    brief = TrustedBrief(
        query="BTC 短期會盤整嗎", supporting=supporting, contrarian=contrarian, confidence=0.7,
    )
    scored = supporting + contrarian
    return build_report(
        query="BTC 短期會盤整嗎", coin="BTC", qtype=qtype, brief=brief, scored=scored,
        run_scope_id="test-insights-d15",
    )[0]


def test_d15_hypothesis_ledger_built():
    sup = [_sc("s1", "BTC 上看 70000", "news", "coindesk", "bullish", 0.7)]
    con = [_sc("c1", "BTC 恐跌至 50000", "social", "x-anon", "bearish", 0.4)]
    report = _hypothesis_report(sup, con)
    assert report.hypothesis_ledger is not None
    hl = report.hypothesis_ledger
    assert hl["pro"] and hl["con"], "pro/con 索引都應有內容"
    assert "不宣稱預測力" in hl["confidence_limit"], "必須明講不過度宣稱預測力"
    joined = " ".join(report.inferences + report.limits)
    assert "假設驗證正反方對照" in joined
    assert "支持方" in joined and "反方" in joined


def test_d15_non_hypothesis_has_no_ledger():
    sup = [_sc("s1", "BTC 上看 70000", "news", "coindesk", "bullish", 0.7)]
    report = _hypothesis_report(sup, [], qtype=QuestionType.MULTI_SOURCE)
    assert report.hypothesis_ledger is None


def test_d15_abstain_still_shows_ledger_in_limits():
    # 證據不足（無 supporting）→ abstain，正反方對照線應進 limits（不過度宣稱）。
    con = [_sc("c1", "BTC 恐跌至 50000", "social", "x-anon", "bearish", 0.4)]
    report = _hypothesis_report([], con)
    assert report.hypothesis_ledger is not None
    assert any("假設驗證正反方對照" in lim for lim in report.limits), "abstain 時對照線應進 limits"


def test_d15_ledger_indices_bind_evidence():
    sup = [_sc("s1", "BTC 上看 70000", "news", "coindesk", "bullish", 0.7)]
    con = [_sc("c1", "BTC 恐跌至 50000", "social", "x-anon", "bearish", 0.4)]
    report, evidence = build_report(
        query="BTC 短期會盤整嗎", coin="BTC", qtype=QuestionType.HYPOTHESIS,
        brief=TrustedBrief(query="q", supporting=sup, contrarian=con, confidence=0.7),
        scored=sup + con, run_scope_id="test-insights-d15-idx",
    )
    pro_i = report.hypothesis_ledger["pro"][0]
    con_i = report.hypothesis_ledger["con"][0]
    # 索引必須指向 evidence 陣列真實存在的條目，且 related_claim 正確。
    assert evidence[pro_i].related_claim == "BTC 市場判斷"
    assert evidence[con_i].related_claim == "反方／低信任訊號"
