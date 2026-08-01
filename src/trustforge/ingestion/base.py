"""多源連接器的統一介面。

每個來源（news/social/onchain/hoyabit/regulatory）實作 Source.fetch()，
輸出標準化 Document。真實 API 在 7/13 企業數據工作坊後接上；目前以離線樣本驅動。
"""
from __future__ import annotations

import inspect
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..coin_scope import coins_mentioned, matches_coin_fields
from ..data_contracts import DOCUMENT_SCHEMA_VERSION

_log = logging.getLogger(__name__)

# 資料根目錄：預設為 repo 根（src 上一層）；Lambda 等打包環境用 TRUSTFORGE_HOME 覆寫
# （Lambda 把 trustforge/ data/ demo/ 都放在 /var/task，設 TRUSTFORGE_HOME=/var/task）。
_HOME = Path(os.getenv("TRUSTFORGE_HOME", Path(__file__).resolve().parents[3]))
SAMPLE_DIR = _HOME / "demo" / "sample_data"
OHLCV_DIR = SAMPLE_DIR / "ohlcv"                 # 合成樣本（測試/快速 demo）
OFFICIAL_OHLCV_DIR = _HOME / "data" / "data"     # HOYA BIT 官方基準 OHLCV

# 文件型來源類型（有對應的 sample_data/*.json）。price 走 OHLCV CSV，另行處理。
# `defi_tvl`（#1162 DefiLlama）：新增客觀 TVL 維度，離線樣本驅動（demo/sample_data/
# defi_tvl.json），使離線管線無需任何外部 API 即可涵蓋此 kind。
SOURCE_KINDS = ("onchain", "regulatory", "hoyabit", "news", "social", "defi_tvl")

@dataclass
class Document:
    id: str
    kind: str            # SOURCE_KINDS 之一
    source: str          # 來源名稱，如 "coindesk" / "hoyabit-ticker"
    text: str            # 原文
    schema_version: str = DOCUMENT_SCHEMA_VERSION
    url: str = ""
    ts: float = 0.0      # epoch 秒；用於時效衰減
    meta: dict = field(default_factory=dict)


class Source:
    """連接器基底。子類別實作 fetch()。"""

    kind: str = "news"
    name: str = "base"
    # issue #155 per-source 通路開關：預設 enabled（fail-closed 預設全 ON）。
    # 任何源「沒被明確 disabled」就照常納入；只有 `set_source_enabled_override`
    # 或 admin_config 的 disabled_sources 明確關掉，才跳過。誤配置/漏配置時
    # 傾向「繼續抓真實資料」，而非「悄悄關掉真實源」。
    enabled: bool = True

    def fetch(self, query: str, coin: str = "") -> list[Document]:  # pragma: no cover - 介面
        raise NotImplementedError


# ── per-source 通路開關（issue #155，fail-closed 預設全 ON）──────────────────
# 單一 override 真值來源：`get_source_enabled(name)` 預設回 True（啟用）；只有
# 明確呼叫 `set_source_enabled_override(name, False)` 或 `sync_source_enabled_from_admin()`
# 從 admin_config 讀到該源在 disabled_sources 裡，才回 False（跳過）。這是
# fail-closed 設計——絕不會因為「忘了設」而誤關真實源。
_SOURCE_ENABLED_OVERRIDES: dict[str, bool] = {}

# issue #154：尚未接真實 API 的 stub 源預設 disabled（與通行 fail-closed
# 「真實源預設全 ON」互補——未完工的 stub 在 spec 到位前絕不啟用，避免佔位
# 資料被當真實高權威）。管理端經 `set_source_enabled_override(name, True)`
# （或 admin_config disabled_sources 反向排除）明確啟用後才納入。
#
# issue #385 台灣監管來源同樣預設 disabled，但理由不同——它們是真實已接線的
# 來源，不是 stub。實測 7 源中只有 `fsc-news` 有實質加密內容（23/800），
# 其餘 coverage 為 0 或近 0；且台灣監管文件多半不提幣別，會走
# `_matches_coin()` 分支 3「全市場通用」而被納入**每一個幣**的證據池。
# 先手動 override 開啟觀察雜訊率，驗過再翻預設。
_TAIWAN_REGULATORY_SOURCES: frozenset[str] = frozenset(
    {
        "fsc-news",
        "fsc-penalty",
        "fsc-notice",
        "mops-twse",
        "mops-tpex",
        "twse-punish",
        "tpex-punish",
        "fsc-vasp-registry",  # issue #721：VASP 登記業者名單
    }
)

