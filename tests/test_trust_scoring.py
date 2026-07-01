"""信任提煉引擎核心測試。確保『信任層』行為符合設計意圖。"""
import pytest

from trustforge.ingestion.base import Document
from trustforge.trust.scoring import DOMAIN_STOP, aggregate, extract_claims, score

_W3_BURST_FOLLOWUP_SKIP_REASON = (
    "W3 burst 指標（指標 B）經 4 輪 codex 對抗審持續挖出新的 subtle 檢測缺陷"
    "（中位數自含候選自己/固定牆鐘分桶可繞/baseline 未對齊候選窗口/只評估各源"
    "最大窗漏掉後續同窗 baseline 偏低的小爆量），降級為 follow-up #15 重新設計，"
    "本輪 W3 只 ship 模板指標 A。程式碼保留（_coordination_burst_flags 等）供 "
    "#15 沿用，但 _coordination_signals 目前不呼叫，故暫時 skip 這些測試。"
)


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


# --- Tier2 可解釋 UX：_manipulation_flags 回溯測試 ------------------------

def test_manipulation_flags_traces_back_to_original_keywords():
    """flags 命中內容須可回溯到原文中的操縱關鍵詞（供 Evidence.flags 顯示）。"""
    from trustforge.trust.scoring import _manipulation_flags, Claim
    from trustforge.ingestion.base import Document
    d = Document(id="e", kind="social", source="x", text="", ts=1)
    c = Claim(id="y", text="BTC 暴漲翻倍穩賺，快上車！", doc=d)
    flags = _manipulation_flags(c)
    assert flags, "應命中至少一個操縱關鍵詞"
    # 每個 flag 都必須能在原文字串裡逐字找到（可回溯）
    for f in flags:
        assert f in c.text, f"flag {f!r} 無法回溯到原文 {c.text!r}"
    assert "暴漲" in flags
    assert "翻倍" in flags
    assert "快上車" in flags


def test_manipulation_flags_empty_when_no_hits():
    """無操縱關鍵詞命中時，flags 為空 list（不誤報）。"""
    from trustforge.trust.scoring import _manipulation_flags, Claim
    from trustforge.ingestion.base import Document
    d = Document(id="d", kind="news", source="coindesk", text="", ts=1)
    c = Claim(id="x", text="BTC 今日盤整，觀察後續動能。", doc=d)
    assert _manipulation_flags(c) == []


def test_manipulation_flags_respects_negation_gate_like_penalty():
    """否定守門對 flags 與 penalty 一致：『不會暴漲』不應被列入 flags。"""
    from trustforge.trust.scoring import _manipulation_flags, Claim
    from trustforge.ingestion.base import Document
    d = Document(id="d", kind="news", source="coindesk", text="", ts=1)
    neg = Claim(id="x", text="分析師認為 BTC 短期不會暴漲", doc=d)
    assert _manipulation_flags(neg) == []


def test_score_fills_scored_claim_manip_flags():
    """score() 應把命中的操縱關鍵詞填進 ScoredClaim.manip_flags，供 orchestrator 回填 Evidence.flags。"""
    docs = [_doc("b", "social", "x-anon", "穩賺 翻倍 to the moon！")]
    sc = score(extract_claims(docs), now=1.0)[0]
    assert sc.manip_flags, "命中操縱關鍵詞時 manip_flags 不應為空"
    for f in sc.manip_flags:
        assert f.lower() in sc.claim.text.lower()


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


# =========================================================================
# W2：truth-discovery 動態來源信譽（_iterate_source_reputation / dynamic_reputation）
# =========================================================================

def _shared_text_docs():
    """4 個不同 source、完全相同文本的 doc，overlap=1.0，用來讓每個 source 的獨立
    佐證來源數達到 MIN_INDEPENDENT_EVIDENCE(3)，跳脫小樣本守門、真正觸發迭代。"""
    shared = "大額 機構 資金 布局 現貨 ETF 通過 推升 市場 信心"
    return [
        _doc("w2-a", "onchain", "glassnode", shared),
        _doc("w2-b", "news", "coindesk", shared),
        _doc("w2-c", "regulatory", "sec-filing", shared),
        _doc("w2-d", "social", "x-analyst", shared),
    ]


def test_dynamic_reputation_default_off_byte_identical_to_legacy_call():
    """`dynamic_reputation` 不傳（預設 False）與明確傳 False，結果須逐字相同；
    且與『完全不知道這個參數存在』的舊呼叫方式（BTC 離線樣本）行為一致
    ——回歸鎖：W2 開發前既有呼叫端不用改一行程式碼、結果不受任何影響。
    """
    from trustforge.ingestion.base import OHLCV_DIR, collect

    docs = collect("BTC", coin="BTC", data_dir=OHLCV_DIR)
    now = max(d.ts for d in docs)
    claims = extract_claims(docs)

    legacy = score(claims, now=now)
    explicit_off = score(claims, now=now, dynamic_reputation=False)

    assert len(legacy) == len(explicit_off) == len(claims)
    for a, b in zip(legacy, explicit_off):
        assert a.claim.id == b.claim.id
        assert a.trust == b.trust, f"trust 不同：{a.trust} vs {b.trust}（回歸！）"
        assert a.components == b.components, "components 不同（回歸！）"
        assert a.reputation_trace is None, "dynamic_reputation=False 時 reputation_trace 應為 None"
        assert b.reputation_trace is None, "dynamic_reputation=False 時 reputation_trace 應為 None"


def test_source_reputation_dynamic_map_none_identical_to_legacy():
    """`_source_reputation(c, dynamic_map=None)` 逐字等同 `_source_reputation(c)`。"""
    from trustforge.trust.scoring import Claim, _source_reputation

    d = _doc("sr1", "news", "coindesk", "BTC 大漲")
    c = Claim(id="sr1#0", text="BTC 大漲", doc=d, direction="bullish")
    assert _source_reputation(c, dynamic_map=None) == _source_reputation(c)
    assert _source_reputation(c, dynamic_map=None) == 0.65  # news 基礎信譽


def test_source_reputation_dynamic_map_used_with_fallback():
    """`dynamic_map` 提供時優先採用；來源不在 map 中則回退先驗值（防禦性）。"""
    from trustforge.trust.scoring import Claim, _source_reputation

    d = _doc("sr2", "news", "coindesk", "BTC 大漲")
    c = Claim(id="sr2#0", text="BTC 大漲", doc=d, direction="bullish")
    assert _source_reputation(c, dynamic_map={"coindesk": 0.8}) == 0.8
    # 來源不在 map → 回退先驗（不 raise、不預設 0）
    assert _source_reputation(c, dynamic_map={"other-source": 0.9}) == 0.65


def test_reputation_floor_social_about_point_one():
    """CEO refinement：social 下限 ≈0.1，防止信譽蒸發。"""
    from trustforge.trust.scoring import _reputation_floor

    assert abs(_reputation_floor("social") - 0.105) < 1e-6
    # 客觀來源（onchain）floor 應明顯高於 social
    assert _reputation_floor("onchain") > _reputation_floor("social")


def test_iterate_source_reputation_deterministic_repeat():
    """同輸入連續 3 次呼叫，結果 bit-for-bit 相同（無隨機性）。"""
    from trustforge.trust.scoring import _iterate_source_reputation

    docs = _shared_text_docs()
    claims = extract_claims(docs)
    results = [_iterate_source_reputation(claims, now=1.0) for _ in range(3)]
    assert results[0] == results[1] == results[2], f"三次結果不同（非確定性！）：{results}"


