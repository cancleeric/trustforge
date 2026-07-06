"""真實新聞連接器 (P0-1)。

來源白名單（寫死，防 SSRF）：
  - CoinDesk RSS  https://www.coindesk.com/arc/outboundfeeds/rss   (公開，免 key)
  - Decrypt RSS   https://decrypt.co/feed                          (公開，免 key)
  - CryptoPanic   https://cryptopanic.com/api/v1/posts/            (選用，需 env CRYPTOPANIC_TOKEN)

資料密度第一批（#24，2026-07，見 docs/archive/plans/PLAN-data-density.md，gray 已逐一 curl
驗證 200 OK）——全部 keyless、公開 RSS，複用同一套 `_parse_rss`：
  - CoinTelegraph    https://cointelegraph.com/rss
  - Bitcoin Magazine https://bitcoinmagazine.com/feed
  - CryptoSlate      https://cryptoslate.com/feed/
  - Bitcoinist       https://bitcoinist.com/feed/
  - NewsBTC          https://www.newsbtc.com/feed/
  - The Daily Hodl   https://dailyhodl.com/feed/

資料密度第二批（#24，2026-07，見 docs/archive/plans/PLAN-data-density.md，gray 已逐一 curl
驗證 200 OK）——同樣全部 keyless、公開 RSS，複用 `_parse_rss`。The Block／
Blockworks 的原始網址（`theblock.co`/`blockworks.co`）會 308 轉到下列終點，
比照 coindesk 教訓直接寫終點 URL，不依賴轉址：
  - The Block   https://www.theblock.co/rss.xml
  - U.Today     https://u.today/rss.php
  - Blockworks  https://blockworks.com/feed（回傳 Atom，`_parse_rss` 已相容）

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
from urllib.parse import urlsplit

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


def _is_valid_http_link(link: str) -> bool:
    """codex MEDIUM 第 7 輪（PR #55，RSS 資料品質最終閉合）：舊版只檢查
    `startswith(("http://", "https://"))`，會把 `https://`（無 host）、
    `https:///article`（空 host）這類殘缺 URL 當合法——子欄位 drift 後
    若 link 剛好長這樣，仍會通過結構驗證、把不可用 URL 寫進 Document
    覆蓋舊快取。改用 `urlsplit` 嚴格驗（比照 `web.py:_safe_href` 的
    http/https 白名單慣例，額外加 host/credentials/控制字元/port 驗證）：
      - scheme 嚴格限定 http/https（大小寫不拘）
      - hostname 必須非空
      - 拒絕帶 credentials 的 URL（`user:pass@host`）
      - 拒絕含控制字元的字串
      - port（若有）必須合法，否則視為無效
    """
    if not link:
        return False
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in link):
        return False
    try:
        parts = urlsplit(link)
    except ValueError:
        return False
    if parts.scheme.lower() not in {"http", "https"}:
        return False
    if not parts.hostname:
        return False
    if parts.username or parts.password:
        return False
    try:
        parts.port  # 存取即觸發驗證，非法 port（如超出範圍）會拋 ValueError
    except ValueError:
        return False
    return True


def _entry_is_structurally_valid(title: str, desc: str, link: str, ts: float) -> bool:
    """codex HIGH 第 6 輪 + MEDIUM 第 7 輪（PR #55，RSS 資料品質最終閉合）：
    單筆 entry 是否有「有意義的內容」——非空 title 或 description、可用
    （絕對、有效 host 的 http(s)，見 `_is_valid_http_link()`）link、可解析
    且非 0 的發布時間戳。三者缺一即視為子欄位 drift（供應商改名/換
    namespace 導致 `_first()` 找不到對應欄位，或 link 殘缺無 host），這筆
    entry 不能被拿去發布空白 title/空/無效 URL/ts=0 的垃圾文件。"""
    has_content = bool(title or desc)
    has_link = _is_valid_http_link(link)
    has_ts = ts != 0.0
    return has_content and has_link and has_ts


def _parse_rss(raw: bytes, source_name: str, query: str, coin: str) -> list[Document]:
    """解析 RSS 2.0 / Atom XML，依 query/coin 關鍵字過濾，回傳 Document list。

    這是所有 RSS 源共用的 parser（coindesk/decrypt 起算共 11 家），區分
    兩層「空」，別誤傷合法情境（codex MEDIUM 第 4 輪 + HIGH 第 6 輪，PR
    #55，資料密度第二批最終閉合）：
      - **容器層**：合法可解析但一個 `<item>`/`<entry>` 都沒有（換
        namespace/schema、或回了個錯誤頁但仍是合法 XML）→ schema drift。
      - **子欄位層**：有 `<item>`/`<entry>` 容器，但供應商把 title/
        description/link/pubDate 改名/換 namespace，導致每筆都解析成
        空白 title、空 URL、ts=0 的垃圾——**結構有效的 entry 數為 0**
        （見 `_entry_is_structurally_valid()`）一樣是 schema drift。
      - 上述兩層皆 **raise ValueError**，讓呼叫端（`fetch_scheduler.py`）
        保留舊快取、計入 failure，不能靜靜用空/垃圾覆蓋掉還能用的舊資料。
      - **合法路徑**：結構有效的 entries 存在，但依 query/coin 關鍵字
        過濾後一則都不符（那個幣剛好沒新聞）→ 仍是合法結果，回 `[]`，
        不 raise。
    """
    root = ET.fromstring(raw)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item")
    if not items:
        items = root.findall(".//atom:entry", ns)
    if not items:
        raise ValueError(
            f"{source_name}: RSS/Atom 回應沒有任何 <item>/<entry>"
            "（可能是供應商 schema drift 或回了錯誤頁）"
        )

    keywords = [kw.lower() for kw in (query, coin) if kw]
    docs: list[Document] = []
    valid_entry_count = 0

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

        # 子欄位結構驗證：title/link/pubDate 被改名/換 namespace 導致解析
        # 全空的 entry，直接跳過，不計入 valid_entry_count，也絕不發布
        # （無關鍵字模式一樣擋，見下方 valid_entry_count==0 才 raise）。
        if not _entry_is_structurally_valid(title, desc, link, ts):
            continue
        valid_entry_count += 1

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

    if valid_entry_count == 0:
        raise ValueError(
            f"{source_name}: 所有 <item>/<entry> 的子欄位皆缺失/型別 drift"
            "（title/description/link/pubDate 全空或不合法，可能是供應商"
            "改版/換 namespace）"
        )

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


class TheBlockRSSSource(Source):
    """The Block RSS，公開無 key。"""
    kind = "news"
    name = "theblock"
    _URL = "https://www.theblock.co/rss.xml"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        raw = _fetch_url(self._URL)
        return _parse_rss(raw, self.name, query, coin)


class UTodayRSSSource(Source):
    """U.Today RSS，公開無 key。"""
    kind = "news"
    name = "utoday"
    _URL = "https://u.today/rss.php"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        raw = _fetch_url(self._URL)
        return _parse_rss(raw, self.name, query, coin)


class BlockworksRSSSource(Source):
    """Blockworks feed（Atom 格式，`_parse_rss` 已相容），公開無 key。"""
    kind = "news"
    name = "blockworks"
    _URL = "https://blockworks.com/feed"

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
        TheBlockRSSSource(),
        UTodayRSSSource(),
        BlockworksRSSSource(),
    ]
    if os.getenv("CRYPTOPANIC_TOKEN"):
        sources.append(CryptoPanicSource())
    return sources