_DEFAULT_DISABLED_SOURCES: frozenset[str] = (
    frozenset({"hoyabit-ticker"}) | _TAIWAN_REGULATORY_SOURCES
)

# collect() is also used directly by older callers and tests.  A ContextVar lets
# pipeline.run attach an ExecutionLog without changing that long-standing public
# signature or leaking a log across concurrent requests.
_ACTIVE_EXECUTION_LOG: ContextVar[Any | None] = ContextVar("trustforge_execution_log", default=None)


@contextmanager
def execution_log_context(log: Any) -> Iterator[None]:
    token = _ACTIVE_EXECUTION_LOG.set(log)
    try:
        yield
    finally:
        _ACTIVE_EXECUTION_LOG.reset(token)


def _record_source_event(
    source: str, kind: str, coin: str, started: float, document_count: int,
    outcome: str, *, data_mode: str, error_type: str | None = None,
) -> None:
    """Record one source boundary without exposing local paths or error text."""
    log = _ACTIVE_EXECUTION_LOG.get()
    if log is None:
        return
    params = {
        "source": source,
        "kind": kind,
        "coin": coin,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        "document_count": document_count,
        "outcome": outcome,
        "data_mode": data_mode,
    }
    if error_type:
        params["error_type"] = error_type
    summary = f"{source}：{outcome}，{document_count} documents，{params['duration_ms']:.1f} ms"
    log.record("ingestion.source", params=params, summary=summary)


def set_source_enabled_override(name: str, enabled: bool) -> None:
    """明確覆寫某源的啟用狀態（admin_config / 啟動初始化 / 測試用）。"""
    _SOURCE_ENABLED_OVERRIDES[name] = bool(enabled)


def is_valid_hoyabit_endpoint(value: str) -> bool:
    """Accept only credential-free HTTPS endpoints with a real hostname."""
    try:
        parsed = urlsplit(value.strip())
        parsed.port
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


def get_source_enabled(name: str) -> bool:
    """回傳某源是否啟用。

    - 若曾被 `set_source_enabled_override` 明確覆寫，以 override 為準（含把
      預設 disabled 的 stub 反向啟用）。
    - 否則：預設 disabled 清單（如 hoyabit-ticker）內的源回 False；其餘真實源
      回 True（fail-closed：沒被明確 disabled 就啟用）。
    """
    if name == "hoyabit-ticker" and not is_valid_hoyabit_endpoint(
        os.getenv("TRUSTFORGE_HOYABIT_TICKER_URL", "")
    ):
        return False
    if name in _SOURCE_ENABLED_OVERRIDES:
        return _SOURCE_ENABLED_OVERRIDES[name]
    # HOYA BIT becomes a real source only after the organizer-provided HTTPS
    # contract is configured.  Keeping the default disabled prevents the old
    # placeholder from ever acquiring first-party trust by accident.
    if name == "hoyabit-ticker":
        return True
    return name not in _DEFAULT_DISABLED_SOURCES


def reset_source_enabled_overrides() -> None:
    """測試隔離用：清空所有 override。"""
    _SOURCE_ENABLED_OVERRIDES.clear()


def sync_source_enabled_from_admin(store=None) -> None:
    """從 admin_config 讀 enabled_sources / disabled_sources，套用為 override。

    預設 admin_config 兩者皆未設定（= None）→ 各源維持自身預設狀態
    （`_DEFAULT_DISABLED_SOURCES` 內為關，其餘為開）。

    - 寫入 `disabled_sources`（如 ["coindesk"]）→ 對應源標成 disabled，
      collect() 隨即跳過。
    - 寫入 `enabled_sources`（如 ["fsc-news"]）→ 對應源標成 enabled，
      **這是啟用預設 disabled 的源的唯一免改碼途徑**（issue #385）。
    - 兩者同時列出同一個源 → **disabled 勝**（fail-closed）。

    TRUSTFORGE_DISABLE_ADMIN_CONFIG=1 時跳過（本機無 DynamoDB 也能跑排程器）。
    """
    import os
    if os.getenv("TRUSTFORGE_DISABLE_ADMIN_CONFIG", "").strip().lower() in ("1", "true", "yes"):
        return

    from .. import admin_config

    config = admin_config.get_config(store)

    # issue #385：先套 enabled_sources（把預設 disabled 的源打開），再套
    # disabled_sources——順序即優先權，**關永遠勝過開**（fail-closed）。
    # 在此之前 admin 只有「關」的方向，`_DEFAULT_DISABLED_SOURCES` 裡的源
    # （hoyabit-ticker、台灣監管七源）除了改碼重新部署之外無法啟用。
    enabled = getattr(config, "enabled_sources", None)
    if enabled:
        for name in enabled:
            _SOURCE_ENABLED_OVERRIDES[name] = True

    disabled = getattr(config, "disabled_sources", None)
    if disabled:
        for name in disabled:
            _SOURCE_ENABLED_OVERRIDES[name] = False


