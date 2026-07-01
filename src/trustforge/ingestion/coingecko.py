"""CoinGecko 真實加密貨幣資料連接器（W-coingecko，CEO 審核 gray 計劃）。

來源白名單（寫死，防 SSRF；比照 `onchain.py` 慣例）：
  - 現價（5 幣一次呼叫，免費端點）：
      GET https://api.coingecko.com/api/v3/simple/price
          ?ids=bitcoin,ethereum,solana,binancecoin,ripple&vs_currencies=usd
          &include_24hr_change=true&include_market_cap=true
  - 社群情緒 + 開發活動（逐幣各一次呼叫，免費端點）：
      GET https://api.coingecko.com/api/v3/coins/{id}
          ?localization=false&tickers=false&market_data=false
          &community_data=false&developer_data=true
      → `sentiment_votes_up_percentage` / `sentiment_votes_down_percentage`
        （CoinGeckoSentimentSource）+ `developer_data.{stars,forks,
        commit_count_4_weeks}`（CoinGeckoDevSource）。
      `community_data` free tier 恆為 null，不使用。

5 幣對映（COIN_POOL 代碼 -> CoinGecko coin id）：
  BTC->bitcoin, ETH->ethereum, SOL->solana, BNB->binancecoin, XRP->ripple
其餘幣種一律視為非目標，`fetch()` 靜默跳過（回傳 []），不會現串任意 URL。

安全措施（同 onchain.py）：
  - timeout 5 秒 / 回應大小上限 512 KB（超過截斷）
  - 固定 User-Agent
  - 不接受外部傳入 URL；URL 只由本檔內建的白名單常數 + 5 幣白名單映射組成
"""
from __future__ import annotations

import hashlib
import json
import time
from urllib.request import Request, urlopen

from .base import Document, Source

_MAX_BYTES = 512 * 1024   # 512 KB
_TIMEOUT = 5
_UA = "TrustForge/1.0 (research)"

# 5 幣白名單：COIN_POOL 代碼 -> CoinGecko coin id（見 schema.COIN_POOL）。
_COINGECKO_IDS: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
}

_PRICE_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids=" + ",".join(_COINGECKO_IDS.values())
    + "&vs_currencies=usd&include_24hr_change=true&include_market_cap=true"
)


def _fetch_url(url: str) -> bytes:
    """帶 timeout / 大小上限 / User-Agent 的 urllib GET（同 onchain.py）。"""
    req = Request(url, headers={"User-Agent": _UA})
    with urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.read(_MAX_BYTES)


def _coin_detail_url(coingecko_id: str) -> str:
    """coins/{id} 詳情端點 URL（social/developer_data 開，其餘關閉省流量）。"""
    return (
        f"https://api.coingecko.com/api/v3/coins/{coingecko_id}"
        "?localization=false&tickers=false&market_data=false"
        "&community_data=false&developer_data=true"
    )


class CoinGeckoPriceSource(Source):
    """CoinGecko 即時現價（simple/price，免費端點，一次呼叫涵蓋 5 幣）。

    `coin` 指定單一目標時只回傳該幣的 Document；`coin` 為空字串時（全市場
    通用查詢）回傳 5 幣各一筆，皆帶顯式 `meta["coin"]`（避免被
    `base._matches_coin()` 誤判成「全市場通用、每幣都納入」的兜底分支——
    現價本質上是幣種特定資料，不是市場通用訊號）。非白名單幣種一律跳過。
    """
    kind = "price_live"
    name = "coingecko-price"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        targets = [coin.upper()] if coin else list(_COINGECKO_IDS)
        targets = [t for t in targets if t in _COINGECKO_IDS]
        if not targets:
            return []
        raw = _fetch_url(_PRICE_URL)
        data = json.loads(raw)
        now = time.time()
        docs: list[Document] = []
        for code in targets:
            gid = _COINGECKO_IDS[code]
            entry = data.get(gid)
            if not isinstance(entry, dict):
                continue
            price = entry.get("usd")
            if price is None:
                continue
            change_24h = entry.get("usd_24h_change")
            mcap = entry.get("usd_market_cap")
            change_str = f"{change_24h:+.2f}%" if isinstance(change_24h, (int, float)) else "N/A"
            mcap_str = f"{mcap:,.0f}" if isinstance(mcap, (int, float)) else "N/A"
            ref = f"{code} 現價 {price} USD，24h 變動 {change_str}，市值 {mcap_str} USD"
            doc_id = "coingecko-price-" + hashlib.md5(f"{code}-{ref}".encode()).hexdigest()[:12]
            docs.append(Document(
                id=doc_id,
                kind=self.kind,
                source=self.name,
                text=ref,
                url=_PRICE_URL,
                ts=now,
                meta={"content_reference": ref, "coin": code},
            ))
        return docs