def test_iterate_source_reputation_idempotent_past_k():
    """超過收斂輪數後再迭代不再變化（idempotent）：K=3 與 K=5 結果應相同
    （本場景在 K=2 已收斂，K=1 應與 K>=2 不同，證明迭代確實有作用）。"""
    from trustforge.trust.scoring import _iterate_source_reputation

    docs = _shared_text_docs()
    claims = extract_claims(docs)
    sr_k1 = _iterate_source_reputation(claims, now=1.0, iterations=1)
    sr_k3 = _iterate_source_reputation(claims, now=1.0, iterations=3)
    sr_k5 = _iterate_source_reputation(claims, now=1.0, iterations=5)
    assert sr_k3 == sr_k5, "K=3 與 K=5 應相同（已收斂，超過 K 不再變）"
    assert sr_k1 != sr_k3, "K=1（尚未收斂）理應與 K=3（已收斂）不同，證明迭代確實生效"


def test_iterate_source_reputation_hard_cap_five():
    """`iterations` 硬上限 5，即使呼叫端傳更大值也不會多跑（不放行超過 5 輪）。"""
    from trustforge.trust.scoring import _iterate_source_reputation

    docs = _shared_text_docs()
    claims = extract_claims(docs)
    sr_100 = _iterate_source_reputation(claims, now=1.0, iterations=100)
    sr_5 = _iterate_source_reputation(claims, now=1.0, iterations=5)
    assert sr_100 == sr_5, "iterations=100 應被硬上限 clamp 到 5，結果應與 iterations=5 相同"


def test_iterate_source_reputation_small_sample_gate_keeps_prior():
    """獨立佐證+矛盾來源聯集 < 3（小樣本）→ 該 source 強制 α=1，動態信譽應與先驗
    完全相同（不因少量樣本被炒高或壓低）。案例：只有 2 個獨立來源互相佐證。
    """
    from trustforge.trust.scoring import _iterate_source_reputation, _source_reputation

    shared = "大額 BTC 轉入 交易所 造成 賣壓 比特幣 下跌"
    docs = [
        _doc("sg-a", "onchain", "glassnode", shared),
        _doc("sg-b", "news", "coindesk", shared),
    ]
    claims = extract_claims(docs)
    sr = _iterate_source_reputation(claims, now=1.0)
    for c in claims:
        prior = _source_reputation(c)
        assert sr[c.doc.source] == prior, (
            f"{c.doc.source}：小樣本（<3 獨立佐證）應強制 α=1，維持先驗 {prior}，"
            f"實際: {sr[c.doc.source]}"
        )


def test_iterate_source_reputation_agreement_raises_reputation_bounded():
    """4 來源互相佐證（達到小樣本守門門檻）：信譽較低的來源（social, prior=0.35）
    因獨立佐證應上升，但幅度應合理（不翻倍/不失控），且反映到最終 trust 的差異
    落在 CEO 要求的 ±0.15 合理區間內。"""
    docs = _shared_text_docs()
    claims = extract_claims(docs)
    now = 1.0

    off = score(claims, now=now)
    on = score(claims, now=now, dynamic_reputation=True)
    by_id_off = {sc.claim.id: sc for sc in off}
    for sc in on:
        prior_trust = by_id_off[sc.claim.id].trust
        delta = sc.trust - prior_trust
        assert abs(delta) <= 0.15, (
            f"{sc.claim.doc.source}: on/off trust 差 {delta:.4f} 超出 ±0.15 合理區間"
        )
        assert sc.trust <= 1.0 and sc.trust >= 0.0

    social_sc = next(sc for sc in on if sc.claim.doc.source == "x-analyst")
    social_prior_trust = next(sc for sc in off if sc.claim.doc.source == "x-analyst").trust
    assert social_sc.trust > social_prior_trust, (
        "x-analyst（social, prior=0.35）獲 3 個獨立來源佐證，動態信譽應上升"
    )
    assert social_sc.trust < social_prior_trust * 2, "不可翻倍"
    assert social_sc.reputation_trace is not None
    assert social_sc.reputation_trace["agree_n"] == 3
    assert social_sc.reputation_trace["contradict_n"] == 0
    assert social_sc.reputation_trace["prior"] == 0.35
    assert social_sc.reputation_trace["final"] > 0.35


def test_iterate_source_reputation_contradiction_lowers_reputation_bounded_by_floor():
    """W1.5 stance 判矛盾時，agreement_score 應下拉該來源動態信譽（-1 訊號生效），
    但每輪 clamp 保證不低於 kind floor（不蒸發到 0）。"""
    from trustforge.trust.scoring import _iterate_source_reputation, _reputation_floor

    docs = _shared_text_docs()
    claims = extract_claims(docs)

    def _always_contradiction(a, b):
        return "contradiction"

    sr = _iterate_source_reputation(claims, now=1.0, stance_fn=_always_contradiction)
    for c in claims:
        floor = _reputation_floor(c.doc.kind)
        assert sr[c.doc.source] >= floor - 1e-9, (
            f"{c.doc.source} 信譽 {sr[c.doc.source]} 低於 floor {floor}（蒸發，回歸！）"
        )
    # 全面矛盾情境下，social 來源（prior 最低）動態信譽應低於先驗
    from trustforge.trust.scoring import _source_reputation
    social_claim = next(c for c in claims if c.doc.source == "x-analyst")
    assert sr["x-analyst"] < _source_reputation(social_claim), (
        "全面矛盾情境下，social 來源動態信譽應低於先驗（矛盾訊號生效）"
    )


def test_anti_spam_single_source_cannot_inflate_own_reputation():
    """反暴走：單一來源大量灌自己的 claims（無其他獨立來源佐證），不能自抬信譽——
    因為 agreement 需要「其他」獨立來源真的來佐證（複用既有反回音室設計，
    `_corroboration_detail` 本就排除同源），小樣本守門也會擋住（0 個外部佐證 <3）。
    """
    from trustforge.trust.scoring import _iterate_source_reputation, _source_reputation

    docs = [
        _doc(f"spam{i}", "social", "x-spammer", f"BTC 即將 大漲 機構 進場 第{i}輪 獨家消息", ts=1.0)
        for i in range(30)
    ]
    claims = extract_claims(docs)
    sr = _iterate_source_reputation(claims, now=1.0)
    prior = _source_reputation(claims[0])
    assert sr["x-spammer"] == prior, (
        f"單一來源自我灌水 30 筆 claims 不應自抬信譽，"
        f"prior={prior}, dynamic={sr['x-spammer']}"
    )


def test_score_reputation_iterations_hard_cap_via_public_api():
    """`score(..., reputation_iterations=100)` 不應 crash，且經 `_iterate_source_reputation`
    的硬上限 clamp，trace 記錄的 `iterations_run` 不應超過 5。"""
    docs = _shared_text_docs()
    claims = extract_claims(docs)
    on = score(claims, now=1.0, dynamic_reputation=True, reputation_iterations=100)
    for sc in on:
        assert sc.reputation_trace is not None
        assert sc.reputation_trace["iterations_run"] <= 5


