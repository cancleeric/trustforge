"""信任提煉引擎核心測試。確保『信任層』行為符合設計意圖。"""
from trustforge.ingestion.base import Document
from trustforge.trust.scoring import aggregate, extract_claims, score


def _doc(id, kind, source, text, ts=1.0):
    return Document(id=id, kind=kind, source=source, text=text, ts=ts)


def test_onchain_outranks_anon_social():
    """客觀鏈上資料的信任分應高於匿名社群喊單。"""
    docs = [
        _doc("a", "onchain", "glassnode", "大額 BTC 轉入交易所造成賣壓。"),
        _doc("b", "social", "x-anon", "BTC 馬上翻倍 to the moon 穩賺快上車！"),
    ]
    scored = {sc.claim.doc.kind: sc.trust for sc in score(extract_claims(docs), now=1.0)}
    assert scored["onchain"] > scored["social"]


def test_manipulation_language_is_penalised():
    """含喊單/操縱語言的主張，操縱懲罰分項 > 0。"""
    docs = [_doc("b", "social", "x-anon", "穩賺 翻倍 to the moon！")]
    sc = score(extract_claims(docs), now=1.0)[0]
    assert sc.components["manipulation"] > 0


def test_independent_corroboration_raises_trust():
    """同一主張被多個獨立來源佐證，交叉佐證分項升高。"""
    shared = "大額 BTC 轉入 交易所 造成 賣壓 比特幣 下跌"
    docs = [
        _doc("a", "onchain", "glassnode", shared),
        _doc("b", "news", "coindesk", shared),
        _doc("c", "social", "x-trader", shared),
    ]
    scored = score(extract_claims(docs), now=1.0)
    target = next(sc for sc in scored if sc.claim.doc.source == "glassnode")
    assert target.components["corroboration"] > 0.5


def test_aggregate_splits_supporting_and_contrarian():
    docs = [
        _doc("a", "onchain", "glassnode", "大額 BTC 轉入交易所造成賣壓，價格下跌。"),
        _doc("b", "social", "x-anon", "BTC 翻倍 to the moon 穩賺！"),
    ]
    brief = aggregate(score(extract_claims(docs), now=1.0), query="BTC 賣壓")
    assert brief.supporting, "應有高信任支撐主張"
    # 喊單主張應落在反方/低信任，不污染結論
    assert all("翻倍" not in sc.claim.text for sc in brief.supporting)
    assert 0.0 <= brief.confidence <= 1.0
    assert brief.provenance()  # 溯源鏈非空