class CoinGeckoSentimentSource(Source):
    """CoinGecko 社群情緒投票百分比（coins/{id}，逐幣各一次呼叫）。

    `coin` 必須是白名單 5 幣之一才知道要打哪個 id 的端點；空字串或非白名單
    幣種一律跳過（回傳 []）——與 `CoinGeckoPriceSource` 不同，此端點無法一次
    呼叫涵蓋多幣，沒有明確目標幣就無法決定要呼叫哪個 URL。
    """
    kind = "sentiment"
    name = "coingecko-sentiment"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        code = coin.upper()
        gid = _COINGECKO_IDS.get(code)
        if gid is None:
            return []
        raw = _fetch_url(_coin_detail_url(gid))
        data = json.loads(raw)
        up = data.get("sentiment_votes_up_percentage")
        down = data.get("sentiment_votes_down_percentage")
        if up is None and down is None:
            return []
        up_str = f"{up:.1f}%" if isinstance(up, (int, float)) else "N/A"
        down_str = f"{down:.1f}%" if isinstance(down, (int, float)) else "N/A"
        ref = f"{code} 社群情緒投票：看漲 {up_str}，看跌 {down_str}"
        doc_id = "coingecko-sentiment-" + hashlib.md5(f"{code}-{ref}".encode()).hexdigest()[:12]
        return [Document(
            id=doc_id,
            kind=self.kind,
            source=self.name,
            text=ref,
            url=_coin_detail_url(gid),
            ts=time.time(),
            meta={"content_reference": ref, "coin": code},
        )]


class CoinGeckoDevSource(Source):
    """CoinGecko 開發活動（coins/{id} 的 developer_data，逐幣各一次呼叫）。

    與 `CoinGeckoSentimentSource` 打同一個端點（回應本身同時含兩種資料），
    但各自獨立呼叫、獨立 cache（不同 kind/refresh 節奏，見 `cache.py`），
    這是刻意的設計取捨，換取兩者可獨立排程/降級，不互相牽連。
    """
    kind = "dev_activity"
    name = "coingecko-dev"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        code = coin.upper()
        gid = _COINGECKO_IDS.get(code)
        if gid is None:
            return []
        raw = _fetch_url(_coin_detail_url(gid))
        data = json.loads(raw)
        dev = data.get("developer_data")
        if not isinstance(dev, dict):
            return []
        stars = dev.get("stars")
        forks = dev.get("forks")
        commits = dev.get("commit_count_4_weeks")
        if stars is None and forks is None and commits is None:
            return []
        stars_str = stars if stars is not None else "N/A"
        forks_str = forks if forks is not None else "N/A"
        commits_str = commits if commits is not None else "N/A"
        ref = (
            f"{code} 開發活動：GitHub stars {stars_str}，forks {forks_str}，"
            f"近 4 週 commits {commits_str}"
        )
        doc_id = "coingecko-dev-" + hashlib.md5(f"{code}-{ref}".encode()).hexdigest()[:12]
        return [Document(
            id=doc_id,
            kind=self.kind,
            source=self.name,
            text=ref,
            url=_coin_detail_url(gid),
            ts=time.time(),
            meta={"content_reference": ref, "coin": code},
        )]


def build_coingecko_sources() -> list[Source]:
    """回傳所有已啟用的 CoinGecko 連接器。"""
    return [CoinGeckoPriceSource(), CoinGeckoSentimentSource(), CoinGeckoDevSource()]
