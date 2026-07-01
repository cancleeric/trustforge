"""連接器快取層（階段 2：排程 fetcher + cache-only 產品讀取）。

老闆硬需求：真連接器（news/social/onchain/regulatory）有各自的 rate limit
（reddit ~10/min、FNG/SEC 30-60min、news/blockchain 10-15min），產品每個 request
都直接打真 API 會被 rate-limit 甚至封鎖。解法：

  1. `scripts/fetch_scheduler.py`（排程，cron/systemd timer 觸發）定時呼叫真
     `Source.fetch()`，把結果連同 `fetched_at` 寫進本檔的 cache backend——
     **全專案唯一**會在「非測試」情境下打真連接器 API 的地方。
  2. 產品路徑（`base.collect()` 的線上分支）改成呼叫 `CachedSource` 包裝過的
     來源：`CachedSource.fetch()` **只讀 cache**，未過期就回傳；cache-miss 或
     已過期一律 raise `CacheMissError`（由 `collect()` 既有的 try/except +
     `_failed` 降級機制接住，等同「這個來源暫時拿不到資料」），**絕不**在產品
     request 當下反過來呼叫被包裝的真 `Source.fetch()`。

⚠️ 「排程 refresh 間隔」與「CachedSource 判斷過期的硬過期時限」**刻意分成
兩組獨立數字**（codex HIGH-1 修正）——`DEFAULT_REFRESH_INTERVAL_SECONDS`
（排程多久打一次真 API）vs `DEFAULT_STALE_AFTER_SECONDS`（cache 多久沒更新
才視為不可用，硬過期 = `STALE_AFTER_MULTIPLIER`×refresh 間隔）。若兩者相同
（如 10min cron + 10min TTL），cron 稍微 jitter 或單次真呼叫失敗，就會出現
「排程還沒跑到、但 cache 已經『剛好』過期」的例行空窗，讓產品在每輪之間
必然有一段時間讀不到資料。硬過期留 2-3 倍 margin，才撐得住「連續幾次 refresh
失敗才會真的觸底」。

Backend 可插拔（CEO 架構決策：跟 `ledger.py` 走同一套 DynamoDB 慣例，
不用 SQLite）：
  - `DynamoDBCache`：線上持久用實作，PK=`source_id`/SK=`coin`，用 DynamoDB
    原生 TTL 屬性（`ttl`）背景自動清除過期項目。本 PR 只寫 code + mock 測試，
    **不打真 AWS、不建表**——真建表 + IAM 權限（`dynamodb:GetItem`/
    `dynamodb:PutItem`）由 CEO 另立步驟完成（比照 `DynamoDBLedger` 那次）。
  - `JsonCacheBackend`：本地 JSON 檔案，離線/測試/開發 fallback。
  - `get_cache_backend()` 依 env `CACHE_BACKEND`（`dynamodb`|`json`，**預設
    `dynamodb`**）選 backend。
  - `cache_get()`（讀）對 primary backend 失敗（缺憑證/表未建/網路問題）**一律**
    fallback 讀本地 `JsonCacheBackend`（比照 `ledger.append_run()` 慣例）——
    讀失敗頂多讓呼叫端多一次 cache-miss 降級，風險低，維持自動。
  - `cache_set()`（寫）**不再**預設自動 fallback（codex HIGH-2 修正）：
    production 的 DynamoDB 若寫入失敗，`cache_set()` 明確回傳
    `CacheWriteResult(ok=False, ...)`，讓呼叫端（`fetch_scheduler.py`）知道
    「這次沒有真的持久化」並可讓排程 exit 非零、被監控抓到。JSON fallback
    寫入是 **opt-in**（env `TRUSTFORGE_CACHE_JSON_FALLBACK=1` 或呼叫端明確
    傳 `allow_json_fallback=True`，預設關閉）：因為它只落在排程機本地磁碟，
    其他 runtime（產品讀取路徑走的是 DynamoDB）完全看不到，若預設自動 fallback
    又回報「成功」，會變成「DynamoDB 早就掛了但沒人發現」的假象。dev/CI 想要
    「沒有真 AWS 也有一個真的能用的本地快取」時，才自行開這個 opt-in。

Cache key 設計：`(source.name, coin)`，**不含 query**——原因：
  - 產品端 `query` 是使用者自由輸入的分析問題（見 `pipeline.run()`），逐字比對
    幾乎不可能命中同一把快取，會讓整層快取形同虛設。
  - 既有離線樣本路徑（`OfflineSampleSource.fetch()`）本來就只用 `coin` 過濾、
    完全不管 `query`（見 `base.py`），這裡延用同一慣例，行為與現有離線模式
    一致，不是新發明的語意。
  - `news`/`social` 的 RSS 解析器用 `keywords=[query, coin]` 篩選文字；排程器
    寫入快取時固定傳 `query=""`，等同只用 `coin` 篩（`keywords` 只剩非空的
    `coin`），跟離線樣本的過濾粒度一致。
  - `alternative-me-fng`/`sec-gov` 兩個來源本身完全不依 `coin` 篩選內容（見
    `onchain.py`/`regulatory.py` 註解：全市場/產業層級信號），為避免對它們
    重複打 5 次（每幣一次）浪費呼叫額度，`COIN_AGNOSTIC_SOURCES` 標記這兩個
    來源，`fetch_scheduler.py` 只呼叫一次真 API 後，把同一份結果廣播寫入
    `COIN_POOL` 每個幣的 cache key。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple

from .base import Document, Source

# ---------------------------------------------------------------------------
# 路徑（JSON fallback backend 用）：可用 env 覆寫，預設放 out/（.gitignore 已
# 排除，可重跑產物）。動態函式而非模組級常數，讓測試可用 monkeypatch.setenv()
# 在建立 backend 之前覆寫，立即生效（同 ledger.py `_default_ledger_path()`）。
# ---------------------------------------------------------------------------
def _default_cache_dir() -> Path:
    home = Path(os.getenv("TRUSTFORGE_HOME", str(Path(__file__).resolve().parents[3])))
    return Path(os.getenv("TRUSTFORGE_CACHE_DIR", str(home / "out" / "connector_cache")))


def _default_json_path() -> Path:
    return Path(
        os.getenv("TRUSTFORGE_CACHE_JSON_PATH", str(_default_cache_dir() / "connector_cache.json"))
    )


# ---------------------------------------------------------------------------
# 每來源預設「排程 refresh 間隔」（秒）——單一事實來源，`scripts/fetch_scheduler.py`
# 讀這裡決定多久打一次真 API（也是新鮮度守門判斷「已經跑過、不必重打」的
# 依據）。數字依老闆給的 rate limit 換算：
#   reddit ~10/min → 需 >=15-30min/feed，取 30min；FNG/SEC 30-60min，取
#   60min；news/blockchain 10-15min，取 15min。
#
# ⚠️ 這組數字是「多久刷新一次」，**不是**「多久沒刷新就要當機」——那是下面
# 的 `DEFAULT_STALE_AFTER_SECONDS`（codex HIGH-1：兩者硬相等會造成例行空窗，
# 見模組頂部說明）。
# ---------------------------------------------------------------------------
DEFAULT_REFRESH_INTERVAL_SECONDS: dict[str, int] = {
    "coindesk": 15 * 60,
    "decrypt": 15 * 60,
    "cryptopanic": 15 * 60,
    "reddit-cryptocurrency": 30 * 60,
    "reddit-bitcoin": 30 * 60,
    "alternative-me-fng": 60 * 60,
    "blockchain-info": 15 * 60,
    "sec-gov": 60 * 60,
}
DEFAULT_REFRESH_INTERVAL_FALLBACK_SECONDS = 15 * 60  # 未知來源名的保守預設

# 硬過期 = refresh 間隔的幾倍——留給「cron jitter / 排程單次真呼叫失敗 /
# DynamoDB 短暫故障」的緩衝：正常情況下每次 refresh 都遠早於硬過期就已更新，
# 只有連續 STALE_AFTER_MULTIPLIER-1 次 refresh 都失敗，cache 才會真的觸底、
# 讓 CachedSource 開始降級。3 倍＝允許連續 2 次刷新失敗仍不影響產品讀取。
STALE_AFTER_MULTIPLIER = 3


def stale_after_for(refresh_interval: float) -> float:
    """把「排程 refresh 間隔」換算成「CachedSource 硬過期時限」，供
    `CachedSource` 的 `ttl_seconds` 預設值、`scripts/fetch_scheduler.py` 寫入
    DynamoDB 原生 `ttl` 屬性時共用同一條換算公式（避免兩處各自寫一份倍數，
    改一邊漏改另一邊）。"""
    return refresh_interval * STALE_AFTER_MULTIPLIER


# `CachedSource` 的硬過期時限——由 `DEFAULT_REFRESH_INTERVAL_SECONDS` 依上述
# 倍數換算而來，是**單一事實來源的衍生值**，不是獨立手填的第二份數字（避免
# 之後改 refresh 間隔卻忘記同步改硬過期，兩份數字漂移又走回 HIGH-1 的坑）。
DEFAULT_STALE_AFTER_SECONDS: dict[str, int] = {
    name: int(stale_after_for(interval))
    for name, interval in DEFAULT_REFRESH_INTERVAL_SECONDS.items()
}
DEFAULT_STALE_AFTER_FALLBACK_SECONDS = int(
    stale_after_for(DEFAULT_REFRESH_INTERVAL_FALLBACK_SECONDS)
)

# env：是否允許 cache_set() 在 primary backend（如 DynamoDB）寫入失敗時，
# 悄悄 fallback 寫本地 JSON 卻仍回報「成功」。**預設關閉**（codex HIGH-2）：
# production 排程若 DynamoDB 故障，必須讓呼叫端看到明確失敗（進而 exit 非零、
# 被監控抓到），而不是被本地 fallback 掩蓋成「看起來一切正常」——本地 JSON
# 只在寫入的那台排程機看得到，其他 runtime（含產品讀取路徑）完全不會知道
# 資料其實沒進到共用的 DynamoDB。dev/CI 沒有真 AWS、想要一個「真的能用」的
# 本地快取時，才明確設這個 env 為 `1`/`true`（或呼叫 `cache_set(...,
# allow_json_fallback=True)`）開啟。
CACHE_JSON_FALLBACK_ENV = "TRUSTFORGE_CACHE_JSON_FALLBACK"


def _json_fallback_enabled(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    return os.getenv(CACHE_JSON_FALLBACK_ENV, "").strip().lower() in {"1", "true", "yes", "on"}

# 內容完全不依 coin 篩選的來源（全市場/產業層級信號）：排程器只打一次真
# API，把同一份結果廣播寫入每個幣的 cache key，避免浪費呼叫額度。
COIN_AGNOSTIC_SOURCES = frozenset({"alternative-me-fng", "sec-gov"})


def _normalize_coin(coin: str | None) -> str:
    return (coin or "").strip().upper()


def cache_key(source_name: str, coin: str | None) -> str:
    """快取 key：`來源名:幣別`（幣別正規化為大寫；空字串代表全市場通用）。"""
    return f"{source_name}:{_normalize_coin(coin)}"


def doc_to_dict(doc: Document) -> dict[str, Any]:
    """`Document` → 可 JSON 序列化的 dict（供 cache backend 寫入）。"""
    return {
        "id": doc.id, "kind": doc.kind, "source": doc.source,
        "text": doc.text, "url": doc.url, "ts": doc.ts, "meta": doc.meta,
    }


def doc_from_dict(d: dict[str, Any]) -> Document:
    """dict → `Document`（供 cache backend 讀出時還原）。缺欄位一律給安全預設，
    不因快取檔案格式輕微漂移就整批炸掉。"""
    return Document(
        id=str(d.get("id", "")), kind=str(d.get("kind", "")), source=str(d.get("source", "")),
        text=str(d.get("text", "")), url=str(d.get("url", "")),
        ts=float(d.get("ts", 0.0) or 0.0),
        meta=d.get("meta") if isinstance(d.get("meta"), dict) else {},
    )


class CacheMissError(RuntimeError):
    """快取未命中或已過期。由 `base.collect()` 既有的 try/except + `_failed`
    降級機制接住，等同「這個來源暫時拿不到資料」——不是程式錯誤，是預期中的
    正常降級路徑，呼叫端不應把它當成需要修的 bug 對待。"""


class CacheBackend(ABC):
    """快取儲存最小介面：單筆 get / 單筆 set。"""

    @abstractmethod
    def get(self, key: str) -> dict[str, Any] | None:
        """回傳 `{"docs": [dict, ...], "fetched_at": float}`；未命中回 `None`。"""

    @abstractmethod
    def set(
        self, key: str, docs: list[dict[str, Any]], fetched_at: float,
        ttl_seconds: float | None = None,
    ) -> None:
        """寫入一筆快取（同 key 覆蓋舊值）。

        `ttl_seconds`：選用，換算成該筆記錄「應視為新鮮」的時間窗。純本地
        backend（`JsonCacheBackend`）忽略它——新鮮度判斷本來就是
        `CachedSource` 讀取時的責任；`DynamoDBCache` 額外拿它換算 DynamoDB
        原生 `ttl` 屬性，作為背景自動清理的最佳努力優化（見該類別 docstring）。
        """


class JsonCacheBackend(CacheBackend):
    """單一 JSON 檔案，`{key: {"docs": [...], "fetched_at": ...}}`。

    離線/測試/開發 fallback（見模組頂部 `cache_get`/`cache_set`）。
    原子寫入（temp file + `os.replace`），避免寫到一半被中斷留下半殘檔案。
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else _default_json_path()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, key: str) -> dict[str, Any] | None:
        entry = self._load().get(key)
        if not isinstance(entry, dict):
            return None
        docs = entry.get("docs")
        fetched_at = entry.get("fetched_at")
        if not isinstance(docs, list) or fetched_at is None:
            return None
        return {"docs": docs, "fetched_at": float(fetched_at)}

    def set(
        self, key: str, docs: list[dict[str, Any]], fetched_at: float,
        ttl_seconds: float | None = None,  # noqa: ARG002 — 本地 backend 不需要，見介面 docstring
    ) -> None:
        data = self._load()
        data[key] = {"docs": docs, "fetched_at": fetched_at}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=f".{self.path.name}.", suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise


class DynamoDBCache(CacheBackend):
    """線上持久用 backend（DynamoDB 實作，完全比照 `ledger.py::DynamoDBLedger`
    的慣例）。

    ⚠️ 本 repo 端（開發/CI）**不打真 AWS**：`__init__` 只讀 env、不連線，
    `boto3` resource/Table 一律 lazy 建立（第一次真的 `get`/`set` 才建），
    確保沒有 AWS 憑證、表也還沒建的環境下，**建構 `DynamoDBCache()` 不會炸**。

    真表建立（PK=`source_id`、SK=`coin`，並在該表啟用原生 TTL、屬性名
    `ttl`）+ IAM 權限（`dynamodb:GetItem`/`dynamodb:PutItem`）+ 生產環境確認
    `CACHE_BACKEND=dynamodb`（本 backend 已是 `get_cache_backend()` 預設值）
    由 CEO 另立步驟完成，本檔不涉及、不真的建表。

    表結構：
      - PK `source_id`（S）：真連接器名稱，如 `coindesk`/`reddit-bitcoin`。
      - SK `coin`（S）：正規化大寫幣別；全市場通用來源（`COIN_AGNOSTIC_SOURCES`）
        广播寫入時仍逐幣各存一筆（跟 `CachedSource` 的讀取 key 對齊，見模組
        頂部「Cache key 設計」說明），SK 一律非空字串。
      - `docs_json`（S）：該筆 `Document` 清單的 JSON 序列化（用字串存，不用
        DynamoDB 巢狀 List/Map，序列化/還原邏輯跟 `JsonCacheBackend` 完全共用
        `doc_to_dict`/`doc_from_dict`，兩個 backend 行為一致）。
      - `fetched_at`（N，`Decimal`）：寫入當下的 epoch 秒。
      - `ttl`（N，`int`，epoch 秒）：DynamoDB 原生 TTL 屬性，背景自動清除。
        **僅為最佳努力清理，不是正確性依據**——AWS 官方說明 TTL 刪除可能延遲
        數小時到 48 小時，`CachedSource` 讀取時仍會用自己的 `fetched_at` +
        `ttl_seconds` 重新驗證新鮮度，不依賴這個屬性做即時過期判斷。
    """

    def __init__(self, table_name: str | None = None, region: str | None = None):
        self.table_name = table_name or os.getenv(
            "TRUSTFORGE_CACHE_TABLE", "trustforge-connector-cache"
        )
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self._table: Any = None  # lazy：建構本身不連 AWS

    def _get_table(self) -> Any:
        """lazy 取得 boto3 Table 物件；第一次呼叫才真的碰 AWS SDK。"""
        if self._table is None:
            import boto3  # 延遲匯入：建構/未啟用 dynamodb backend 時不需要憑證

            self._table = boto3.resource("dynamodb", region_name=self.region).Table(
                self.table_name
            )
        return self._table

    @staticmethod
    def _split_key(key: str) -> tuple[str, str]:
        source_id, _, coin = key.partition(":")
        return source_id, coin

    def get(self, key: str) -> dict[str, Any] | None:
        source_id, coin = self._split_key(key)
        resp = self._get_table().get_item(Key={"source_id": source_id, "coin": coin})
        item = resp.get("Item")
        if not isinstance(item, dict):
            return None
        docs_json = item.get("docs_json")
        fetched_at = item.get("fetched_at")
        if docs_json is None or fetched_at is None:
            return None
        try:
            docs = json.loads(docs_json)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(docs, list):
            return None
        return {"docs": docs, "fetched_at": float(fetched_at)}

    def set(
        self, key: str, docs: list[dict[str, Any]], fetched_at: float,
        ttl_seconds: float | None = None,
    ) -> None:
        source_id, coin = self._split_key(key)
        window = ttl_seconds if ttl_seconds is not None else DEFAULT_STALE_AFTER_FALLBACK_SECONDS
        item = {
            "source_id": source_id,
            "coin": coin,
            "docs_json": json.dumps(docs, ensure_ascii=False),
            "fetched_at": Decimal(str(fetched_at)),
            "ttl": int(fetched_at + window),
        }
        self._get_table().put_item(Item=item)