def _coins_mentioned(text: str) -> set[str]:
    """回傳 text 中提及的所有幣別代碼集合。"""
    return coins_mentioned(text)


def _matches_coin(doc: "Document", coin: str) -> bool:
    """判斷離線樣本 doc 是否與 coin 相關（或為全市場通用資料）。

    支援多幣（"BTC,ETH"，comparison 用）。優先順序：
    1. meta["coin"] 顯式標記 → 須屬目標幣集合。
    2. 先掃出 doc 提及的「所有」幣；目標幣被提及且「無其他非目標幣」→ 納入。
       目標幣與其他幣同時出現（跨幣內容如「BTC 與 ETH 連動」）→ 排除，
       避免他幣訊號被誤當目標幣訊號污染。
    3. 無任何幣別提及 → 全市場通用，納入。
    """
    return matches_coin_fields(
        document_id=doc.id,
        text=doc.text,
        explicit_coin=doc.meta.get("coin"),
        target_coin=coin,
    )


def _mentions_coin(doc: "Document", coin: str) -> bool:
    """判斷 doc 是否「明確」提及目標 coin（不含 `_matches_coin` 分支 3 的
    「無任何幣別提及→全市場通用」兜底）。

    供 `trust.scoring.aggregate` 的 coin-filter 判斷使用：僅「明確提及該幣」
    的樣本才算該幣的「特定」佐證，藉此與泛用市場新聞（如「多家交易所遭
    SEC 警告」）區分——後者雖然也會通過 `_matches_coin`（全市場通用分支）
    納入該幣的資料池，但不應被視為該幣「特定」證據。
    """
    targets = {t.strip().upper() for t in re.split(r"[,\s]+", coin) if t.strip()}
    if not targets:
        return False

    explicit = doc.meta.get("coin")
    if explicit:
        return str(explicit).upper() in targets

    mentioned = _coins_mentioned(doc.id + " " + doc.text)
    return bool(mentioned & targets) and not (mentioned - targets)


class OfflineSampleSource(Source):
    """從 demo/sample_data/*.json 讀取，讓整條管線無需任何外部 API 即可跑通。"""

    def __init__(self, kind: str, name: str):
        self.kind = kind
        self.name = name

    def fetch(self, query: str, coin: str = "") -> list[Document]:  # noqa: ARG002
        f = SAMPLE_DIR / f"{self.kind}.json"
        if not f.exists():
            return []
        raw = json.loads(f.read_text(encoding="utf-8"))
        docs = [
            Document(
                id=d["id"], kind=self.kind, source=d.get("source", self.name),
                text=d["text"], url=d.get("url", ""), ts=d.get("ts", 0.0),
                meta=d.get("meta", {}),
            )
            for d in raw
        ]
        # 按幣種過濾：只回目標幣 + 全市場通用，排除其他幣專屬樣本
        if coin:
            docs = [d for d in docs if _matches_coin(d, coin)]
        return docs


class _WhaleOfflineSampleSource(Source):
    """從 demo/sample_data/whale_trades.json 讀取鯨魚/名人交易離線樣本。

    whale_trades.json 包含兩種 kind（whale_onchain 和 celebrity_trade），
    此 Source 在載入時按 self.kind 過濾，只回傳對應 kind 的文件。
    """

    def __init__(self, kind: str):
        self.kind = kind
        self.name = f"whale-trades-offline-{kind}"

    def fetch(self, query: str, coin: str = "") -> list[Document]:  # noqa: ARG002
        f = SAMPLE_DIR / "whale_trades.json"
        if not f.exists():
            return []
        raw = json.loads(f.read_text(encoding="utf-8"))
        docs = [
            Document(
                id=d["id"], kind=d.get("kind", self.kind),
                source=d.get("source", self.name),
                text=d["text"], url=d.get("url", ""), ts=d.get("ts", 0.0),
                meta=d.get("meta", {}),
            )
            for d in raw
            if d.get("kind") == self.kind
        ]
        if coin:
            docs = [d for d in docs if _matches_coin(d, coin)]
        return docs