def test_btc_eth_sol_offline_sample_on_off_bounded_no_double_no_zero():
    """CEO 要求：BTC/ETH/SOL 離線樣本 on/off 對照，trust 差在 ±0.15 合理區間內，
    不翻倍、不歸零。（離線樣本各來源間高重疊-跨來源主張稀少，多數來源獨立佐證
    <3 觸發小樣本守門、維持先驗——這正是設計上刻意保守的安全預設，見下方
    `test_iterate_source_reputation_agreement_raises_reputation_bounded` 驗證迭代
    本身在有充分證據時確實會生效。）
    """
    from trustforge.ingestion.base import OHLCV_DIR, collect

    for coin in ("BTC", "ETH", "SOL"):
        docs = collect(coin, coin=coin, data_dir=OHLCV_DIR)
        now = max(d.ts for d in docs)
        claims = extract_claims(docs)
        off = score(claims, now=now)
        on = score(claims, now=now, dynamic_reputation=True)
        assert len(off) == len(on)
        for a, b in zip(off, on):
            assert a.claim.id == b.claim.id
            delta = b.trust - a.trust
            assert abs(delta) <= 0.15, f"{coin} {a.claim.doc.source}: delta={delta:.4f} 超出 ±0.15"
            if a.trust > 0.05:
                assert b.trust > 0.0, f"{coin} {a.claim.doc.source}: trust 被歸零（回歸！）"
            if a.trust > 0.01:
                assert b.trust <= a.trust * 2 + 1e-9, f"{coin} {a.claim.doc.source}: trust 翻倍以上"


# --- codex 對抗審修正（PR #29 review，[HIGH-1] 重複計票 / [HIGH-2] 溢位）--------

def test_stable_sigmoid_no_overflow_at_extreme_net():
    """[HIGH-2] `_stable_sigmoid` 在極端 net 值下不應 raise（純 `math.exp(-net)`
    在 |net| 夠大時會直接 OverflowError，這裡驗證 clamp 後的版本不會）。"""
    import math

    import pytest

    from trustforge.trust.scoring import _stable_sigmoid

    with pytest.raises(OverflowError):
        math.exp(2000)  # 先證明「不 clamp 就會炸」，證明修法確實必要

    for net in (2000.0, -2000.0, 1e15, -1e15, 0.0, 30.0, -30.0):
        v = _stable_sigmoid(net)
        assert 0.0 <= v <= 1.0
    assert _stable_sigmoid(2000.0) > 0.999
    assert _stable_sigmoid(-2000.0) < 0.001
    assert _stable_sigmoid(0.0) == 0.5


def test_duplicate_corroborated_claim_does_not_inflate_reputation():
    """[HIGH-1] 同一來源把「已有 3 個固定外部佐證的 claim」重複貼 1 次 vs 20 次，
    動態信譽必須完全相同（重複貼文不可放大票數、繞過反暴走）。"""
    from trustforge.trust.scoring import _iterate_source_reputation

    shared = "大額 機構 資金 布局 現貨 ETF 通過 推升 市場 信心"
    fixed_external = [
        _doc("dup-ext-a", "onchain", "glassnode", shared),
        _doc("dup-ext-b", "news", "coindesk", shared),
        _doc("dup-ext-c", "regulatory", "sec-filing", shared),
    ]

    docs_once = fixed_external + [_doc("dup-x-1", "social", "x-analyst", shared)]
    docs_20x = fixed_external + [
        _doc(f"dup-x-{i}", "social", "x-analyst", shared) for i in range(20)
    ]

    claims_once = extract_claims(docs_once)
    claims_20x = extract_claims(docs_20x)

    sr_once = _iterate_source_reputation(claims_once, now=1.0)
    sr_20x = _iterate_source_reputation(claims_20x, now=1.0)

    assert sr_once["x-analyst"] == sr_20x["x-analyst"], (
        "重複貼同一已佐證 claim 20 次不應放大信譽："
        f"1 次={sr_once['x-analyst']}, 20 次={sr_20x['x-analyst']}"
    )
    # 確認不是被小樣本守門「意外擋掉」造成的假陽性——這裡應確實吃到佐證加成
    assert sr_once["x-analyst"] > 0.35, "應確實吃到 3 個獨立佐證的加成（非小樣本守門情境）"
    # 順便驗證固定外部來源的信譽也不因 x-analyst 重複貼文而被放大
    assert sr_once["glassnode"] == sr_20x["glassnode"]
    assert sr_once["coindesk"] == sr_20x["coindesk"]
    assert sr_once["sec-filing"] == sr_20x["sec-filing"]


def test_duplicate_corroborated_claim_does_not_inflate_reputation_via_public_api():
    """同上，但走 `score(..., dynamic_reputation=True)` 公開 API 端到端驗證。"""
    shared = "大額 機構 資金 布局 現貨 ETF 通過 推升 市場 信心"
    fixed_external = [
        _doc("dup2-ext-a", "onchain", "glassnode", shared),
        _doc("dup2-ext-b", "news", "coindesk", shared),
        _doc("dup2-ext-c", "regulatory", "sec-filing", shared),
    ]
    docs_once = fixed_external + [_doc("dup2-x-1", "social", "x-analyst", shared)]
    docs_20x = fixed_external + [
        _doc(f"dup2-x-{i}", "social", "x-analyst", shared) for i in range(20)
    ]

    on_once = score(extract_claims(docs_once), now=1.0, dynamic_reputation=True)
    on_20x = score(extract_claims(docs_20x), now=1.0, dynamic_reputation=True)

    final_once = next(sc for sc in on_once if sc.claim.doc.source == "x-analyst").reputation_trace["final"]
    final_20x = next(sc for sc in on_20x if sc.claim.doc.source == "x-analyst").reputation_trace["final"]
    assert final_once == final_20x, (
        f"公開 API 層級：1 次 vs 20 次重複貼文的動態信譽應相同，"
        f"實際: {final_once} vs {final_20x}"
    )


def test_large_scale_contradiction_score_does_not_crash_bounded():
    """[HIGH-2] 壓力測試：目標來源被 500 個獨立來源同時判定矛盾，
    `score(dynamic_reputation=True)` 不應 crash（OverflowError 或其他例外），
    且動態信譽仍落在 `[floor, 1]` 範圍內。"""
    shared = "大額 機構 資金 布局 現貨 ETF 通過 推升 市場 信心"
    target_doc = _doc("big-target", "social", "x-target", shared)
    contra_docs = [
        _doc(f"big-contra-{i}", "news", f"contra-source-{i}", shared) for i in range(500)
    ]
    docs = [target_doc] + contra_docs
    claims = extract_claims(docs)

    class _AlwaysContradictClient:
        def classify_stance(self, a, b):
            return "contradiction"

    from trustforge.trust.scoring import _reputation_floor

    scored = score(
        claims,
        now=1.0,
        dynamic_reputation=True,
        stance_client=_AlwaysContradictClient(),
        stance_pair_budget=10_000,
    )
    assert len(scored) == len(claims)
    for sc in scored:
        assert sc.reputation_trace is not None
        floor = _reputation_floor(sc.claim.doc.kind)
        final = sc.reputation_trace["final"]
        assert floor - 1e-9 <= final <= 1.0 + 1e-9, (
            f"{sc.claim.doc.source}: final={final} 超出 [{floor},1] 範圍"
        )
        assert 0.0 <= sc.trust <= 1.0