def get_cache_backend() -> CacheBackend:
    """依 env `CACHE_BACKEND`（`dynamodb`|`json`，**預設 `dynamodb`**）選
    backend。

    選 `dynamodb` 本身不會 raise（`DynamoDBCache.__init__` 只讀 env、不連
    AWS），實際是否可用（憑證/表是否存在）要到 `get`/`set` 呼叫時才知道：
    讀失敗由 `cache_get` 自動 fallback 讀 `JsonCacheBackend`；寫失敗由
    `cache_set` 回傳明確的 `CacheWriteResult(ok=False, ...)`（預設**不**
    fallback 寫本地 JSON，見該函式與 `CACHE_JSON_FALLBACK_ENV` 說明）。
    """
    backend = os.getenv("CACHE_BACKEND", "dynamodb").strip().lower()
    if backend == "json":
        return JsonCacheBackend()
    return DynamoDBCache()


def cache_get(backend: CacheBackend, key: str) -> dict[str, Any] | None:
    """讀快取；`backend` 失敗（如 DynamoDB 缺憑證/表未建/網路問題）自動
    fallback 讀本地 `JsonCacheBackend`（比照 `ledger.append_run()` 的 fallback
    慣例）。

    ⚠️ 只在 **backend 本身丟例外**時才 fallback；backend 正常回應「這個 key
    沒有資料」（回傳 `None`）視為合法的 cache-miss，不觸發 fallback 去查
    另一個 backend（避免兩個 backend 資料不同步時，把「A 沒有」誤判成
    「應該去問 B」，語意模糊）。
    """
    try:
        return backend.get(key)
    except Exception as exc:
        print(f"[cache] WARNING: get 失敗（backend={type(backend).__name__}）：{exc}",
              file=sys.stderr)

    if isinstance(backend, JsonCacheBackend):
        return None  # 同一顆 JsonCacheBackend 剛失敗，換個新實例打同路徑必再失敗，不重試

    try:
        return JsonCacheBackend().get(key)
    except Exception as exc:
        print(f"[cache] WARNING: fallback JsonCacheBackend get 仍失敗：{exc}", file=sys.stderr)
        return None


