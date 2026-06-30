"""多源連接器的統一介面。

每個來源（news/social/onchain/hoyabit/regulatory）實作 Source.fetch()，
輸出標準化 Document。真實 API 在 7/13 企業數據工作坊後接上；目前以離線樣本驅動。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# 資料根目錄：預設為 repo 根（src 上一層）；Lambda 等打包環境用 TRUSTFORGE_HOME 覆寫
# （Lambda 把 trustforge/ data/ demo/ 都放在 /var/task，設 TRUSTFORGE_HOME=/var/task）。
_HOME = Path(os.getenv("TRUSTFORGE_HOME", Path(__file__).resolve().parents[3]))
SAMPLE_DIR = _HOME / "demo" / "sample_data"
OHLCV_DIR = SAMPLE_DIR / "ohlcv"                 # 合成樣本（測試/快速 demo）
OFFICIAL_OHLCV_DIR = _HOME / "data" / "data"     # HOYA BIT 官方基準 OHLCV

# 文件型來源類型（有對應的 sample_data/*.json）。price 走 OHLCV CSV，另行處理。
SOURCE_KINDS = ("onchain", "regulatory", "hoyabit", "news", "social")


@dataclass
class Document:
    id: str
    kind: str            # SOURCE_KINDS 之一
    source: str          # 來源名稱，如 "coindesk" / "hoyabit-ticker"
    text: str            # 原文
    url: str = ""
    ts: float = 0.0      # epoch 秒；用於時效衰減
    meta: dict = field(default_factory=dict)


class Source:
    """連接器基底。子類別實作 fetch()。"""

    kind: str = "news"
    name: str = "base"

    def fetch(self, query: str, coin: str = "") -> list[Document]:  # pragma: no cover - 介面
        raise NotImplementedError


class OfflineSampleSource(Source):
    """從 demo/sample_data/*.json 讀取，讓整條管線無需任何外部 API 即可跑通。"""

    def __init__(self, kind: str, name: str):
        self.kind = kind
        self.name = name

    def fetch(self, query: str, coin: str = "") -> list[Document]:  # noqa: ARG002
        f = SAMPLE_DIR / f"{self.kind}.json"
        if not f.exists():
            return []
        raw = json.loads(f.read_text(encoding="utf-8"))
        return [
            Document(
                id=d["id"], kind=self.kind, source=d.get("source", self.name),
                text=d["text"], url=d.get("url", ""), ts=d.get("ts", 0.0),
                meta=d.get("meta", {}),
            )
            for d in raw
        ]


def collect(query: str, coin: str | None = None,
            sources: Iterable[Source] | None = None,
            offline: bool = False, data_dir=None,
            _failed: list | None = None) -> list[Document]:
    """匯流所有來源（文件型 + OHLCV 價格事實）。offline=True 時用樣本資料。

    _failed：可選 list，失敗的來源名稱（source.name）會被 append 進去，
             供呼叫者（如 pipeline.run）填入 report.limits。
    """
    docs: list[Document] = []

    # 1. 價格事實（官方基準 OHLCV）
    if coin:
        from .prices import load_ohlcv, price_facts
        # 顯式 data_dir 優先；否則官方資料存在就用官方，再退合成樣本（offline）。
        if data_dir:
            d = data_dir
        elif OFFICIAL_OHLCV_DIR.exists():
            d = OFFICIAL_OHLCV_DIR
        elif offline:
            d = OHLCV_DIR
        else:
            d = None
        if d:
            bars = load_ohlcv(coin, d)
            docs.extend(price_facts(coin, bars, source_file=f"{coin.upper()}_daily_ohlcv.csv",
                                    ts=_latest_bar_ts(bars)))

    # 2. 文件型來源
    if sources is None:
        if offline:
            sources = [OfflineSampleSource(k, k) for k in SOURCE_KINDS]
        else:
            # 線上模式：延遲匯入以避免循環依賴
            from .news import build_news_sources
            from .onchain import build_onchain_sources
            from .social import build_social_sources
            from .regulatory import build_regulatory_sources
            sources = (
                build_news_sources()
                + build_onchain_sources()
                + build_social_sources()
                + build_regulatory_sources()
            )
    _coin = coin or ""
    for s in sources:
        try:
            docs.extend(s.fetch(query, coin=_coin))
        except Exception:
            # 單一來源失敗（逾時 / 網路錯誤）→ 跳過不崩，其他來源照常
            if _failed is not None:
                _failed.append(getattr(s, "name", str(s)))
    return docs


def _latest_bar_ts(bars) -> float:
    """用最後一根 K 的日期當價格事實的 fetched_at（UTC 當日 00:00）。"""
    if not bars:
        return 0.0
    from datetime import datetime, timezone
    try:
        dt = datetime.strptime(bars[-1].date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0
