"""真實新聞連接器 (P0-1)。

來源白名單（寫死，防 SSRF）：
  - CoinDesk RSS  https://www.coindesk.com/arc/outboundfeeds/rss   (公開，免 key)
  - Decrypt RSS   https://decrypt.co/feed                          (公開，免 key)
  - CryptoPanic   https://cryptopanic.com/api/v1/posts/            (選用，需 env CRYPTOPANIC_TOKEN)

生產事故修復（coindesk 全 308 Permanent Redirect）：CoinDesk 把舊網址
`.../rss/`（末尾帶斜線）永久重導到 `.../rss`（無斜線），同網域、只差路徑
末尾斜線。已直接改用新網址（見下方 `CoinDeskRSSSource._URL`），不必再依賴
redirect 才能拿到內容。`_fetch_url` 額外補上「跟 308」的防禦（見下方
說明）——`urllib.request` 的 `HTTPRedirectHandler` 在 Python 3.11 之前
不認得 308（只認 301/302/303/307），舊網址在較舊 Python 版本上就算 CoinDesk
未來又搬家一次也會直接炸 `HTTPError`，不會像 3.11+ 那樣自動轉址；補這層
可以讓「新網址又被 308 到另一個路徑」這種未來情境不必等下一次程式碼修改
就能自動跟上（僅限同 host + https，避免任意網域跳轉造成 SSRF）。

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
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from .base import Document, Source

_MAX_BYTES = 512 * 1024   # 512 KB
_TIMEOUT = 5
_UA = "TrustForge/1.0 (research)"


def _fetch_url(url: str) -> bytes:
    """帶 timeout / 大小上限 / User-Agent 的 urllib GET。

    對 308 Permanent Redirect 額外手動跟一次（urllib 在 Python 3.11 之前不
    認得 308，見模組頂部「生產事故修復」說明）；301/302/303/307 由
    `urllib.request` 內建 `HTTPRedirectHandler` 自動處理，不受影響。只跟
    「同 host + https」的單一跳轉，避免任意網域跳轉造成 SSRF（本檔所有
    URL 皆為寫死白名單，跳轉目標理應仍是同一個網域）。
    """
    req = Request(url, headers={"User-Agent": _UA})
    try:
        with urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.read(_MAX_BYTES)
    except HTTPError as exc:
        if exc.code != 308:
            raise
        location = exc.headers.get("Location") if exc.headers else None
        if not location:
            raise
        redirect_url = urljoin(url, location)
        parsed = urlparse(redirect_url)
        if parsed.scheme != "https" or parsed.hostname != urlparse(url).hostname:
            raise
        req2 = Request(redirect_url, headers={"User-Agent": _UA})
        with urlopen(req2, timeout=_TIMEOUT) as resp2:
            return resp2.read(_MAX_BYTES)


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
    sources: list[Source] = [CoinDeskRSSSource(), DecryptRSSSource()]
    if os.getenv("CRYPTOPANIC_TOKEN"):
        sources.append(CryptoPanicSource())
    return sources
