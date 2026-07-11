"""D1.4 來源自我矛盾（不確定性信號）— #21 / #72 驗收測試。

驗證：
  - 同一來源同時 bullish + bearish → 產生 source_self_contradiction 洞察，
    兩個貢獻來源方向分別為 bullish / bearish（左右互搏）。
  - 僅單一方向（全 bullish）→ 不產生（非自我矛盾）。
  - 同源大小寫/空白變體收斂（#72）："CoinDesk" 與 " coindesk " 視為同一來源，
    仍能被判自我矛盾。
  - detect_insights 聚合會納入。
  - 前端 InsightExplainabilityPanel 對此類型顯示「來源自我矛盾」徽章。
"""
from trustforge.ingestion.base import Document
from trustforge.trust.insights import detect_insights, detect_source_self_contradiction
from trustforge.trust.scoring import Claim, ScoredClaim

TRUST = 0.6


def _c(source: str, text: str, direction: str, trust: float = TRUST) -> ScoredClaim:
    doc = Document(id=f"{source}-{direction}", kind="news", source=source, text=text, ts=1.0)
    claim = Claim(id=f"{source}-{direction}#0", text=text, doc=doc, direction=direction)
    return ScoredClaim(claim=claim, trust=trust)


def test_d14_self_contradiction_detected():
    scored = [
        _c("coindesk", "BTC 上看 70000", "bullish"),
        _c("coindesk", "BTC 恐跌至 50000", "bearish"),
    ]
    ins_list = detect_source_self_contradiction(scored)
    assert len(ins_list) == 1
    ins = ins_list[0]
    assert ins.insight_type == "source_self_contradiction"
    assert ins.direction == "ambiguous"
    dirs = {c.direction for c in ins.contributions}
    assert dirs == {"bullish", "bearish"}, "應含左右互搏的兩個相反方向"


def test_d14_single_direction_not_contradiction():
    scored = [
        _c("coindesk", "BTC 上看 70000", "bullish"),
        _c("coindesk", "ETH 上看 4000", "bullish"),
    ]
    assert detect_source_self_contradiction(scored) == []


def test_d14_canonical_source_collapse_issue72():
    # 同源大小寫/空白變體應收斂成同一來源，仍判自我矛盾。
    scored = [
        _c("CoinDesk", "BTC 上看 70000", "bullish"),
        _c(" coindesk ", "BTC 恐跌至 50000", "bearish"),
    ]
    ins_list = detect_source_self_contradiction(scored)
    assert len(ins_list) == 1, "大小寫/空白變體應視為同一來源並判自我矛盾"


def test_d14_aggregation_includes_self_contradiction():
    scored = [
        _c("coindesk", "BTC 上看 70000", "bullish"),
        _c("coindesk", "BTC 恐跌至 50000", "bearish"),
    ]
    insights = detect_insights(
        None, scored, "BTC",  # type: ignore[arg-type]
    )
    assert any(i.insight_type == "source_self_contradiction" for i in insights)
