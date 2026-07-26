"""Taiwan regulatory source adapters — MOPS, FSC, TWSE, TPEx.

Discovery status: 2026-07-26 — 官方端點尚未大量研究，adapters 以 stub/mock 先行。
待官方 API 規格確認後接上真實存取。
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

from .base import Document, Source

# ── 官方端點（待確認後更新）──────────────────────────────────────────
MOPS_BASE = "https://mops.twse.com.tw/mops/web/"
FSC_BASE = "https://www.fsc.gov.tw/"
TWSE_BASE = "https://www.twse.com.tw/"
TPEX_BASE = "https://www.tpex.org.tw/"

ALLOWED_TW_HOSTS = frozenset({
    "mops.twse.com.tw",
    "www.fsc.gov.tw",
    "www.twse.com.tw",
    "www.tpex.org.tw",
})


class TaiwanRegulatorySource(Source):
    """Base class for Taiwan regulatory sources with fail-closed design.

    - 認證失敗/403/schema drift → 回空清單，不拋例外
    - 同一官方公告的鏡像不能算多票（dedup by URL + content hash）
    - PIT 邊界：排除分析時間後發布的資料
    - 無資料時誠實留白
    """
    kind = "regulatory"
    name = "taiwan-base"
    _timeout: float = 10.0

    def _validate_host(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.hostname in ALLOWED_TW_HOSTS

    def _build_document(self, *, source: str, title: str, url: str,
                         text: str, published_at: str) -> Document:
        ts = datetime.fromisoformat(published_at).timestamp()
        return Document(
            id=f"tw-reg:{source}:{hash(text) & 0xFFFFFFFF:08x}",
            kind="regulatory",
            source=source,
            text=f"{title}\n{text}",
            url=url,
            ts=ts,
            meta={
                "published_at": published_at,
                "source_region": "TW",
                "adapter_status": "stub",  # → "live" after real API
            },
        )


class MOPSSource(TaiwanRegulatorySource):
    """MOPS（公開資訊觀測站）— 上市櫃公司重大訊息。

    Stub: 等待官方 API endpoint 確認。
    """
    name = "mops"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        # Stub — no real fetch until API confirmed
        return []


class FSCSource(TaiwanRegulatorySource):
    """FSC（金融監督管理委員會）— 新聞稿、VASP 警示清單。

    Stub: 等待官方 API endpoint 確認。
    """
    name = "fsc"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        # Stub — no real fetch until API confirmed
        return []


class TWSESource(TaiwanRegulatorySource):
    """TWSE（台灣證券交易所）— 公告、交易資訊。

    Stub: 等待官方 API endpoint 確認。
    """
    name = "twse"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        # Stub — no real fetch until API confirmed
        return []


class TPEXSource(TaiwanRegulatorySource):
    """TPEx（證券櫃檯買賣中心）— 上櫃公司公告。

    Stub: 等待官方 API endpoint 確認。
    """
    name = "tpex"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        # Stub — no real fetch until API confirmed
        return []
