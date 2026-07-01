"""信任提煉引擎核心測試。確保『信任層』行為符合設計意圖。"""
from trustforge.ingestion.base import Document
from trustforge.trust.scoring import DOMAIN_STOP, aggregate, extract_claims, score


# --- _infer_direction 純函式測試 -----------------------------------------

def test_infer_direction_bullish():
    """明確看多詞彙 → bullish。"""
    from trustforge.trust.scoring import _infer_direction
    assert _infer_direction("BTC 看漲 突破，買盤 累積 增持。") == "bullish"


def test_infer_direction_bearish():
    """明確看空詞彙 → bearish。"""
    from trustforge.trust.scoring import _infer_direction
    assert _infer_direction("BTC 下跌 賣壓 恐慌 走低。") == "bearish"


def test_infer_direction_neutral_no_keywords():
    """無方向詞彙 → neutral。"""
    from trustforge.trust.scoring import _infer_direction
    assert _infer_direction("BTC 近期 整合 等待 方向。") == "neutral"


def test_infer_direction_neutral_tie():
    """bullish 命中 == bearish 命中 → neutral。"""
    from trustforge.trust.scoring import _infer_direction
    # 流入(1 bullish) vs 賣壓(1 bearish) → tie → neutral
    assert _infer_direction("BTC 流入 交易所 賣壓 急增。") == "neutral"


def test_infer_direction_negation_cancels_bearish():
    """否定守門：不會下跌 → 下跌不計 → neutral（無其他方向詞）。"""
    from trustforge.trust.scoring import _infer_direction
    assert _infer_direction("分析師認為 BTC 短期不會下跌。") == "neutral"


def test_infer_direction_negation_only_nearby():
    """否定守門只作用於前 4 字內：否定詞距離超過 4 字不生效，bearish 仍計。"""
    from trustforge.trust.scoring import _infer_direction
    # 不會 在 下跌 前 4 字以外（中間有 在短期內），下跌 仍算 bearish
    assert _infer_direction("BTC 不會在短期內 下跌 賣壓 升高。") == "bearish"


def test_extract_claims_sets_direction():
    """extract_claims 建出的 Claim 應有非預設方向（不全為 neutral）。"""
    docs = [
        Document(id="d1", kind="news", source="coindesk",
                 text="BTC 下跌 賣壓 急增，市場 走低。", ts=1.0),
        Document(id="d2", kind="onchain", source="glassnode",
                 text="ETH 買盤 累積 增持，長線 看漲。", ts=1.0),
    ]
    claims = extract_claims(docs)
    directions = {c.direction for c in claims}
    assert "bearish" in directions, "含跌/賣壓的句子應被推斷為 bearish"
    assert "bullish" in directions, "含買盤/累積的句子應被推斷為 bullish"


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
    """onchain-btc-inflow 應被獨立來源交叉佐證（corroboration > 0.5）。

    直接斷言 production 行為（_corroboration 結果），不在測試裡重算 token-overlap 邏輯，
    避免實作與測試同步出錯導致假綠。
    """
    from trustforge.ingestion.base import collect, OHLCV_DIR
    docs = collect("BTC 交易所", coin="BTC", offline=True, data_dir=OHLCV_DIR)
    assert docs, "離線樣本不可為空"
    now = max(d.ts for d in docs)
    claims = extract_claims(docs)
    scored_all = score(claims, now=now)

    # 斷言具體主張 onchain-btc-inflow 的 production corroboration 分數
    btc_inflow = next(
        (sc for sc in scored_all if sc.claim.doc.id == "onchain-btc-inflow"),
        None,
    )
    assert btc_inflow is not None, "onchain-btc-inflow 主張必須存在於離線樣本"
    assert btc_inflow.components["corroboration"] > 0.5, (
        f"onchain-btc-inflow corroboration={btc_inflow.components['corroboration']:.3f}，應 > 0.5"
    )

    # 驗資料完備性：樣本中確實有 news 與 social 不同 kind 的來源（不重算 overlap）
    kinds_in_sample = {sc.claim.doc.kind for sc in scored_all}
    assert {"news", "social"} <= kinds_in_sample, (
        f"離線樣本應同時包含 news 與 social kind，實際: {kinds_in_sample}"
    )


# --- PLAN 3-1 驗收測試 V1 / V2 / V3 ----------------------------------------

