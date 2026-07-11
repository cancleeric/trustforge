"""監管連接器 (P2-3)。

資料源改用 SEC EDGAR 全文檢索 API（efts.sec.gov Full-Text Search），
取代舊版的 getcurrent Atom feed。

舊版 getcurrent 只回全公司最新 40 筆 filing，加密相關占比極低，
常態篩出 0 筆命中；全文檢索直接對 filing 全文做關鍵字檢索，
命中率大幅提升（見 #9）。

來源白名單（寫死，防 SSRF）：
  - SEC EDGAR 全文檢索 API（efts.sec.gov，無需 API key）

安全措施：
  - timeout 5 秒 / 回應大小上限 512 KB（超過截斷）
  - 固定 User-Agent（含聯絡資訊，符合 SEC 規範）
  - 不接受外部傳入 URL
  - SSRF-safe fetch（見 `safe_fetch.py`）：逐跳驗證（含初始 URL）scheme/
    hostname/port/私有 IP，DNS pinning 杜絕 rebinding，禁自動跟轉

禮貌性措施：
  - 多關鍵字查詢間加入禮貌性延遲（`_REQUEST_DELAY_SECONDS`），
    避免短時間內連續高頻打 SEC API。

低頻監管來源特性：低頻是正常現象，平時無相關 filing 命中時回空清單，
pipeline limits 會反映；本次修法目的是把「加密占比極低的全站 feed」
換成「對 filing 全文做關鍵字檢索」，讓真正有加密相關內容的 filing
能被命中，而不是改變「平時可能 0 筆」這個本質。

content_reference 說明：efts.sec.gov 全文檢索 API 的回應中沒有
highlight/snippet 欄位（人工 curl 驗證），無法直接取用命中的內文片段；
因此改將「命中的查詢詞（matched query term）」放進 content_reference
最前面（`[matched:"..."]`），讓使用者一眼看出該筆 filing 與加密
關鍵字的關聯性，同時在 meta 新增 `matched_term` 鍵以供程式化使用。
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from . import safe_fetch
from .base import Document, Source

_MAX_BYTES = 512 * 1024   # 512 KB
_TIMEOUT = 5
_UA = "TrustForge-Hackathon research contact@hurricanessoft.com.tw"

_BASE_URL = "https://efts.sec.gov/LATEST/search-index"
_FORMS = "8-K,10-K,10-Q,S-1"
_LOOKBACK_DAYS = 30
# issue #133：詞表覆蓋率擴充。原始 3 詞（bitcoin/ethereum/cryptocurrency）只命中
# 極窄的加密相關 filing 子集。擴充為「具名幣 + 通用加密概念詞」組合，提升加密
# 相關 filing 的召回率（recall）。`fetch()` 會對每個詞各打一次 API、再依 doc.id
# 去重（見 `test_sec_dedup_across_query_terms`），所以擴詞不會產生重複 Document，
# 只會把更多真正加密相關的 filing 拉進來；`_MAX_DOCS` 上限仍兜住總量。
# ⚠️ 保守原則：只收「明確加密相關」的詞，避免過度泛化到與加密無關的財報。
_QUERY_TERMS = (
    "bitcoin", "ethereum", "cryptocurrency", "crypto",
    "stablecoin", "blockchain", "defi", "web3",
    "solana", "xrp", "dogecoin", "digital asset",
)
_REQUEST_DELAY_SECONDS = 0.1
_MAX_DOCS = 20


def _fetch_url(url: str) -> bytes:
    """帶 timeout / 大小上限 / User-Agent 的 SSRF-safe GET（見 safe_fetch.py）。"""
    return safe_fetch.fetch_url(url, user_agent=_UA, timeout=_TIMEOUT, max_bytes=_MAX_BYTES)


def _date_window(now: datetime | None = None) -> tuple[str, str]:
    """回傳 (startdt, enddt) 兩個 YYYY-MM-DD 字串，往前推 _LOOKBACK_DAYS 天。"""
    if now is None:
        now = datetime.now(timezone.utc)
    enddt = now.strftime("%Y-%m-%d")
    start = now - timedelta(days=_LOOKBACK_DAYS)
    startdt = start.strftime("%Y-%m-%d")
    return startdt, enddt


def _build_search_url(term: str, startdt: str, enddt: str) -> str:
    """組出 EDGAR 全文檢索 URL（q 參數用雙引號做 phrase search）。"""
    params = {
        "q": f'"{term}"',
        "forms": _FORMS,
        "startdt": startdt,
        "enddt": enddt,
    }
    return f"{_BASE_URL}?{urlencode(params)}"


def _extract_filing_url(hit_id: str, cik: str) -> str:
    """依 _id 與 cik 拼出 filing 的 Archives 網址；格式不符回空字串。"""
    if not hit_id or ":" not in hit_id:
        return ""
    if not cik or not isinstance(cik, str):
        return ""
    cik_stripped = cik.lstrip("0")
    if not cik_stripped.isdigit():
        return ""
    accession_part, _, filename = hit_id.partition(":")
    if not filename:
        return ""
    accession_nodash = accession_part.replace("-", "")
    if not accession_nodash:
        return ""
    return (
        f"https://www.sec.gov/Archives/edgar/data/"
        f"{cik_stripped}/{accession_nodash}/{filename}"
    )


def _parse_fts_hit(hit: dict, term: str) -> Document | None:
    """解析單筆 hits.hits[] 元素；缺欄位或無法拼 URL 時回 None（不 raise）。

    issue #133 型別防禦：對每個欄位做明確型別檢查，任何關鍵欄位型別不符
    （如 `ciks` 不是 list、`_id` 不是 str、`display_names`/`items` 夾雜非 str
    元素、`form`/`file_date` 非 str）一律視為 malformed → 回 None，絕不讓
    型別錯誤往上炸到 `fetch()` 的批次迴圈（見下方單詞失敗隔離）。"""
    if not isinstance(hit, dict):
        return None
    hit_id = hit.get("_id")
    if not isinstance(hit_id, str) or not hit_id:
        return None
    source = hit.get("_source")
    if not isinstance(source, dict):
        return None

    ciks = source.get("ciks")
    if not isinstance(ciks, list) or not ciks:
        return None
    # 只取第一個可解析的 str CIK；其餘忽略（防禦非 str 元素，如 int/None）
    cik = next((c for c in ciks if isinstance(c, str) and c), None)
    if not cik:
        return None
    filing_url = _extract_filing_url(hit_id, cik)
    if not filing_url:
        return None

    display_names = source.get("display_names")
    if isinstance(display_names, list):
        # 過濾非 str 元素，避免 f-string 拼接或下游誤用型別
        company_names = [d for d in display_names if isinstance(d, str) and d]
        company = company_names[0] if company_names else "Unknown filer"
    else:
        company = "Unknown filer"

    form = source.get("form")
    form = form if isinstance(form, str) else ""
    file_date = source.get("file_date")
    file_date = file_date if isinstance(file_date, str) else ""

    items = source.get("items")
    if isinstance(items, list):
        items = [i for i in items if isinstance(i, str)]
    else:
        items = []

    title = f"{company} — {form} filing"
    if file_date:
        title += f"({file_date})"

    combined = f'[matched:"{term}"] {title}'
    if items:
        combined = f'{combined} items:{",".join(items)}'
    content_reference = combined[:120].strip()

    ts = 0.0
    if file_date:
        try:
            ts = datetime.strptime(file_date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            ).timestamp()
        except ValueError:
            ts = 0.0

    doc_id = "reg-sec-" + hashlib.md5(hit_id.encode()).hexdigest()[:12]

    meta = {
        "content_reference": content_reference,
        "regulatory_scope": "industry-level",
        "form_type": form,
        "matched_term": term,
    }

    return Document(
        id=doc_id,
        kind="regulatory",
        source="sec-gov",
        text=title,
        url=filing_url,
        ts=ts,
        meta=meta,
    )


class SECFullTextSearchSource(Source):
    """SEC EDGAR 全文檢索 API，命中加密相關 filing（8-K/10-K/10-Q/S-1，近 30 天）。"""

    kind = "regulatory"
    name = "sec-gov"

    def fetch(self, query: str, coin: str = "") -> list[Document]:  # noqa: ARG002
        startdt, enddt = _date_window()
        docs: list[Document] = []
        seen_ids: set[str] = set()

        for idx, term in enumerate(_QUERY_TERMS):
            url = _build_search_url(term, startdt, enddt)
            # issue #133 單詞失敗隔離：一詞炸（網路逾時 / SSRF 攔截 / HTTP 錯誤 /
            # 回應非 JSON）不拖累整批——該詞跳過、其餘詞照常檢索。避免「一個詞
            # 失敗讓整個 SEC 監管 feed 歸零」的單點崩潰。
            try:
                raw = self._fetch_url_term(url)
            except Exception:
                continue
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            hits_obj = data.get("hits")
            if not isinstance(hits_obj, dict):
                continue
            raw_hits = hits_obj.get("hits", [])
            if not isinstance(raw_hits, list):
                continue

            for hit in raw_hits:
                doc = _parse_fts_hit(hit, term)
                if doc is None:
                    continue
                if doc.id in seen_ids:
                    continue
                seen_ids.add(doc.id)
                docs.append(doc)

            if idx < len(_QUERY_TERMS) - 1:
                time.sleep(_REQUEST_DELAY_SECONDS)

        if len(docs) > _MAX_DOCS:
            docs.sort(key=lambda d: d.ts, reverse=True)
            docs = docs[:_MAX_DOCS]

        return docs

    def _fetch_url_term(self, url: str) -> bytes:
        """內部 helper，呼叫模組層 _fetch_url（方便測試沿用既有 monkeypatch 慣例）。"""
        return _fetch_url(url)


def build_regulatory_sources() -> list[Source]:
    """回傳所有已啟用的監管連接器。"""
    return [SECFullTextSearchSource()]