class CacheWriteResult(NamedTuple):
    """`cache_set()` 的明確回傳結果（codex HIGH-2：避免寫入失敗被靜默吞掉、
    呼叫端誤以為成功）。

    - `ok`：`True` 表示資料真的被某個 backend 持久化成功；`False` 表示
      primary backend 失敗，且沒有（或不允許）fallback 成功——呼叫端應視為
      「這次沒有真的寫入」，不能當成功繼續往下走。
    - `used_fallback`：`True` 表示 primary backend 失敗、靠本地
      `JsonCacheBackend` fallback 才寫入成功。**這是一個警訊，不是正常路徑**
      ——本地 JSON 只在寫入的那台機器看得到，其他 runtime（含產品讀取路徑）
      看不到這筆資料，呼叫端不應把它當成跟 primary 成功等價的「一切正常」。
    - `backend`：實際成功寫入的 backend 類別名稱；全部失敗時為 primary
      backend 的類別名稱（供記錄用）。
    - `error`：primary backend 失敗時的例外訊息字串；primary 成功則為 `None`。
    """

    ok: bool
    used_fallback: bool
    backend: str
    error: str | None = None


def cache_set(
    backend: CacheBackend, key: str, docs: list[dict[str, Any]], fetched_at: float,
    ttl_seconds: float | None = None,
    allow_json_fallback: bool | None = None,
) -> CacheWriteResult:
    """寫快取，回傳 `CacheWriteResult`（明確成功/失敗，不再靜默吞例外，見
    codex HIGH-2）。

    `allow_json_fallback`：primary backend（如 DynamoDB）寫入失敗時，是否
    fallback 寫本地 `JsonCacheBackend`。`None`（預設）讀 env
    `CACHE_JSON_FALLBACK_ENV`（`TRUSTFORGE_CACHE_JSON_FALLBACK`），**該 env
    預設關閉**——production 排程遇到 primary 故障應該回報明確失敗（`ok=False`）
    讓監控抓到，而不是悄悄寫到排程機本地磁碟、卻回報「成功」造成假象（本地
    fallback 對其他 runtime 完全不可見）。dev/CI 沒有真 AWS、想要一個真正
    能用的本地快取時，才明確開這個 opt-in（env 設 `1`，或呼叫時傳
    `allow_json_fallback=True`）。
    """
    backend_name = type(backend).__name__
    try:
        backend.set(key, docs, fetched_at, ttl_seconds=ttl_seconds)
        return CacheWriteResult(ok=True, used_fallback=False, backend=backend_name, error=None)
    except Exception as exc:
        err = str(exc)
        print(f"[cache] WARNING: set 失敗（backend={backend_name}）：{exc}", file=sys.stderr)

    if isinstance(backend, JsonCacheBackend):
        # 同一顆 JsonCacheBackend 剛失敗，換個新實例打同路徑必再失敗，不重試
        return CacheWriteResult(ok=False, used_fallback=False, backend=backend_name, error=err)

    if not _json_fallback_enabled(allow_json_fallback):
        return CacheWriteResult(ok=False, used_fallback=False, backend=backend_name, error=err)

    try:
        JsonCacheBackend().set(key, docs, fetched_at, ttl_seconds=ttl_seconds)
        return CacheWriteResult(
            ok=True, used_fallback=True, backend="JsonCacheBackend", error=err
        )
    except Exception as exc2:
        print(f"[cache] WARNING: fallback JsonCacheBackend set 仍失敗：{exc2}", file=sys.stderr)
        return CacheWriteResult(
            ok=False, used_fallback=False, backend=backend_name,
            error=f"{err}; fallback JsonCacheBackend 也失敗：{exc2}",
        )


