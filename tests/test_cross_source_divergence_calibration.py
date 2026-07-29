"""#864：跨源分歧偵測觸發校準 + 新聞信任分布驗證。

使用固定 fixture（已知文字/時間戳/source/kind），通過完整 scoring pipeline
驗證分歧/共識/未觸發行為。不依賴即時新聞、不打 Bedrock。

設計原則（見 .kiro/specs/cross-source-divergence-864/design.md）：
  - AD-0: corroboration 用 Jaccard token overlap ≥ 0.4，非語意比對
  - AD-1: 不修改 KIND_REPUTATION/DEFAULT_WEIGHTS，只透過 fixture 觀察
  - AD-3: 新聞 claim 需有跨源佐證才能突破 0.5 門檻
  - AD-4: fixture 文字需刻意保留足夠重疊詞彙，模擬同議題不同來源報導
"""
from __future__ import annotations

import pytest

from trustforge.agent.orchestrator import (
    _independent_source_keys,
    detect_cross_source_signal,
)
from trustforge.ingestion.base import Document
from trustforge.trust.scoring import (
    Claim,
    ScoredClaim,
    extract_claims,
    score,
)


# ---------------------------------------------------------------------------
# 固定時間戳（2025-06-15 00:00:00 UTC）
# ---------------------------------------------------------------------------
NOW = 1_750_000_000.0


# ---------------------------------------------------------------------------
# Fixture 工廠函式
# ---------------------------------------------------------------------------

def _doc(
    id_: str,
    kind: str,
    source: str,
    text: str,
    ts: float = NOW,
    meta: dict | None = None,
) -> Document:
    """建立固定 Document，預設時間戳 = NOW（最新鮮，recency_decay ≈ 1.0）。"""
    return Document(id=id_, kind=kind, source=source, text=text, ts=ts, meta=meta or {})


def _sc(
    id_: str,
    kind: str,
    source: str,
    direction: str,
    trust: float,
) -> ScoredClaim:
    """手工構造 ScoredClaim（跳過 scoring pipeline，用於測試分歧偵測邏輯本身）。"""
    doc = _doc(id_, kind, source, text=f"claim-{id_}")
    claim = Claim(id=id_, text=f"claim-{id_}", doc=doc, direction=direction)
    return ScoredClaim(claim=claim, trust=trust)


def _docs_to_scored(docs: list[Document], now: float = NOW) -> list[ScoredClaim]:
    """端到端：Document → extract_claims → score → ScoredClaim[]。

    使用 offline 模式（stance_client=None），佐證走確定性 Jaccard 比對。
    """
    claims = extract_claims(docs)
    return score(claims, now=now, stance_client=None, offline=True)


# ===========================================================================
# TestNewsTrustDistribution — 新聞信任分布校準 (FR-2)
# ===========================================================================

