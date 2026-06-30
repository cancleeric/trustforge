"""OHLCV 價格連接器 — 讀主辦提供的基準 CSV（date,open,high,low,close,volume）。

把價格資料轉成客觀「事實型」Document（kind=price），並在 meta 帶足以回溯的
content_reference（交易對 / 日期區間 / 指標數值 / 檔名），供 Evidence List 使用。
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

from .base import Document

OHLCV_FIELDS = ("date", "open", "high", "low", "close", "volume")


@dataclass
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


def load_ohlcv(coin: str, data_dir: str | Path) -> list[Bar]:
    """讀指定目錄的 OHLCV CSV，依日期排序回傳。

    依序嘗試官方命名與簡名：
      {COIN}_daily_ohlcv.csv（HOYA BIT 官方）/ {COIN}.csv / {COIN}USDT.csv
    """
    coin = coin.upper()
    d = Path(data_dir)
    f = next((d / n for n in (f"{coin}_daily_ohlcv.csv", f"{coin}.csv", f"{coin}USDT.csv")
              if (d / n).exists()), None)
    if f is None:
        return []
    bars: list[Bar] = []
    with f.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                bars.append(Bar(
                    date=row["date"],
                    open=float(row["open"]), high=float(row["high"]),
                    low=float(row["low"]), close=float(row["close"]),
                    volume=float(row["volume"]),
                ))
            except (KeyError, ValueError):
                continue  # 跳過壞行，不崩
    bars.sort(key=lambda b: b.date)
    return bars


def _pct(a: float, b: float) -> float:
    return 0.0 if a == 0 else (b - a) / a * 100.0


def _volatility(closes: list[float]) -> float:
    """日報酬標準差（%）。"""
    rets = [_pct(closes[i - 1], closes[i]) for i in range(1, len(closes))]
    if not rets:
        return 0.0
    m = sum(rets) / len(rets)
    return math.sqrt(sum((r - m) ** 2 for r in rets) / len(rets))


def price_facts(coin: str, bars: list[Bar], window: int = 14,
                source_file: str = "ohlcv.csv", ts: float = 0.0) -> list[Document]:
    """從 OHLCV 算出客觀事實，每條一個 Document（高信任、可回溯）。"""
    if len(bars) < 2:
        return []
    coin = coin.upper()
    seg = bars[-window:] if len(bars) >= window else bars
    start, end = seg[0], seg[-1]
    closes = [b.close for b in seg]
    ret = _pct(start.close, end.close)
    vol = _volatility(closes)
    hi = max(b.high for b in seg)
    lo = min(b.low for b in seg)
    vol_recent = sum(b.volume for b in seg[-3:]) / max(1, len(seg[-3:]))
    vol_earlier = sum(b.volume for b in seg[:3]) / max(1, len(seg[:3]))
    vol_trend = _pct(vol_earlier, vol_recent)
    pair = f"{coin}/USDT"
    period = f"{start.date}~{end.date}"

    def fact(fid: str, text: str, ref: str) -> Document:
        return Document(
            id=fid, kind="price", source="ohlcv-csv",
            text=text, url="", ts=ts,
            meta={"content_reference": ref, "trading_pair": pair,
                  "date_range": period, "source_file": source_file},
        )

    direction = "上漲" if ret > 1 else "下跌" if ret < -1 else "盤整"
    return [
        fact(f"price-{coin}-ret",
             f"{coin} 近 {len(seg)} 日收盤從 {start.close:g} 變動至 {end.close:g}，"
             f"報酬 {ret:+.1f}%，呈{direction}。",
             f"{pair} {period} close {start.close:g}->{end.close:g} ({ret:+.1f}%)"),
        fact(f"price-{coin}-vol",
             f"{coin} 近 {len(seg)} 日日報酬波動度約 {vol:.1f}%，區間高低 {lo:g}~{hi:g}。",
             f"{pair} {period} daily-return stdev {vol:.2f}%, range {lo:g}-{hi:g}"),
        fact(f"price-{coin}-volume",
             f"{coin} 近期成交量相對區間初期變化 {vol_trend:+.0f}%。",
             f"{pair} {period} volume trend {vol_trend:+.1f}% (recent3 vs first3)"),
    ]
