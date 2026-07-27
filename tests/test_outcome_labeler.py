"""Tests for outcome_labeler — ground-truth labeling from OHLCV."""
from __future__ import annotations

import csv
import tempfile
from pathlib import Path
from unittest.mock import patch

from src.trustforge.trust.outcome_labeler import (
    batch_label_from_ohlcv,
    label_n_day_direction,
)


def _write_temp_ohlcv(tmpdir: Path, coin: str, data: list[dict]) -> Path:
    """Write a temporary OHLCV CSV for testing."""
    fp = tmpdir / f"{coin.upper()}_daily_ohlcv.csv"
    with open(fp, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(data)
    return fp


def _make_ohlcv_data(
    start_date: str, prices: list[float]
) -> list[dict]:
    """Generate OHLCV rows from a list of closing prices, one per day."""
    from datetime import datetime, timedelta
    start = datetime.strptime(start_date, "%Y-%m-%d")
    rows = []
    for i, price in enumerate(prices):
        d = start + timedelta(days=i)
        rows.append({
            "date": d.strftime("%Y-%m-%d"),
            "open": str(price),
            "high": str(price * 1.05),
            "low": str(price * 0.95),
            "close": str(price),
            "volume": "1000",
        })
    return rows


def test_label_bullish():
    """Close goes up 5% over 7 days → bullish."""
    # 10 days of data: days 0-9. Day 0 close=100, Day 7 close=107 → +7% > 3%
    data = _make_ohlcv_data("2023-01-01", [100, 101, 102, 103, 104, 105, 106, 107, 108, 109])
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        _write_temp_ohlcv(tmp, "FAKE", data)
        with patch(
            "src.trustforge.trust.outcome_labeler._ohlcv_path",
            return_value=tmp / "FAKE_daily_ohlcv.csv",
        ):
            result = label_n_day_direction("FAKE", "2023-01-01", n=7, threshold=0.03)
            assert result == "bullish", f"Expected bullish, got {result}"


def test_label_bearish():
    """Close drops 5% over 7 days → bearish."""
    data = _make_ohlcv_data("2023-01-01", [100, 99, 98, 97, 96, 95, 94, 93, 92, 91])
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        _write_temp_ohlcv(tmp, "FAKE", data)
        with patch(
            "src.trustforge.trust.outcome_labeler._ohlcv_path",
            return_value=tmp / "FAKE_daily_ohlcv.csv",
        ):
            result = label_n_day_direction("FAKE", "2023-01-01", n=7, threshold=0.03)
            assert result == "bearish", f"Expected bearish, got {result}"


def test_label_neutral_near_threshold():
    """Close changes less than threshold → neutral."""
    # Day 0 close=100, Day 7 close=101 → +1% < 3%
    data = _make_ohlcv_data("2023-01-01", [100, 100.1, 100.2, 100.3, 100.4, 100.5, 100.6, 101, 101.1, 101.2])
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        _write_temp_ohlcv(tmp, "FAKE", data)
        with patch(
            "src.trustforge.trust.outcome_labeler._ohlcv_path",
            return_value=tmp / "FAKE_daily_ohlcv.csv",
        ):
            result = label_n_day_direction("FAKE", "2023-01-01", n=7, threshold=0.03)
            assert result == "neutral", f"Expected neutral, got {result}"


def test_label_date_not_found():
    """Date not in CSV → None."""
    data = _make_ohlcv_data("2023-01-01", [100, 101])
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        _write_temp_ohlcv(tmp, "FAKE", data)
        with patch(
            "src.trustforge.trust.outcome_labeler._ohlcv_path",
            return_value=tmp / "FAKE_daily_ohlcv.csv",
        ):
            result = label_n_day_direction("FAKE", "1999-01-01", n=7)
            assert result is None


def test_label_beyond_data():
    """T+N beyond CSV range → None."""
    data = _make_ohlcv_data("2023-01-01", [100, 101])
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        _write_temp_ohlcv(tmp, "FAKE", data)
        with patch(
            "src.trustforge.trust.outcome_labeler._ohlcv_path",
            return_value=tmp / "FAKE_daily_ohlcv.csv",
        ):
            # Only 2 rows, T+7 = index 8 (out of bounds)
            result = label_n_day_direction("FAKE", "2023-01-01", n=7)
            assert result is None


def test_batch_label():
    """Batch returns dict with correct labels."""
    data = _make_ohlcv_data("2023-01-01", [100, 101, 102, 103, 104, 105, 106, 107, 108, 109])
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        _write_temp_ohlcv(tmp, "FAKE", data)
        with patch(
            "src.trustforge.trust.outcome_labeler._ohlcv_path",
            return_value=tmp / "FAKE_daily_ohlcv.csv",
        ):
            results = batch_label_from_ohlcv(
                "FAKE",
                ["2023-01-01", "2023-01-04", "2099-01-01"],
                n=7,
            )
            assert results["2023-01-01"] == "bullish"  # 100 → 107, +7%
            assert results["2023-01-04"] is None  # T+7 = index 11 (out of 10)
            assert results["2099-01-01"] is None  # date not found


def test_batch_empty_dates():
    """Empty dates list → empty dict."""
    data = _make_ohlcv_data("2023-01-01", [100, 101])
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        _write_temp_ohlcv(tmp, "FAKE", data)
        with patch(
            "src.trustforge.trust.outcome_labeler._ohlcv_path",
            return_value=tmp / "FAKE_daily_ohlcv.csv",
        ):
            results = batch_label_from_ohlcv("FAKE", [])
            assert results == {}


def test_custom_threshold():
    """Custom threshold changes label."""
    # Day 0=100, Day 7=104 → +4%. With threshold=5% → neutral; 3% → bullish
    data = _make_ohlcv_data("2023-01-01", [100, 100.5, 101, 101.5, 102, 102.5, 103, 104, 104.5, 105])
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        _write_temp_ohlcv(tmp, "FAKE", data)
        with patch(
            "src.trustforge.trust.outcome_labeler._ohlcv_path",
            return_value=tmp / "FAKE_daily_ohlcv.csv",
        ):
            result_strict = label_n_day_direction("FAKE", "2023-01-01", n=7, threshold=0.05)
            assert result_strict == "neutral"
            result_loose = label_n_day_direction("FAKE", "2023-01-01", n=7, threshold=0.03)
            assert result_loose == "bullish"


def test_exact_threshold_boundary():
    """Return just below and just above threshold."""
    # 2.99% return → neutral. 3.01% return → bullish.
    # 103/100 = 1.03 has floating point noise; use large integer prices.
    # Below: 100000 → 102990 = +2.99% → neutral
    data_below = _make_ohlcv_data("2023-01-01", [100000, 100200, 100400, 100600, 100800, 101200, 102000, 102990])
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        _write_temp_ohlcv(tmp, "FAKE", data_below)
        with patch(
            "src.trustforge.trust.outcome_labeler._ohlcv_path",
            return_value=tmp / "FAKE_daily_ohlcv.csv",
        ):
            result = label_n_day_direction("FAKE", "2023-01-01", n=7, threshold=0.03)
            assert result == "neutral", f"2.99% return + 3% threshold → neutral, got {result}"

    # Above: 100000 → 103010 = +3.01% → bullish
    data_above = _make_ohlcv_data("2023-01-01", [100000, 100200, 100400, 100600, 100800, 101200, 102000, 103010])
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        _write_temp_ohlcv(tmp, "FAKE", data_above)
        with patch(
            "src.trustforge.trust.outcome_labeler._ohlcv_path",
            return_value=tmp / "FAKE_daily_ohlcv.csv",
        ):
            result = label_n_day_direction("FAKE", "2023-01-01", n=7, threshold=0.03)
            assert result == "bullish", f"3.01% return + 3% threshold → bullish, got {result}"