def _make_doc(id_, kind, source):
    return Document(id=id_, kind=kind, source=source, text="", ts=1.0)


def test_v1_only_stopwords_no_corroboration():
    """V1：兩條主張共享詞全在 DOMAIN_STOP（幣名/市場通用詞），過濾後具體詞無交集 → corr = 0.0。

    Regression guard：不加 DOMAIN_STOP 過濾時，unfiltered overlap ≈ 0.67（≥ 0.4 → 會誤判為佐證）。
    此測試確保 stopword 過濾真的生效——若移除 DOMAIN_STOP 過濾，本測試應失敗。
    設計：A="BTC 成交量 創新高 交易所 買壓 價格" vs B="BTC 成交量 萎縮 交易所 拋壓 價格"
      - 共享 {btc,成交量,交易所,價格}（全在 DOMAIN_STOP）→ unfiltered overlap = 4/6 ≈ 0.67
      - 過濾後 A 具體詞 {創新高,買壓}，B 具體詞 {萎縮,拋壓} → overlap = 0
    """
    from trustforge.trust.scoring import Claim, _corroboration

    doc_a = _make_doc("da", "news", "coindesk")
    doc_b = _make_doc("db", "social", "x-user")
    c_a = Claim(id="v1a", text="BTC 成交量 創新高 交易所 買壓 價格", doc=doc_a)
    c_b = Claim(id="v1b", text="BTC 成交量 萎縮 交易所 拋壓 價格", doc=doc_b)

    corr = _corroboration(c_a, [c_a, c_b])
    assert corr == 0.0, f"V1：共享詞全在 DOMAIN_STOP，過濾後具體詞無交集，corr 應為 0.0，實際: {corr}"


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


# --- Task 1: _infer_direction 雙重計數修復測試 --------------------------------

def test_infer_direction_no_double_count_substring():
    """「上漲」不可被「漲」重複計數：「上漲 看空」應為 neutral（不誤判 bullish）。

    舊實作：上漲(+1) + 漲(+1) = bullish=2 vs 看空(+1) = bearish=1 → 誤判 bullish。
    修正後：上漲(+1，消耗「漲」) vs 看空(+1) → 1v1 → neutral。
    """
    from trustforge.trust.scoring import _infer_direction
    assert _infer_direction("上漲 看空") == "neutral", (
        "上漲 看空：bullish=1 bearish=1 → neutral，不可因雙計「漲」誤報 bullish"
    )


def test_infer_direction_kanzhan_kankong_neutral():
    """「看漲 看空」各含 1 個方向詞，應為 neutral（不因「漲」雙重計數而誤判 bullish）。

    舊實作：看漲(+1) + 漲(+1) = bullish=2 vs 看空(+1) = bearish=1 → 誤判 bullish。
    修正後：看漲(+1，消耗「漲」) vs 看空(+1) → 1v1 → neutral。
    """
    from trustforge.trust.scoring import _infer_direction
    assert _infer_direction("看漲 看空") == "neutral", (
        "看漲(1) 看空(1) → neutral，不可雙計「漲」"
    )


# --- Task 2: 離線 collect 幣別過濾測試 ----------------------------------------

def test_eth_collect_excludes_btc_contamination():
    """ETH 離線 collect 不應包含 BTC 專屬文件（跨幣污染修復）。

    修復前：collect(coin='ETH') 回傳全部樣本，BTC 鏈上/社群資料污染 ETH 判斷。
    修復後：BTC 專屬 doc 排除，ETH 專屬 doc 保留，全市場通用 doc 保留。
    """
    from trustforge.ingestion.base import collect, OHLCV_DIR
    docs = collect("ETH", coin="ETH", offline=True, data_dir=OHLCV_DIR)
    ids = {d.id for d in docs}
    # BTC 專屬 doc 不應出現在 ETH 查詢
    assert "onchain-btc-inflow" not in ids, "BTC 轉入不應汙染 ETH 查詢"
    assert "onchain-whale" not in ids, "BTC 鯨魚不應汙染 ETH 查詢"
    assert "onchain-miner" not in ids, "BTC 礦工不應汙染 ETH 查詢"
    assert "soc-pump1" not in ids, "BTC 喊單不應汙染 ETH 查詢"
    # ETH 專屬 doc 必須保留
    assert "onchain-eth-outflow" in ids, "ETH 流出應在 ETH 查詢中"
    assert "onchain-eth-accumulation-1" in ids, "ETH 累積-1 應在 ETH 查詢中"
    # 全市場通用 doc 必須保留
    assert "reg-sec-warning" in ids, "全市場監管 doc 應在 ETH 查詢中"
    assert "reg-international" in ids, "全市場國際監管 doc 應在 ETH 查詢中"


