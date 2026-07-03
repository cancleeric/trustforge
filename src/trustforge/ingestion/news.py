"""真實新聞連接器 (P0-1)。

來源白名單（寫死，防 SSRF）：
  - CoinDesk RSS  https://www.coindesk.com/arc/outboundfeeds/rss   (公開，免 key)
  - Decrypt RSS   https://decrypt.co/feed                          (公開，免 key)
  - CryptoPanic   https://cryptopanic.com/api/v1/posts/            (選用，需 env CRYPTOPANIC_TOKEN)

資料密度第一批（#24，2026-07，見 docs/PLAN-data-density.md，gray 已逐一 curl
驗證 200 OK）——全部 keyless、公開 RSS，複用同一套 `_parse_rss`：
  - CoinTelegraph    https://cointelegraph.com/rss
  - Bitcoin Magazine https://bitcoinmagazine.com/feed
  - CryptoSlate      https://cryptoslate.com/feed/
  - Bitcoinist       https://bitcoinist.com/feed/
  - NewsBTC          https://www.newsbtc.com/feed/
  - The Daily Hodl   https://dailyhodl.com/feed/

生產事故修復（coindesk 全 308 Permanent Redirect）：CoinDesk 把舊網址
`.../rss/`（末尾帶斜線）永久重導到 `.../rss`（無斜線），同網域、只差路徑
末尾斜線。已直接改用新網址（見下方 `CoinDeskRSSSource._URL`），不必再依賴
redirect 才能拿到內容。

安全事故修復（codex 對抗審發現，2026-07，逐輪加深）：
  第 1 輪：`urlopen()` 預設會安裝 `HTTPRedirectHandler`，對 301/302/303/
  307**在所有 Python 版本**、對 308**在 Python 3.11+**都會**自動跟轉**，
  完全不檢查目的地 host/scheme/是否為私有 IP（本專案 Dockerfile 用
  `python:3.12-slim`，正是這個高風險版本）——第一版寫在
  `except HTTPError` 裡的「同 host + https」檢查因此形同虛設。
  第 2 輪（本模組當時的修法）：改用自訂 `HTTPRedirectHandler` 禁用自動
  跟轉，手動逐跳驗證轉址目標（scheme/hostname/port/私有 IP）。
  第 3 輪（HIGH，本模組現況已修復）：第 2 輪只驗證了「轉址目的地」，
  **初始白名單 URL 本身完全沒驗證**——網域 DNS 被污染/誤配置可讓第一個
  請求就直接 SSRF；且就算驗證了轉址目標，`urlopen()`/`http.client` 連線
  時還是會用 hostname 重新解析一次 DNS，「檢查」跟「連線」之間有 rebinding
  窗口。修法：抽成共用模組 `safe_fetch.py`——每一跳（含初始 URL）都驗證，
  驗證用的 IP 直接 DNS pinning 給實際連線用，不再讓連線階段重新解析
  hostname；套用到所有連接器（news/coingecko/onchain/regulatory/social），
  不只本模組。詳見 `safe_fetch.py` 模組說明。

安全措施：
  - timeout 5 秒 / 回應大小上限 512 KB（超過截斷）
  - 固定 User-Agent
  - 不接受外部傳入 URL
"""
from __future__ import annotations

import hashlib
import json
import os
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from . import safe_fetch
from .base import Document, Source

_MAX_BYTES = 512 * 1024   # 512 KB
_TIMEOUT = 5
_UA = "TrustForge/1.0 (research)"


def _fetch_url(url: str) -> bytes:
    """帶 timeout / 大小上限 / User-Agent 的 SSRF-safe GET（見 safe_fetch.py：
    逐跳驗證含初始 URL、DNS pinning、禁自動跟轉、最多 3 跳）。"""
    return safe_fetch.fetch_url(url, user_agent=_UA, timeout=_TIMEOUT, max_bytes=_MAX_BYTES)


def _parse_ts(text: str) -> float:
    """嘗試 RFC 2822（pubDate）→ ISO 8601 → 0.0。"""
    text = text.strip()
    try:
        return parsedate_to_datetime(text).timestamp()
    except Exception:
        pass
    try:
        from datetime import datetime, timezone
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _first(item: ET.Element, *tags: str, ns: dict | None = None) -> ET.Element | None:
    """依序嘗試多個 tag，回傳第一個非 None 的 Element（規避 ElementTree 元素 bool 陷阱）。"""
    _ns = ns or {}
    for tag in tags:
        el = item.find(tag, _ns)
        if el is not None:
            return el
    return None