def _pit_filter(docs: list[Document], as_of: datetime | None) -> list[Document]:
    """通用 PIT 後置過濾器。

    - 優先用 doc.meta['visible_at_epoch']（精確 PIT，台灣監管用）。
    - 無則退守 doc.ts（上游發佈時間戳，所有來源通用近似）。
    - naive as_of 視為 UTC（與 tw_datetime 一致）。
    """
    if as_of is None:
        return docs
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    as_of_ts = as_of.timestamp()
    kept: list[Document] = []
    for doc in docs:
        visible_at = doc.meta.get("visible_at_epoch")
        if isinstance(visible_at, (int, float)):
            if float(visible_at) <= as_of_ts:
                kept.append(doc)
            else:
                _log.debug("PIT 過濾 (visible_at): %s %s", doc.id, doc.source)
            continue
        if doc.ts <= as_of_ts:
            kept.append(doc)
        else:
            _log.debug("PIT 過濾 (ts): %s %s ts=%.0f as_of=%.0f",
                       doc.id, doc.source, doc.ts, as_of_ts)
    return kept


def _fetch_with_as_of(
    source: Source, query: str, coin: str, as_of: datetime | None
) -> list[Document]:
    """向 source.fetch() 傳遞 as_of（若該 source 支援）。

    用 inspect 檢查簽名，不修改任何來源類別。
    若傳入失敗（TypeError），fallback 到無 as_of 呼叫。
    """
    if as_of is None:
        return source.fetch(query, coin=coin)
    sig = inspect.signature(source.fetch)
    if "as_of" in sig.parameters:
        return source.fetch(query, coin=coin, as_of=as_of)
    try:
        return source.fetch(query, coin=coin, as_of=as_of)
    except TypeError:
        _log.warning(
            "Source %s 不支援 as_of，fallback 到無 as_of 呼叫",
            getattr(source, "name", str(source)),
        )
        return source.fetch(query, coin=coin)


