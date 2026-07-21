"""Issue #338 Layer 1：_price_trend_direction / _direction 單元測試。

使用 mock ScoredClaim，不依賴真實 DB。覆蓋：
- 漲 >3% → 偏多
- 跌 >3% → 偏空
- 盤整 ±3% 內 → 中性
- 邊界值 +3%、-3% 精確
- 無 price claims → None / 不明
- 價格序列不足 14 天仍能計算
- 舊版 claim（meta 無 close，text 有 C=xxx）
- close 非正數排除
- date 缺失排除
- 單筆 price claim → None
"""
from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from datetime import date, timedelta

from trustforge.agent.orchestrator import _price_trend_direction, _direction


# --- Mock 物件 ---

@dataclass
class MockDocument:
    kind: str = "price"
    source: str = "ohlcv-official"
    text: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class MockClaim:
    doc: MockDocument = field(default_factory=MockDocument)
    id: str = "test-claim"
    text: str = ""
    direction: str = "neutral"
    claim_type: str = "fact"


@dataclass
class MockScoredClaim:
    claim: MockClaim = field(default_factory=MockClaim)
    trust: float = 0.9
    components: dict = field(default_factory=dict)
    reputation_trace: dict | None = None
    manip_flags: list = field(default_factory=list)
    info_flags: list = field(default_factory=list)


def _make_price_claims(
    prices: list[tuple[str, float]],
    coin: str = "BTC",
) -> list[MockScoredClaim]:
    """建立一組 mock price ScoredClaim。prices = [(date_str, close_val), ...]"""
    claims = []
    for date_str, close_val in prices:
        doc = MockDocument(
            kind="price",
            source="ohlcv-official",
            text=f"{coin} Daily OHLCV {date_str}: O=100 H=110 L=90 C={close_val:.2f} V=1000",
            meta={
                "coin": coin,
                "date": date_str,
                "open": 100.0,
                "high": 110.0,
                "low": 90.0,
                "close": close_val,
                "volume": 1000.0,
            },
        )
        claim = MockClaim(doc=doc, id=f"ohlcv-{coin}-{date_str}", text=doc.text)
        claims.append(MockScoredClaim(claim=claim))
    return claims


def _date_range(start: str, days: int) -> list[str]:
    """產生 start 起算 days 天的日期字串清單。"""
    base = date.fromisoformat(start)
    return [(base + timedelta(days=i)).isoformat() for i in range(days)]


# ======================================================================
# Test cases
# ======================================================================