class CachedSource(Source):
    """包裝任一真 `Source`，`fetch()` 只讀 cache，絕不呼叫被包裝來源的真
    `fetch()`（那是 `scripts/fetch_scheduler.py` 排程任務的職責）。

    - cache 命中且未過期 → 回傳快取的 `Document` 清單。
    - cache-miss / 已過期 → raise `CacheMissError`，交由 `base.collect()`
      既有的 try/except + `_failed` 降級機制處理（等同該來源這次拿不到資料，
      report.limits 會反映出來），**不**回退去打真線上 API。
    """

    def __init__(
        self,
        wrapped: Source,
        ttl_seconds: float | None = None,
        backend: CacheBackend | None = None,
    ):
        self._wrapped = wrapped
        self.kind = wrapped.kind
        self.name = wrapped.name
        self.ttl_seconds = (
            ttl_seconds if ttl_seconds is not None
            else DEFAULT_STALE_AFTER_SECONDS.get(wrapped.name, DEFAULT_STALE_AFTER_FALLBACK_SECONDS)
        )
        self._backend = backend if backend is not None else get_cache_backend()

    def fetch(self, query: str, coin: str = "") -> list[Document]:  # noqa: ARG002
        # query 刻意不參與 cache key（見模組頂部說明），只用 coin 查快取。
        key = cache_key(self.name, coin)
        entry = cache_get(self._backend, key)
        if entry is None:
            raise CacheMissError(
                f"{self.name}: 無快取資料（key={key!r}）——"
                "尚未由 scripts/fetch_scheduler.py 寫入，或 backend 讀取失敗"
            )
        fetched_at = float(entry.get("fetched_at", 0.0) or 0.0)
        age = time.time() - fetched_at
        if age > self.ttl_seconds:
            raise CacheMissError(
                f"{self.name}: 快取已過期（key={key!r}，age={age:.0f}s > "
                f"ttl={self.ttl_seconds}s）"
            )
        docs_raw = entry.get("docs") or []
        return [doc_from_dict(d) for d in docs_raw if isinstance(d, dict)]