# --- Task 3: ETH 新樣本 trust / 多源性斷言 ------------------------------------

def test_eth_accumulation_sources_distinct():
    """ETH 三筆鏈上累積樣本應來自不同來源且時戳各異（誠實差異化驗證）。"""
    from trustforge.ingestion.base import collect, OHLCV_DIR
    docs = collect("ETH", coin="ETH", offline=True, data_dir=OHLCV_DIR)
    eth_acc = [d for d in docs if d.id.startswith("onchain-eth-accumulation-")]
    assert len(eth_acc) == 3, f"應有 3 筆 ETH 累積樣本，實際 {len(eth_acc)}"
    sources = {d.source for d in eth_acc}
    assert len(sources) == 3, f"3 筆應來自不同來源，實際: {sources}"
    timestamps = {d.ts for d in eth_acc}
    assert len(timestamps) == 3, f"3 筆時戳應各不同（錯開 2-6h），實際: {timestamps}"


def test_eth_onchain_accumulation_has_positive_trust():
    """ETH 鏈上累積樣本（高信譽 onchain）trust 應 > 0。"""
    from trustforge.ingestion.base import collect, OHLCV_DIR
    docs = collect("ETH", coin="ETH", offline=True, data_dir=OHLCV_DIR)
    now = max(d.ts for d in docs)
    claims = extract_claims(docs)
    scored = score(claims, now=now)
    eth_acc_scored = [
        sc for sc in scored
        if sc.claim.doc.id.startswith("onchain-eth-accumulation-")
    ]
    assert len(eth_acc_scored) >= 3, f"ETH 累積樣本應有 ≥3 筆主張，實際 {len(eth_acc_scored)}"
    for sc in eth_acc_scored:
        assert sc.trust > 0, (
            f"{sc.claim.doc.id} trust 應 > 0（onchain 高信譽基礎分），實際: {sc.trust}"
        )


# --- Task 4: 方向相容佐證回歸 fixture ----------------------------------------

def test_direction_compatible_neutral_allows_corroboration():
    """neutral + bullish 方向相容 → 不被方向閘攔截，仍可互相佐證。

    回歸保護：確保方向閘只攔截明確對立（bullish vs bearish），
    不過度攔截 neutral 搭配有方向的主張（這才是設計意圖）。
    """
    from trustforge.trust.scoring import Claim, _corroboration

    doc_a = _make_doc("da", "onchain", "glassnode")
    doc_b = _make_doc("db", "news", "coindesk")
    # c_a 方向 neutral，c_b 方向 bullish → 應相容，可佐證
    c_a = Claim(id="v4a", text="清算 瀑布 觸發 ETF 審批 加速", doc=doc_a, direction="neutral")
    c_b = Claim(id="v4b", text="清算 瀑布 影響 ETF 申請 結果", doc=doc_b, direction="bullish")
    corr = _corroboration(c_a, [c_a, c_b])
    assert corr > 0.0, (
        f"neutral + bullish 方向相容應可佐證，corr={corr}；"
        "若為 0 表示方向閘過度攔截（回歸）"
    )


# --- W1 案2b（#15）：反義/否定感知 stance 佐證矛盾閘 --------------------------

def test_issue15_english_antonym_claims_not_corroborated():
    """#15 復現案例：英文『監管明朗』vs『監管收緊』token overlap 高但語意對立。

    修復前：corr≈0.5（overlap 高 + 英文永遠判 neutral 繞過方向閘 → 誤判佐證）。
    修復後：語意矛盾閘攔截 → corr 應 = 0.0，且 semantic_stance 應判為 contradict。
    """
    from trustforge.trust.scoring import Claim, _corroboration
    from trustforge.trust.stance import semantic_stance

    doc_a = _make_doc("da", "news", "coindesk")
    doc_b = _make_doc("db", "news", "reuters")
    text_a = "Market analysts expect regulatory clarity to boost institutional adoption significantly"
    text_b = "Market observers expect regulatory scrutiny to boost investor caution significantly"
    c_a = Claim(id="i15a", text=text_a, doc=doc_a)
    c_b = Claim(id="i15b", text=text_b, doc=doc_b)

    corr = _corroboration(c_a, [c_a, c_b])
    assert corr == 0.0, f"#15：英文反義主張不應被判為佐證，corr 應 = 0.0，實際: {corr}"

    stance, evidence = semantic_stance(text_a, text_b, {"clarity"}, {"scrutiny"})
    assert stance == "contradict", f"#15：應判為 contradict，實際: {stance}（evidence={evidence}）"


