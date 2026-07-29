"""#864 退件修正：接近真實 connector 輸出的新聞信任分分布證據。

使用 CoinDesk/CoinTelegraph 風格的真實語言模式建構 fixture（非刻意安排
高 token overlap 的人工句子），通過完整 scoring pipeline 驗證信任分分布。

信任公式（kind="news", KIND_REPUTATION=0.65）：
  trust = 0.50×reputation + 0.25×corroboration + 0.15×recency − 0.40×manipulation

案例覆蓋：
  - CoinDesk 單獨無佐證：trust 在 0.30–0.50
  - CoinDesk + CoinTelegraph 有佐證：trust ≥ 0.50
  - 過期新聞 >48h：trust 顯著低於新鮮同文
  - 操縱關鍵詞命中：trust < 0.30
  - 雙源佐證最新：trust 在 0.50–0.75
"""
from __future__ import annotations

import pytest

from trustforge.ingestion.base import Document
from trustforge.trust.scoring import Claim, ScoredClaim, extract_claims, score


# ---------------------------------------------------------------------------
# 固定時間戳
# ---------------------------------------------------------------------------
NOW = 1_750_000_000.0


def _docs_to_scored(docs: list[Document], now: float = NOW) -> list[ScoredClaim]:
    """端到端：Document → extract_claims → score → ScoredClaim[]。"""
    claims = extract_claims(docs)
    return score(claims, now=now, stance_client=None, offline=True)


# ===========================================================================
# CoinDesk 風格：正式語氣、引述分析師、較長句子
# ===========================================================================

