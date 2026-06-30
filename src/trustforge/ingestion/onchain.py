"""真實鏈上資料連接器 (P0-2)。

來源白名單（寫死，防 SSRF）：
  - Alternative.me Fear & Greed Index  https://api.alternative.me/fng/?limit=7  (完全免費)
  - Blockchain.info stats              https://api.blockchain.info/stats         (公開，BTC only)

安全措施：
  - timeout 5 秒 / 回應大小上限 512 KB（超過截斷）
  - 固定 User-Agent
  - 不接受外部傳入 URL
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from .base import Document, Source

_MAX_BYTES = 512 * 1024   # 512 KB
_TIMEOUT = 5
_UA = "TrustForge/1.0 (research)"


def _fetch_url(url: str) -> bytes:
    """帶 timeout / 大小上限 / User-Agent 的 urllib GET。"""
    req = Request(url, headers={"User-Agent": _UA})
    with urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.read(_MAX_BYTES)


class FearGreedSource(Source):
    """Alternative.me 恐懼貪婪指數（完全免費，所有幣種通用）。"""
    kind = "onchain"
    name = "alternative-me-fng"
    _URL = "https://api.alternative.me/fng/?limit=7"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        raw = _fetch_url(self._URL)
        data = json.loads(raw)
        docs: list[Document] = []
        for entry in data.get("data", []):
            value = entry.get("value", "")
            classification = entry.get("value_classification", "")
            ts_str = entry.get("timestamp", "")
            try:
                ts = float(ts_str)
            except (ValueError, TypeError):
                ts = 0.0
            if ts > 0:
                date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            else:
                date_str = ""
            ref = f"Fear & Greed Index: {value} ({classification}), {date_str}"
            doc_id = "onchain-fng-" + hashlib.md5(ref.encode()).hexdigest()[:12]
            docs.append(Document(
                id=doc_id,
                kind="onchain",
                source=self.name,
                text=ref,
                url=self._URL,
                ts=ts,
                meta={"content_reference": ref},
            ))
        return docs


class BlockchainInfoSource(Source):
    """Blockchain.info BTC 全網統計（公開，免 key，僅適用 BTC）。"""
    kind = "onchain"
    name = "blockchain-info"
    _URL = "https://api.blockchain.info/stats"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        # 非 BTC 幣種跳過
        if coin and coin.upper() not in ("BTC",):
            return []
        raw = _fetch_url(self._URL)
        data = json.loads(raw)
        # blockchain.info/stats returns timestamp in **milliseconds**; divide by 1000
        ts_raw = float(data.get("timestamp", datetime.now(tz=timezone.utc).timestamp() * 1000))
        ts = ts_raw / 1000
        market_price = data.get("market_price_usd", "N/A")
        hash_rate = data.get("hash_rate", "N/A")
        ref = f"BTC market_price_usd={market_price}, hash_rate={hash_rate}"
        doc_id = "onchain-binfo-" + hashlib.md5(ref.encode()).hexdigest()[:12]
        return [Document(
            id=doc_id,
            kind="onchain",
            source=self.name,
            text=f"BTC 市場價格：{market_price} USD，算力：{hash_rate} GH/s",
            url=self._URL,
            ts=ts,
            meta={"content_reference": ref},
        )]


def build_onchain_sources() -> list[Source]:
    """回傳所有已啟用的鏈上連接器。"""
    return [FearGreedSource(), BlockchainInfoSource()]