# codex 對抗審修正（第 2 輪 HIGH，PR #29 review，票權聚合去重）


def test_duplicate_high_trust_claim_heterogeneous_all_sources_sr_equal_across_rounds():
    """[第 2 輪 HIGH] 異質 trust 場景：同一來源同時貼一條「高 trust」（有 3 個
    固定外部佐證）與一條「低 trust」（操縱語氣、無佐證）claim。把高 trust 那條
    重複 1 次 vs 20 次——`avg_temp_by_source` 去重後，所有來源（不只攻擊來源
    自己）在 iterations=1~5 每一輪的最終 SR 皆應完全相等，證明 claim 重複次數
    對整個系統的迭代結果沒有任何影響（同文本、同 trust 的舊測試測不出這個
    bug，需要異質 trust 才會暴露）。"""
    from trustforge.trust.scoring import _iterate_source_reputation

    high_trust_text = "大額 機構 資金 布局 現貨 ETF 通過 推升 市場 信心"
    low_trust_text = "BTC 馬上翻倍 moon 穩賺快上車！"

    fixed_external = [
        _doc("het-ext-a", "onchain", "glassnode", high_trust_text),
        _doc("het-ext-b", "news", "coindesk", high_trust_text),
        _doc("het-ext-c", "regulatory", "sec-filing", high_trust_text),
    ]

    docs_once = fixed_external + [
        _doc("het-x-high-1", "social", "x-analyst", high_trust_text),
        _doc("het-x-low-1", "social", "x-analyst", low_trust_text),
    ]
    docs_20x = (
        fixed_external
        + [_doc(f"het-x-high-{i}", "social", "x-analyst", high_trust_text) for i in range(20)]
        + [_doc("het-x-low-1", "social", "x-analyst", low_trust_text)]
    )

    claims_once = extract_claims(docs_once)
    claims_20x = extract_claims(docs_20x)

    for k in range(1, 6):
        sr_once = _iterate_source_reputation(claims_once, now=1.0, iterations=k)
        sr_20x = _iterate_source_reputation(claims_20x, now=1.0, iterations=k)
        assert sr_once.keys() == sr_20x.keys(), f"iterations={k}: 來源集合不一致"
        for s in sr_once:
            assert sr_once[s] == sr_20x[s], (
                f"iterations={k}, source={s}: 高 trust claim 重複 1 次 vs 20 次的 SR 不相等："
                f"{sr_once[s]} vs {sr_20x[s]}"
            )

    # 確認不是被小樣本守門「意外擋掉」造成的假陽性——x-analyst 應確實吃到高 trust
    # claim 的佐證加成，也吃到低 trust claim 的操縱懲罰（介於 floor 與 1 之間，
    # 不是原封不動的先驗值）。
    sr5_once = _iterate_source_reputation(claims_once, now=1.0, iterations=5)
    assert 0.1 < sr5_once["x-analyst"] < 1.0


def test_duplicate_high_trust_claim_heterogeneous_via_public_api():
    """同上，但走 `score(..., dynamic_reputation=True)` 公開 API 端到端驗證。"""
    high_trust_text = "大額 機構 資金 布局 現貨 ETF 通過 推升 市場 信心"
    low_trust_text = "BTC 馬上翻倍 moon 穩賺快上車！"

    fixed_external = [
        _doc("het2-ext-a", "onchain", "glassnode", high_trust_text),
        _doc("het2-ext-b", "news", "coindesk", high_trust_text),
        _doc("het2-ext-c", "regulatory", "sec-filing", high_trust_text),
    ]

    docs_once = fixed_external + [
        _doc("het2-x-high-1", "social", "x-analyst", high_trust_text),
        _doc("het2-x-low-1", "social", "x-analyst", low_trust_text),
    ]
    docs_20x = (
        fixed_external
        + [_doc(f"het2-x-high-{i}", "social", "x-analyst", high_trust_text) for i in range(20)]
        + [_doc("het2-x-low-1", "social", "x-analyst", low_trust_text)]
    )

    on_once = score(extract_claims(docs_once), now=1.0, dynamic_reputation=True)
    on_20x = score(extract_claims(docs_20x), now=1.0, dynamic_reputation=True)

    for source in {sc.claim.doc.source for sc in on_once}:
        finals_once = {
            sc.reputation_trace["final"] for sc in on_once if sc.claim.doc.source == source
        }
        finals_20x = {
            sc.reputation_trace["final"] for sc in on_20x if sc.claim.doc.source == source
        }
        assert finals_once == finals_20x, (
            f"公開 API 層級 source={source}: 1 次 vs 20 次重複貼文的動態信譽應相同，"
            f"實際: {finals_once} vs {finals_20x}"
        )


# codex 對抗審修正（第 4 輪 HIGH，PR #29 review，跨 process 確定性）


def test_iterate_source_reputation_deterministic_across_pythonhashseed():
    """[第 4 輪 HIGH] `agree_union_of`/`contra_union_of` 是 set，其迭代順序受
    `PYTHONHASHSEED` 影響；配合浮點加法無結合律，理論上同一輸入在不同 process
    可能得到不同的 net/agreement_score/SR，甚至跨過
    `REPUTATION_CONVERGENCE_EPS` 影響收斂輪數。用兩個不同的 `PYTHONHASHSEED`
    （0 與 1）各起一個子 process 跑同一份輸入的 `score(dynamic_reputation=True)`，
    斷言完整 SR（`trust`）與 `reputation_trace` 逐字相等（bit-for-bit）。"""
    import json
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"

    script = """
import json
from trustforge.ingestion.base import Document
from trustforge.trust.scoring import extract_claims, score


def _doc(id, kind, source, text, ts=1.0):
    return Document(id=id, kind=kind, source=source, text=text, ts=ts)


shared = "大額 機構 資金 布局 現貨 ETF 通過 推升 市場 信心"
docs = [
    _doc("ph-a", "onchain", "glassnode", shared),
    _doc("ph-b", "news", "coindesk", shared),
    _doc("ph-c", "regulatory", "sec-filing", shared),
    _doc("ph-d", "hoyabit", "hoyabit-x", shared),
    _doc("ph-e", "social", "x-analyst", shared),
    _doc("ph-f", "news", "bloomberg", shared),
    _doc("ph-g", "onchain", "nansen", shared),
]
claims = extract_claims(docs)
scored = score(claims, now=1.0, dynamic_reputation=True)
out = {
    sc.claim.id: {"trust": sc.trust, "reputation_trace": sc.reputation_trace}
    for sc in scored
}
print(json.dumps(out, sort_keys=True))
"""

    def _run_with_seed(seed: str) -> dict:
        env = {"PYTHONHASHSEED": seed, "PATH": __import__("os").environ.get("PATH", "")}
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(repo_root),
            env={**env, "PYTHONPATH": str(src_dir)},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"PYTHONHASHSEED={seed} 子程序執行失敗：\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        return json.loads(result.stdout)

    out_seed0 = _run_with_seed("0")
    out_seed1 = _run_with_seed("1")
    out_seed42 = _run_with_seed("42")

    assert out_seed0 == out_seed1, (
        "PYTHONHASHSEED=0 與 PYTHONHASHSEED=1 下的 SR/reputation_trace 不相等：\n"
        f"seed=0: {out_seed0}\nseed=1: {out_seed1}"
    )
    assert out_seed0 == out_seed42, (
        "PYTHONHASHSEED=0 與 PYTHONHASHSEED=42 下的 SR/reputation_trace 不相等：\n"
        f"seed=0: {out_seed0}\nseed=42: {out_seed42}"
    )


