"""真實鏈上資料連接器 (P0-2)。

來源白名單（寫死，防 SSRF）：
  - Alternative.me Fear & Greed Index  https://api.alternative.me/fng/?limit=7  (完全免費)
  - Blockchain.info stats              https://api.blockchain.info/stats         (公開，BTC only)

資料密度第二批（#24，2026-07，見 docs/PLAN-data-density.md，gray 已逐一 curl
驗證 200 OK）——全部 keyless、僅 BTC（比照 `BlockchainInfoSource` 非 BTC
跳過的既有慣例，非全市場通用信號，不列入 `COIN_AGNOSTIC_SOURCES`）：
  - mempool.space 建議手續費   https://mempool.space/api/v1/fees/recommended
  - mempool.space 難度調整進度 https://mempool.space/api/v1/difficulty-adjustment
  - Blockchair BTC 鏈上統計    https://api.blockchair.com/bitcoin/stats
    （免費層 1440 req/day，官方文件：https://blockchair.com/api/docs）

安全措施：
  - timeout 5 秒 / 回應大小上限 512 KB（超過截斷）
  - 固定 User-Agent
  - 不接受外部傳入 URL
  - SSRF-safe fetch（見 `safe_fetch.py`）：逐跳驗證（含初始 URL）scheme/
    hostname/port/私有 IP，DNS pinning 杜絕 rebinding，禁自動跟轉
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone

from . import safe_fetch
from .base import Document, Source

_MAX_BYTES = 512 * 1024   # 512 KB
_TIMEOUT = 5
_UA = "TrustForge/1.0 (research)"


def _fetch_url(url: str) -> bytes:
    """帶 timeout / 大小上限 / User-Agent 的 SSRF-safe GET（見 safe_fetch.py）。"""
    return safe_fetch.fetch_url(url, user_agent=_UA, timeout=_TIMEOUT, max_bytes=_MAX_BYTES)


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


class MempoolSpaceFeesSource(Source):
    """mempool.space BTC 建議手續費（keyless，公開，僅適用 BTC）。"""
    kind = "onchain"
    name = "mempool-space-fees"
    _URL = "https://mempool.space/api/v1/fees/recommended"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        # 非 BTC 幣種跳過（同 BlockchainInfoSource 慣例，非全市場通用信號）
        if coin and coin.upper() not in ("BTC",):
            return []
        raw = _fetch_url(self._URL)
        data = json.loads(raw)
        fastest = data.get("fastestFee", "N/A")
        half_hour = data.get("halfHourFee", "N/A")
        hour = data.get("hourFee", "N/A")
        economy = data.get("economyFee", "N/A")
        minimum = data.get("minimumFee", "N/A")
        ref = (
            f"BTC 建議手續費（sat/vB）：最快={fastest}，30分鐘={half_hour}，"
            f"1小時={hour}，經濟={economy}，最低={minimum}"
        )
        ts = time.time()
        doc_id = "onchain-mpfee-" + hashlib.md5(f"{ref}-{int(ts)}".encode()).hexdigest()[:12]
        return [Document(
            id=doc_id,
            kind="onchain",
            source=self.name,
            text=ref,
            url=self._URL,
            ts=ts,
            meta={"content_reference": ref},
        )]


class MempoolSpaceDifficultySource(Source):
    """mempool.space BTC 難度調整進度（keyless，公開，僅適用 BTC）。"""
    kind = "onchain"
    name = "mempool-space-difficulty"
    _URL = "https://mempool.space/api/v1/difficulty-adjustment"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        if coin and coin.upper() not in ("BTC",):
            return []
        raw = _fetch_url(self._URL)
        data = json.loads(raw)
        progress = data.get("progressPercent", "N/A")
        change = data.get("difficultyChange", "N/A")
        remaining = data.get("remainingBlocks", "N/A")
        try:
            progress_str = f"{float(progress):.1f}%"
        except (TypeError, ValueError):
            progress_str = str(progress)
        try:
            change_str = f"{float(change):+.2f}%"
        except (TypeError, ValueError):
            change_str = str(change)
        ref = f"BTC 難度調整進度：{progress_str}，預估變化 {change_str}，剩餘 {remaining} 區塊"
        ts = time.time()
        doc_id = "onchain-mpdiff-" + hashlib.md5(f"{ref}-{int(ts)}".encode()).hexdigest()[:12]
        return [Document(
            id=doc_id,
            kind="onchain",
            source=self.name,
            text=ref,
            url=self._URL,
            ts=ts,
            meta={"content_reference": ref},
        )]


class BlockchairStatsSource(Source):
    """Blockchair BTC 鏈上統計（公開，免費層 1440 req/day，僅適用 BTC）。"""
    kind = "onchain"
    name = "blockchair"
    _URL = "https://api.blockchair.com/bitcoin/stats"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        if coin and coin.upper() not in ("BTC",):
            return []
        raw = _fetch_url(self._URL)
        payload = json.loads(raw)
        data = payload.get("data", {})
        if not isinstance(data, dict):
            data = {}
        blocks = data.get("blocks", "N/A")
        difficulty = data.get("difficulty", "N/A")
        mempool_tx = data.get("mempool_transactions", "N/A")
        tx_24h = data.get("transactions_24h", "N/A")
        best_block_time = data.get("best_block_time", "")
        ref = (
            f"BTC 鏈上統計：區塊高度={blocks}，難度={difficulty}，"
            f"mempool 交易數={mempool_tx}，24h 交易數={tx_24h}"
        )
        try:
            ts = (
                datetime.strptime(best_block_time, "%Y-%m-%d %H:%M:%S")
                .replace(tzinfo=timezone.utc)
                .timestamp()
            )
        except (TypeError, ValueError):
            ts = time.time()
        doc_id = "onchain-bcair-" + hashlib.md5(ref.encode()).hexdigest()[:12]
        return [Document(
            id=doc_id,
            kind="onchain",
            source=self.name,
            text=ref,
            url=self._URL,
            ts=ts,
            meta={"content_reference": ref},
        )]


def build_onchain_sources() -> list[Source]:
    """回傳所有已啟用的鏈上連接器。"""
    return [
        FearGreedSource(),
        BlockchainInfoSource(),
        MempoolSpaceFeesSource(),
        MempoolSpaceDifficultySource(),
        BlockchairStatsSource(),
    ]