class TestNewsTrustDistribution:
    """驗證新聞 claim 在完整 scoring pipeline 下的信任分布。

    關鍵公式（kind="news", KIND_REPUTATION=0.65）：
      trust = 0.50×0.65 + 0.25×corr + 0.15×recency - 0.40×manip
            = 0.325 + 0.25×corr + 0.15×recency - 0.40×manip
    """

    def test_solo_news_claim_below_threshold(self):
        """單獨新聞 claim（無佐證）：trust < 0.5。

        預期：0.325 + 0.0(corr) + 0.15(recency=1.0) = 0.475 < 0.5
        """
        docs = [
            _doc("n1", "news", "coindesk",
                 "分析師 警告 BTC 超買 可能 回調 下跌 修正"),
        ]
        scored = _docs_to_scored(docs)
        assert len(scored) >= 1
        # 所有來自此 news source 的 claim trust 應 < 0.5
        news_claims = [sc for sc in scored if sc.claim.doc.kind == "news"]
        assert news_claims, "應產生至少一筆 news claim"
        for sc in news_claims:
            assert sc.trust < 0.5, (
                f"單獨新聞 claim（無佐證）trust 應 < 0.5，實得 {sc.trust:.4f}，"
                f"components={sc.components}"
            )

    def test_news_with_corroboration_above_threshold(self):
        """新聞 + 另一來源佐證（同方向、文字重疊）：trust ≥ 0.5。

        兩筆情緒面 claim 互為佐證（token overlap ≥ 0.4）：
        - coindesk news: "分析師 警告 BTC 超買 回調 下跌 修正"
        - crypto_twitter social: "BTC 超買 回調 下跌 修正 恐慌"
        重疊詞（去 DOMAIN_STOP 後）：超買, 回調, 修正 → overlap ≥ 0.4
        """
        docs = [
            _doc("n1", "news", "coindesk",
                 "分析師 警告 BTC 超買 回調 下跌 修正"),
            _doc("s1", "social", "crypto_twitter",
                 "BTC 超買 回調 下跌 修正 恐慌"),
        ]
        scored = _docs_to_scored(docs)
        news_claims = [sc for sc in scored if sc.claim.doc.kind == "news"]
        assert news_claims, "應產生至少一筆 news claim"
        # 至少一筆 news claim trust ≥ 0.5（佐證加分後過門檻）
        max_trust = max(sc.trust for sc in news_claims)
        assert max_trust >= 0.5, (
            f"有佐證的新聞 claim trust 應 ≥ 0.5，實得最高 {max_trust:.4f}"
        )

    def test_news_with_manipulation_flag_penalty(self):
        """操縱關鍵詞命中時 trust 顯著下降。

        使用操縱嫌疑詞彙（如「暴漲」「必漲」）觸發 manipulation_penalty。
        """
        docs_clean = [
            _doc("n1", "news", "coindesk",
                 "分析師 指出 BTC 技術面 偏多 支撐位 穩固"),
        ]
        docs_manip = [
            _doc("n2", "news", "pump_channel",
                 "BTC 即將 暴漲 百倍 必漲 快上車 穩賺不賠 翻倍"),
        ]
        scored_clean = _docs_to_scored(docs_clean)
        scored_manip = _docs_to_scored(docs_manip)

        clean_trust = max((sc.trust for sc in scored_clean if sc.claim.doc.kind == "news"), default=0.0)
        manip_trust = max((sc.trust for sc in scored_manip if sc.claim.doc.kind == "news"), default=0.0)

        # 操縱命中的 trust 應顯著低於無操縱的
        assert manip_trust < clean_trust, (
            f"操縱命中 trust({manip_trust:.4f}) 應 < 無操縱 trust({clean_trust:.4f})"
        )

    def test_news_recency_decay(self):
        """過期新聞（12 小時半衰期之後）trust 下降。"""
        twelve_hours = 12 * 3600
        docs_fresh = [
            _doc("n1", "news", "coindesk",
                 "分析師 警告 BTC 超買 回調 下跌", ts=NOW),
        ]
        docs_stale = [
            _doc("n2", "news", "coindesk",
                 "分析師 警告 BTC 超買 回調 下跌", ts=NOW - 3 * twelve_hours),
        ]
        scored_fresh = _docs_to_scored(docs_fresh)
        scored_stale = _docs_to_scored(docs_stale, now=NOW)

        fresh_trust = max((sc.trust for sc in scored_fresh if sc.claim.doc.kind == "news"), default=0.0)
        stale_trust = max((sc.trust for sc in scored_stale if sc.claim.doc.kind == "news"), default=0.0)

        assert stale_trust < fresh_trust, (
            f"過期 trust({stale_trust:.4f}) 應 < 新鮮 trust({fresh_trust:.4f})"
        )


# ===========================================================================
# TestDivergenceFixture — 固定 fixture 分歧觸發 (FR-1)
# ===========================================================================

