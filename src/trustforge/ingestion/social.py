"""社群連接器 (P2-2)。

來源白名單（寫死，防 SSRF）：
  - Reddit r/CryptoCurrency  https://www.reddit.com/r/CryptoCurrency/search.rss
  - Reddit r/Bitcoin         https://www.reddit.com/r/Bitcoin/search.rss

NOTE: Reddit 在 cloud IP 可能 403，生產可靠存取需 OAuth（待辦）。
      此連接器在遭封鎖時，collect() 的 try/except 會優雅降級跳過。

安全措施：
  - timeout 5 秒 / 回應大小上限 512 KB（超過截斷）
  - 描述性 User-Agent（Reddit 預設 UA 會 429/403）
  - 不接受外部傳入 URL
  - query 參數以 urlencode 編碼，防止 &limit=100 等注入改寫請求語義
"""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen

from .base import Document, Source

_MAX_BYTES = 512 * 1024   # 512 KB
_TIMEOUT = 5
# Reddit 要求描述性 User-Agent，否則回 429/403
_UA = (
    "TrustForge/1.0 research bot "
    "(github.com/hurricanesoft/trustforge; contact: trustforge@hurricanesoft.com)"
)

_ALLOWED_SUBREDDITS = {"CryptoCurrency", "Bitcoin"}
_REDDIT_BASE = "https://www.reddit.com"


def _fetch_url(url: str) -> bytes:
    """帶 timeout / 大小上限 / 描述性 User-Agent 的 urllib GET。"""
    req = Request(url, headers={"User-Agent": _UA})
    with urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.read(_MAX_BYTES)


def _build_permalink(permalink: str) -> str:
    """permalink 已含 host 就直接回傳，否則補 _REDDIT_BASE 前綴，防雙 domain。

    以 urlsplit 判斷是否已有 scheme + netloc：
      - "/r/..."          → "https://www.reddit.com/r/..."
      - "https://www.reddit.com/r/..."  → 原樣回傳（不重複前綴）
    """
    if not permalink:
        return ""
    parts = urlsplit(permalink)
    if parts.scheme and parts.netloc:
        return permalink
    return urljoin(_REDDIT_BASE, permalink)


_ATOM_NS = "http://www.w3.org/2005/Atom"


def _text(el: "ET.Element | None") -> str:
    """安全取元素 text；el=None 或 text=None 均回傳空字串。"""
    if el is None or el.text is None:
        return ""
    return str(el.text).strip()


def _parse_reddit_rss(raw: bytes, coin: str = "", source_name: str = "reddit") -> list[Document]:
    """解析 Reddit feed（RSS 2.0 <item> 或 Atom <entry>），回傳符合幣種過濾的 Document list。

    Reddit 的 /search.rss 實際回傳 Atom feed；本函式同時支援 RSS 2.0 和 Atom，
    逐層型別驗證，元素缺失或型別異常均給預設值，不拋 AttributeError/TypeError。

    NOTE: Reddit 在 cloud IP 可能 403，生產可靠存取需 OAuth（待辦）。
    """
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []

    # 優先找 RSS 2.0 <item>；無則找 Atom <entry>
    items = root.findall(".//item")
    if not items:
        items = root.findall(f".//{{{_ATOM_NS}}}entry")
    if not items:
        items = root.findall(".//entry")

    docs: list[Document] = []

    for item in items:
        # --- title ---
        title = (
            _text(item.find("title"))
            or _text(item.find(f"{{{_ATOM_NS}}}title"))
        )

        # --- body (description / summary / content) ---
        selftext = (
            _text(item.find("description"))
            or _text(item.find(f"{{{_ATOM_NS}}}summary"))
            or _text(item.find(f"{{{_ATOM_NS}}}content"))
        )

        # --- link / permalink ---
        # RSS 2.0: <link>text</link>；Atom: <link href="..."/>
        # 注意：ET.Element 有 children 時才 truthy，必須明確用 is None 判斷
        raw_link = ""
        link_el = item.find("link")
        if link_el is None:
            link_el = item.find(f"{{{_ATOM_NS}}}link")
        if link_el is not None:
            raw_link = link_el.get("href", "") or _text(link_el)
        permalink = _build_permalink(raw_link)

        # --- timestamp（RSS pubDate RFC2822 / Atom updated|published ISO8601）---
        created_utc: float = 0.0
        pub_el = item.find("pubDate")
        if pub_el is None:
            pub_el = item.find(f"{{{_ATOM_NS}}}updated")
        if pub_el is None:
            pub_el = item.find(f"{{{_ATOM_NS}}}published")
        if pub_el is not None and pub_el.text:
            ts_text = pub_el.text.strip()
            try:
                created_utc = parsedate_to_datetime(ts_text).timestamp()
            except Exception:
                try:
                    from datetime import datetime, timezone
                    created_utc = datetime.fromisoformat(
                        ts_text.replace("Z", "+00:00")
                    ).timestamp()
                except Exception:
                    created_utc = 0.0

        # --- coin 過濾：title 或 body 須含幣種關鍵字 ---
        if coin:
            combined_check = (title + " " + selftext).lower()
            if coin.lower() not in combined_check:
                continue

        content_ref = (title + " " + selftext)[:120].strip()
        doc_id = (
            "social-reddit-"
            + hashlib.md5((permalink + title).encode()).hexdigest()[:12]
        )
        docs.append(Document(
            id=doc_id,
            kind="social",
            source=source_name,
            text=title,
            url=permalink,
            ts=created_utc,
            meta={"content_reference": content_ref},
        ))

    return docs


class RedditCryptoSource(Source):
    """Reddit RSS feed，搜尋加密幣相關貼文（r/CryptoCurrency 或 r/Bitcoin）。

    NOTE: Reddit 在 cloud IP 可能 403，生產可靠存取需 OAuth（待辦）。
          此連接器在遭封鎖時，collect() 的 try/except 會優雅降級跳過。
    """

    kind = "social"

    def __init__(self, subreddit: str = "CryptoCurrency") -> None:
        if subreddit not in _ALLOWED_SUBREDDITS:
            raise ValueError(f"subreddit 不在白名單：{subreddit}")
        self.subreddit = subreddit
        self.name = f"reddit-{subreddit.lower()}"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        # urlencode 防注入（如 btc&limit=100 改寫請求語義）
        search_q = coin if coin else (query if query else "crypto")
        params = urlencode({"q": search_q, "restrict_sr": "1", "sort": "new"})
        url = f"{_REDDIT_BASE}/r/{self.subreddit}/search.rss?{params}"
        raw = _fetch_url(url)
        return _parse_reddit_rss(raw, coin, self.name)


def build_social_sources() -> list[Source]:
    """回傳所有已啟用的社群連接器。"""
    return [
        RedditCryptoSource("CryptoCurrency"),
        RedditCryptoSource("Bitcoin"),
    ]
