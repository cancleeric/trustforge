"""多源連接器的統一介面。

每個來源（news/social/onchain/hoyabit/regulatory）實作 Source.fetch()，
輸出標準化 Document。真實 API 在 7/13 企業數據工作坊後接上；目前以離線樣本驅動。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

SAMPLE_DIR = Path(__file__).resolve().parents[3] / "demo" / "sample_data"

# 來源類型 → 預設信譽分（0–1）。鏈上/監管最高，匿名社群最低。詳見 trust/scoring.py。
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

    def fetch(self, query: str) -> list[Document]:  # pragma: no cover - 介面
        raise NotImplementedError


class OfflineSampleSource(Source):
    """從 demo/sample_data/*.json 讀取，讓整條管線無需任何外部 API 即可跑通。"""

    def __init__(self, kind: str, name: str):
        self.kind = kind
        self.name = name

    def fetch(self, query: str) -> list[Document]:
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


def collect(query: str, sources: Iterable[Source] | None = None, offline: bool = False) -> list[Document]:
    """匯流所有來源。offline=True 時用樣本資料。"""
    if sources is None:
        if offline:
            sources = [OfflineSampleSource(k, k) for k in SOURCE_KINDS]
        else:
            # TODO(7/13 後)：接 HOYA BIT 企業數據與各真實 API 連接器。
            sources = []
    docs: list[Document] = []
    for s in sources:
        docs.extend(s.fetch(query))
    return docs