# =========================================================================
# W3：確定性協同操縱偵測（_coordination_signals / 指標 A 模板相似 / 指標 B 單源爆量）
# =========================================================================

_BURST_BASE_TS = 1780185600.0  # 對齊 demo/sample_data 慣用的樣本時戳，避免魔法數字


def test_w3_a_synonym_template_flood_triggers_and_evades_regex():
    """指標 A：3 個假來源用近義詞置換（換詞不換意）灌水同一套話術。

    刻意選用完全不在 `_MANIP_PATTERNS`（to moon/暴漲/翻倍/shill/喊單/穩賺/
    financial advice/pump/快上車/百倍）內的字眼（起飛/十倍/情報），證明舊版
    regex 對這種灌水完全失效（`_manip_hits` 全部命中 0），而 W3 協同指標仍能
    靠 3 個不同來源間 ≥0.8 的模板 Jaccard 相似度抓到——**但這只是 informational
    flag（見下方「資訊:」前綴），不代表已判定操縱，不扣信任分**（CEO 定案，
    見 `test_w3_a_informational_only_does_not_penalize_trust_and_flows_into_info_flags`）。
    """
    from trustforge.trust.scoring import Claim, _coordination_signals, _manip_hits

    texts = {
        "x-shill-a": "內幕 情報 XYZ幣 即將 起飛 十倍 現在 機會 難得 進場",
        "x-shill-b": "內幕 情報 XYZ幣 即將 起飛 十倍 現在 機會 難得 買入",
        "x-shill-c": "內幕 情報 XYZ幣 即將 起飛 十倍 現在 機會 難得 加碼",
    }
    claims = [
        Claim(id=f"w3a-{src}", text=t, doc=_doc(f"d-{src}", "social", src, t, ts=_BURST_BASE_TS))
        for src, t in texts.items()
    ]

    # 回歸證據：舊版關鍵詞 regex 對這批「換詞不換意」文字完全繞過（0 命中）。
    for c in claims:
        assert _manip_hits(c.text) == [], f"{c.doc.source} 不應命中任何 _MANIP_PATTERNS 關鍵詞"

    flags = _coordination_signals(claims)
    assert set(flags.keys()) == {c.id for c in claims}, "3 個來源都應被標記模板相似 informational flag"
    for c in claims:
        fl = flags[c.id]
        assert len(fl) == 1
        flag = fl[0]
        assert flag.startswith("資訊:多源文字高度相似(")
        # 措辭中性：只允許「可能協同或聯播」這種並列可能性描述，不可用「協同:」
        # 這種直接指控前綴（前綴已由上面 startswith 斷言把關）。
        # 可回溯：flag 內必須列出涉入的 3 個來源與 Jaccard 數值
        assert "x-shill-a" in flag and "x-shill-b" in flag and "x-shill-c" in flag
        assert "Jaccard 0.8" in flag


def test_w3_a_informational_only_does_not_penalize_trust_and_flows_into_info_flags():
    """CEO 定案（codex 對抗審確認根本限制）：文字相似度單獨無法區分「協同操縱」
    vs「合法聯播/引用」，指標 A 命中改為 informational-only，應：
    (1) **不扣信任分**——`components["manipulation"]` 必須與「完全沒有協同訊號時」
        逐位元相等，斷言 manip 分量不含模板貢獻（不是只驗證某個 `>=`/`<=`
        方向，而是直接跟 `_manipulation_penalty(c)`（無 extra_hits）比對相等）；
    (2) flag 併入 `ScoredClaim.info_flags`（供 `agent.orchestrator._scored_to_evidence`
        回填 `Evidence.info_flags`，web 用中性樣式顯示，不是操縱🚩紅旗）；
    (3) **不**混入 `ScoredClaim.manip_flags`（那是操縱紅旗專用，維持只裝
        regex 關鍵詞命中）。
    """
    from trustforge.trust.scoring import _manipulation_penalty

    texts = {
        "x-shill-a": "內幕 情報 XYZ幣 即將 起飛 十倍 現在 機會 難得 進場",
        "x-shill-b": "內幕 情報 XYZ幣 即將 起飛 十倍 現在 機會 難得 買入",
        "x-shill-c": "內幕 情報 XYZ幣 即將 起飛 十倍 現在 機會 難得 加碼",
    }
    docs = [_doc(f"d-{src}", "social", src, t, ts=_BURST_BASE_TS) for src, t in texts.items()]
    claims = extract_claims(docs)
    scored = score(claims, now=_BURST_BASE_TS)

    for sc in scored:
        # 模板相似「應該」命中（否則這條測試沒測到東西）：info_flags 非空。
        assert any(f.startswith("資訊:多源文字高度相似(") for f in sc.info_flags), (
            f"{sc.claim.doc.source} 的 info_flags 應含模板相似 informational flag，"
            f"實際: {sc.info_flags}"
        )
        # 但完全不扣分：manipulation 分量必須跟「不考慮任何協同訊號」時的
        # _manipulation_penalty(c) 逐位元相等，不是「還在合理範圍」的模糊比較。
        no_signal_manip = _manipulation_penalty(sc.claim)
        assert sc.components["manipulation"] == no_signal_manip, (
            f"{sc.claim.doc.source} 的 manipulation 分量不應含模板相似貢獻："
            f"實際 {sc.components['manipulation']}，無協同訊號應為 {no_signal_manip}"
        )
        # 且不混入操縱紅旗清單：manip_flags 只該有 regex 關鍵詞命中（本例無）。
        assert sc.manip_flags == [], (
            f"{sc.claim.doc.source} 的 manip_flags 不應含模板相似 flag（那應在 "
            f"info_flags），實際: {sc.manip_flags}"
        )


def test_w3_a_two_source_verbatim_wire_repost_not_flagged():
    """防呆：2 家媒體逐字轉載同一份官方通稿（只有 2 個來源，即使 Jaccard=1.0）
    不應觸發協同 flag——避免正常的通稿轉載被誤判為協同操縱。"""
    from trustforge.trust.scoring import Claim, _coordination_signals

    official = "歐盟 監管 機關 發布 加密 資產 新規 業者 需 於 六個 月 內 完成 合規 申報"
    c1 = Claim(id="wire-1", text=official, doc=_doc("d1", "news", "reuters", official, ts=_BURST_BASE_TS))
    c2 = Claim(id="wire-2", text=official, doc=_doc("d2", "news", "apnews", official, ts=_BURST_BASE_TS))

    flags = _coordination_signals([c1, c2])
    assert flags == {}, f"2 家轉載同一通稿不應觸發協同 flag，實際: {flags}"