def _parse_rss(raw: bytes, source_name: str, query: str, coin: str) -> list[Document]:
    """解析 RSS 2.0 / Atom XML，依 query/coin 關鍵字過濾，回傳 Document list。"""
    root = ET.fromstring(raw)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item")
    if not items:
        items = root.findall(".//atom:entry", ns)

    keywords = [kw.lower() for kw in (query, coin) if kw]
    docs: list[Document] = []

    for item in items:
        title_el = _first(item, "title", "atom:title", ns=ns)
        link_el = _first(item, "link", "atom:link", ns=ns)
        desc_el = _first(item, "description", "atom:summary", "atom:content", ns=ns)
        pub_el = _first(item, "pubDate", "atom:published", "atom:updated", ns=ns)

        title = (title_el.text or "").strip() if title_el is not None else ""
        # Atom <link> 可能是 href 屬性，無文字
        if link_el is not None:
            link = link_el.get("href") or (link_el.text or "").strip()
        else:
            link = ""
        desc = (desc_el.text or "").strip() if desc_el is not None else ""
        ts = _parse_ts(pub_el.text) if pub_el is not None and pub_el.text else 0.0

        # 關鍵字過濾（無關鍵字時全收；有關鍵字時至少命中一個）
        if keywords:
            combined = (title + " " + desc).lower()
            if not any(kw in combined for kw in keywords):
                continue

        # content_reference = 標題 + 摘要前 120 字
        snippet = (title + " " + desc)[:120].strip()

        doc_id = "news-" + hashlib.md5((source_name + link + title).encode()).hexdigest()[:12]
        docs.append(Document(
            id=doc_id,
            kind="news",
            source=source_name,
            text=title or desc[:200],
            url=link,
            ts=ts,
            meta={"content_reference": snippet},
        ))

    return docs


class CoinDeskRSSSource(Source):
    """CoinDesk RSS，公開無 key。

    ⚠️ URL 末尾**不帶**斜線：`.../rss/`（帶斜線）已被 CoinDesk 永久重導
    （308）到 `.../rss`（無斜線），生產環境曾因此全部收到
    `HTTP Error 308: Permanent Redirect` 而拿不到任何新聞。
    """
    kind = "news"
    name = "coindesk"
    _URL = "https://www.coindesk.com/arc/outboundfeeds/rss"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        raw = _fetch_url(self._URL)
        return _parse_rss(raw, self.name, query, coin)


class DecryptRSSSource(Source):
    """Decrypt RSS，公開無 key。"""
    kind = "news"
    name = "decrypt"
    _URL = "https://decrypt.co/feed"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        raw = _fetch_url(self._URL)
        return _parse_rss(raw, self.name, query, coin)


class CoinTelegraphRSSSource(Source):
    """CoinTelegraph RSS，公開無 key。"""
    kind = "news"
    name = "cointelegraph"
    _URL = "https://cointelegraph.com/rss"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        raw = _fetch_url(self._URL)
        return _parse_rss(raw, self.name, query, coin)


class BitcoinMagazineRSSSource(Source):
    """Bitcoin Magazine RSS，公開無 key。"""
    kind = "news"
    name = "bitcoinmagazine"
    _URL = "https://bitcoinmagazine.com/feed"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        raw = _fetch_url(self._URL)
        return _parse_rss(raw, self.name, query, coin)


class CryptoSlateRSSSource(Source):
    """CryptoSlate RSS，公開無 key。"""
    kind = "news"
    name = "cryptoslate"
    _URL = "https://cryptoslate.com/feed/"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        raw = _fetch_url(self._URL)
        return _parse_rss(raw, self.name, query, coin)


class BitcoinistRSSSource(Source):
    """Bitcoinist RSS，公開無 key。"""
    kind = "news"
    name = "bitcoinist"
    _URL = "https://bitcoinist.com/feed/"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        raw = _fetch_url(self._URL)
        return _parse_rss(raw, self.name, query, coin)


class NewsBTCRSSSource(Source):
    """NewsBTC RSS，公開無 key。"""
    kind = "news"
    name = "newsbtc"
    _URL = "https://www.newsbtc.com/feed/"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        raw = _fetch_url(self._URL)
        return _parse_rss(raw, self.name, query, coin)


class DailyHodlRSSSource(Source):
    """The Daily Hodl RSS，公開無 key。"""
    kind = "news"
    name = "dailyhodl"
    _URL = "https://dailyhodl.com/feed/"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        raw = _fetch_url(self._URL)
        return _parse_rss(raw, self.name, query, coin)


class CryptoPanicSource(Source):
    """CryptoPanic API（需 env CRYPTOPANIC_TOKEN；無 token 時安靜回空）。"""
    kind = "news"
    name = "cryptopanic"
    _BASE = "https://cryptopanic.com/api/v1/posts/"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        token = os.getenv("CRYPTOPANIC_TOKEN", "")
        if not token:
            return []
        currencies = coin.upper() if coin else "BTC"
        url = f"{self._BASE}?auth_token={token}&currencies={currencies}&public=true"
        raw = _fetch_url(url)
        data = json.loads(raw)
        docs: list[Document] = []
        for post in data.get("results", []):
            title = post.get("title", "")
            link = post.get("url", "")
            published_at = post.get("published_at", "")
            ts = _parse_ts(published_at) if published_at else 0.0
            snippet = title[:120]
            doc_id = "news-cp-" + hashlib.md5((link + title).encode()).hexdigest()[:12]
            docs.append(Document(
                id=doc_id,
                kind="news",
                source=self.name,
                text=title,
                url=link,
                ts=ts,
                meta={"content_reference": snippet},
            ))
        return docs


def build_news_sources() -> list[Source]:
    """回傳所有已啟用的新聞連接器（CryptoPanic 僅在 token 存在時加入）。"""
    sources: list[Source] = [
        CoinDeskRSSSource(),
        DecryptRSSSource(),
        CoinTelegraphRSSSource(),
        BitcoinMagazineRSSSource(),
        CryptoSlateRSSSource(),
        BitcoinistRSSSource(),
        NewsBTCRSSSource(),
        DailyHodlRSSSource(),
    ]
    if os.getenv("CRYPTOPANIC_TOKEN"):
        sources.append(CryptoPanicSource())
    return sources
