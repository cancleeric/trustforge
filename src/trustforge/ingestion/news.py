"""真實新聞連接器 (P0-1)。

來源白名單（寫死，防 SSRF）：
  - CoinDesk RSS  https://www.coindesk.com/arc/outboundfeeds/rss   (公開，免 key)
  - Decrypt RSS   https://decrypt.co/feed                          (公開，免 key)
  - CryptoPanic   https://cryptopanic.com/api/v1/posts/            (選用，需 env CRYPTOPANIC_TOKEN)

生產事故修復（coindesk 全 308 Permanent Redirect）：CoinDesk 把舊網址
`.../rss/`（末尾帶斜線）永久重導到 `.../rss`（無斜線），同網域、只差路徑
末尾斜線。已直接改用新網址（見下方 `CoinDeskRSSSource._URL`），不必再依賴
redirect 才能拿到內容。

安全事故修復（codex 對抗審發現，2026-07）：第一版「跟 308」的防禦有洞——
`urlopen()` 預設會安裝 `HTTPRedirectHandler`，這個 handler 對 301/302/303/
307**在所有 Python 版本**、對 308**在 Python 3.11+**都會**自動跟轉**，
而且完全不檢查目的地 host/scheme/是否為私有 IP。也就是說第一版程式碼裡
`except HTTPError` 裡手寫的「同 host + https」檢查，在 3.11+ 的 Python 上
對 308 根本**永遠不會被執行**——`urlopen()` 早就在進入 except 區塊之前，
用預設 handler 把 3xx 自動跟到任意 host 去了（本專案 Dockerfile 用
`python:3.12-slim`，正是這個高風險版本）；對 301/302/303/307 這個洞則
**不分 Python 版本、從一開始就存在**。若任一白名單來源的網域未來被入侵/
CDN 誤配置成把請求重導到內網位址（如雲端 metadata endpoint、內部管理
API），服務會在毫無察覺的情況下把請求送過去，構成典型的 SSRF via redirect。

修法：`_fetch_url` 改用**自訂 opener，完全禁用自動轉址**
（`_NoRedirectHandler.redirect_request()` 恆回傳 `None`，讓 urllib 對任何
3xx 一律拋出 `HTTPError`，不會偷偷自己跟走）；轉址改成**手動、逐跳驗證**
後才進行：
  - scheme 必須是 `https`（拒絕降級到 http 或非 http(s) scheme）
  - hostname 必須與「最初白名單 URL」的 hostname 完全相同（`urlparse().
    hostname` 已自動小寫正規化）——整條轉址鏈只要出現任何一跳的目的地
    host 不同，立刻拒絕，不是只比對「上一跳」（防禦第二跳才變更 host 的
    繞過手法）
  - port 必須是 https 預設 443（或未指定 port）
  - hostname 解析出的**所有** IP 都必須不是私有/迴環/link-local/保留/
    多播/未指定位址（`ipaddress` 標準庫判斷）——即使 host 字串本身合法，
    仍防禦 DNS rebinding 把同一個網域名稱解析到內網 IP 的攻擊手法
  - 最多跟 `_MAX_REDIRECTS`（3）跳，避免無限轉址鏈
  全部通過才手動發下一次請求；任一項檢查沒過，原樣拋出 `HTTPError`，不跟。

安全措施：
  - timeout 5 秒 / 回應大小上限 512 KB（超過截斷）
  - 固定 User-Agent
  - 不接受外部傳入 URL
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, HTTPRedirectHandler, build_opener

from .base import Document, Source

_MAX_BYTES = 512 * 1024   # 512 KB
_TIMEOUT = 5
_UA = "TrustForge/1.0 (research)"

# 轉址鏈最多允許的跳數（見模組頂部「安全事故修復」說明）：擋無限轉址鏈，
# 3 跳對正常的白名單來源（同網域內頂多一次路徑搬家）綽綽有餘。
_MAX_REDIRECTS = 3

_ALLOWED_HTTPS_PORTS = (None, 443)  # None = URL 未寫明 port，走預設 443


class _NoRedirectHandler(HTTPRedirectHandler):
    """完全禁用 urllib 預設的自動轉址（見模組頂部「安全事故修復」說明）：
    `redirect_request` 回傳 `None` 讓 urllib 視為「不處理這個轉址」，改為
    對呼叫端拋出 `HTTPError`，轉由 `_fetch_url` 手動逐跳驗證後才決定是否
    要跟。這是本模組唯一安裝的 opener，`_fetch_url` 是唯一出口，因此所有
    真請求（不論哪個白名單來源）都受這層保護。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = build_opener(_NoRedirectHandler)


def _is_safe_redirect_target(url: str, allowed_hostname: str) -> bool:
    """驗證轉址目標是否安全（見模組頂部「安全事故修復」逐跳驗證說明）：
    scheme=https、hostname 與最初白名單 URL 完全相同、port 是 https 預設
    值、解析出的所有 IP 皆非私有/內網（防 DNS rebinding）。"""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    if not parsed.hostname or parsed.hostname != allowed_hostname:
        return False
    if parsed.port not in _ALLOWED_HTTPS_PORTS:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        ):
            return False
    return True


def _fetch_url(url: str) -> bytes:
    """帶 timeout / 大小上限 / User-Agent 的 urllib GET，透過禁用自動轉址
    的 `_opener` 送出（見模組頂部「安全事故修復」說明）——3xx 一律先拋出
    `HTTPError`，由這裡手動逐跳驗證（同 https、同 hostname、標準 port、
    解析後 IP 非私有）通過才跟，最多 `_MAX_REDIRECTS` 跳。任一跳驗證失敗
    或超過跳數上限，原樣拋出最後一次的 `HTTPError`。
    """
    allowed_hostname = urlparse(url).hostname
    current_url = url
    for hop in range(_MAX_REDIRECTS + 1):
        req = Request(current_url, headers={"User-Agent": _UA})
        try:
            with _opener.open(req, timeout=_TIMEOUT) as resp:
                return resp.read(_MAX_BYTES)
        except HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308):
                raise
            if hop >= _MAX_REDIRECTS:
                raise
            location = exc.headers.get("Location") if exc.headers else None
            if not location:
                raise
            redirect_url = urljoin(current_url, location)
            if not _is_safe_redirect_target(redirect_url, allowed_hostname):
                raise
            current_url = redirect_url
    raise AssertionError("unreachable：迴圈必定在跳數上限內 return 或 raise")


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