class TestDivergenceFixture:
    """使用固定 fixture 穩定觸發分歧判斷（不依賴即時新聞）。

    策略：
    - 客觀面（price/onchain）：kind_rep=0.95，不需佐證即過 0.5 門檻
    - 情緒面（news+social）：互為佐證（文字重疊 ≥ 0.4）推升 trust 過 0.5
    - 兩面方向相反 → divergence
    """

    def test_price_bullish_vs_news_bearish_divergence(self):
        """客觀(price) bullish + 情緒(news+social 互為佐證) bearish → divergence。"""
        docs = [
            # 客觀面 bullish（trust 高，不需佐證）
            _doc("p1", "price", "binance",
                 "BTC 突破 七萬 美金 新高 量能 放大 漲幅 擴大"),
            _doc("o1", "onchain", "glassnode",
                 "BTC 活躍 地址數 創新高 鏈上 轉帳量 上升 累積"),
            # 情緒面 bearish（需互相佐證才過門檻）
            _doc("n1", "news", "coindesk",
                 "分析師 警告 BTC 超買 可能 回調 下跌 修正 賣壓"),
            _doc("s1", "social", "crypto_twitter",
                 "BTC 超買 預計 回調 下跌 修正 恐慌 賣壓"),
        ]
        scored = _docs_to_scored(docs)

        result = detect_cross_source_signal(scored)
        assert result is not None, (
            "應偵測到分歧訊號。"
            f"eligible(trust>=0.5): {[(sc.claim.doc.kind, sc.claim.direction, sc.trust) for sc in scored if sc.trust >= 0.5]}"
        )
        assert result["type"] == "divergence", f"期望 divergence，實得 {result['type']}"
        assert result["objective_direction"] == "bullish"
        assert result["sentiment_direction"] == "bearish"
        assert "背離" in result["summary"]
        assert len(result["supporting_claim_ids"]) >= 2

    def test_onchain_bearish_vs_social_bullish_divergence(self):
        """客觀(onchain) bearish + 情緒(news+social 互為佐證) bullish → divergence。"""
        docs = [
            # 客觀面 bearish
            _doc("o1", "onchain", "glassnode",
                 "BTC 活躍 地址數 下降 鏈上 轉帳量 萎縮 流出 賣壓"),
            _doc("p1", "price", "binance",
                 "BTC 跌破 關鍵 支撐位 量能 萎縮 下跌 走低"),
            # 情緒面 bullish（互相佐證）
            _doc("n1", "news", "cointelegraph",
                 "機構 投資人 逢低 買入 BTC 累積 增持 看多"),
            _doc("s1", "social", "reddit_crypto",
                 "機構 逢低 買入 BTC 累積 增持 看多 突破"),
        ]
        scored = _docs_to_scored(docs)

        result = detect_cross_source_signal(scored)
        assert result is not None, (
            "應偵測到分歧訊號。"
            f"eligible(trust>=0.5): {[(sc.claim.doc.kind, sc.claim.direction, sc.trust) for sc in scored if sc.trust >= 0.5]}"
        )
        assert result["type"] == "divergence", f"期望 divergence，實得 {result['type']}"
        assert result["objective_direction"] == "bearish"
        assert result["sentiment_direction"] == "bullish"
        assert "背離" in result["summary"]


# ===========================================================================
# TestConsensusFixture — 共識案例 (FR-1)
# ===========================================================================

class TestConsensusFixture:
    """固定 fixture 共識案例：客觀 + 情緒同向。"""

    def test_price_and_news_both_bullish_consensus(self):
        """客觀(price) bullish + 情緒(news+social) bullish → consensus。"""
        docs = [
            # 客觀面 bullish
            _doc("p1", "price", "binance",
                 "BTC 突破 七萬 美金 新高 量能 放大 漲幅 擴大"),
            # 情緒面 bullish（互相佐證）
            _doc("n1", "news", "coindesk",
                 "機構 投資人 逢低 買入 BTC 累積 增持 看多 突破"),
            _doc("s1", "social", "crypto_twitter",
                 "機構 逢低 買入 BTC 累積 增持 看多 突破"),
        ]
        scored = _docs_to_scored(docs)

        result = detect_cross_source_signal(scored)
        assert result is not None, (
            "應偵測到共識訊號。"
            f"eligible(trust>=0.5): {[(sc.claim.doc.kind, sc.claim.direction, sc.trust) for sc in scored if sc.trust >= 0.5]}"
        )
        assert result["type"] == "consensus", f"期望 consensus，實得 {result['type']}"
        assert result["objective_direction"] == "bullish"
        assert result["sentiment_direction"] == "bullish"
        assert "一致" in result["summary"]


# ===========================================================================
# TestNotTriggered — 未觸發案例 (FR-6)
# ===========================================================================