class TestPriceTrendDirection:
    """_price_trend_direction 單元測試。"""

    def test_bullish_above_3pct(self):
        """漲 >3% → 偏多"""
        dates = _date_range("2024-01-01", 15)
        base_price = 100.0
        latest_price = 104.0  # +4%
        prices = [(dates[0], base_price)] + [(dates[-1], latest_price)]
        # 中間補幾天，確保有足夠數據
        for i in range(1, 14):
            prices.append((dates[i], base_price + i * 0.2))
        claims = _make_price_claims(prices)
        assert _price_trend_direction(claims) == "偏多"

    def test_bearish_below_neg3pct(self):
        """跌 >3% → 偏空"""
        dates = _date_range("2024-01-01", 15)
        base_price = 100.0
        latest_price = 96.0  # -4%
        prices = [(dates[0], base_price)] + [(dates[-1], latest_price)]
        for i in range(1, 14):
            prices.append((dates[i], base_price - i * 0.2))
        claims = _make_price_claims(prices)
        assert _price_trend_direction(claims) == "偏空"

    def test_neutral_within_3pct(self):
        """盤整 ±3% 內 → 中性"""
        dates = _date_range("2024-01-01", 15)
        base_price = 100.0
        latest_price = 101.5  # +1.5%
        prices = [(dates[0], base_price), (dates[-1], latest_price)]
        for i in range(1, 14):
            prices.append((dates[i], 100.5))
        claims = _make_price_claims(prices)
        assert _price_trend_direction(claims) == "中性"

    def test_boundary_exactly_plus_3pct(self):
        """+3% 邊界：(最近-基準)/基準 = 0.03 剛好，不超過 → 中性"""
        dates = _date_range("2024-01-01", 15)
        base_price = 100.0
        latest_price = 103.0  # exactly +3%
        prices = [(dates[0], base_price), (dates[-1], latest_price)]
        for i in range(1, 14):
            prices.append((dates[i], 101.0))
        claims = _make_price_claims(prices)
        # 0.03 is NOT > 0.03, so it's 中性
        assert _price_trend_direction(claims) == "中性"

    def test_boundary_exactly_neg_3pct(self):
        """-3% 邊界：(最近-基準)/基準 = -0.03 剛好，不低於 → 中性"""
        dates = _date_range("2024-01-01", 15)
        base_price = 100.0
        latest_price = 97.0  # exactly -3%
        prices = [(dates[0], base_price), (dates[-1], latest_price)]
        for i in range(1, 14):
            prices.append((dates[i], 99.0))
        claims = _make_price_claims(prices)
        # -0.03 is NOT < -0.03, so it's 中性
        assert _price_trend_direction(claims) == "中性"

    def test_boundary_just_above_plus_3pct(self):
        """+3.01% → 偏多"""
        dates = _date_range("2024-01-01", 15)
        base_price = 10000.0
        latest_price = 10301.0  # +3.01%
        prices = [(dates[0], base_price), (dates[-1], latest_price)]
        for i in range(1, 14):
            prices.append((dates[i], 10100.0))
        claims = _make_price_claims(prices)
        assert _price_trend_direction(claims) == "偏多"

    def test_boundary_just_below_neg_3pct(self):
        """-3.01% → 偏空"""
        dates = _date_range("2024-01-01", 15)
        base_price = 10000.0
        latest_price = 9699.0  # -3.01%
        prices = [(dates[0], base_price), (dates[-1], latest_price)]
        for i in range(1, 14):
            prices.append((dates[i], 9900.0))
        claims = _make_price_claims(prices)
        assert _price_trend_direction(claims) == "偏空"

    def test_no_price_claims_returns_none(self):
        """無 price claims → None"""
        # 只有 news claims
        doc = MockDocument(kind="news", text="BTC 上漲", meta={})
        claim = MockClaim(doc=doc, text="BTC 上漲")
        claims = [MockScoredClaim(claim=claim)]
        assert _price_trend_direction(claims) is None

    def test_empty_list_returns_none(self):
        """空清單 → None"""
        assert _price_trend_direction([]) is None

    def test_single_price_claim_returns_none(self):
        """僅一筆 price claim（不足計算報酬率）→ None"""
        claims = _make_price_claims([("2024-01-15", 50000.0)])
        assert _price_trend_direction(claims) is None

    def test_less_than_14_days_still_computes(self):
        """不足 14 天資料→ 用最早和最晚算"""
        # 只有 5 天，漲 5%
        dates = _date_range("2024-01-01", 5)
        prices = [(dates[0], 100.0), (dates[-1], 105.0)]
        for i in range(1, 4):
            prices.append((dates[i], 101.0 + i))
        claims = _make_price_claims(prices)
        assert _price_trend_direction(claims) == "偏多"

    def test_less_than_14_days_bearish(self):
        """不足 14 天資料→ 跌 5%"""
        dates = _date_range("2024-01-01", 5)
        prices = [(dates[0], 100.0), (dates[-1], 95.0)]
        for i in range(1, 4):
            prices.append((dates[i], 99.0 - i))
        claims = _make_price_claims(prices)
        assert _price_trend_direction(claims) == "偏空"

    def test_legacy_claim_text_c_pattern(self):
        """舊版 claim：meta 無 close，text 有 C=xxx pattern"""
        dates = _date_range("2024-01-01", 15)
        claims = []
        base_price = 100.0
        latest_price = 110.0  # +10%
        all_prices = [(dates[0], base_price)] + [(dates[-1], latest_price)]
        for i in range(1, 14):
            all_prices.append((dates[i], 102.0))

        for date_str, close_val in all_prices:
            doc = MockDocument(
                kind="price",
                source="ohlcv-official",
                text=f"BTC OHLCV {date_str}: O=100 H=110 L=90 C={close_val:.2f} V=1000",
                meta={"date": date_str},  # 沒有 close！
            )
            claim = MockClaim(doc=doc, id=f"legacy-{date_str}", text=doc.text)
            claims.append(MockScoredClaim(claim=claim))
        assert _price_trend_direction(claims) == "偏多"

    def test_negative_close_excluded(self):
        """close <= 0 的資料被排除"""
        dates = _date_range("2024-01-01", 15)
        # 只有兩筆有效（第一筆和最後一筆），中間都是 -1
        prices = [(dates[0], 100.0), (dates[-1], 110.0)]
        claims = _make_price_claims(prices)
        # 加入非正 close 的 claim
        bad_doc = MockDocument(
            kind="price",
            meta={"date": dates[5], "close": -50.0},
            text="bad data",
        )
        bad_claim = MockClaim(doc=bad_doc, text="bad data")
        claims.append(MockScoredClaim(claim=bad_claim))
        assert _price_trend_direction(claims) == "偏多"

    def test_missing_date_excluded(self):
        """meta 缺 date 的 claim 被排除"""
        doc = MockDocument(
            kind="price",
            meta={"close": 50000.0},  # 有 close 但沒 date
            text="no date",
        )
        claim = MockClaim(doc=doc, text="no date")
        claims = [MockScoredClaim(claim=claim)]
        assert _price_trend_direction(claims) is None


class TestDirection:
    """_direction 整合：wrap _price_trend_direction + fallback。"""

    def test_with_price_data_bullish(self):
        """有價格資料且漲 → 偏多"""
        dates = _date_range("2024-01-01", 15)
        prices = [(dates[0], 100.0), (dates[-1], 110.0)]
        for i in range(1, 14):
            prices.append((dates[i], 102.0))
        claims = _make_price_claims(prices)
        assert _direction(claims) == "偏多"

    def test_no_price_data_returns_unknown(self):
        """無 price claims → '不明'"""
        doc = MockDocument(kind="news", text="something", meta={})
        claim = MockClaim(doc=doc, text="something")
        claims = [MockScoredClaim(claim=claim)]
        assert _direction(claims) == "不明"

    def test_empty_returns_unknown(self):
        """空清單 → '不明'"""
        assert _direction([]) == "不明"