def test_chinese_antonym_claims_not_corroborated():
    """中文反義對照：『監管明朗』vs『監管收緊』同樣應被矛盾閘擋下，不能只修英文。"""
    from trustforge.trust.scoring import Claim, _corroboration

    doc_a = _make_doc("da", "news", "coindesk")
    doc_b = _make_doc("db", "news", "financial-times")
    c_a = Claim(id="zh15a", text="監管 明朗 有助 市場 信心 大幅 提升", doc=doc_a)
    c_b = Claim(id="zh15b", text="監管 收緊 導致 市場 信心 大幅 下滑", doc=doc_b)

    corr = _corroboration(c_a, [c_a, c_b])
    assert corr == 0.0, f"中文反義主張不應被判為佐證，corr 應 = 0.0，實際: {corr}"


def test_genuine_synonym_support_not_falsely_blocked():
    """真同義支撐（正向案例）：具體詞同向 → 仍應正確判為佐證，不被矛盾閘誤殺。"""
    from trustforge.trust.scoring import Claim, _corroboration

    doc_a = _make_doc("da", "onchain", "glassnode")
    doc_b = _make_doc("db", "news", "coindesk")
    text_a = "Market analysts expect regulatory clarity to boost institutional adoption significantly"
    text_b = "Industry observers expect regulatory clarity to boost institutional adoption meaningfully"
    c_a = Claim(id="supa", text=text_a, doc=doc_a)
    c_b = Claim(id="supb", text=text_b, doc=doc_b)

    corr = _corroboration(c_a, [c_a, c_b])
    assert corr > 0.0, f"真同義（雙方皆談 clarity/adoption）應正確佐證，corr 應 > 0，實際: {corr}"


def test_double_negation_english_not_misjudged_as_contradiction():
    """雙重否定（CEO 追加）：『not without scrutiny』≈『其實有 scrutiny』。

    否定標記為偶數（ambiguous，parity 判斷）→ 不嘗試還原語意二次判斷方向，
    保守回 neutral；不可被誤判成『無 scrutiny』（單純看到 not/without 就翻轉方向）。
    """
    from trustforge.trust.stance import semantic_stance

    stance, evidence = semantic_stance(
        "monitoring regulatory clarity closely",
        "framework is not without scrutiny",
        {"clarity"}, {"scrutiny"},
    )
    assert stance == "neutral", f"雙重否定應保守判為 neutral，實際: {stance}（evidence={evidence}）"


def test_double_negation_chinese_not_misjudged_as_contradiction():
    """雙重否定中文對照：『並非沒有收緊』= 其實有收緊，偶數否定 → 保守 neutral。"""
    from trustforge.trust.stance import semantic_stance

    stance, evidence = semantic_stance(
        "監管 明朗",
        "監管 並非 沒有 收緊",
        {"明朗"}, {"收緊"},
    )
    assert stance == "neutral", f"中文雙重否定應保守判為 neutral，實際: {stance}（evidence={evidence}）"


def test_ambiguous_negation_falls_back_to_neutral_not_hard_verdict():
    """ambiguous（偶數個否定標記）不可被硬判為 support 或 contradict，一律保守回 neutral。

    唯一訊號來源是雙重否定的反義詞命中，且無其他共享詞干擾，驗證 evidence 亦為空。
    """
    from trustforge.trust.stance import semantic_stance

    stance, evidence = semantic_stance(
        "分析師 認為 監管 明朗",
        "報告 指出 監管 並非 沒有 收緊",
        {"明朗"}, {"收緊"},
    )
    assert stance == "neutral", f"ambiguous 否定應保守回 neutral，實際: {stance}"
    assert evidence == [], f"ambiguous 否定不應產生 evidence，實際: {evidence}"


