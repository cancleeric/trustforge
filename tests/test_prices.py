"""OHLCV 價格連接器測試。"""
from pathlib import Path

from trustforge.ingestion.prices import load_ohlcv, price_facts

DATA = Path(__file__).resolve().parents[1] / "demo" / "sample_data" / "ohlcv"


def test_load_ohlcv_sorted_and_typed():
    bars = load_ohlcv("BTC", DATA)
    assert len(bars) >= 30
    assert bars == sorted(bars, key=lambda b: b.date)
    assert isinstance(bars[0].close, float)


def test_price_facts_have_traceable_reference():
    bars = load_ohlcv("BTC", DATA)
    facts = price_facts("BTC", bars, source_file="BTC.csv")
    assert facts, "應產出價格事實"
    for d in facts:
        assert d.kind == "price"
        assert d.meta.get("content_reference"), "每條事實需有可回溯的 content_reference"
        assert d.meta.get("trading_pair") == "BTC/USDT"


def test_btc_sample_is_downtrend():
    """樣本 BTC 設計為緩跌，方向事實應為下跌。"""
    bars = load_ohlcv("BTC", DATA)
    ret_fact = price_facts("BTC", bars)[0]
    assert "下跌" in ret_fact.text
