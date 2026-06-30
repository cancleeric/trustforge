"""信任提煉引擎核心測試。確保『信任層』行為符合設計意圖。"""
from trustforge.ingestion.base import Document
from trustforge.trust.scoring import DOMAIN_STOP, aggregate, extract_claims, score


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

    # 驗證跨源：佐證來自 ≥2 個不同 kind（使用與 _corroboration 相同的 token-overlap 邏輯，含 DOMAIN_STOP 過濾）
    def _tokens_filtered(text: str) -> set:
        raw = {t for t in re.findall(r"[\w一-鿿]+", text.lower()) if len(t) > 1}
        return raw - DOMAIN_STOP

    target_tokens = _tokens_filtered(btc_inflow.claim.text)
    corroborating_kinds: set = set()
    for sc in scored_all:
        c = sc.claim
        if c.doc.source == btc_inflow.claim.doc.source:
            continue
        ct = _tokens_filtered(c.text)
        overlap = len(target_tokens & ct) / max(1, len(target_tokens))
        if overlap >= 0.4:
            corroborating_kinds.add(c.doc.kind)

    assert len(corroborating_kinds) >= 2, (
        f"onchain-btc-inflow 應被 ≥2 種不同 kind 來源佐證，實際: {corroborating_kinds}"
    )
    assert {"news", "social"} <= corroborating_kinds, (
        f"佐證 kind 集合應同時包含 news 與 social，實際: {corroborating_kinds}"
    )


# --- PLAN 3-1 驗收測試 V1 / V2 / V3 ----------------------------------------

def _make_doc(id_, kind, source):
    return Document(id=id_, kind=kind, source=source, text="", ts=1.0)


def test_v1_only_stopwords_no_corroboration():
    """V1：兩條主張只共享幣名/市場通用詞（全在 DOMAIN_STOP），不同來源 → corr = 0.0。"""
    from trustforge.trust.scoring import Claim, _corroboration

    doc_a = _make_doc("da", "news", "coindesk")
    doc_b = _make_doc("db", "social", "x-user")
    # 兩條都含 btc/市場/價格/成交量，但各自的具體詞不重疊
    c_a = Claim(id="v1a", text="BTC 市場 買入 機會 值得 關注", doc=doc_a)
    c_b = Claim(id="v1b", text="BTC 市場 賣出 風險 出現 警示", doc=doc_b)

    corr = _corroboration(c_a, [c_a, c_b])
    assert corr == 0.0, f"V1：只有通用詞重疊，corr 應為 0.0，實際: {corr}"


def test_v2_opposite_direction_no_corroboration():
    """V2：明確 bullish vs bearish 方向相反 → direction gate 攔截 → corr = 0.0。"""
    from trustforge.trust.scoring import Claim, _corroboration

    doc_a = _make_doc("da", "news", "coindesk")
    doc_b = _make_doc("db", "social", "x-user")
    c_a = Claim(id="v2a", text="清算 瀑布 觸發 ETF 審批 加速 看漲", doc=doc_a, direction="bullish")
    c_b = Claim(id="v2b", text="清算 瀑布 觸發 ETF 審批 加速 看空", doc=doc_b, direction="bearish")

    corr = _corroboration(c_a, [c_a, c_b])
    assert corr == 0.0, f"V2：方向相反（bullish vs bearish），corr 應為 0.0，實際: {corr}"


def test_v3_specific_rare_words_corroborate():
    """V3：兩條主張共享具體稀有詞（清算/瀑布/ETF/審批），不同來源 → corr > 0。"""
    from trustforge.trust.scoring import Claim, _corroboration

    doc_a = _make_doc("da", "onchain", "glassnode")
    doc_b = _make_doc("db", "news", "coindesk")
    c_a = Claim(id="v3a", text="清算 瀑布 觸發 ETF 審批 加速", doc=doc_a)
    c_b = Claim(id="v3b", text="清算 瀑布 影響 ETF 申請 結果", doc=doc_b)

    corr = _corroboration(c_a, [c_a, c_b])
    assert corr > 0.0, f"V3：共享具體稀有詞，corr 應 > 0，實際: {corr}"
