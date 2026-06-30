"""監管連接器 (P2-3)。

來源白名單（寫死，防 SSRF）：
  - SEC EDGAR 公開 Atom feed（無需 API key）

安全措施：
  - timeout 5 秒 / 回應大小上限 512 KB（超過截斷）
  - 固定 User-Agent
  - 不接受外部傳入 URL
  - 本地篩選加密相關關鍵字（無相關事件時回空清單，pipeline limits 會反映）
"""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from .base import Document, Source

_MAX_BYTES = 512 * 1024   # 512 KB
_TIMEOUT = 5
_UA = "TrustForge/1.0 (research)"

# 加密相關關鍵字（小寫）
_CRYPTO_KEYWORDS = frozenset([
    "crypto", "bitcoin", "digital asset", "token", "blockchain",
    "cryptocurrency", "ether", "ethereum", "virtual currency", "defi",
    "nft", "stablecoin", "initial coin offering", "ico",
])


def _fetch_url(url: str) -> bytes:
    """帶 timeout / 大小上限 / User-Agent 的 urllib GET。"""
    req = Request(url, headers={"User-Agent": _UA})
    with urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.read(_MAX_BYTES)


def _is_crypto_related(text: str) -> bool:
    """判斷文字是否含加密相關關鍵字（不區分大小寫）。"""
    lower = text.lower()
    return any(kw in lower for kw in _CRYPTO_KEYWORDS)


def _parse_sec_atom(raw: bytes, coin: str = "") -> list[Document]:  # noqa: ARG001
    """解析 SEC EDGAR Atom XML，篩選加密相關條目。"""
    root = ET.fromstring(raw)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries = root.findall("atom:entry", ns)

    docs: list[Document] = []
    for entry in entries:
        title_el = entry.find("atom:title", ns)
        link_el = entry.find("atom:link", ns)
        updated_el = entry.find("atom:updated", ns)
        summary_el = entry.find("atom:summary", ns)

        title = (title_el.text or "").strip() if title_el is not None else ""
        link = ""
        if link_el is not None:
            link = link_el.get("href", "") or (link_el.text or "").strip()
        updated = (
            (updated_el.text or "").strip() if updated_el is not None else ""
        )
        summary = (
            (summary_el.text or "").strip() if summary_el is not None else ""
        )

        # 篩選：title 或 summary 含加密關鍵字
        # 監管文件為產業層級信號，不進一步過濾特定幣種
        combined = title + " " + summary
        if not _is_crypto_related(combined):
            continue

        # 解析發布時間（ISO 8601）
        ts = 0.0
        if updated:
            try:
                ts = datetime.fromisoformat(
                    updated.replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                pass

        content_ref = combined[:120].strip()
        doc_id = (
            "reg-sec-"
            + hashlib.md5((link + title).encode()).hexdigest()[:12]
        )
        docs.append(Document(
            id=doc_id,
            kind="regulatory",
            source="sec-gov",
            text=title,
            url=link,
            ts=ts,
            meta={"content_reference": content_ref},
        ))

    return docs


class SECRSSSource(Source):
    """SEC EDGAR 公開 Atom feed，篩選加密相關監管動作。"""

    kind = "regulatory"
    name = "sec-gov"
    # EDGAR 公開 Atom feed（action=getcurrent 取近期 filing，search_text 預篩）
    _URL = (
        "https://www.sec.gov/cgi-bin/browse-edgar"
        "?action=getcurrent&type=&dateb=&owner=include"
        "&count=40&search_text=cryptocurrency&output=atom"
    )

    def fetch(self, query: str, coin: str = "") -> list[Document]:  # noqa: ARG002
        raw = _fetch_url(self._URL)
        return _parse_sec_atom(raw, coin)


def build_regulatory_sources() -> list[Source]:
    """回傳所有已啟用的監管連接器。"""
    return [SECRSSSource()]