class TestNotTriggered:
    """驗證各種「不應觸發」的邊界情況。"""

    def test_no_trigger_missing_sentiment(self):
        """缺情緒類 → None（只有客觀面 claim，情緒面 eligible 為空）。"""
        scored = [
            _sc("p1", "price", "binance", "bullish", 0.90),
            _sc("o1", "onchain", "glassnode", "bullish", 0.85),
        ]
        # 前置條件：無 _SENTIMENT_KINDS
        sentiment_eligible = [sc for sc in scored if sc.claim.doc.kind in {"news", "social", "sentiment"} and sc.trust >= 0.5]
        assert len(sentiment_eligible) == 0, "前置條件：無情緒類 eligible"
        assert detect_cross_source_signal(scored) is None, "缺情緒類應回 None"

    def test_no_trigger_missing_objective(self):
        """缺客觀類 → None（只有情緒面 claim）。"""
        scored = [
            _sc("n1", "news", "coindesk", "bearish", 0.65),
            _sc("s1", "social", "twitter", "bearish", 0.55),
        ]
        objective_eligible = [sc for sc in scored if sc.claim.doc.kind in {"price", "price_live", "onchain", "regulatory", "hoyabit"} and sc.trust >= 0.5]
        assert len(objective_eligible) == 0, "前置條件：無客觀類 eligible"
        assert detect_cross_source_signal(scored) is None, "缺客觀類應回 None"

    def test_no_trigger_low_trust(self):
        """全低 trust (< 0.5) → None（eligible 為空）。"""
        scored = [
            _sc("p1", "price", "binance", "bullish", 0.40),
            _sc("n1", "news", "coindesk", "bearish", 0.35),
        ]
        eligible = [sc for sc in scored if sc.trust >= 0.5]
        assert len(eligible) == 0, "前置條件：全部 trust < 0.5"
        assert detect_cross_source_signal(scored) is None, "全低 trust 應回 None"

    def test_no_trigger_neutral_dominant(self):
        """兩類都有 eligible 但主導方向為 neutral → None。

        場景：客觀面有 bullish 和 bearish 各半（投票打平，最高票 < 0.3×total），
        使得主導方向為 neutral。
        """
        scored = [
            _sc("p1", "price", "binance", "bullish", 0.60),
            _sc("p2", "price", "kraken", "bearish", 0.60),
            _sc("n1", "news", "coindesk", "bearish", 0.55),
        ]
        result = detect_cross_source_signal(scored)
        # 客觀面 bullish weight == bearish weight → 主導是 neutral（佔比恰好 50%/50%，
        # 最高票 = 0.5×total，超過 0.3）→ 其實會有主導
        # 調整：讓三方都不達 0.3 門檻
        scored_neutral = [
            _sc("p1", "price", "binance", "bullish", 0.51),
            _sc("p2", "price", "kraken", "bearish", 0.51),
            _sc("p3", "price", "coinbase", "neutral", 0.80),
            _sc("n1", "news", "coindesk", "bearish", 0.55),
        ]
        # 客觀面：bullish=0.51, bearish=0.51, neutral=0.80 → total=1.82
        # 最高票=neutral(0.80) → 但 neutral 不是 valid direction → 實際上
        # max(bullish, bearish)=0.51 < 0.3×1.82=0.546 → 主導為 neutral
        result = detect_cross_source_signal(scored_neutral)
        assert result is None, "客觀面主導 neutral 時應回 None"

    def test_no_trigger_insufficient_independent_sources(self):
        """同一來源大小寫/空白變體不膨脹 → 正規化後獨立來源 < 2 → None。

        場景：客觀和情緒都只有 1 個真正獨立來源（同源），
        正規化後合計仍只有 1 個 → 不足 2 → None。
        """
        scored = [
            _sc("p1", "price", "Binance", "bullish", 0.90),
            _sc("n1", "news", " binance ", "bearish", 0.60),  # 正規化後跟 price 同源
        ]
        result = detect_cross_source_signal(scored)
        assert result is None, (
            "同源（大小寫變體 Binance / binance）正規化後只有 1 個獨立來源，應回 None"
        )


# ===========================================================================
# TestSourceNormalization — 來源正規化不變量 (FR-3)
# ===========================================================================