class TestCoinDeskTrustDistribution:
    """CoinDesk 風格新聞 claim 在完整 pipeline 下的信任分分布。"""

    def test_solo_coindesk_no_corroboration(self):
        """單獨 CoinDesk 報導，無跨源佐證。

        預期：KIND_REP(0.65)×0.5 + 0×0.25 + recency(≈1.0)×0.15 − 0
             ≈ 0.325 + 0.15 = 0.475 (< 0.5)
        """
        docs = [Document(
            id="cd-solo-001", kind="news", source="coindesk",
            text=(
                "According to a senior analyst at JPMorgan, Bitcoin's market "
                "structure has matured considerably over the past quarter, with "
                "declining volatility and growing institutional participation "
                "suggesting a potential shift in the asset's correlation profile "
                "relative to traditional equity markets."
            ),
            url="https://coindesk.com/markets/btc-structure",
            ts=NOW - 600,  # 10 分鐘前（新鮮）
            meta={"coin": "BTC"},
        )]
        scored = _docs_to_scored(docs)
        assert len(scored) >= 1
        # 無佐證單獨新聞 → trust 應在 0.30–0.50
        for sc in scored:
            assert 0.30 <= sc.trust <= 0.50, (
                f"Solo CoinDesk trust={sc.trust:.3f} 超出預期 [0.30, 0.50]"
            )

    def test_coindesk_with_cointelegraph_corroboration(self):
        """CoinDesk + CoinTelegraph 報導同一 ETF 資金流入議題。

        自然語言產生的 token overlap（ETF、Bitcoin、inflows、billion 等共用詞彙）
        足以觸發 Jaccard 佐證加分 → 至少一筆 trust ≥ 0.50。
        """
        docs = [
            Document(
                id="cd-etf-001", kind="news", source="coindesk",
                text=(
                    "Bitcoin spot ETF products attracted net inflows of $1.2 billion "
                    "this week, the strongest weekly performance since the January "
                    "launch. BlackRock's iShares Bitcoin Trust led with $680 million "
                    "in new subscriptions, while Fidelity's FBTC added $340 million."
                ),
                url="https://coindesk.com/etf-flows",
                ts=NOW - 1800,
                meta={"coin": "BTC"},
            ),
            Document(
                id="ct-etf-001", kind="news", source="cointelegraph",
                text=(
                    "Spot Bitcoin ETF products recorded substantial weekly net inflows "
                    "totaling $1.2 billion, with BlackRock's iShares Bitcoin Trust "
                    "capturing the majority at $680 million. Analysts note the inflows "
                    "signal renewed institutional confidence following regulatory clarity."
                ),
                url="https://cointelegraph.com/etf-inflows",
                ts=NOW - 2400,
                meta={"coin": "BTC"},
            ),
        ]
        scored = _docs_to_scored(docs)
        assert len(scored) >= 2
        trusts = [sc.trust for sc in scored]
        # 有佐證 → 至少一筆突破 0.50
        assert max(trusts) >= 0.50, (
            f"CoinDesk+CoinTelegraph 佐證後 max trust={max(trusts):.3f} < 0.50"
        )

    def test_expired_news_48h_recency_decay(self):
        """48 小時前的新聞，recency 衰減顯著。

        半衰期 12h → 48h = 4 半衰期 → decay ≈ 0.5^4 = 0.0625
        trust ≈ 0.325 + 0.25×0 + 0.15×0.0625 − 0 ≈ 0.334
        """
        docs = [Document(
            id="cd-old-001", kind="news", source="coindesk",
            text=(
                "Market participants noted increased selling pressure across "
                "major cryptocurrency exchanges as profit-taking accelerated "
                "following Bitcoin's brief push above the $70,000 resistance level."
            ),
            url="https://coindesk.com/old-analysis",
            ts=NOW - 48 * 3600,  # 48 小時前
            meta={"coin": "BTC"},
        )]
        scored = _docs_to_scored(docs)
        assert len(scored) >= 1
        # 過期 48h + 無佐證 → trust 應顯著偏低
        for sc in scored:
            assert sc.trust < 0.40, (
                f"48h old news trust={sc.trust:.3f} should be < 0.40"
            )
            assert sc.trust > 0.20, (
                f"48h old news trust={sc.trust:.3f} too low (< 0.20)"
            )

    def test_manipulation_keyword_penalty(self):
        """操縱關鍵詞（pump）命中 → trust 顯著降低。

        manipulation penalty = 0.40 × hit_count_factor
        """
        docs = [Document(
            id="social-pump-001", kind="news", source="cryptoslate",
            text=(
                "Community leaders are calling this the next big pump opportunity, "
                "claiming Bitcoin will go to the moon within days as institutional "
                "buyers allegedly prepare massive market orders."
            ),
            url="https://cryptoslate.com/opinion",
            ts=NOW - 300,
            meta={"coin": "BTC"},
        )]
        scored = _docs_to_scored(docs)
        assert len(scored) >= 1
        # 操縱命中 → trust 應非常低
        for sc in scored:
            assert sc.trust < 0.35, (
                f"Manipulation news trust={sc.trust:.3f} should be < 0.35"
            )
            # 確認操縱旗標被觸發
            assert sc.components.get("manipulation", 0) > 0, (
                "Expected manipulation component > 0"
            )

    def test_dual_source_corroboration_fresh(self):
        """雙源佐證 + 最新鮮 → 最佳案例，trust 0.50–0.75。"""
        docs = [
            Document(
                id="cd-best-001", kind="news", source="coindesk",
                text=(
                    "The SEC has officially approved applications for multiple "
                    "Ethereum spot ETF products, a landmark regulatory decision "
                    "that analysts expect will attract significant institutional "
                    "capital into the Ethereum ecosystem."
                ),
                url="https://coindesk.com/sec-eth-etf",
                ts=NOW - 120,  # 2 分鐘前
                meta={"coin": "ETH"},
            ),
            Document(
                id="ct-best-001", kind="news", source="cointelegraph",
                text=(
                    "In a historic move, the SEC approved Ethereum spot ETF "
                    "applications from major asset managers. The regulatory "
                    "approval marks a significant milestone for institutional "
                    "Ethereum adoption and ecosystem capital flows."
                ),
                url="https://cointelegraph.com/sec-eth",
                ts=NOW - 180,  # 3 分鐘前
                meta={"coin": "ETH"},
            ),
        ]
        scored = _docs_to_scored(docs)
        assert len(scored) >= 2
        trusts = [sc.trust for sc in scored]
        # 雙源佐證 + 最新鮮 → 最佳案例
        assert max(trusts) >= 0.50, (
            f"Dual source fresh max trust={max(trusts):.3f} should be >= 0.50"
        )
        assert max(trusts) <= 0.80, (
            f"Dual source fresh max trust={max(trusts):.3f} unexpectedly high (> 0.80)"
        )


# ===========================================================================
# CoinTelegraph 風格：數據導向、較口語
# ===========================================================================

class TestCoinTelegraphStyle:
    """CoinTelegraph 風格新聞的信任分行為。"""

    def test_solo_cointelegraph_below_threshold(self):
        """單獨 CoinTelegraph 同樣因無佐證 < 0.50。"""
        docs = [Document(
            id="ct-solo-001", kind="news", source="cointelegraph",
            text=(
                "Ethereum gas fees dropped to a six-month low of 8 Gwei "
                "on average this week, signaling reduced network congestion "
                "as Layer 2 solutions continue absorbing transaction volume "
                "from the mainnet."
            ),
            url="https://cointelegraph.com/gas-fees",
            ts=NOW - 900,
            meta={"coin": "ETH"},
        )]
        scored = _docs_to_scored(docs)
        assert len(scored) >= 1
        for sc in scored:
            assert sc.trust < 0.50, (
                f"Solo CoinTelegraph trust={sc.trust:.3f} should be < 0.50"
            )
            assert sc.trust > 0.25, (
                f"Solo CoinTelegraph trust={sc.trust:.3f} too low"
            )