def test_w3_a_three_plus_news_outlets_verbatim_wire_repost_not_flagged():
    """對抗性回歸（codex 對抗審 [HIGH]）：3 家以上新聞媒體逐字/近似轉載同一份
    官方通稿——即使涉及來源數 ≥ `_TEMPLATE_MIN_SOURCES`（3），過去只靠
    Jaccard 門檻判斷會誤標為協同操縱，因為確定性相似度分數本身分不清
    「合法通稿聯播」與「協同灌水」。修正後 `kind="news"` 不在
    `_TEMPLATE_ELIGIBLE_KINDS`（只有 social/sentiment）內，整批直接排除在
    模板比對之外，不應觸發任何協同 flag。"""
    from trustforge.trust.scoring import Claim, _coordination_signals

    texts = {
        "reuters": "歐盟 監管 機關 發布 加密 資產 新規 業者 需 於 六個 月 內 完成 合規 申報",
        "apnews": "歐盟 監管 機關 發布 加密 資產 新規 業者 需 於 六個 月 內 完成 合規 申報",
        "bloomberg": "歐盟 監管 機關 發布 加密 資產 新規 業者 需 於 六個 月 內 完成 合規 申報",
        "coindesk": "歐盟 監管 機關 發布 加密 資產 新規 業者 需 於 六個 月 內 完成 合規 申報",
    }
    claims = [
        Claim(id=f"wire3-{src}", text=t, doc=_doc(f"d-{src}", "news", src, t, ts=_BURST_BASE_TS))
        for src, t in texts.items()
    ]
    flags = _coordination_signals(claims)
    assert flags == {}, (
        f"≥3 家新聞媒體轉載同一通稿（kind=news）應因豁免清單不觸發協同 flag，實際: {flags}"
    )


def test_w3_a_objective_kinds_exempt_from_template_matching():
    """防呆：只有 `_TEMPLATE_ELIGIBLE_KINDS`（social/sentiment）才納入模板比對，
    客觀事實類（price/price_live/onchain/regulatory/hoyabit）一律跳過——即使
    3 個 onchain 來源文字高度相似（客觀數據本來就該長得像），也不應被誤判為
    協同操縱。"""
    from trustforge.trust.scoring import Claim, _coordination_signals

    texts = {
        "glassnode": "鏈上 數據 顯示 BTC 交易所 餘額 過去 24 小時 減少 一 萬 枚",
        "nansen": "鏈上 數據 顯示 BTC 交易所 餘額 過去 24 小時 減少 一點一 萬 枚",
        "cryptoquant": "鏈上 數據 顯示 BTC 交易所 餘額 過去 24 小時 減少 一點二 萬 枚",
    }
    claims = [
        Claim(id=f"obj-{src}", text=t, doc=_doc(f"d-{src}", "onchain", src, t, ts=_BURST_BASE_TS))
        for src, t in texts.items()
    ]
    flags = _coordination_signals(claims)
    assert flags == {}, f"onchain 不在 _TEMPLATE_ELIGIBLE_KINDS 內，不應納入模板比對，實際: {flags}"


def test_w3_a_regulatory_kind_exempt_from_template_matching():
    """對抗性回歸（codex 對抗審 [HIGH] 擴大豁免清單）：`kind="regulatory"`
    現已明確排除在 `_TEMPLATE_ELIGIBLE_KINDS` 之外，3 個官方監管來源公告
    高度相似（監管口徑本來就該一致）不應被誤判為協同操縱。"""
    from trustforge.trust.scoring import Claim, _coordination_signals

    texts = {
        "sec-gov": "證券 主管機關 公告 加密貨幣 交易所 需 完成 牌照 申請 方可 營運",
        "fca-uk": "證券 主管機關 公告 加密貨幣 交易所 需 完成 牌照 申請 方可 繼續 營運",
        "mas-sg": "證券 主管機關 公告 加密貨幣 交易所 需 完成 牌照 申請 才可 營運",
    }
    claims = [
        Claim(id=f"reg-{src}", text=t, doc=_doc(f"d-{src}", "regulatory", src, t, ts=_BURST_BASE_TS))
        for src, t in texts.items()
    ]
    flags = _coordination_signals(claims)
    assert flags == {}, f"regulatory 不在 _TEMPLATE_ELIGIBLE_KINDS 內，不應納入模板比對，實際: {flags}"


def test_w3_a_three_social_sources_template_flood_still_triggers():
    """對抗性回歸（收斂驗證）：豁免清單擴大後，`kind="social"` 的模板相似
    偵測仍必須正常觸發——確認收斂修法沒有連社群相似偵測本身也一起弱化。

    CEO 定案後（informational-only），「觸發」只代表產生中性 informational
    flag 供人工判讀，**不代表判定操縱、不扣分**——見下方 `score()` 層驗證。
    """
    from trustforge.trust.scoring import Claim, _coordination_signals, _manipulation_penalty

    texts = {
        "tg-shill-a": "重磅 消息 ABC幣 馬上 噴發 十倍 機會 現在 立刻 馬上 卡位",
        "tg-shill-b": "重磅 消息 ABC幣 馬上 噴發 十倍 機會 現在 立刻 馬上 進場",
        "tg-shill-c": "重磅 消息 ABC幣 馬上 噴發 十倍 機會 現在 立刻 馬上 加倉",
    }
    docs = [_doc(f"d-{src}", "social", src, t, ts=_BURST_BASE_TS) for src, t in texts.items()]
    claims = [
        Claim(id=f"soc-{src}", text=t, doc=doc)
        for (src, t), doc in zip(texts.items(), docs)
    ]
    flags = _coordination_signals(claims)
    assert set(flags.keys()) == {c.id for c in claims}, (
        f"3 個 social 來源模板相似仍應觸發 informational flag，實際: {flags}"
    )
    for c in claims:
        assert flags[c.id][0].startswith("資訊:多源文字高度相似(")

    # 供人工判讀，但不扣分：走完整 score() 確認 info_flags 有值、manipulation
    # 分量跟無協同訊號時逐位元相等。
    all_claims = extract_claims(docs)
    scored = score(all_claims, now=_BURST_BASE_TS)
    for sc in scored:
        assert any(f.startswith("資訊:多源文字高度相似(") for f in sc.info_flags)
        assert sc.components["manipulation"] == _manipulation_penalty(sc.claim)


@pytest.mark.skip(reason=_W3_BURST_FOLLOWUP_SKIP_REASON)
def test_w3_b_single_source_burst_triggers_and_baseline_untouched():
    """指標 B：單一來源在 60 分鐘窗口內連發 8 則相異主張（10 分鐘內），
    相對全池同窗口中位數（=1）達 8 倍 → 觸發爆量 flag；同窗口內正常的 3 個
    單則來源不應被誤傷。"""
    from trustforge.trust.scoring import Claim, _coordination_signals

    claims = []
    for i in range(8):
        t = f"快訊{i} XYZ幣 突破 關鍵 價位 值得 留意 第{i}則"
        claims.append(
            Claim(id=f"burst-{i}", text=t,
                  doc=_doc(f"burst-doc-{i}", "social", "x-spammer", t, ts=_BURST_BASE_TS + i * 60))
        )
    for j, src in enumerate(["news-a", "news-b", "news-c"]):
        t = f"正常報導{j} 市場 觀察 淡定"
        claims.append(
            Claim(id=f"base-{j}", text=t,
                  doc=_doc(f"base-doc-{j}", "news", src, t, ts=_BURST_BASE_TS + 30))
        )

    flags = _coordination_signals(claims)

    for i in range(8):
        fl = flags.get(f"burst-{i}")
        assert fl, f"burst-{i} 應被標記單源爆量"
        flag = fl[0]
        assert flag.startswith("協同:單源爆量(")
        assert "x-spammer" in flag
        assert "8則" in flag
        assert "8.0倍" in flag

    for j in range(3):
        assert flags.get(f"base-{j}") is None, "正常單則來源不應被爆量指標誤傷"