def collect(query: str, coin: str | None = None,
            sources: Iterable[Source] | None = None,
            offline: bool = False, data_dir=None,
            _failed: list | None = None,
            *, as_of: datetime | None = None) -> list[Document]:
    """匯流所有來源（文件型 + OHLCV 價格事實）。offline=True 時用樣本資料。

    _failed：可選 list，失敗的來源名稱（source.name）會被 append 進去，
             供呼叫者（如 pipeline.run）填入 report.limits。

    as_of：分析時間點；帶入時只保留在該時刻已對外可見的文件。
    """
    docs: list[Document] = []

    # 1. 價格事實（官方基準 OHLCV）
    if coin:
        from .prices import load_ohlcv, ohlcv_lineage, price_facts
        # 顯式 data_dir 優先；否則官方資料存在就用官方，再退合成樣本（offline）。
        if data_dir:
            d = data_dir
        elif OFFICIAL_OHLCV_DIR.exists():
            d = OFFICIAL_OHLCV_DIR
        elif offline:
            d = OHLCV_DIR
        else:
            d = None
        if d:
            started = time.perf_counter()
            try:
                bars = load_ohlcv(coin, d)
                lineage = ohlcv_lineage(coin, d, bars)
                price_docs = price_facts(
                    coin, bars, source_file=lineage.get("file", f"{coin.upper()}_daily_ohlcv.csv"),
                    ts=_latest_bar_ts(bars), data_lineage=lineage,
                )
                if as_of is not None:
                    price_docs = _pit_filter(price_docs, as_of)
                docs.extend(price_docs)
                _record_source_event(
                    "official-ohlcv", "price", coin, started, len(price_docs),
                    "ok" if price_docs else "empty", data_mode="sample" if offline else "official",
                )
            except Exception as exc:
                _record_source_event(
                    "official-ohlcv", "price", coin, started, 0, "failed",
                    data_mode="sample" if offline else "official", error_type=type(exc).__name__,
                )
                raise

    # 2. 文件型來源
    if sources is None:
        if offline:
            sources = [OfflineSampleSource(k, k) for k in SOURCE_KINDS]
            # 鯨魚/名人交易離線樣本（whale_trades.json 含兩種 kind，
            # 用專用的 WhaleOfflineSampleSource 載入並按 kind 過濾）
            sources.append(_WhaleOfflineSampleSource("whale_onchain"))
            sources.append(_WhaleOfflineSampleSource("celebrity_trade"))
        else:
            # 線上模式：延遲匯入以避免循環依賴
            from .news import build_news_sources
            from .onchain import build_onchain_sources
            from .social import build_social_sources
            from .regulatory import build_regulatory_sources
            from .taiwan_regulatory import build_taiwan_regulatory_sources
            from .coingecko import build_coingecko_sources
            from .hoyabit import build_hoyabit_sources
            from .whale_trades import build_whale_sources
            from .defillama import build_defillama_sources
            from .cmc import build_cmc_sources
            from .etherscan import build_etherscan_sources
            from .cache import CachedSource
            raw_sources = (
                build_news_sources()
                + build_onchain_sources()
                + build_social_sources()
                + build_regulatory_sources()
                # issue #385：台灣監管來源預設 disabled（見
                # `_TAIWAN_REGULATORY_SOURCES`），仍需在此建構，
                # 才能透過 override 啟用而不必改碼。
                + build_taiwan_regulatory_sources()
                + build_coingecko_sources()
                + build_hoyabit_sources()
                + build_whale_sources()
                + build_defillama_sources()
                # #1161 CoinMarketCap（key-based，無 key→build_cmc_sources() 回 []）：
                # 第三條獨立現價來源，與 coingecko-price/defillama-price 形成
                # corroboration consensus。無憑證時不註冊任何來源（靜默降級）。
                + build_cmc_sources()
                # #1168 Etherscan（key-based V2 query-param key）。沿用 whale_onchain
                # kind，與 whale-alert 同 kind 不同 source，互為獨立佐證。build 永
                # 遠註冊（同 cmc/whale 慣例），憑證在 fetch() 時解析——unconfigured→回 []
                # （靜默），unavailable→raise（可觀測）。只覆 ETH。
                + build_etherscan_sources()
            )
            # 階段2（cache + 排程 fetcher）：產品路徑一律讀快取，不直接打真連接器
            # API（rate-limit 風險），真呼叫只在 scripts/fetch_scheduler.py 排程
            # 任務裡發生。見 .cache 模組頂部說明。
            sources = [CachedSource(s) for s in raw_sources]
    # issue #155 per-source 通路開關（fail-closed 預設全 ON）：disabled 的源
    # 不納入。作用於「顯式傳入的 sources」與「線上/離線預設組裝」兩條路徑，
    # 單一過濾點，確保 admin_config / override 關掉的源在任何呼叫方式下都被跳過。
    sources = [s for s in sources if get_source_enabled(getattr(s, "name", ""))]
    _coin = coin or ""
    for s in sources:
        started = time.perf_counter()
        source_name = getattr(s, "name", str(s))
        source_kind = getattr(s, "kind", "unknown")
        try:
            source_docs = _fetch_with_as_of(s, query, _coin, as_of)
            if as_of is not None:
                source_docs = _pit_filter(source_docs, as_of)
            docs.extend(source_docs)
            _record_source_event(
                source_name, source_kind, _coin, started, len(source_docs),
                "ok" if source_docs else "empty", data_mode="sample" if offline else "cache",
            )
        except Exception as exc:
            # 單一來源失敗（逾時 / 網路錯誤）→ 跳過不崩，其他來源照常
            _record_source_event(
                source_name, source_kind, _coin, started, 0, "failed",
                data_mode="sample" if offline else "cache", error_type=type(exc).__name__,
            )
            if _failed is not None:
                _failed.append(source_name)

    return _dedupe_by_id(docs)


def _dedupe_by_id(docs: list["Document"]) -> list["Document"]:
    """跨來源按 `Document.id` 去重，保留第一次出現者（順序穩定）。

    issue #385：同一份官方公告可能出現在多個 feed。實測 FSC 三個 RSS feed 中，
    `tw-reg:fsc:202602260001` 同時出現在 `fsc-news` 與 `fsc-notice`——各來源
    自己的 `fetch()` 內部去重擋不到這種跨來源鏡像，會讓一份公告算兩票。

    doc id 依設計即為「同一份文件的唯一鍵」（FSC 用來源自身的 `dataserno`），
    所以這裡不需要任何內容比對，相同 id 就是同一份。

    ⚠️ 丟棄時記 WARNING：id 相同但內容不同代表某個來源的 id 生成有誤，
    那是必須被看見的 bug，不能靜默吞掉。
    """
    seen: dict[str, "Document"] = {}
    unique: list["Document"] = []
    for doc in docs:
        kept = seen.get(doc.id)
        if kept is None:
            seen[doc.id] = doc
            unique.append(doc)
            continue
        if kept.text != doc.text:
            _log.warning(
                "文件 id 重複但內容不同（丟棄後者，疑為 id 生成錯誤）："
                "id=%s kept_source=%s dropped_source=%s",
                doc.id, kept.source, doc.source,
            )
    return unique


def _latest_bar_ts(bars) -> float:
    """用最後一根 K 的日期當價格事實的 fetched_at（UTC 當日 00:00）。"""
    if not bars:
        return 0.0
    from datetime import datetime, timezone
    try:
        dt = datetime.strptime(bars[-1].date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0