def test_antonym_pairs_avoid_domain_drift_generic_words():
    """領域漂移守則（CEO 追加）：ANTONYM_PAIRS 只收金融/監管語境明確反義詞。

    不收 trust/doubt、growth/decline 這類多領域通用詞（例如 "trust" 在資安/科技
    語境常表示完全不同的意思，與市場信心無關）——對不確定的詞寧可不收，
    避免在非金融語境誤殺合法佐證。
    """
    from trustforge.trust.stance import ANTONYM_PAIRS

    all_words: set[str] = set()
    for group_x, group_y in ANTONYM_PAIRS:
        all_words |= group_x | group_y
    for risky_word in ("trust", "doubt", "growth", "decline", "confidence"):
        assert risky_word not in all_words, (
            f"'{risky_word}' 為多領域通用詞，不應收錄於 ANTONYM_PAIRS，避免領域漂移誤判"
        )


def test_score_components_has_new_corroboration_evidence_key():
    """score() 的 components dict 新增 corroboration_evidence（非替換），
    既有分項 key（reputation/corroboration/recency/manipulation）維持不變。
    """
    from trustforge.trust.scoring import extract_claims, score

    docs = [
        _doc("a", "news", "coindesk", "Market analysts expect regulatory clarity to boost institutional adoption significantly"),
        _doc("b", "news", "reuters", "Market observers expect regulatory scrutiny to boost investor caution significantly"),
    ]
    scored = score(extract_claims(docs), now=1.0)
    for sc in scored:
        assert {"reputation", "corroboration", "recency", "manipulation"} <= set(sc.components.keys())
        assert "corroboration_evidence" in sc.components
        assert isinstance(sc.components["corroboration_evidence"], list)


# --- W1 案2b 追加修正：中文單字「不」否定漏偵測（CEO 親測抓到的假 contradict）--------

def test_zh_single_negation_bu_cancels_antonym_hit():
    """『監管不明確』vs『監管收緊』：單字「不」否定「明確」，兩者其實同為偏空方向，
    不可判 contradict（修復前：_NEG_RX_ZH 漏收單字「不」，導致「明確」誤判命中，
    與「收緊」湊成假 contradict）。
    """
    from trustforge.trust.stance import semantic_stance
    from trustforge.trust.scoring import _normalize, DOMAIN_STOP

    a = "監管不明確 令人擔憂"
    b = "監管收緊 令人擔憂"
    ta = _normalize(a) - DOMAIN_STOP
    tb = _normalize(b) - DOMAIN_STOP
    stance, evidence = semantic_stance(a, b, ta, tb)
    assert stance != "contradict", f"單字「不」應取消「明確」命中，不可判 contradict，實際: {stance}（evidence={evidence}）"


def test_zh_bukanduo_not_false_contradict():
    """『不看多』vs『看空』：不看多 ≈ 看空，同向，不可判 contradict。"""
    from trustforge.trust.stance import semantic_stance
    from trustforge.trust.scoring import _normalize, DOMAIN_STOP

    a = "市場 情緒 不看多"
    b = "市場 情緒 看空"
    ta = _normalize(a) - DOMAIN_STOP
    tb = _normalize(b) - DOMAIN_STOP
    stance, evidence = semantic_stance(a, b, ta, tb)
    assert stance != "contradict", f"「不看多」應取消「看多」命中，不可判 contradict，實際: {stance}（evidence={evidence}）"


def test_zh_bujin_not_treated_as_negation():
    """『不僅監管明朗，且機構採用』vs『監管收緊 投資人觀望』：「不僅」是連接詞，
    不是否定「明朗」，仍應正確判為 contradict（不可被誤濾成 negated 而漏判矛盾）。
    """
    from trustforge.trust.stance import semantic_stance
    from trustforge.trust.scoring import _normalize, DOMAIN_STOP

    a = "不僅監管明朗，且機構採用"
    b = "監管收緊 投資人觀望"
    ta = _normalize(a) - DOMAIN_STOP
    tb = _normalize(b) - DOMAIN_STOP
    stance, evidence = semantic_stance(a, b, ta, tb)
    assert stance == "contradict", f"「不僅」不應被當否定詞，仍應判 contradict，實際: {stance}（evidence={evidence}）"
