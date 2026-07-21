"""Issue #338 Layer 1：真實 OHLCV 方向分佈測試。

讀 data/data/BTC_daily_ohlcv.csv，抽 50 個不同 14 天窗口，
斷言三態（偏多/偏空/中性）分佈至少各 ≥3。

確保新的 _price_trend_direction 在真實市場資料上能產出合理分佈，
不會「全部走進預設分支」。
"""
from __future__ import annotations

import csv
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pytest

from trustforge.agent.orchestrator import _price_trend_direction


# 路徑
_BTC_OHLCV = Path(__file__).resolve().parents[1] / "data" / "data" / "BTC_daily_ohlcv.csv"


@dataclass
class MockDocument:
    kind: str = "price"
    source: str = "ohlcv-official"
    text: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class MockClaim:
    doc: MockDocument = field(default_factory=MockDocument)
    id: str = "test"
    text: str = ""
    direction: str = "neutral"
    claim_type: str = "fact"


@dataclass
class MockScoredClaim:
    claim: MockClaim = field(default_factory=MockClaim)
    trust: float = 0.95
    components: dict = field(default_factory=dict)
    reputation_trace: dict | None = None
    manip_flags: list = field(default_factory=list)
    info_flags: list = field(default_factory=list)


def _load_ohlcv_rows() -> list[dict]:
    """讀取真實 BTC OHLCV CSV，回傳 list of dict。"""
    if not _BTC_OHLCV.exists():
        pytest.skip(f"BTC OHLCV 資料不存在：{_BTC_OHLCV}")
    rows = []
    with open(_BTC_OHLCV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def _make_window_claims(rows: list[dict], start_idx: int, window_size: int = 15) -> list:
    """從 rows[start_idx:start_idx+window_size] 建 mock claims。"""
    window = rows[start_idx:start_idx + window_size]
    claims = []
    for row in window:
        close_val = float(row["close"])
        date_str = row["date"]
        doc = MockDocument(
            kind="price",
            source="ohlcv-official",
            text=f"BTC OHLCV {date_str}: C={close_val:.2f}",
            meta={
                "coin": "BTC",
                "date": date_str,
                "close": close_val,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "volume": float(row["volume"]),
            },
        )
        claim = MockClaim(doc=doc, id=f"ohlcv-BTC-{date_str}", text=doc.text)
        claims.append(MockScoredClaim(claim=claim))
    return claims


class TestDirectionDistribution:
    """真實 OHLCV 三態分佈驗證。"""

    def test_btc_50_windows_distribution(self):
        """抽 50 個不同日期窗口，三態各 ≥3。"""
        rows = _load_ohlcv_rows()
        assert len(rows) >= 50, f"BTC OHLCV 資料不足 50 行：{len(rows)}"

        # window_size = 15（需涵蓋 14 天）
        window_size = 15
        max_start = len(rows) - window_size

        # 用固定 seed 確保可重現
        rng = random.Random(338)
        starts = rng.sample(range(max_start), min(50, max_start))

        results = {"偏多": 0, "偏空": 0, "中性": 0}
        for start_idx in starts:
            claims = _make_window_claims(rows, start_idx, window_size)
            direction = _price_trend_direction(claims)
            if direction in results:
                results[direction] += 1

        # 斷言三態各 ≥3
        assert results["偏多"] >= 3, f"偏多只有 {results['偏多']}（需 ≥3）：{results}"
        assert results["偏空"] >= 3, f"偏空只有 {results['偏空']}（需 ≥3）：{results}"
        assert results["中性"] >= 3, f"中性只有 {results['中性']}（需 ≥3）：{results}"

        # 列印分佈供 review（不影響 pass/fail）
        total = sum(results.values())
        print(f"\n方向分佈（50 窗口）：{results}，有效 {total}/50")
