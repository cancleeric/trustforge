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


def test_negated_manipulation_not_penalised():
    """否定詞守門:『不會暴漲』不該被當操縱訊號扣分。"""
    from trustforge.trust.scoring import _manipulation_penalty, Claim
    from trustforge.ingestion.base import Document
    d = Document(id="d", kind="news", source="coindesk", text="", ts=1)
    neg = Claim(id="x", text="分析師認為 BTC 短期不會暴漲", doc=d)
    pos = Claim(id="y", text="BTC 暴漲翻倍穩賺", doc=Document(id="e", kind="social", source="x", text="", ts=1))
    # 「不僅暴漲」是肯定語義(不+副詞),不可被否定守門誤放
    aff = Claim(id="z", text="BTC 不僅暴漲還翻倍", doc=Document(id="f", kind="social", source="x", text="", ts=1))
    assert _manipulation_penalty(neg) == 0
    assert _manipulation_penalty(pos) > 0
    assert _manipulation_penalty(aff) > 0


# --- P2-1 sample-enrich 新增整合測試 ------------------------------------

def test_manipulation_entries_land_in_contrarian():
    """Sample data 的喊單社群訊息（含 to the moon/翻倍/穩賺）應被評為 trust < 0.3 並落入 contrarian。"""
    from trustforge.ingestion.base import collect, OHLCV_DIR
    docs = collect("BTC", coin="BTC", offline=True, data_dir=OHLCV_DIR)
    assert docs, "離線樣本不可為空"
    now = max(d.ts for d in docs)
    claims = extract_claims(docs)
    scored_all = score(claims, now=now)
    brief = aggregate(scored_all, "BTC")
    # contrarian 中應有 kind=social + manipulation>0 + trust<0.3 的訊息
    pump_in_contrarian = [
        sc for sc in brief.contrarian
        if sc.claim.doc.kind == "social"
        and sc.components["manipulation"] > 0
        and sc.trust < 0.3
    ]
    assert pump_in_contrarian, (
        "操縱語言社群訊息應落入 contrarian（trust < 0.3），"
        f"contrarian social: {[(sc.claim.doc.source, round(sc.trust, 3), round(sc.components['manipulation'], 2)) for sc in brief.contrarian if sc.claim.doc.kind == 'social']}"
    )


def test_cross_source_corroboration_active():
    """onchain-btc-inflow 應被 news + social 至少 2 種不同 kind 獨立佐證（corroboration > 0.5）。"""
    import re
    from trustforge.ingestion.base import collect, OHLCV_DIR
    docs = collect("BTC 交易所", coin="BTC", offline=True, data_dir=OHLCV_DIR)
    assert docs, "離線樣本不可為空"
    now = max(d.ts for d in docs)
    claims = extract_claims(docs)
    scored_all = score(claims, now=now)

    # 釘住具體主張 onchain-btc-inflow
    btc_inflow = next(
        (sc for sc in scored_all if sc.claim.doc.id == "onchain-btc-inflow"),
        None,
    )
    assert btc_inflow is not None, "onchain-btc-inflow 主張必須存在於離線樣本"
    assert btc_inflow.components["corroboration"] > 0.5, (
        f"onchain-btc-inflow corroboration={btc_inflow.components['corroboration']:.3f}，應 > 0.5"
    )

    # 驗證跨源：佐證來自 ≥2 個不同 kind（使用與 _corroboration 相同的 token-overlap 邏輯）
    def _tokens(text: str) -> set:
        return {t for t in re.findall(r"[\w一-鿿]+", text.lower()) if len(t) > 1}

    target_tokens = _tokens(btc_inflow.claim.text)
    corroborating_kinds: set = set()
    for sc in scored_all:
        c = sc.claim
        if c.doc.source == btc_inflow.claim.doc.source:
            continue
        overlap = len(target_tokens & _tokens(c.text)) / max(1, len(target_tokens))
        if overlap >= 0.4:
            corroborating_kinds.add(c.doc.kind)

    assert len(corroborating_kinds) >= 2, (
        f"onchain-btc-inflow 應被 ≥2 種不同 kind 來源佐證，實際: {corroborating_kinds}"
    )
    assert {"news", "social"} <= corroborating_kinds, (
        f"佐證 kind 集合應同時包含 news 與 social，實際: {corroborating_kinds}"
    )
