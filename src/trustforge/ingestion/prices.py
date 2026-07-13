"""OHLCV 價格連接器 — 讀主辦提供的基準 CSV（date,open,high,low,close,volume）。

把價格資料轉成客觀「事實型」Document（kind=price），並在 meta 帶足以回溯的
content_reference（交易對 / 日期區間 / 指標數值 / 檔名），供 Evidence List 使用。
"""
from __future__ import annotations

import csv
import hashlib
import json
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


def _ohlcv_file(coin: str, data_dir: str | Path) -> Path | None:
    """Resolve the accepted official/local filename without exposing its path."""
    d = Path(data_dir)
    return next(
        (d / name for name in (f"{coin.upper()}_daily_ohlcv.csv", f"{coin.upper()}.csv", f"{coin.upper()}USDT.csv")
         if (d / name).exists()),
        None,
    )


def load_ohlcv(coin: str, data_dir: str | Path) -> list[Bar]:
    """讀指定目錄的 OHLCV CSV，依日期排序回傳。

    依序嘗試官方命名與簡名：
      {COIN}_daily_ohlcv.csv（HOYA BIT 官方）/ {COIN}.csv / {COIN}USDT.csv
    """
    coin = coin.upper()
    f = _ohlcv_file(coin, data_dir)
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


def ohlcv_lineage(coin: str, data_dir: str | Path, bars: list[Bar]) -> dict:
    """Return an export-safe lineage record for a supplied OHLCV file.

    The competition may audit a numerical claim back to its supplied five-year
    dataset.  A filename alone is not enough: this record pins the exact file
    bytes, schema, full coverage, and supplied metadata without disclosing an
    absolute local path.
    """
    source_file = _ohlcv_file(coin, data_dir)
    if source_file is None or not bars:
        return {}

    metadata: dict = {}
    metadata_path = Path(data_dir).parent / "dataset_metadata.json"
    if metadata_path.is_file():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            metadata = {}
    symbol = next(
        (item for item in metadata.get("symbols", []) if item.get("asset") == coin.upper()), {}
    )
    return {
        "dataset_role": "competition_baseline" if metadata else "local_ohlcv",
        "dataset_name": metadata.get("dataset_name", "Local OHLCV CSV"),
        "dataset_generated_at": metadata.get("generated_at", ""),
        "file": source_file.name,
        "sha256": hashlib.sha256(source_file.read_bytes()).hexdigest(),
        "rows": len(bars),
        "coverage": {"start_date": bars[0].date, "end_date": bars[-1].date},
        "trading_pair": symbol.get("pair", f"{coin.upper()}USDT"),
        "time_basis": metadata.get("time_basis", "UTC"),
        "interval": metadata.get("interval", "1d"),
        "price_unit": metadata.get("price_unit", "USDT"),
        "columns": list(OHLCV_FIELDS),
    }


def latest_bar_date(coin: str, data_dir: str | Path) -> str | None:
    """讀指定目錄的 OHLCV CSV，回傳最後一筆日期字串（`YYYY-MM-DD`）；查無資料
    （檔案不存在/空檔）回 `None`，不猜測、不補一個假日期。

    世界第一重寫 Phase 2：供 `web.py` 首頁/`/analyze` 預設查詢文案動態顯示
    「基準資料涵蓋至 {日期}」，取代先前寫死的「近兩週」措辭——HOYA OHLCV
    是定期更新的官方基準檔（非即時串流），寫死的相對時間字樣會隨資料未
    同步更新而逐漸變成對判審的誤導破綻，日期必須每次動態讀 CSV 算出。
    """
    bars = load_ohlcv(coin, data_dir)
    return bars[-1].date if bars else None


def _pct(a: float, b: float) -> float:
    return 0.0 if a == 0 else (b - a) / a * 100.0


def _volatility(closes: list[float]) -> float:
    """日報酬標準差（%）。"""
    rets = [_pct(closes[i - 1], closes[i]) for i in range(1, len(closes))]
    if not rets:
        return 0.0
    m = sum(rets) / len(rets)
    return math.sqrt(sum((r - m) ** 2 for r in rets) / len(rets))


def _max_drawdown(closes: list[float]) -> float:
    peak = closes[0]
    worst = 0.0
    for close in closes:
        peak = max(peak, close)
        worst = min(worst, _pct(peak, close))
    return worst


def price_facts(coin: str, bars: list[Bar], window: int = 14,
                source_file: str = "ohlcv.csv", ts: float = 0.0,
                data_lineage: dict | None = None) -> list[Document]:
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

    def fact(fid: str, text: str, ref: str, *, analysis_window: str, **extra_meta: float) -> Document:
        lineage = dict(data_lineage or {})
        if lineage:
            lineage["analysis_window"] = analysis_window
        return Document(
            id=fid, kind="price", source="ohlcv-csv",
            text=text, url="", ts=ts,
            meta={"content_reference": ref, "trading_pair": pair,
                  "date_range": period, "source_file": source_file,
                  "data_lineage": lineage, **extra_meta},
        )

    direction = "上漲" if ret > 1 else "下跌" if ret < -1 else "盤整"
    return [
        fact(f"price-{coin}-ret",
             f"{coin} 近 {len(seg)} 日收盤從 {start.close:g} 變動至 {end.close:g}，"
             f"報酬 {ret:+.1f}%，呈{direction}。",
             f"{pair} {period} close {start.close:g}->{end.close:g} ({ret:+.1f}%)",
             analysis_window=period,
             ret_pct=ret),
        fact(f"price-{coin}-vol",
             f"{coin} 近 {len(seg)} 日日報酬波動度約 {vol:.1f}%，區間高低 {lo:g}~{hi:g}。",
             f"{pair} {period} daily-return stdev {vol:.2f}%, range {lo:g}-{hi:g}",
             analysis_window=period),
        fact(f"price-{coin}-volume",
             f"{coin} 近期成交量相對區間初期變化 {vol_trend:+.0f}%。",
             f"{pair} {period} volume trend {vol_trend:+.1f}% (recent3 vs first3)",
             analysis_window=period,
             volume_trend_pct=vol_trend),
        fact(
            f"price-{coin}-five-year-context",
            f"{coin} 官方基準完整歷史涵蓋 {bars[0].date}~{bars[-1].date}（{len(bars)} 日），"
            f"累計報酬 {_pct(bars[0].close, bars[-1].close):+.1f}%，"
            f"最大回撤 {_max_drawdown([b.close for b in bars]):.1f}%。",
            f"{pair} full-history {bars[0].date}~{bars[-1].date}; "
            f"{len(bars)} daily bars; close {bars[0].close:g}->{bars[-1].close:g}",
            analysis_window=f"{bars[0].date}~{bars[-1].date}",
            full_history_return_pct=_pct(bars[0].close, bars[-1].close),
        ),
    ]
