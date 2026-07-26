"""台灣監管來源 adapters — FSC / MOPS / TWSE / TPEx（issue #385）。

來源白名單（寫死，防 SSRF）：

- FSC 金融監督管理委員會 RSS（`www.fsc.gov.tw`）
- TWSE 臺灣證券交易所 OpenAPI（`openapi.twse.com.tw`）
- TPEx 證券櫃檯買賣中心 OpenAPI（`www.tpex.org.tw`）

全部為政府公開資料介面：無需 API key、無登入、無付費牆，robots 亦無
相關 Disallow。端點實測與法遵評估見
`docs/audit/TAIWAN-REGULATORY-SOURCE-DISCOVERY-385.md`。

安全措施
--------
- SSRF-safe fetch（`safe_fetch.py`）：逐跳驗證 scheme/hostname/port/私有 IP，
  DNS pinning，禁自動跟轉
- 不接受外部傳入 URL；端點寫死在各 Source 的 `_endpoints`
- 固定 User-Agent（含聯絡信箱）

兩類來源的差異（這是本模組拆兩層的原因）
----------------------------------------
=========  ==========================  ==============================
面向       FSC RSS                     MOPS / TWSE / TPEx OpenAPI
=========  ==========================  ==============================
格式       XML（RSS 2.0）              JSON
永久連結   guid（含 dataserno）        **無**，需自組查詢頁 reference
歷史       數年                        重大訊息＝當日；裁罰＝年度
時間       RFC822 GMT，**僅日精度**    民國年＋發言時間（到秒）
單次回應   1.3〜3.0 MB                 4〜12 KB
=========  ==========================  ==============================

三個實作地雷（皆由實測發現，對應測試已鎖）
------------------------------------------
1. `safe_fetch` 預設上限 512 KB 且**超過即靜默截斷不報錯**。FSC feed 實測
   3 MB，會拿到殘缺 XML。故 RSS 走 8 MB 上限，**並額外驗結尾 `</rss>`**
   當完整性 sentinel——因為 `safe_fetch` 不會告訴你有沒有截斷。
2. TWSE `t187ap04_L` 的欄位名是 `'主旨 '`（**結尾有空白**），TPEx 同欄位卻無
   空白，且兩者公司代號/名稱欄位名完全不同。故所有 key 先 `.strip()`
   正規化，並走顯式欄位映射表，絕不直接 index 原始鍵。
3. MOPS 資料集**無任何 per-announcement URL**，且重大訊息只有當日 snapshot。
   故 meta 標 `url_kind="query-page"` 與 `history_backfillable=False`，
   不假裝是永久連結、不假裝有歷史。

fail-closed
-----------
timeout / 非 200 / SSRF 攔截 / parse 失敗 / 結構不符 / 截斷 → 該端點回空並
記 WARNING 與可觀測狀態；**所有**端點都失敗才拋 `TaiwanRegulatoryUnavailable`
讓上游 `_record_source_event` 記成 failed（而非把「來源掛了」誤當成
「本來就沒有相關公告」）。單筆髒值只跳過該筆，不拖累整批。
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlparse

from . import safe_fetch
from .base import Document, Source
from .tw_datetime import (
    day_precision_visible_at,
    end_of_taipei_day,
    is_visible_at,
    pit_visible_at,
    roc_date_to_date,
    roc_datetime_to_taipei,
)

_log = logging.getLogger(__name__)

_UA = "TrustForge/1.0 (+https://github.com/cancleeric/trustforge; eric.wang@hurricanesoft.com.tw)"
_TIMEOUT = 10.0

# FSC feed 實測 1.3〜3.0 MB。上限抓 8 MB 留成長空間；真正的防線是下方的
# `</rss>` sentinel，而非這個數字。
_RSS_MAX_BYTES = 8 * 1024 * 1024
_JSON_MAX_BYTES = 2 * 1024 * 1024

# 單一來源單次回傳上限，避免極端情況灌爆下游（比照 regulatory.py）。
_MAX_DOCS = 200

ALLOWED_TW_HOSTS = frozenset(
    {
        "www.fsc.gov.tw",
        "openapi.twse.com.tw",
        "mops.twse.com.tw",
        "www.twse.com.tw",
        "www.tpex.org.tw",
    }
)

# 加密關鍵字閘門。
#
# ⚠️ 這組詞是**量測後**定下來的，不是憑感覺列的。對 FSC 裁罰 feed（498 筆）
# 實測：若納入「加密」與「洗錢防制」兩個寬鬆詞，命中 42 筆，但其中 38 筆是
# 銀行洗錢防制裁罰、4 筆是資安「資料加密」缺失，**全數為誤報**。故一律用
# 「加密貨幣」「加密資產」這類複合詞，不用單獨的「加密」；也不收「洗錢防制」
# 「詐騙」這類與加密無必然關係的詞。
#
# 閘門的必要性：`base._matches_coin()` 分支 3 會把「未提及任何幣別」的文件
# 視為全市場通用而**納入每一個幣的證據池**。不擋就是拿數百筆銀行裁罰淹沒
# 每個幣的證據。
_CRYPTO_TERMS = (
    "虛擬資產",
    "虛擬通貨",
    "VASP",
    "加密貨幣",
    "加密資產",
    "穩定幣",
    "比特幣",
    "以太幣",
    "數位資產",
    "區塊鏈",
    "代幣化",
)


class TaiwanRegulatoryUnavailable(RuntimeError):
    """某台灣監管來源的**所有**端點都失敗，代表該來源整體不可用。

    與「查無相關公告」語意不同：後者回空清單，前者拋此例外，
    讓上游把來源掛掉與真的沒資料區分開。
    """


def _sha256(payload: bytes | str) -> str:
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _mentions_crypto(*parts: str) -> bool:
    """關鍵字閘門：任一欄位命中即通過。"""
    blob = " ".join(p for p in parts if p)
    return any(term in blob for term in _CRYPTO_TERMS)


def _gate_match(title: str, body: str) -> str | None:
    """判斷閘門命中的位置：`"title"`（高精準）或 `"body"`（低精準）。

    實測 fsc-news：通過閘門的 23 筆中，**標題**命中的 7 筆全數為真正的
    VASP／虛擬資產監管事件（含 #385 指名要的「金管會公告完成洗錢防制登記
    之提供虛擬資產服務之事業或人員名單」）；其餘 16 筆只在內文順帶提及，
    多為每日新聞彙編、普惠金融指標、記者會等，與加密監管無實質關係。

    兩者都保留（避免砍掉內文才有實質討論的公告），但用 `meta["gate_match"]`
    把精準度訊號交給下游，讓證據權重可以據此區分。本模組**不**自行調整
    任何權重——那屬於 Trust Kernel，#385 明確不做。
    """
    if _mentions_crypto(title):
        return "title"
    if _mentions_crypto(body):
        return "body"
    return None


def _strip_html(raw: str) -> str:
    """去 HTML 標籤並還原 entity。

    FSC `<description>` 是整份裁處書的 HTML（實測單筆 3 KB 以上），
    含大量 `<br />`、`&nbsp;`。轉純文字後才適合進 Document.text。
    """
    text = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t　]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class TaiwanRegulatorySource(Source):
    """台灣監管來源共用基底。

    子類別提供 `_endpoints` 與 `_parse()`，本類負責：
    host 白名單、有界擷取、關鍵字閘門、PIT 閘門、dedup、
    可觀測狀態與降級紀錄、fail-closed 收斂。
    """

    kind = "regulatory"
    name = "taiwan-base"

    # 子類別覆寫
    _endpoints: tuple[str, ...] = ()
    _max_bytes: int = _JSON_MAX_BYTES
    _agency: str = ""
    _url_kind: str = "permalink"
    _history_backfillable: bool = True

    def __init__(self) -> None:
        self.last_attempts = 0
        self.last_failures = 0
        self.last_failed_endpoints: list[str] = []
        self.last_degraded = False
        self.last_truncated = False

    # ── 對外介面 ─────────────────────────────────────────────────────────

    def fetch(
        self, query: str, coin: str = "", *, as_of: datetime | None = None
    ) -> list[Document]:
        """擷取並正規化為 Document。

        `as_of` 為 PIT 分析時間；帶入時排除該時刻尚未對外可見的資料。
        """
        docs: list[Document] = []
        seen: set[str] = set()
        attempts = 0
        failed: list[str] = []
        truncated = False

        for url in self._endpoints:
            attempts += 1
            if not self._validate_host(url):
                _log.warning(
                    "台灣監管來源端點不在白名單：source=%s host=%s",
                    self.name,
                    urlparse(url).hostname,
                )
                failed.append(url)
                continue

            raw = self._fetch_endpoint(url)
            if raw is None:
                failed.append(url)
                continue
            if not self._is_complete(raw):
                # `safe_fetch` 超過 max_bytes 會靜默截斷，這裡是唯一的偵測點。
                _log.warning(
                    "台灣監管來源回應不完整（疑似截斷）：source=%s url=%s bytes=%d",
                    self.name,
                    url,
                    len(raw),
                )
                truncated = True
                failed.append(url)
                continue

            fetched_at = datetime.now(timezone.utc)
            content_hash = _sha256(raw)
            try:
                parsed = self._parse(raw, url)
            except Exception as exc:  # parse 失敗＝schema drift，記錄後降級
                _log.warning(
                    "台灣監管來源解析失敗：source=%s url=%s error_type=%s",
                    self.name,
                    url,
                    type(exc).__name__,
                )
                failed.append(url)
                continue

            for doc in self._build_documents(
                parsed, url=url, fetched_at=fetched_at, content_hash=content_hash,
                as_of=as_of,
            ):
                if doc.id in seen:
                    # 同一官方公告的鏡像不能算多票（#385 驗收條件）。
                    continue
                seen.add(doc.id)
                docs.append(doc)

        self._record_fetch_stats(attempts, failed, truncated, len(docs))

        if attempts > 0 and len(failed) == attempts:
            raise TaiwanRegulatoryUnavailable(
                f"台灣監管來源全數端點失敗（{len(failed)}/{attempts}）；"
                f"來源={self.name}"
            )

        if len(docs) > _MAX_DOCS:
            docs.sort(key=lambda d: d.ts, reverse=True)
            docs = docs[:_MAX_DOCS]
        return docs

    # ── 子類別覆寫點 ─────────────────────────────────────────────────────

    def _parse(self, raw: bytes, url: str) -> list[dict]:
        """把原始回應轉成中介 record 清單。"""
        raise NotImplementedError

    def _to_document(
        self, record: dict, *, url: str, fetched_at: datetime, content_hash: str
    ) -> Document | None:
        """把單一 record 轉成 Document；不合格回 None（跳過該筆）。"""
        raise NotImplementedError

    def _is_complete(self, raw: bytes) -> bool:
        """回應完整性檢查。預設不檢（JSON 走 parse 即可驗）。"""
        return True

    # ── 共用實作 ─────────────────────────────────────────────────────────

    def _validate_host(self, url: str) -> bool:
        """端點必須落在寫死的白名單主機上。"""
        try:
            parsed = urlparse(url)
        except ValueError:
            return False
        return parsed.scheme == "https" and parsed.hostname in ALLOWED_TW_HOSTS

    def _fetch_endpoint(self, url: str) -> bytes | None:
        """有界擷取。任何失敗回 None 並記 WARNING，不拋。"""
        try:
            return safe_fetch.fetch_url(
                url, user_agent=_UA, timeout=_TIMEOUT, max_bytes=self._max_bytes
            )
        except Exception as exc:
            _log.warning(
                "台灣監管來源擷取失敗：source=%s url=%s error_type=%s",
                self.name,
                url,
                type(exc).__name__,
            )
            return None

    def _build_documents(
        self,
        records: list[dict],
        *,
        url: str,
        fetched_at: datetime,
        content_hash: str,
        as_of: datetime | None,
    ) -> list[Document]:
        docs: list[Document] = []
        for record in records:
            try:
                doc = self._to_document(
                    record, url=url, fetched_at=fetched_at, content_hash=content_hash
                )
            except Exception as exc:
                # 單筆髒值不拖累整批（沿用 regulatory.py 的單筆失敗隔離）。
                _log.warning(
                    "台灣監管來源單筆轉換失敗（跳過）：source=%s error_type=%s",
                    self.name,
                    type(exc).__name__,
                )
                continue
            if doc is None:
                continue
            if as_of is not None:
                visible_at = doc.meta.get("visible_at_epoch")
                moment = (
                    datetime.fromtimestamp(visible_at, tz=timezone.utc)
                    if isinstance(visible_at, (int, float))
                    else None
                )
                if not is_visible_at(moment, as_of):
                    continue
            docs.append(doc)
        return docs

    def _finish_document(
        self,
        *,
        doc_id: str,
        title: str,
        body: str,
        url: str,
        published: datetime | None,
        visible_at: datetime | None,
        fetched_at: datetime,
        content_hash: str,
        extra_meta: dict | None = None,
    ) -> Document | None:
        """組出 Document，套用關鍵字閘門與必填欄位。"""
        gate_match = _gate_match(title, body)
        if gate_match is None:
            return None
        if visible_at is None:
            # 無法判定可見時間 ＝ 無法做 PIT ＝ 不可用（fail-closed）。
            return None

        meta = {
            "source_region": "TW",
            "agency": self._agency,
            "adapter_status": "live",
            "live_source": True,
            "content_hash": content_hash,
            "fetched_at": fetched_at.isoformat(),
            "published_at": published.isoformat() if published else None,
            "visible_at": visible_at.isoformat(),
            "visible_at_epoch": visible_at.timestamp(),
            "url_kind": self._url_kind,
            "history_backfillable": self._history_backfillable,
            "regulatory_scope": "industry-level",
            # "title" ＝ 標題即為加密監管事件（實測精準度 7/7）；
            # "body" ＝ 僅內文順帶提及（實測多為新聞彙編等雜訊）。
            "gate_match": gate_match,
        }
        if extra_meta:
            meta.update(extra_meta)

        text = f"{title}\n{body}".strip() if body else title
        return Document(
            id=doc_id,
            kind=self.kind,
            source=self.name,
            text=text,
            url=url,
            ts=(published or visible_at).timestamp(),
            meta=meta,
        )

    def _record_fetch_stats(
        self, attempts: int, failed: list[str], truncated: bool, doc_count: int
    ) -> None:
        """更新可觀測狀態；部分/全部降級各記一筆彙總 WARNING。"""
        self.last_attempts = attempts
        self.last_failures = len(failed)
        self.last_failed_endpoints = list(failed)
        self.last_degraded = bool(failed)
        self.last_truncated = truncated

        if not failed:
            return
        if len(failed) == attempts and attempts > 0:
            _log.warning(
                "台灣監管來源全數端點失敗（%d/%d）→ 來源不可用；source=%s",
                len(failed),
                attempts,
                self.name,
            )
        else:
            _log.warning(
                "台灣監管來源部分降級（%d/%d 失敗）；source=%s documents=%d",
                len(failed),
                attempts,
                self.name,
                doc_count,
            )


# ── FSC：RSS feed ────────────────────────────────────────────────────────


class _RssFeedSource(TaiwanRegulatorySource):
    """FSC RSS feed 共用實作。"""

    _max_bytes = _RSS_MAX_BYTES
    _agency = "金融監督管理委員會"
    _url_kind = "permalink"
    _history_backfillable = True

    def _is_complete(self, raw: bytes) -> bool:
        """`</rss>` 完整性 sentinel。

        `safe_fetch` 超過 max_bytes 是**靜默截斷**，不會拋也不會回報。
        RSS 必以 `</rss>` 收尾，缺了就代表回應不完整。
        """
        return raw.rstrip().endswith(b"</rss>")

    def _parse(self, raw: bytes, url: str) -> list[dict]:
        root = ET.fromstring(raw)
        records: list[dict] = []
        for item in root.findall("./channel/item"):
            records.append(
                {
                    "title": (item.findtext("title") or "").strip(),
                    "link": (item.findtext("link") or "").strip(),
                    "guid": (item.findtext("guid") or "").strip(),
                    "pub_date": (item.findtext("pubDate") or "").strip(),
                    "description": item.findtext("description") or "",
                }
            )
        return records

    def _to_document(
        self, record: dict, *, url: str, fetched_at: datetime, content_hash: str
    ) -> Document | None:
        # guid/link 在 CDATA 內，實測含**字面** `&amp;`（CDATA 不解 entity），
        # 需先還原才是可用的 URL。
        permalink = html.unescape(record.get("guid") or record.get("link") or "")
        dataserno = self._extract_dataserno(permalink)
        if not dataserno:
            return None

        # 發文日期（pubDate，日精度）與上架日期（dataserno 前 8 碼）可能差一日，
        # 取較晚者才是真正對外可見的時刻。
        issued = day_precision_visible_at(record.get("pub_date"))
        listed = self._listed_visible_at(dataserno)
        visible_at = pit_visible_at(issued, listed)

        return self._finish_document(
            # canonical id 用來源自身的唯一鍵，而非內容 hash——
            # 官方公告在多個 feed 出現時才擋得掉重複計票。
            doc_id=f"tw-reg:fsc:{dataserno}",
            title=record.get("title", ""),
            body=_strip_html(record.get("description", "")),
            url=permalink,
            published=issued,
            visible_at=visible_at,
            fetched_at=fetched_at,
            content_hash=content_hash,
            extra_meta={"dataserno": dataserno, "feed_url": url},
        )

    @staticmethod
    def _extract_dataserno(permalink: str) -> str | None:
        """從 FSC 永久連結取出 `dataserno`（來源自身的唯一鍵）。"""
        if not permalink:
            return None
        try:
            query = parse_qs(urlparse(permalink).query)
        except ValueError:
            return None
        values = query.get("dataserno") or []
        value = values[0].strip() if values else ""
        return value or None

    @staticmethod
    def _listed_visible_at(dataserno: str) -> datetime | None:
        """`dataserno` 前 8 碼為上架日（實測 `202607220001` ＝ 2026-07-22）。

        日精度，同樣取台北該日結束作為 fail-closed 可見時間。
        """
        if len(dataserno) < 8 or not dataserno[:8].isdigit():
            return None
        try:
            day = date(int(dataserno[:4]), int(dataserno[4:6]), int(dataserno[6:8]))
        except ValueError:
            return None
        return end_of_taipei_day(day)


_FSC_RSS_BASE = "https://www.fsc.gov.tw/RSS/Messages"

# feed serno 由 FSC RSS 索引頁取得：
# https://www.fsc.gov.tw/ch/main.jsp?websitelink=rss.jsp&mtitle=RSS
_FSC_FEEDS: dict[str, tuple[str, str]] = {
    # key: (serno, 中文說明)
    "fsc-news": ("201202290009", "新聞稿"),
    "fsc-penalty": ("201202290003", "裁罰案件"),
    "fsc-notice": ("201202290001", "重要公告"),
}


class FSCSource(_RssFeedSource):
    """FSC（金融監督管理委員會）RSS。

    三個 feed 的加密相關度實測差異極大（見 discovery 文件第五節）：
    新聞稿 23/800、重要公告 8/800、裁罰案件 1/498。新聞稿是 VASP 與
    虛擬資產服務法等監管事件的實際落點，裁罰 feed 目前近乎無加密內容，
    但保留以承接未來 VASP 開罰。
    """

    def __init__(self, feed: str = "fsc-news") -> None:
        if feed not in _FSC_FEEDS:
            raise ValueError(f"未知的 FSC feed：{feed}")
        super().__init__()
        serno, label = _FSC_FEEDS[feed]
        self.name = feed
        self.feed_label = label
        self._endpoints = (f"{_FSC_RSS_BASE}?serno={serno}&language=chinese",)


# ── MOPS / TWSE / TPEx：OpenAPI ──────────────────────────────────────────


class _OpenApiSource(TaiwanRegulatorySource):
    """TWSE / TPEx OpenAPI 共用實作。

    ⚠️ 同一份資料在 TWSE 與 TPEx 用**兩套欄位名**，且 TWSE 的 `'主旨 '`
    帶結尾空白。所有 key 一律先 `.strip()`，再走顯式映射表。
    """

    _max_bytes = _JSON_MAX_BYTES
    _url_kind = "query-page"

    # 子類別覆寫：中介欄位 → 該端點的實際欄位名
    _field_map: dict[str, str] = {}

    def _parse(self, raw: bytes, url: str) -> list[dict]:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError(f"OpenAPI 回應頂層非陣列：{type(data).__name__}")
        records = []
        for row in data:
            if not isinstance(row, dict):
                continue
            # key 正規化：吸收 TWSE `'主旨 '` 這類結尾空白。
            records.append({str(k).strip(): v for k, v in row.items()})
        return records

    def _field(self, record: dict, key: str) -> str:
        """依映射表取值；缺鍵或非字串回空字串。"""
        source_key = self._field_map.get(key)
        if not source_key:
            return ""
        value = record.get(source_key)
        return value.strip() if isinstance(value, str) else ""


class MOPSSource(_OpenApiSource):
    """MOPS（公開資訊觀測站）每日重大訊息。

    ⚠️ 只有**當日 snapshot**（實測上市 8 筆、上櫃 3 筆，發言日期單一）。
    無法回填歷史，只能靠排程逐日累積 cache 當檔案庫，故
    `history_backfillable=False`。
    """

    _agency = "公開資訊觀測站"
    _history_backfillable = False

    _MARKETS: dict[str, dict] = {
        "mops-twse": {
            "url": "https://openapi.twse.com.tw/v1/opendata/t187ap04_L",
            "agency": "臺灣證券交易所公開資訊觀測站",
            "market": "上市",
            "fields": {
                "code": "公司代號",
                "company": "公司名稱",
                "subject": "主旨",  # 原始鍵為 '主旨 '，靠 key.strip() 吸收
                "date": "發言日期",
                "time": "發言時間",
                "body": "說明",
                "clause": "符合條款",
            },
        },
        "mops-tpex": {
            "url": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O",
            "agency": "證券櫃檯買賣中心公開資訊觀測站",
            "market": "上櫃",
            "fields": {
                "code": "SecuritiesCompanyCode",
                "company": "CompanyName",
                "subject": "主旨",
                "date": "發言日期",
                "time": "發言時間",
                "body": "說明",
                "clause": "符合條款",
            },
        },
    }

    def __init__(self, market: str = "mops-twse") -> None:
        if market not in self._MARKETS:
            raise ValueError(f"未知的 MOPS 市場：{market}")
        super().__init__()
        spec = self._MARKETS[market]
        self.name = market
        self.market_label = spec["market"]
        self._agency = spec["agency"]
        self._field_map = spec["fields"]
        self._endpoints = (spec["url"],)

    def _to_document(
        self, record: dict, *, url: str, fetched_at: datetime, content_hash: str
    ) -> Document | None:
        code = self._field(record, "code")
        subject = self._field(record, "subject")
        if not code or not subject:
            return None

        published = roc_datetime_to_taipei(
            self._field(record, "date"), self._field(record, "time")
        )
        if published is None:
            return None

        company = self._field(record, "company")
        title = f"{company}（{code}）{subject}" if company else subject
        body = self._field(record, "body")

        # 資料集無 per-announcement URL，只能組查詢頁；meta 已標
        # url_kind="query-page"，不假裝是永久連結。
        reference_url = (
            "https://mops.twse.com.tw/mops/web/t05st01"
            f"?TYPEK=all&co_id={code}"
        )

        return self._finish_document(
            doc_id=(
                f"tw-reg:{self.name}:"
                + _sha256(
                    "|".join(
                        [
                            code,
                            self._field(record, "date"),
                            self._field(record, "time"),
                            subject,
                        ]
                    )
                )[:16]
            ),
            title=title,
            body=body,
            url=reference_url,
            published=published,
            visible_at=published,  # 有到秒的精度，不套用日結束規則
            fetched_at=fetched_at,
            content_hash=content_hash,
            extra_meta={
                "company_code": code,
                "company_name": company,
                "market": self.market_label,
                "clause": self._field(record, "clause"),
                "dataset_url": url,
            },
        )


class _PunishSource(_OpenApiSource):
    """裁罰專區共用實作（TWSE / TPEx）。

    與重大訊息不同，裁罰專區**有年度歷史**（實測 TWSE 21 筆橫跨
    1150105〜1150518、TPEx 18 筆 17 個相異日期），PIT replay 價值較高。
    """

    _history_backfillable = True

    def _to_document(
        self, record: dict, *, url: str, fetched_at: datetime, content_hash: str
    ) -> Document | None:
        code = self._field(record, "code")
        reason = self._field(record, "reason")
        if not code or not reason:
            return None

        day = roc_date_to_date(self._field(record, "date"))
        if day is None:
            return None
        # 發函日期僅日精度 → fail-closed 取台北該日結束。
        visible_at = end_of_taipei_day(day)

        company = self._field(record, "company")
        law = self._field(record, "law")
        disposition = self._field(record, "disposition")
        title = f"{company}（{code}）違規裁罰：{reason}" if company else reason
        body = "\n".join(p for p in (f"違反法規：{law}" if law else "",
                                     f"裁處情形：{disposition}" if disposition else "") if p)

        reference_url = (
            "https://mops.twse.com.tw/mops/web/t05st01"
            f"?TYPEK=all&co_id={code}"
        )

        return self._finish_document(
            doc_id=(
                f"tw-reg:{self.name}:"
                + _sha256("|".join([code, self._field(record, "date"), reason]))[:16]
            ),
            title=title,
            body=body,
            url=reference_url,
            published=visible_at,
            visible_at=visible_at,
            fetched_at=fetched_at,
            content_hash=content_hash,
            extra_meta={
                "company_code": code,
                "company_name": company,
                "violated_law": law,
                "disposition": disposition,
                "dataset_url": url,
            },
        )


class TWSESource(_PunishSource):
    """TWSE（臺灣證券交易所）— 上市公司金管會證期局裁罰案件專區。"""

    name = "twse-punish"
    _agency = "臺灣證券交易所"
    _field_map = {
        "code": "股票代號",
        "company": "公司名稱",
        "date": "發函日期",
        "reason": "違規事由",
        "law": "違反法規",
        "disposition": "裁處情形",
    }

    def __init__(self) -> None:
        super().__init__()
        self._endpoints = ("https://openapi.twse.com.tw/v1/opendata/t187ap22_L",)


class TPEXSource(_PunishSource):
    """TPEx（證券櫃檯買賣中心）— 上櫃公司裁罰案件專區。

    欄位名與 TWSE 不同（`SecuritiesCompanyCode` / `CompanyName`），
    其餘中文欄位相同。
    """

    name = "tpex-punish"
    _agency = "證券櫃檯買賣中心"
    _field_map = {
        "code": "SecuritiesCompanyCode",
        "company": "CompanyName",
        "date": "發函日期",
        "reason": "違規事由",
        "law": "違反法規",
        "disposition": "裁處情形",
    }

    def __init__(self) -> None:
        super().__init__()
        self._endpoints = ("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap22_O",)


def build_taiwan_regulatory_sources() -> list[Source]:
    """回傳所有台灣監管連接器。

    是否實際啟用由 `base.get_source_enabled()` 決定——本批來源在
    `base._DEFAULT_DISABLED_SOURCES` 內預設關閉，需明確 override 才啟用
    （理由見 `docs/plans/PLAN-385-TAIWAN-REGULATORY-ADAPTERS-2026-07-26.md`）。
    """
    return [
        FSCSource("fsc-news"),
        FSCSource("fsc-penalty"),
        FSCSource("fsc-notice"),
        MOPSSource("mops-twse"),
        MOPSSource("mops-tpex"),
        TWSESource(),
        TPEXSource(),
    ]