@pytest.mark.skip(reason=_W3_BURST_FOLLOWUP_SKIP_REASON)
def test_w3_b_repeated_identical_text_does_not_count_as_burst():
    """防呆／回歸鎖：同一來源在同一窗口內重複貼「逐字相同」的文本 N 次，
    不應被判定為爆量——與 `_iterate_source_reputation` 的
    `unique_claims_by_source` 去重原則一致（見既有 W2 反暴走測試：同一 claim
    重貼 1 次 vs 20 次，動態信譽必須逐字相同）。若本指標對逐字重複的文本也
    計數，會與既有 W2 回歸鎖互相打架。"""
    from trustforge.trust.scoring import Claim, _coordination_burst_flags

    shared = "大額 機構 資金 布局 現貨 ETF 通過 推升 市場 信心"
    claims = [
        Claim(id=f"dup-{i}", text=shared,
              doc=_doc(f"dup-doc-{i}", "social", "x-analyst", shared, ts=_BURST_BASE_TS))
        for i in range(20)
    ] + [
        Claim(id="other-1", text="其他 來源 的 一則 主張",
              doc=_doc("other-doc-1", "news", "coindesk", "其他 來源 的 一則 主張", ts=_BURST_BASE_TS)),
        Claim(id="other-2", text="第二個 其他 來源 主張",
              doc=_doc("other-doc-2", "news", "bloomberg", "第二個 其他 來源 主張", ts=_BURST_BASE_TS)),
    ]

    flags = _coordination_burst_flags(claims)
    assert flags == {}, f"逐字重複同一文本 20 次不應觸發爆量，實際: {flags}"


@pytest.mark.skip(reason=_W3_BURST_FOLLOWUP_SKIP_REASON)
def test_w3_burst_window_requires_at_least_two_sources():
    """防呆：窗口內只有單一來源時（無從比較），不應觸發爆量（避免用「只有
    自己」當分母誤判）。"""
    from trustforge.trust.scoring import Claim, _coordination_burst_flags

    claims = [
        Claim(id=f"solo-{i}", text=f"訊息{i} 內容 各不相同 第{i}篇",
              doc=_doc(f"solo-doc-{i}", "social", "x-only",
                       f"訊息{i} 內容 各不相同 第{i}篇", ts=_BURST_BASE_TS + i * 30))
        for i in range(10)
    ]
    flags = _coordination_burst_flags(claims)
    assert flags == {}, "整個資料池只有 1 個來源時不應觸發爆量（無從比較）"


@pytest.mark.skip(reason=_W3_BURST_FOLLOWUP_SKIP_REASON)
def test_w3_b_two_source_flood_vs_normal_detected_via_leave_one_out_median():
    """對抗性回歸（codex [HIGH]）：只有 2 個來源時，若中位數誤含候選自己會
    造成數學上偵測不到——例如 (100, 1) 兩來源，`median([100,1])=50.5`，
    `100 > 3×50.5=151.5` 為 False，灌水來源逃脫。改為 leave-one-out 中位數
    （排除候選自己）後，對照組退化為單一來源本身的值：候選=灌水源時，
    其餘來源中位數=1，`8 > 3×1` 成立而觸發；候選=正常源時，其餘來源中位數
    =8，`1 > 3×8` 不成立而不觸發。"""
    from trustforge.trust.scoring import Claim, _coordination_burst_flags

    claims = []
    for i in range(8):
        t = f"急報{i} ABC幣 即將 暴衝 提前 卡位 第{i}條"
        claims.append(
            Claim(id=f"flood-{i}", text=t,
                  doc=_doc(f"flood-doc-{i}", "social", "x-flooder", t, ts=_BURST_BASE_TS + i * 60))
        )
    claims.append(
        Claim(id="normal-1", text="單純 觀察 市場 動態",
              doc=_doc("normal-doc-1", "news", "solo-outlet", "單純 觀察 市場 動態", ts=_BURST_BASE_TS))
    )

    flags = _coordination_burst_flags(claims)

    for i in range(8):
        fl = flags.get(f"flood-{i}")
        assert fl, f"2 來源情境下 flood-{i} 仍應被偵測到單源爆量，實際: {flags}"
        assert "x-flooder" in fl[0]
        assert "8則" in fl[0]

    assert flags.get("normal-1") is None, "正常單則來源不應被誤傷"


