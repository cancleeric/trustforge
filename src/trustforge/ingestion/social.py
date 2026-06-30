"""社群連接器 (P2-2)。

來源白名單（寫死，防 SSRF）：
  - Reddit r/CryptoCurrency  https://www.reddit.com/r/CryptoCurrency/search.json
  - Reddit r/Bitcoin         https://www.reddit.com/r/Bitcoin/search.json

安全措施：
  - timeout 5 秒 / 回應大小上限 512 KB（超過截斷）
  - 描述性 User-Agent（Reddit 預設 UA 會 429）
  - 不接受外部傳入 URL
"""
from __future__ import annotations

import hashlib
import json
from urllib.request import Request, urlopen

from .base import Document, Source

_MAX_BYTES = 512 * 1024   # 512 KB
_TIMEOUT = 5
# Reddit 要求描述性 User-Agent，否則回 429
_UA = (
    "TrustForge/1.0 research bot "
    "(github.com/hurricanesoft/trustforge; contact: trustforge@hurricanesoft.com)"
)

_ALLOWED_SUBREDDITS = {"CryptoCurrency", "Bitcoin"}


def _fetch_url(url: str) -> bytes:
    """帶 timeout / 大小上限 / 描述性 User-Agent 的 urllib GET。"""
    req = Request(url, headers={"User-Agent": _UA})
    with urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.read(_MAX_BYTES)


class RedditCryptoSource(Source):
    """Reddit 搜尋加密幣相關貼文（r/CryptoCurrency 或 r/Bitcoin）。"""

    kind = "social"

    def __init__(self, subreddit: str = "CryptoCurrency") -> None:
        if subreddit not in _ALLOWED_SUBREDDITS:
            raise ValueError(f"subreddit 不在白名單：{subreddit}")
        self.subreddit = subreddit
        self.name = f"reddit-{subreddit.lower()}"

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        search_q = coin if coin else (query if query else "crypto")
        url = (
            f"https://www.reddit.com/r/{self.subreddit}/search.json"
            f"?q={search_q}&sort=new&limit=10&restrict_sr=1"
        )
        raw = _fetch_url(url)
        data = json.loads(raw)
        docs: list[Document] = []

        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})
            title: str = post.get("title", "")
            selftext: str = post.get("selftext", "")
            permalink: str = post.get("permalink", "")
            created_utc: float = float(post.get("created_utc", 0.0))

            # coin 過濾：title 或 selftext 須含 coin 關鍵字（不區分大小寫）
            if coin:
                combined = (title + " " + selftext).lower()
                if coin.lower() not in combined:
                    continue

            post_url = (
                f"https://www.reddit.com{permalink}" if permalink else ""
            )
            # content_reference = title + selftext 前 120 字
            content_ref = (title + " " + selftext)[:120].strip()

            doc_id = (
                "social-reddit-"
                + hashlib.md5((post_url + title).encode()).hexdigest()[:12]
            )
            docs.append(Document(
                id=doc_id,
                kind="social",
                source=self.name,
                text=title,
                url=post_url,
                ts=created_utc,
                meta={"content_reference": content_ref},
            ))

        return docs


def build_social_sources() -> list[Source]:
    """回傳所有已啟用的社群連接器。"""
    return [
        RedditCryptoSource("CryptoCurrency"),
        RedditCryptoSource("Bitcoin"),
    ]