class TestSourceNormalization:
    """驗證 _independent_source_keys 正規化行為。"""

    def test_case_whitespace_variants_collapse(self):
        """大小寫/空白變體收斂為同一源。"""
        sources = ["CoinDesk", " coindesk ", "COINDESK"]
        keys = _independent_source_keys(sources)
        assert len(keys) == 1, f"期望 1 個獨立來源，實得 {len(keys)}: {keys}"

    def test_truly_distinct_sources_preserved(self):
        """不同來源不被過度合併。"""
        sources = ["binance", "coindesk", "glassnode"]
        keys = _independent_source_keys(sources)
        assert len(keys) == 3, f"期望 3 個獨立來源，實得 {len(keys)}: {keys}"

    def test_divergence_with_normalized_source_still_triggers(self):
        """正規化後 ≥2 個真正不同的獨立來源仍可觸發。"""
        scored = [
            _sc("p1", "price", "Binance", "bullish", 0.90),
            _sc("n1", "news", "CoinDesk", "bearish", 0.65),
            _sc("n2", "news", " coindesk ", "bearish", 0.55),  # 同源變體，不膨脹
        ]
        result = detect_cross_source_signal(scored)
        # 情緒面只有 1 個獨立來源（coindesk），合計 2（binance + coindesk）→ 可觸發
        assert result is not None, "2 個不同獨立來源（binance + coindesk）應可觸發"
        assert result["sentiment_source_count"] == 1, (
            f"情緒面只有 1 個獨立來源，期望 sentiment_source_count=1，"
            f"實得 {result['sentiment_source_count']}"
        )

    def test_same_source_case_variants_do_not_inflate_signal(self):
        """同源大小寫變體不會虛增 sentiment_source_count。"""
        scored = [
            _sc("p1", "price", "binance", "bullish", 0.90),
            _sc("n1", "news", "CoinDesk", "bearish", 0.65),
            _sc("n2", "news", " COINDESK ", "bearish", 0.60),
            _sc("n3", "news", "coindesk", "bearish", 0.55),
        ]
        result = detect_cross_source_signal(scored)
        assert result is not None
        # 三筆 coindesk 變體正規化後只算 1 個
        assert result["sentiment_source_count"] == 1


# ===========================================================================
# TestExplainability — 結果可追溯性 (FR-4)
# ===========================================================================

class TestExplainability:
    """驗證判斷結果包含完整溯源資訊。"""

    def test_supporting_claim_ids_present(self):
        """divergence 結果包含 supporting_claim_ids 且非空。"""
        scored = [
            _sc("obj1", "onchain", "glassnode", "bullish", 0.80),
            _sc("obj2", "price", "binance", "bullish", 0.75),
            _sc("sen1", "news", "coindesk", "bearish", 0.65),
            _sc("sen2", "social", "twitter", "bearish", 0.55),
        ]
        result = detect_cross_source_signal(scored)
        assert result is not None
        assert "supporting_claim_ids" in result
        assert len(result["supporting_claim_ids"]) >= 2, (
            f"應有 ≥2 個佐證 claim_id，實得 {len(result['supporting_claim_ids'])}"
        )

    def test_claim_ids_traceable_to_source(self):
        """每個 supporting_claim_id 可追回原始 source/kind/direction。"""
        scored = [
            _sc("obj1", "onchain", "glassnode", "bullish", 0.80),
            _sc("sen1", "news", "coindesk", "bearish", 0.65),
        ]
        result = detect_cross_source_signal(scored)
        assert result is not None

        # 建立 claim_id → ScoredClaim 的映射
        id_to_sc = {sc.claim.id: sc for sc in scored}

        for cid in result["supporting_claim_ids"]:
            assert cid in id_to_sc, f"claim_id '{cid}' 應可追回 ScoredClaim"
            sc = id_to_sc[cid]
            assert sc.claim.doc.source, f"claim_id '{cid}' 應有 source"
            assert sc.claim.doc.kind, f"claim_id '{cid}' 應有 kind"
            assert sc.claim.direction in ("bullish", "bearish", "neutral")

    def test_summary_contains_direction_labels(self):
        """summary 包含方向標籤文字（偏多/偏空）。"""
        scored = [
            _sc("obj1", "price", "binance", "bullish", 0.85),
            _sc("sen1", "news", "coindesk", "bearish", 0.65),
        ]
        result = detect_cross_source_signal(scored)
        assert result is not None
        summary = result["summary"]
        assert "偏多" in summary or "偏空" in summary, (
            f"summary 應含方向標籤，實得：{summary}"
        )

    def test_no_investment_advice_in_summary(self):
        """summary 嚴禁買賣決策字眼（HOYA BIT 合規）。"""
        scored = [
            _sc("obj1", "onchain", "glassnode", "bullish", 0.80),
            _sc("obj2", "price", "binance", "bullish", 0.75),
            _sc("sen1", "news", "coindesk", "bearish", 0.65),
            _sc("sen2", "social", "twitter", "bearish", 0.55),
        ]
        result = detect_cross_source_signal(scored)
        assert result is not None
        forbidden = ("買", "賣", "進場", "出場", "該買", "該賣")
        for word in forbidden:
            assert word not in result["summary"], (
                f"summary 嚴禁決策字眼「{word}」，實得：{result['summary']}"
            )