@pytest.mark.skip(reason=_W3_BURST_FOLLOWUP_SKIP_REASON)
def test_w3_b_burst_spanning_hour_boundary_detected_via_rolling_window():
    """對抗性回歸（codex [MEDIUM]）：爆量橫跨牆鐘整點（xx:59:30 ~
    xx+1:00:45，全部在 75 秒內），若用固定 `int(ts // 3600)` 分桶會被切成
    「整點前」「整點後」兩個各自低於門檻的子群而逃脫；改為依 ts 排序後
    雙指標(two-pointer)找任一 60 分鐘滾動窗內的最大相異文本數，應能正確
    偵測到橫跨整點的完整爆量。"""
    from trustforge.trust.scoring import Claim, _coordination_burst_flags

    hour_boundary = (int(_BURST_BASE_TS // 3600) + 1) * 3600  # 對齊到下一個整點
    offsets = [-30, -15, 0, 15, 30, 45]  # xx:59:30 ~ xx+1:00:45，橫跨整點，全在 75 秒內
    claims = []
    for i, off in enumerate(offsets):
        t = f"整點突襲{i} DEF幣 快訊 搶先 布局 第{i}波"
        claims.append(
            Claim(id=f"boundary-{i}", text=t,
                  doc=_doc(f"boundary-doc-{i}", "social", "x-boundary-flooder", t,
                           ts=hour_boundary + off))
        )
    claims.append(
        Claim(id="normal-2", text="平靜 觀望 中",
              doc=_doc("normal-doc-2", "news", "quiet-outlet", "平靜 觀望 中", ts=hour_boundary))
    )

    flags = _coordination_burst_flags(claims)

    for i in range(len(offsets)):
        fl = flags.get(f"boundary-{i}")
        assert fl, f"橫跨整點的爆量 boundary-{i} 仍應被滾動窗偵測到，實際: {flags}"
        assert "x-boundary-flooder" in fl[0]
        assert f"{len(offsets)}則" in fl[0]

    assert flags.get("normal-2") is None, "正常單則來源不應被誤傷"


@pytest.mark.skip(reason=_W3_BURST_FOLLOWUP_SKIP_REASON)
def test_w3_b_baseline_uses_aligned_window_not_other_sources_historical_max():
    """對抗性回歸（codex 對抗審 [burst 第 3 個 HIGH]）：比較基準必須對齊到候選
    「現在」爆量的那個時間窗口，去看其他來源在同一時段各發了幾則，而不是
    「其他來源自己歷史上不相干時段的最大值」。

    情境：`old-spiker` 3 小時前自己也曾爆量 8 則（跟 `x-real-flooder` 現在
    爆量的時段完全不相干），但在 `x-real-flooder` 現在爆量的當下同一窗口內，
    `old-spiker` 只發了 1 則。若 baseline 誤用 old-spiker 的歷史最大值（8）
    當中位數，`8 ≤ 3×8=24` 不會觸發，即使 old-spiker 當下其實幾乎無動靜；
    改為同窗對齊比較後，old-spiker 在候選窗口內的『真實』同窗計數只有 1，
    中位數應為 1，`8 > 3×1=3`，`x-real-flooder` 應正確觸發。

    old-spiker 自己 3 小時前的歷史爆量，因為在那個時段完全沒有其他來源可
    比較（同窗中位數為 0），依既有保守防呆不予回溯標記——這是刻意的取捨
    （見 `_coordination_burst_flags` docstring），不是本測試要驗證的重點。"""
    from trustforge.trust.scoring import Claim, _coordination_burst_flags

    claims = []
    for i in range(8):
        t = f"現爆{i} GHI幣 快訊 搶進 第{i}條"
        claims.append(
            Claim(id=f"now-flood-{i}", text=t,
                  doc=_doc(f"now-flood-doc-{i}", "social", "x-real-flooder", t,
                           ts=_BURST_BASE_TS + i * 60))
        )

    old_window_start = _BURST_BASE_TS - 3 * 3600  # 3 小時前，跟候選窗口完全不相干
    for i in range(8):
        t = f"舊爆{i} JKL幣 舊聞 快訊 第{i}條"
        claims.append(
            Claim(id=f"old-flood-{i}", text=t,
                  doc=_doc(f"old-flood-doc-{i}", "social", "old-spiker", t,
                           ts=old_window_start + i * 60))
        )
    claims.append(
        Claim(id="old-spiker-recent", text="老玩家 近況 更新",
              doc=_doc("old-spiker-recent-doc", "social", "old-spiker", "老玩家 近況 更新",
                       ts=_BURST_BASE_TS + 30))
    )

    flags = _coordination_burst_flags(claims)

    for i in range(8):
        fl = flags.get(f"now-flood-{i}")
        assert fl, (
            "baseline 必須對齊候選當下窗口，不能被 old-spiker 3 小時前不相干的"
            f"歷史爆量『掩護』；now-flood-{i} 仍應被偵測，實際: {flags}"
        )
        assert "x-real-flooder" in fl[0]
        assert "8則" in fl[0]

    for i in range(8):
        assert flags.get(f"old-flood-{i}") is None, (
            "old-spiker 3 小時前的歷史事件，因當下同窗內無其他來源可比較"
            "（中位數為 0，保守不判），不應被回溯標記"
        )
    assert flags.get("old-spiker-recent") is None


def test_w3_normal_multi_source_corroboration_not_flagged():
    """回歸：既有的正常多源佐證情境（onchain/news/social 三方各自轉述同一事實，
    onchain 為 OBJECTIVE_KINDS 豁免、news/social 僅 2 個非豁免來源，未達
    `_TEMPLATE_MIN_SOURCES`）不應被 W3 誤傷——trust 應與 W3 加入前一致。"""
    from trustforge.trust.scoring import _coordination_signals

    shared = "大額 BTC 轉入 交易所 造成 賣壓 比特幣 下跌"
    docs = [
        _doc("a", "onchain", "glassnode", shared, ts=_BURST_BASE_TS),
        _doc("b", "news", "coindesk", shared, ts=_BURST_BASE_TS),
        _doc("c", "social", "x-trader", shared, ts=_BURST_BASE_TS),
    ]
    claims = extract_claims(docs)
    scored = score(claims, now=_BURST_BASE_TS)
    assert _coordination_signals(claims) == {}, "正常三方佐證不應觸發任何協同 flag"
    for sc in scored:
        assert sc.manip_flags == [], f"{sc.claim.doc.source} 不應有任何操縱 flag（正常佐證）"


def test_w3_coordination_signals_deterministic_repeat_calls():
    """確定性：同一輸入重複呼叫 `_coordination_signals` 必須逐字相同（無隨機性、
    無 LLM），符合『確定性、免 LLM、credit-safe』的設計要求。"""
    from trustforge.trust.scoring import Claim, _coordination_signals

    texts = {
        "x-shill-a": "內幕 情報 XYZ幣 即將 起飛 十倍 現在 機會 難得 進場",
        "x-shill-b": "內幕 情報 XYZ幣 即將 起飛 十倍 現在 機會 難得 買入",
        "x-shill-c": "內幕 情報 XYZ幣 即將 起飛 十倍 現在 機會 難得 加碼",
    }
    claims = [
        Claim(id=f"det-{src}", text=t, doc=_doc(f"det-doc-{src}", "social", src, t, ts=_BURST_BASE_TS))
        for src, t in texts.items()
    ]
    results = [_coordination_signals(claims) for _ in range(5)]
    assert all(r == results[0] for r in results), "重複呼叫應逐字相同（確定性）"


def test_w3_coordination_signals_burst_indicator_disabled_only_template_active():
    """W3 burst 指標（指標 B）降級為 follow-up #15，`_coordination_signals`
    目前只接指標 A（模板相似）。用一個「單源在短時間內連發多則相異主張」的
    典型爆量情境驗證：即使該情境若直接呼叫 `_coordination_burst_flags`
    仍會產生『協同:單源爆量』flag，`_coordination_signals`（實際掛在
    `score()` 主流程上的入口）也不應該回傳任何『協同:單源爆量』字樣的
    flag——證明 burst 指標確實已從 active 路徑移除，且移除後不影響模板
    指標本身的行為（此情境文本彼此不相似，模板指標本來就不該命中）。"""
    from trustforge.trust.scoring import Claim, _coordination_burst_flags, _coordination_signals

    claims = []
    for i in range(8):
        t = f"快訊{i} XYZ幣 突破 關鍵 價位 值得 留意 第{i}則"
        claims.append(
            Claim(id=f"burst-{i}", text=t,
                  doc=_doc(f"burst-doc-{i}", "social", "x-spammer", t, ts=_BURST_BASE_TS + i * 60))
        )
    for j, src in enumerate(["news-a", "news-b", "news-c"]):
        t = f"正常報導{j} 市場 觀察 淡定"
        claims.append(
            Claim(id=f"base-{j}", text=t,
                  doc=_doc(f"base-doc-{j}", "news", src, t, ts=_BURST_BASE_TS + 30))
        )

    # 佐證：_coordination_burst_flags 本身（獨立函式，程式碼保留供 #15 沿用）
    # 對這個典型爆量情境仍然會命中，代表移除的是「呼叫端接線」而不是把偵測
    # 邏輯本身弄壞了。
    raw_burst_flags = _coordination_burst_flags(claims)
    assert any("協同:單源爆量(" in fl for fls in raw_burst_flags.values() for fl in fls), (
        "前提假設：_coordination_burst_flags 獨立呼叫應仍能命中典型爆量情境"
        "（若這裡都不命中，代表函式本身被誤改壞了，不是本測試要驗證的降級行為）"
    )

    active_signals = _coordination_signals(claims)
    for cid, fls in active_signals.items():
        for fl in fls:
            assert "單源爆量" not in fl, (
                f"burst 指標已降級 follow-up #15，_coordination_signals 的 active "
                f"路徑不應再產生單源爆量 flag，實際: {cid} -> {fl}"
            )
