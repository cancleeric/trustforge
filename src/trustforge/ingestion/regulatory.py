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
import logging
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from . import safe_fetch
from .base import Document, Source

_log = logging.getLogger(__name__)

_MAX_BYTES = 512 * 1024   # 512 KB
_TIMEOUT = 5
# #141 對外合規：此 User-Agent 會隨每次請求送給 SEC（SEC 規範要求帶聯絡資訊）。
# 網域修正 `hurricanessoft`（多一個 s，拼字錯誤）→ `hurricanesoft`，避免對外
# 送出錯誤聯絡網域、也避免 SEC 端以 UA 不合規理由限流／封鎖。
_UA = "TrustForge-Hackathon research contact@hurricanesoft.com.tw"

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


class RegulatoryFTSUnavailable(RuntimeError):
    """SEC EDGAR 全文檢索「整體不可用」（所有查詢詞都抓取/解析失敗）時拋出。

    #141 可觀測性修法：先前所有查詢詞失敗會靜默回空清單，讓監管訊號無聲流失，
    下游只看到「0 筆」而**無法區分**兩種截然不同的情況：
      1. 本來就沒有相關 filing（正常低頻，合法的空結果）；
      2. FTS 整個掛掉（網路/上游/格式全壞）——這才是需要被看見的降級。

    改為在「全部查詢詞都失敗」時拋出本例外，交由上游既有降級機制如實反映
    「FTS 不可用」而非靜默：
      - `base.collect()` 的 `try/except` + `_failed` → `report.limits` 會補一句
        「以下來源本輪未取得資料，不納入計算：SEC…」，讓 abstain 可解釋；
      - `scripts/fetch_scheduler.py` 的 `failures` 計數 → cron/監控可見。

    ⚠️ 只有「全部」查詢詞失敗才拋（＝FTS 整體不可用）。部分查詢詞失敗仍回傳
    其餘詞的命中（部分降級），只記 log + 計數、不拋——維持 #133「單詞失敗隔離」
    精神，避免單一詞失敗就讓整個監管來源歸零。
    """


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

    def __init__(self) -> None:
        # #141 可觀測性狀態：每次 fetch() 後更新，供「直接持有此來源實例」者
        # （測試、排程器診斷）不必解析 log 就能檢視 FTS 本輪健康度。上游正式的
        # 「不可用」訊號仍走 raise RegulatoryFTSUnavailable → collect._failed /
        # scheduler.failures（見該例外 docstring）。
        self.last_attempts: int = 0        # 本輪嘗試的查詢詞數
        self.last_failures: int = 0        # 抓取/解析失敗的查詢詞數
        self.last_failed_terms: list[str] = []  # 失敗的查詢詞清單
        self.last_degraded: bool = False   # 本輪是否有任何查詢詞失敗（部分或全部）

    def fetch(self, query: str, coin: str = "") -> list[Document]:  # noqa: ARG002
        startdt, enddt = _date_window()
        docs: list[Document] = []
        seen_ids: set[str] = set()
        attempts = 0
        failed_terms: list[str] = []

        for idx, term in enumerate(_QUERY_TERMS):
            attempts += 1
            url = _build_search_url(term, startdt, enddt)
            # issue #133 單詞失敗隔離 + #141 可觀測性：一詞炸（網路逾時 / SSRF
            # 攔截 / HTTP 錯誤 / 回應非 JSON / 結構不符）不拖累整批——但**不再靜默
            # 吞錯**：每種失敗都由 `_fetch_term_hits` 記 WARNING（含命中詞、來源、
            # 例外類型）並回 None，這裡計入 `failed_terms` 供降級判斷與計數。
            raw_hits = self._fetch_term_hits(term, url)
            if raw_hits is None:
                failed_terms.append(term)
            else:
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

        self._record_fetch_stats(attempts, failed_terms, len(docs))

        # #141 降級處理：全部查詢詞都失敗 → FTS 整體不可用 → 拋例外讓上游知道
        # （而非靜默回空清單，把「FTS 掛掉」誤當成「本來就沒有相關 filing」）。
        if attempts > 0 and len(failed_terms) == attempts:
            raise RegulatoryFTSUnavailable(
                f"SEC EDGAR FTS 全數查詢詞失敗（{len(failed_terms)}/{attempts}）；"
                f"來源={self.name}，失敗詞={failed_terms}"
            )

        if len(docs) > _MAX_DOCS:
            docs.sort(key=lambda d: d.ts, reverse=True)
            docs = docs[:_MAX_DOCS]

        return docs

    def _record_fetch_stats(
        self, attempts: int, failed_terms: list[str], doc_count: int
    ) -> None:
        """更新可觀測狀態 + 對部分/全部降級各記一筆彙總 WARNING（#141）。"""
        self.last_attempts = attempts
        self.last_failures = len(failed_terms)
        self.last_failed_terms = list(failed_terms)
        self.last_degraded = bool(failed_terms)

        if not failed_terms:
            return
        if len(failed_terms) == attempts and attempts > 0:
            _log.warning(
                "SEC EDGAR FTS 全數查詢詞失敗（%d/%d）→ FTS 不可用；"
                "source=%s failed_terms=%s",
                len(failed_terms), attempts, self.name, failed_terms,
            )
        else:
            _log.warning(
                "SEC EDGAR FTS 部分降級：%d/%d 查詢詞失敗，仍回傳其餘詞命中 %d 筆；"
                "source=%s failed_terms=%s",
                len(failed_terms), attempts, doc_count, self.name, failed_terms,
            )

    def _fetch_term_hits(self, term: str, url: str) -> list | None:
        """抓取並解析單一查詢詞的 FTS 回應，回傳 hits 清單；任何失敗回 None 並記 log。

        #141：先前這些失敗分支是靜默 `continue`，導致監管訊號無聲流失、判斷掉
        abstain 也無法解釋。改為每種失敗都以 WARNING 記錄命中詞、來源、例外類型/
        原因，供 log-based 告警與事後診斷。回 None 代表「該詞失敗」（供呼叫端計數/
        降級判斷）；回 list（含空 list）代表「該詞成功」——**空 list 是合法的
        『查無相關 filing』，不算失敗**（低頻監管來源常態）。
        """
        try:
            raw = self._fetch_url_term(url)
        except Exception as exc:  # noqa: BLE001 — 單詞失敗隔離，但改為記錄而非靜默
            _log.warning(
                "SEC EDGAR FTS 抓取失敗：term=%r source=%s error_type=%s error=%s",
                term, self.name, type(exc).__name__, exc,
            )
            return None
        try:
            data = json.loads(raw)
        except (ValueError, TypeError) as exc:
            _log.warning(
                "SEC EDGAR FTS 回應非合法 JSON：term=%r source=%s error_type=%s",
                term, self.name, type(exc).__name__,
            )
            return None
        if not isinstance(data, dict):
            _log.warning(
                "SEC EDGAR FTS 回應頂層非物件：term=%r source=%s got=%s",
                term, self.name, type(data).__name__,
            )
            return None
        hits_obj = data.get("hits")
        if not isinstance(hits_obj, dict):
            _log.warning(
                "SEC EDGAR FTS 回應缺 hits 物件：term=%r source=%s got=%s",
                term, self.name, type(hits_obj).__name__,
            )
            return None
        raw_hits = hits_obj.get("hits", [])
        if not isinstance(raw_hits, list):
            _log.warning(
                "SEC EDGAR FTS hits.hits 非陣列：term=%r source=%s got=%s",
                term, self.name, type(raw_hits).__name__,
            )
            return None
        return raw_hits

    def _fetch_url_term(self, url: str) -> bytes:
        """內部 helper，呼叫模組層 _fetch_url（方便測試沿用既有 monkeypatch 慣例）。"""
        return _fetch_url(url)


def build_regulatory_sources() -> list[Source]:
    """回傳所有已啟用的監管連接器。"""
    return [SECFullTextSearchSource()]
