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
import sqlite3
import sys
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, NamedTuple

from ..data_contracts import DOCUMENT_SCHEMA_VERSION
from .base import Document, Source

try:
    import fcntl
except ModuleNotFoundError:  # pragma: no cover - exercised via Windows import tests.
    fcntl = None  # type: ignore[assignment]

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


def _default_sqlite_path() -> Path:
    """本機持久化資料庫；JSON 僅保留為匯入/故障備援。"""
    home = Path(os.getenv("TRUSTFORGE_HOME", str(Path(__file__).resolve().parents[3])))
    return Path(
        os.getenv("TRUSTFORGE_SQLITE_PATH", str(home / "out" / "trustforge.sqlite3"))
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
    # 資料密度第一批（#24，docs/archive/plans/PLAN-data-density.md）新增 6 家新聞 RSS，
    # 同 coindesk/decrypt 統一 15 分鐘一輪，keyless 公開 RSS 無 rate limit
    # 硬性公告，比照現有新聞源節奏即可。
    "cointelegraph": 15 * 60,
    "bitcoinmagazine": 15 * 60,
    "cryptoslate": 15 * 60,
    "bitcoinist": 15 * 60,
    "newsbtc": 15 * 60,
    "dailyhodl": 15 * 60,
    # 資料密度第二批（#24，docs/archive/plans/PLAN-data-density.md）新增 The Block/U.Today/
    # Blockworks 3 家新聞 RSS，同上統一 15 分鐘一輪。
    "theblock": 15 * 60,
    "utoday": 15 * 60,
    "blockworks": 15 * 60,
    "reddit-cryptocurrency": 30 * 60,
    "reddit-bitcoin": 30 * 60,
    "alternative-me-fng": 60 * 60,
    "blockchain-info": 15 * 60,
    # 資料密度第二批：mempool.space（即時手續費/難度進度，波動快，跟
    # blockchain-info 一樣取 15 分鐘）、Blockchair（免費層 1440 req/day ≈
    # 每分鐘 1 次上限，遠低於此排程頻率，60 分鐘一輪保守使用額度）。
    "mempool-space-fees": 15 * 60,
    "mempool-space-difficulty": 15 * 60,
    "blockchair": 60 * 60,
    "sec-gov": 60 * 60,
    # CoinGecko（W-coingecko，CEO 審核 gray 計劃 + 老闆修正）：呼叫量極小
    # （見 ingestion/coingecko.py 模組頂部「高效抓取」——一輪 5 幣合計只需
    # ≈6 次真呼叫，price 1 次全涵蓋 + coins/{id} 詳情每幣 1 次由 sentiment/
    # dev 共用），三者統一 5 分鐘一輪，keyless（5-15 req/min）綽綽有餘。
    "coingecko-price": 5 * 60,        # price_live：5 分鐘
    "coingecko-sentiment": 5 * 60,    # sentiment：5 分鐘
    "coingecko-dev": 5 * 60,          # dev_activity：5 分鐘
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
# API，把「同一份」結果廣播寫入每個幣的 cache key，避免浪費呼叫額度。
# ⚠️ 跟下面 `COIN_KEYED_BATCH_SOURCES` 不同：這裡廣播的內容對每個幣是
# **完全相同**的（FNG/SEC 本來就不分幣），適合直接整份複製。
#
# issue #385 台灣監管來源（FSC/MOPS/TWSE/TPEx）同屬此類：政府公告本來就不分
# 幣別，一份回應對每個幣完全相同。不登記在此的話，排程器會為每個幣各打一次
# 真 API——5 幣 × 7 源 ＝ 每輪 35 次請求打政府站，既浪費也不禮貌。
COIN_AGNOSTIC_SOURCES = frozenset(
    {
        "alternative-me-fng",
        "sec-gov",
        "fsc-news",
        "fsc-penalty",
        "fsc-notice",
        "mops-twse",
        "mops-tpex",
        "twse-punish",
        "tpex-punish",
    }
)

# 一次真呼叫的回應本身就「涵蓋多幣、且已用 `Document.meta['coin']` 明確
# 標示各自歸屬」的來源（生產事故修復：CoinGecko price 429 風暴根因，見
# `scripts/fetch_scheduler.py::run_once()` 對應分支說明）：
#   - `coingecko-price`（`ingestion/coingecko.py::CoinGeckoPriceSource`）
#     `simple/price` 端點一次回應就含 5 幣現價，`fetch(coin="")` 會回傳
#     5 筆 Document，各自帶顯式 `meta["coin"]`。
# 跟 `COIN_AGNOSTIC_SOURCES` 的關鍵差異：這裡每個幣的內容**不同**（各幣
# 現價本來就不一樣），排程器只能真呼叫一次，但**不能**像 coin-agnostic
# 那樣把同一份完整結果廣播到每個幣的 cache key（那樣 BTC 的 cache 裡會
# 混進 ETH/SOL/BNB/XRP 的價格文件，即使下游 `_matches_coin` 有過濾網，
# 也不該讓「他幣資料」平白出現在「本幣」的 cache 快取裡）——而是把單次
# 回應依每筆 Document 自帶的 `meta["coin"]` **分流**寫入各自對應的 cache
# key，讓每個幣的 cache 內容天生就「只含自己」，語意與其餘逐幣來源一致。
COIN_KEYED_BATCH_SOURCES = frozenset({"coingecko-price"})

# Axis C #1（task #23，PLAN docs/archive/plans/PLAN-axisC-snapshots.md）：多幣信任快照 +
# 首頁總覽正確讀路徑——`scripts/fetch_scheduler.py --snapshot` 這個「寫入者」
# 與 `web.py::_render_home_page()` 這個「讀路徑」用的 cache key 名稱必須逐字
# 一致，兩處若各自寫死字串、之後改一邊忘了同步改另一邊，會變成「寫入者寫進
# A key，讀路徑讀 B key」——cache-miss 靜默降級成「不顯總覽」，不會有任何
# 錯誤訊息可循。定義在本模組（兩者共同的依賴）作為單一事實來源，避免這個坑
# （呼應模組頂部「codex HIGH-1」refresh/stale 兩組數字分離時同樣的教訓）。
#
# `TRUST_SNAPSHOT_SOURCE`：逐幣快照 key 前綴，實際 key 為
# `cache_key(TRUST_SNAPSHOT_SOURCE, coin)`（如 `__trust_snapshot__:BTC`）。
# `TRUST_OVERVIEW_SOURCE`/`TRUST_OVERVIEW_COIN`：單一總覽 HTML blob 的
# (source, coin) 組合——`coin` 刻意給非空 sentinel（同 `web.py`
# `_STATUS_PROBE_COIN` 慣例）：`DynamoDBCache` 的 SK 絕不接受空字串，傳空字串
# 會被 DynamoDB 直接拒絕（`ValidationException`），不是「backend 連不上」，
# 兩者不可混為一談。
TRUST_SNAPSHOT_SOURCE = "__trust_snapshot__"
TRUST_OVERVIEW_SOURCE = "__trust_overview_html__"
TRUST_OVERVIEW_COIN = "__trust_overview_html__"

# codex HIGH（PR #47 review）：`web.py` 讀路徑原本只檢查總覽 blob 是否
# 非空，未檢查 `fetched_at`——DynamoDB TTL 刪除是 best-effort，官方文件
# 說明可能延遲數小時到 48 小時（見 `DynamoDBCache` docstring「表結構」
# 段），若快照寫入者（cron）停擺，reader 仍會持續讀到舊 item，且被
# `web.py` module 級 TTL cache 每次 renew 成「新鮮」，導致斷網/排程停擺
# 期間首頁一直顯示過期的信任判斷當成即時——信任產品不能這樣。
#
# 修法比照本模組 `CachedSource.fetch()` 既有的自驗新鮮度慣例（見下方
# `age = now - fetched_at; age > ttl_seconds` 一段）：reader 讀到 entry 後
# 自己拿 `fetched_at` 跟這個新鮮窗比對，超過就視同 cache-miss（不顯總
# 覽），不依賴 DynamoDB TTL 的非同步刪除語意。窗口沿用 `stale_after_for()`
# 既有 3x margin 公式，refresh 間隔跟 `scripts/fetch_scheduler.py
# --snapshot` 建議 cron cadence 共用同一份數字（單一事實來源，避免寫入者
# 與讀路徑各自定義漂移，同上方 key 常數的理由）。
TRUST_SNAPSHOT_REFRESH_INTERVAL_SECONDS = 15 * 60  # 建議 cron cadence
TRUST_SNAPSHOT_FRESH_WINDOW_SECONDS = stale_after_for(
    TRUST_SNAPSHOT_REFRESH_INTERVAL_SECONDS
)  # = 45 分鐘

# task #26（docs/archive/plans/PLAN-multicore-worldfirst.md，「新核心#1 持久化寫入基礎：
# 快照按日累積歷史」）：`TRUST_SNAPSHOT_SOURCE` 只存「最新一筆」、每輪覆寫，
# 完全沒有歷史——這裡加一組**按日**的 key pattern，讓「歷史信任趨勢
# Point-in-Time」的資料現在就開始累積（UI 晚做沒關係，但寫入現在不開始，
# 賣點就永遠無法成立）。
#
# Key 慣例：`cache_key(TRUST_SNAPSHOT_HISTORY_SOURCE, f"{coin}:{YYYY-MM-DD}")`
# （見下方 `trust_snapshot_history_key()`）——沿用既有 `cache_key(source,
# coin)` 兩段式介面，把「幣別:日期」塞進第二段。`_normalize_coin()` 只做
# `strip().upper()`，日期裡的數字/連字號不受影響。**這是新 key pattern，
# 不是 DynamoDB schema/表異動**：底層仍是同一張 `trustforge-connector-cache`
# 表、同樣的 `source_id`/`coin` 兩個屬性，`DynamoDBCache._split_key()` 用
# `partition(":")` 只切第一個冒號，`coin` 屬性會存進完整的
# `"BTC:2026-07-01"` 字串，跟其他逐幣 key 一樣只是個字串值，沒有新增任何
# 屬性/索引，不需要 CDO 過 6 步異動流程。
#
# 同一天多次跑 `--snapshot` 對同一把 key 覆寫（`cache_set()` 本來就是「同
# key 覆蓋舊值」，見 `JsonCacheBackend.set`/`DynamoDBCache.set`）＝uPsert；
# 跨日才會因 key 不同而各自累積成一筆，天然形成按日序列。
#
# TTL 刻意跟「最新一筆」快照的 45 分鐘新鮮窗**分開**：歷史快照本來就該讓
# 舊資料繼續存在供未來趨勢圖用（不能套「45 分鐘沒更新視為過期」那套語意），
# 但也不能無限期累積佔用儲存——90 天是合理上限，交給 DynamoDB 原生 TTL
# 背景清除（best effort，見 `DynamoDBCache` docstring）；`JsonCacheBackend`
# 忽略 TTL 屬性（本地開發用途，不會真的無限膨脹到有感的程度）。
TRUST_SNAPSHOT_HISTORY_SOURCE = "__trust_snapshot_history__"
TRUST_SNAPSHOT_HISTORY_TTL_SECONDS = 90 * 24 * 60 * 60  # 90 天

# codex HIGH（PR #59 review，task #26 追加修正）：「最新一筆」（上面
# `TRUST_SNAPSHOT_SOURCE`）跟「按日歷史」原本都是各自獨立、無條件的
# `cache_set()`——若兩輪排程重疊（cron jitter/重試/DynamoDB 延遲），run A
# （較舊 `fetched_at`）跟 run B（較新）交錯執行，可能 A 的 latest 先寫、B
# 兩者都寫、但 **A 的 history 寫入最後才完成**，當日 key 就會被 A 的舊快照
# 覆蓋回去，而 latest 卻是 B 的新快照——歷史序列被舊值污染，且每次寫都回報
# 成功，是靜默資料損毀（違反 #24：歷史趨勢會顯示錯誤數值）。
#
# 修法：「按日歷史」key 改用**單調條件寫入**（`CacheBackend.set_if_newer()`／
# `cache_set_if_newer()`）——寫入前先讀當日 key，只有「不存在」或「既有
# `fetched_at` 嚴格小於 incoming `fetched_at`」才真的覆寫，較舊的 incoming
# 直接跳過（不寫、不算失敗）。「最新一筆」`TRUST_SNAPSHOT_SOURCE` 維持原樣
# 不變——它本來就是 last-write-wins 語意，沒有歷史正確性要求，見
# `set_if_newer()`/`DynamoDBCache.set_if_newer()`/`JsonCacheBackend.set_if_newer()`
# 三處實作與 `scripts/fetch_scheduler.py::run_snapshot()` 呼叫端。
#
# 比照 `scheduler_log.py::DynamoDBSchedulerRunLog._update_latest_pointer`／
# `JsonlSchedulerRunLog._update_pointers_locked` 既有的 compare-and-set 慣例
# （同一顆 repo 內已有先例，非本次新發明）：DynamoDB 用條件式 `PutItem`
# （`ConditionExpression`）在伺服器端把「比較＋覆寫」合成單一原子操作；本地
# JSON 用 `fcntl.flock` 包住整段「讀→比較→寫」臨界區，跨行程/跨執行緒序列化。


def snapshot_history_date(fetched_at: float) -> str:
    """把 epoch 秒換算成歷史快照按日 key 用的 UTC 日期字串（`YYYY-MM-DD`）。

    固定用 UTC（比照 `scheduler_log.py`/`schema.py::iso_utc()` 既有時間戳
    慣例），不用本機時區——避免「同一次真實寫入，換一台跑排程的機器就切出
    不同日期」這種亂跳（PLAN 明確點名的風險）。"""
    return datetime.fromtimestamp(fetched_at, tz=timezone.utc).strftime("%Y-%m-%d")


def trust_snapshot_history_key(coin: str, date_str: str) -> str:
    """按日歷史快照 cache key，寫入者（`scripts/fetch_scheduler.py
    --snapshot`）與讀取者（`get_trust_history()`）共用同一份組字串邏輯，
    避免兩處各自手串日後漂移（同模組其餘 key 常數一貫的教訓）。"""
    return cache_key(TRUST_SNAPSHOT_HISTORY_SOURCE, f"{_normalize_coin(coin)}:{date_str}")


def get_trust_history(
    coin: str,
    days: int,
    backend: CacheBackend | None = None,
    *,
    end_date: str | None = None,
    strict: bool = False,
) -> list[dict[str, Any]]:
    """讀回 `coin` 過去 `days` 天（含 `end_date` 當天，預設今天 UTC）的按日
    信任快照序列——供未來 #26 UI 趨勢圖使用（本輪只做寫入 + 這個讀取
    helper，UI 本身晚做沒關係，見 PLAN）。

    純讀：只呼叫既有 `cache_get()`（本身已具備 backend 失敗自動 fallback
    讀 `JsonCacheBackend` 的語意），**不寫入**、不觸發任何真連接器 API 或
    `pipeline.run()` 計算——資料只可能來自 `scripts/fetch_scheduler.py
    --snapshot` 既有寫入的按日快照，credit-safe。

    回傳依日期由舊到新排序的清單，每筆是該日 `_snapshot_dict()` 原樣內容
    + 補上 `"date"` 欄位標明所屬日期；缺漏的日期（尚未跑過 `--snapshot`，
    或該日該幣 `pipeline.run()` 失敗未寫入）直接跳過，不補假值（#24 鐵律）。

    `end_date`：選用，`YYYY-MM-DD` 字串，供測試/未來 UI 指定「以哪天為
    終點往回看」；不傳預設用當下 UTC 日期。

    `strict`（預設 `False`，opt-in，不影響既有呼叫端）：直接透傳給底層
    `cache_get(..., strict=...)`——`True` 時任一天讀取「真的失敗」
    （不是單純沒快照）會 `raise CacheReadFailure`，讓呼叫端（如
    `/api/history`）能區分「cache outage 該 502」與「這幾天沒快照，正常
    回空序列」（見 `cache_get()` docstring、codex 複審 HIGH 根因修復）。
    """
    resolved_backend = backend if backend is not None else get_cache_backend()
    if end_date is not None:
        end = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        end = datetime.now(timezone.utc)

    history: list[dict[str, Any]] = []
    for offset in range(days):
        day_str = (end - timedelta(days=offset)).strftime("%Y-%m-%d")
        entry = cache_get(
            resolved_backend, trust_snapshot_history_key(coin, day_str), strict=strict
        )
        if entry is None:
            continue
        docs = entry.get("docs") or []
        if not docs or not isinstance(docs[0], dict):
            continue
        snap = dict(docs[0])
        snap["date"] = day_str
        history.append(snap)

    history.sort(key=lambda s: s["date"])
    return history


def _normalize_coin(coin: str | None) -> str:
    return (coin or "").strip().upper()


def cache_key(source_name: str, coin: str | None) -> str:
    """快取 key：`來源名:幣別`（幣別正規化為大寫；空字串代表全市場通用）。"""
    return f"{source_name}:{_normalize_coin(coin)}"


def doc_to_dict(doc: Document) -> dict[str, Any]:
    """`Document` → 可 JSON 序列化的 dict（供 cache backend 寫入）。"""
    return {
        "schema_version": doc.schema_version,
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
        schema_version=str(d.get("schema_version") or DOCUMENT_SCHEMA_VERSION),
    )


class CacheMissError(RuntimeError):
    """快取未命中或已過期。由 `base.collect()` 既有的 try/except + `_failed`
    降級機制接住，等同「這個來源暫時拿不到資料」——不是程式錯誤，是預期中的
    正常降級路徑，呼叫端不應把它當成需要修的 bug 對待。"""


class CacheBackend(ABC):
    """快取儲存最小介面：單筆 get / 單筆 set。"""

    @abstractmethod
    def get(self, key: str, *, consistent_read: bool = False) -> dict[str, Any] | None:
        """回傳 `{"docs": [dict, ...], "fetched_at": float}`；未命中回 `None`。

        `consistent_read`：選用。純本地 backend（`JsonCacheBackend`）忽略它
        （本來就沒有最終一致性問題）；`DynamoDBCache` 傳給 `get_item` 的
        `ConsistentRead`。預設 `False`（沿用正常讀取路徑的最終一致讀，較省
        成本）——只有 `scripts/fetch_scheduler.py --probe` 這種「剛寫完馬上
        要驗證讀不讀得回同一筆」的場景才需要 `True`，否則固定 canary key
        遇到最終一致讀的複寫延遲，可能讀到上一輪的舊 sentinel，誤判成
        「讀回內容不一致」而非確定性地失敗。
        """

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

    def set_if_newer(
        self, key: str, docs: list[dict[str, Any]], fetched_at: float,
        ttl_seconds: float | None = None,
    ) -> bool:
        """單調條件寫入：只有這把 `key` 目前不存在、或既有 `fetched_at`
        嚴格小於這次要寫入的 `fetched_at` 時，才真的覆寫；否則跳過（回傳
        `False`，**不是**失敗——只是這次的值比已存在的舊，不該覆寫，見
        codex HIGH「歷史快照 out-of-order 覆蓋」修正說明）。

        ⚠️ 這是**未優化/非原子的參考實作**（get → 比較 → set，中間有
        race window）——只給沒有專用原子 compare-and-set 能力的假想 backend
        當退路用。兩個實際 backend（`JsonCacheBackend`／`DynamoDBCache`）都
        **必須 override** 成真正原子的操作（`fcntl.flock` 包住臨界區／
        DynamoDB 條件式 `PutItem`），不可讓兩個重疊排程的讀-比較-寫序列
        彼此打斷（同 `scheduler_log.py` 兩個子類別各自 override `latest()`/
        `recent()` 的既有教訓）。"""
        existing = self.get(key, consistent_read=True)
        if existing is not None and existing.get("fetched_at", float("-inf")) >= fetched_at:
            return False
        self.set(key, docs, fetched_at, ttl_seconds=ttl_seconds)
        return True


class JsonCacheBackend(CacheBackend):
    """單一 JSON 檔案，`{key: {"docs": [...], "fetched_at": ...}}`。

    離線/測試/開發 fallback（見模組頂部 `cache_get`/`cache_set`）。
    原子寫入（temp file + `os.replace`），避免寫到一半被中斷留下半殘檔案。
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else _default_json_path()
        self._read_cache_lock = threading.RLock()
        self._write_lock = threading.RLock()
        self._read_cache_signature: tuple[int, int, int] | None = None
        self._read_cache_data: dict[str, Any] | None = None

    def _file_signature(self) -> tuple[int, int, int] | None:
        try:
            stat = self.path.stat()
        except OSError:
            return None
        return (stat.st_ino, stat.st_mtime_ns, stat.st_size)

    def _load(self) -> dict[str, Any]:
        """Load one immutable-on-read snapshot and reuse it until the file changes.

        The status matrix reads every source/coin cell from the same JSON file.  The
        old implementation parsed the whole file once per cell (currently 115
        parses of a ~2 MB file per request), which made local observability slow
        enough to cascade into frontend timeouts.  The scheduler replaces this file
        atomically, so inode/mtime/size form a cheap invalidation signature.
        """
        with self._read_cache_lock:
            signature = self._file_signature()
            if signature is None:
                self._read_cache_signature = None
                self._read_cache_data = {}
                return self._read_cache_data
            if signature == self._read_cache_signature and self._read_cache_data is not None:
                return self._read_cache_data

            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
            resolved = data if isinstance(data, dict) else {}
            self._read_cache_signature = self._file_signature()
            self._read_cache_data = resolved
            return resolved

    def get(self, key: str, *, consistent_read: bool = False) -> dict[str, Any] | None:
        del consistent_read  # 單一本機 JSON 檔案，本來就沒有最終一致性問題
        entry = self._load().get(key)
        if not isinstance(entry, dict):
            return None
        docs = entry.get("docs")
        fetched_at = entry.get("fetched_at")
        if not isinstance(docs, list) or fetched_at is None:
            return None
        return {"docs": docs, "fetched_at": float(fetched_at)}

    @property
    def _lock_path(self) -> Path:
        """跟 `self.path` 同目錄的鎖檔（比照 `scheduler_log.py
        ::JsonlSchedulerRunLog._latest_lock_path` 慣例，`fcntl.flock` 用）。

        codex HIGH（PR #59 review 第二輪）：**所有**會整檔 read-modify-write
        `self.path` 的 mutation（`set()`／`set_if_newer()`，未來若加
        `delete()` 之類也一樣）都必須共用同一把鎖，缺一不可——先前只有
        `set_if_newer()` 拿鎖，`set()` 沒拿，導致：`set_if_newer()` 在鎖內
        load 出一份資料、判斷該覆寫、寫回；但另一個執行緒/行程同時呼叫的
        普通 `set()` 若剛好在 `set_if_newer()` 寫入前就已經 load 了同一份
        「舊」資料到自己的記憶體，`set_if_newer()` 寫完之後，`set()` 才拿
        著手上那份舊 copy（不包含 `set_if_newer()` 剛寫進去的 key）整檔
        覆寫回去——等於把 `set_if_newer()` 剛寫的 key 直接刪掉（lost
        update，只是這次遺失的是「整把 key」而不是單一欄位）。`get()` 維持
        不拿鎖：`os.replace()` 保證讀者只會看到「舊的完整檔」或「新的完整
        檔」其中一種，不會讀到寫一半的殘檔，讀不需要鎖也不會有這個坑。"""
        return self.path.with_name(self.path.name + ".lock")

    @contextmanager
    def _locked_for_write(self):
        """Serialize JSON read-modify-write on platforms with and without fcntl."""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._write_lock:
            if fcntl is None:
                yield
                return

            with open(self._lock_path, "a+", encoding="utf-8") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _set_unlocked(
        self, key: str, docs: list[dict[str, Any]], fetched_at: float,
        ttl_seconds: float | None = None,  # noqa: ARG002 — 本地 backend 不需要，見介面 docstring
    ) -> None:
        """`set()` 的核心 read-modify-write 邏輯，**假設呼叫端已經持有
        `_lock_path` 的 flock**——只給 `set()`/`set_if_newer()` 兩個公開方法
        在各自的鎖臨界區內呼叫，避免 `set_if_newer()` 呼叫 `self.set()` 時
        對同一把鎖再巢狀 `open()`/`flock()` 一次（語意上容易搞混，直接用
        不拿鎖的內部版本更清楚，不依賴 flock 對同執行緒重入的細節）。"""
        # Copy-on-write: `_load()` may return the shared read snapshot.  Mutating it
        # before `os.replace()` succeeds would expose data that is not on disk yet.
        data = dict(self._load())
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

    def set(
        self, key: str, docs: list[dict[str, Any]], fetched_at: float,
        ttl_seconds: float | None = None,
    ) -> None:
        """公開介面：**先拿 `_lock_path` 的 flock，再做 read-modify-write**
        （見 `_lock_path` docstring 的 lost-update 說明）——跟
        `set_if_newer()` 共用同一把鎖，兩者交錯呼叫時彼此序列化，任一方都
        不會拿著對方寫入前的舊資料整檔覆寫回去。"""
        with self._locked_for_write():
            self._set_unlocked(key, docs, fetched_at, ttl_seconds=ttl_seconds)

    def set_if_newer(
        self, key: str, docs: list[dict[str, Any]], fetched_at: float,
        ttl_seconds: float | None = None,
    ) -> bool:
        with self._locked_for_write():
            existing = self.get(key)
            if existing is not None and existing["fetched_at"] >= fetched_at:
                return False
            self._set_unlocked(key, docs, fetched_at, ttl_seconds=ttl_seconds)
            return True


class SQLiteCacheBackend(CacheBackend):
    """本機正式 cache backend。

    使用 SQLite WAL，讓 Web 的並行讀取與 15 分鐘排程寫入可共用同一資料庫；
    每個 key 是 `(source_id, coin)`，內容仍維持既有 docs/fetched_at 契約。
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else _default_sqlite_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path, timeout=10.0, check_same_thread=False, isolation_level=None,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS connector_cache (
                source_id TEXT NOT NULL,
                coin TEXT NOT NULL,
                docs_json TEXT NOT NULL,
                fetched_at REAL NOT NULL,
                expires_at REAL,
                PRIMARY KEY (source_id, coin)
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_connector_cache_fetched_at "
            "ON connector_cache(fetched_at)"
        )

    @staticmethod
    def _split_key(key: str) -> tuple[str, str]:
        source_id, separator, coin = key.partition(":")
        if not separator or not source_id or not coin:
            raise ValueError(f"invalid cache key: {key!r}")
        return source_id, coin

    def get(self, key: str, *, consistent_read: bool = False) -> dict[str, Any] | None:
        del consistent_read  # SQLite 讀取本身具交易一致性
        source_id, coin = self._split_key(key)
        with self._lock:
            row = self._conn.execute(
                "SELECT docs_json, fetched_at FROM connector_cache "
                "WHERE source_id = ? AND coin = ?",
                (source_id, coin),
            ).fetchone()
        if row is None:
            return None
        try:
            docs = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(docs, list):
            return None
        return {"docs": docs, "fetched_at": float(row[1])}

    def set(
        self, key: str, docs: list[dict[str, Any]], fetched_at: float,
        ttl_seconds: float | None = None,
    ) -> None:
        source_id, coin = self._split_key(key)
        expires_at = fetched_at + ttl_seconds if ttl_seconds is not None else None
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO connector_cache
                    (source_id, coin, docs_json, fetched_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id, coin) DO UPDATE SET
                    docs_json = excluded.docs_json,
                    fetched_at = excluded.fetched_at,
                    expires_at = excluded.expires_at
                """,
                (source_id, coin, json.dumps(docs, ensure_ascii=False), fetched_at, expires_at),
            )

    def set_if_newer(
        self, key: str, docs: list[dict[str, Any]], fetched_at: float,
        ttl_seconds: float | None = None,
    ) -> bool:
        source_id, coin = self._split_key(key)
        expires_at = fetched_at + ttl_seconds if ttl_seconds is not None else None
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO connector_cache
                    (source_id, coin, docs_json, fetched_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id, coin) DO UPDATE SET
                    docs_json = excluded.docs_json,
                    fetched_at = excluded.fetched_at,
                    expires_at = excluded.expires_at
                WHERE excluded.fetched_at > connector_cache.fetched_at
                """,
                (source_id, coin, json.dumps(docs, ensure_ascii=False), fetched_at, expires_at),
            )
        return cursor.rowcount > 0

    def close(self) -> None:
        with self._lock:
            conn = self._conn
            if conn is None:
                return
            self._conn = None
            conn.close()

    def __del__(self) -> None:
        # Tests and short-lived CLI commands do not always own the backend long
        # enough to use a context manager.  Release SQLite deterministically
        # when the backend itself is collected instead of relying on the
        # interpreter to close the raw connection at shutdown.
        try:
            self.close()
        except Exception:
            pass


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

    def __init__(
        self,
        table_name: str | None = None,
        region: str | None = None,
        *,
        connect_timeout: float | None = None,
        read_timeout: float | None = None,
        max_attempts: int | None = None,
    ):
        self.table_name = table_name or os.getenv(
            "TRUSTFORGE_CACHE_TABLE", "trustforge-connector-cache"
        )
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self._table: Any = None  # lazy：建構本身不連 AWS
        # 三個 timeout/重試參數**預設一律 None**（沿用 boto3/botocore 內建
        # 預設值，等同修改前行為）——只有明確傳入才會限縮，避免影響既有呼叫端
        # （`fetch_scheduler.py`／`get_cache_backend()` 預設路徑）既有的容錯
        # 空間。保留原因（codex HIGH，Phase 3）：高流量、讀失敗可優雅降級的
        # 呼叫端應該能自帶嚴格 timeout，而非全域改變 DynamoDB client 行為
        # （不可因 AWS/憑證/DNS/表降級而長時間 hang）。Phase 3 曾在
        # `web.py` 加過一個這樣的呼叫端（首頁多幣總覽），但因該功能本身
        # （結果快照尚無寫入者、現在必然全空）不值得冒 ThreadPool 孤兒
        # 執行緒風險，已整個移除（見 `web.py::_render_home_page` 註解）；
        # 這三個參數留著給 Axis C（快照寫入者 + 正確讀路徑）用。
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._max_attempts = max_attempts

    def _get_table(self) -> Any:
        """lazy 取得 boto3 Table 物件；第一次呼叫才真的碰 AWS SDK。"""
        if self._table is None:
            import boto3  # 延遲匯入：建構/未啟用 dynamodb backend 時不需要憑證

            config = None
            if (
                self._connect_timeout is not None
                or self._read_timeout is not None
                or self._max_attempts is not None
            ):
                from botocore.config import Config

                kwargs: dict[str, Any] = {}
                if self._connect_timeout is not None:
                    kwargs["connect_timeout"] = self._connect_timeout
                if self._read_timeout is not None:
                    kwargs["read_timeout"] = self._read_timeout
                if self._max_attempts is not None:
                    kwargs["retries"] = {"max_attempts": self._max_attempts, "mode": "standard"}
                config = Config(**kwargs)

            self._table = boto3.resource(
                "dynamodb", region_name=self.region, config=config
            ).Table(self.table_name)
        return self._table

    @staticmethod
    def _split_key(key: str) -> tuple[str, str]:
        source_id, _, coin = key.partition(":")
        return source_id, coin

    def get(self, key: str, *, consistent_read: bool = False) -> dict[str, Any] | None:
        source_id, coin = self._split_key(key)
        resp = self._get_table().get_item(
            Key={"source_id": source_id, "coin": coin}, ConsistentRead=consistent_read,
        )
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

    def set_if_newer(
        self, key: str, docs: list[dict[str, Any]], fetched_at: float,
        ttl_seconds: float | None = None,
    ) -> bool:
        """用**條件式 `PutItem`**做原子 compare-and-set（完全比照
        `scheduler_log.py::DynamoDBSchedulerRunLog._update_latest_pointer`
        同一套慣例）：`ConditionExpression` 讓「這把 key 目前不存在，或其
        `fetched_at` 嚴格小於這次要寫入的值」與「覆寫」在 DynamoDB 伺服器端
        合成單一原子操作，取代「先 `GetItem` 比較、再無條件 `PutItem`」——
        後者在兩個重疊排程交錯時，較舊的一筆可能照著自己更早讀到的舊狀態
        把值蓋回去（見模組頂部 codex HIGH「歷史快照 out-of-order 覆蓋」
        說明）。`ConditionalCheckFailedException` 代表已有更新（或相同時間
        戳）的一筆贏得這場 race，視為「跳過」，不是錯誤，往上層一律不
        raise。"""
        from boto3.dynamodb.conditions import Attr
        from botocore.exceptions import ClientError

        source_id, coin = self._split_key(key)
        window = ttl_seconds if ttl_seconds is not None else DEFAULT_STALE_AFTER_FALLBACK_SECONDS
        fetched_at_dec = Decimal(str(fetched_at))
        item = {
            "source_id": source_id,
            "coin": coin,
            "docs_json": json.dumps(docs, ensure_ascii=False),
            "fetched_at": fetched_at_dec,
            "ttl": int(fetched_at + window),
        }
        condition = Attr("fetched_at").not_exists() | Attr("fetched_at").lt(fetched_at_dec)
        try:
            self._get_table().put_item(Item=item, ConditionExpression=condition)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise


def get_cache_backend() -> CacheBackend:
    """依 env `CACHE_BACKEND`（`dynamodb`|`sqlite`|`json`）選
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
    if backend == "sqlite":
        return SQLiteCacheBackend()
    if backend == "dynamodb":
        return DynamoDBCache()
    raise ValueError(f"unsupported CACHE_BACKEND: {backend!r}")


class CacheReadFailure(Exception):
    """`cache_get(..., strict=True)` 專用：primary **和** fallback（若有
    嘗試）讀取都失敗——不是「這個 key 沒資料」的正常 miss，是 cache 本身
    outage（DynamoDB 掛了/憑證壞了/本地磁碟也讀不了）。

    codex 複審 HIGH（根因修復）：預設（非 strict）`cache_get()` 一律把這
    種情況吞掉回 `None`，讓「outage」跟「miss」在呼叫端看起來一模一樣
    ——`/api/overview`／`/api/history`／`/api/status` 鮮度這類需要對外
    回報「依賴失敗→502」契約的呼叫端，用不到這個區分就會誤把 outage
    當成「沒資料」回 200，監控/使用者都看不出依賴真的掛了。"""


def cache_get(
    backend: CacheBackend,
    key: str,
    *,
    strict: bool = False,
    degradation_out: dict[str, bool] | None = None,
    skip_primary: bool = False,
) -> dict[str, Any] | None:
    """讀快取；`backend` 失敗（如 DynamoDB 缺憑證/表未建/網路問題）自動
    fallback 讀本地 `JsonCacheBackend`（比照 `ledger.append_run()` 的 fallback
    慣例）。

    ⚠️ 只在 **backend 本身丟例外**時才 fallback；backend 正常回應「這個 key
    沒有資料」（回傳 `None`）視為合法的 cache-miss，不觸發 fallback 去查
    另一個 backend（避免兩個 backend 資料不同步時，把「A 沒有」誤判成
    「應該去問 B」，語意模糊）。

    `strict`（預設 `False`，opt-in——**不傳這個參數的既有呼叫端行為逐字
    不變**）：`False` 時維持上述既有行為，primary+fallback 都失敗一樣吞
    例外回 `None`。`strict=True` 時，若 primary **和**（有嘗試的話）
    fallback 都讀取失敗，改成 `raise CacheReadFailure`，不再悄悄回
    `None`——讓呼叫端能分辨「outage（讀取真的失敗，該視為依賴不可用）」
    跟「miss（backend 正常運作，只是這個 key 沒資料，該視為合法的『沒有
    這筆資料』）」。純 miss（`backend.get()` 正常回傳 `None`，沒有丟例外）
    在 `strict=True`／`False` 兩種模式下行為完全一樣，一律回 `None`——
    `strict` 只影響「讀取真的失敗」這個分支，不影響「合法沒資料」。

    `degradation_out`（預設 `None`，opt-in，不傳不影響任何既有行為）：
    codex 複審 MEDIUM（觀測準確性）——讀取「有沒有靠 fallback 才成功」的
    訊號原本完全丟失，呼叫端（如 `/api/status`）沒辦法知道這次讀取是
    primary 親自答的，還是 primary 掛了、靠本地 `JsonCacheBackend` 頂著。
    傳一個共用的 `dict` 進來，這個函式會：primary 直接成功 →
    `degradation_out.setdefault("used_fallback", False)`（**不覆蓋**已經
    被前面呼叫標成 `True` 的值，讓呼叫端能對多次 `cache_get()` 呼叫做
    「只要曾經用過 fallback 就算 degraded」的 OR 聚合，比照
    `get_freshness_snapshot()` 逐 (source, coin) 迴圈呼叫的用法）；
    fallback 成功 → 無條件 `degradation_out["used_fallback"] = True`。

    `skip_primary`（預設 `False`，opt-in，不傳不影響任何既有行為）：codex
    複審 HIGH（circuit breaker，production 安全）——`get_freshness_snapshot()`
    這類逐 (source, coin) 迴圈呼叫 `cache_get()` 的呼叫端，一旦在同一次
    請求內已經觀察到 primary 讀取失敗一次，後續每一格繼續重新嘗試 primary
    只是在對一個已知掛掉的依賴（如 DynamoDB outage）疊加流量，且吃 SDK
    預設 timeout/重試會讓整支請求的延遲隨格數線性放大（可拖到多分鐘）。
    `skip_primary=True` 時**完全跳過**呼叫 `backend.get(key)`，直接讀
    fallback `JsonCacheBackend`（成功 → 視同 fallback 成功，
    `degradation_out["used_fallback"] = True`；失敗 → 視同 primary+fallback
    皆失敗，`strict=True` 時 raise `CacheReadFailure`）。若 `backend` 本身
    就是 `JsonCacheBackend`（沒有另一層可跳過去的 fallback），這個參數
    沒有意義，直接忽略、照常呼叫 `backend.get()`。
    """
    if skip_primary and not isinstance(backend, JsonCacheBackend):
        try:
            result = JsonCacheBackend().get(key)
            if degradation_out is not None:
                degradation_out["used_fallback"] = True
            return result
        except Exception as exc:
            print(
                f"[cache] WARNING: fallback JsonCacheBackend get 仍失敗"
                f"（circuit breaker 生效中，已跳過 primary）：{exc}",
                file=sys.stderr,
            )
            if strict:
                raise CacheReadFailure(
                    f"cache read failed (circuit breaker, fallback): {exc}"
                ) from exc
            return None

    try:
        result = backend.get(key)
        if degradation_out is not None:
            degradation_out.setdefault("used_fallback", False)
        return result
    except Exception as exc:
        primary_exc = exc
        print(f"[cache] WARNING: get 失敗（backend={type(backend).__name__}）：{exc}",
              file=sys.stderr)

    if isinstance(backend, JsonCacheBackend):
        # 同一顆 JsonCacheBackend 剛失敗，換個新實例打同路徑必再失敗，不重試
        if strict:
            raise CacheReadFailure(f"cache read failed: {primary_exc}") from primary_exc
        return None

    try:
        result = JsonCacheBackend().get(key)
        if degradation_out is not None:
            degradation_out["used_fallback"] = True
        return result
    except Exception as exc:
        print(f"[cache] WARNING: fallback JsonCacheBackend get 仍失敗：{exc}", file=sys.stderr)
        if strict:
            raise CacheReadFailure(
                f"cache read failed (primary+fallback): {primary_exc}; fallback: {exc}"
            ) from exc
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
    - `skipped`：`True` 表示這是 `cache_set_if_newer()` 的單調條件寫入判定
      「incoming 比既有值舊（或一樣新），不該覆寫」而**主動跳過**——`ok`
      仍為 `True`（這不是失敗，backend 正常運作，只是這次沒有真的寫入新
      內容），呼叫端不應把它算進失敗清單。一般 `cache_set()` 一律
      `skipped=False`（它沒有條件判斷，寫就是寫）。
    - `monotonic_blocked`：`cache_set_monotonic()` 專用——這次寫入中，因為
      「incoming doc 的 `ts` 比快取裡同 id 的 doc 舊」而被擋下、沒有回灌的
      doc 數（供應商回舊版 / 時光倒流）。`ok`/`skipped` 語意不變，這只是
      一筆觀測訊號，供排程 log 顯示「這輪有一批比快取還舊的資料被擋掉」。
    """

    ok: bool
    used_fallback: bool
    backend: str
    error: str | None = None
    skipped: bool = False
    monotonic_blocked: int = 0


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


def cache_set_if_newer(
    backend: CacheBackend, key: str, docs: list[dict[str, Any]], fetched_at: float,
    ttl_seconds: float | None = None,
    allow_json_fallback: bool | None = None,
) -> CacheWriteResult:
    """`cache_set()` 的**單調條件寫入**版本（codex HIGH，PR #59 review，
    task #26 追加修正）：只有這把 `key` 目前不存在、或既有 `fetched_at`
    嚴格小於 incoming `fetched_at` 時才真的覆寫，否則跳過（`ok=True,
    skipped=True`——不是失敗，只是這次的值比已存在的舊，見
    `CacheBackend.set_if_newer()` 與模組頂部 `TRUST_SNAPSHOT_HISTORY_SOURCE`
    附近的完整說明）。

    專供「按日歷史快照」這類**需要歷史正確性**（值只能被更新的覆寫、不能
    被更舊的值污染）的 key 使用；一般「latest 覆寫」語意（如
    `TRUST_SNAPSHOT_SOURCE`）仍應繼續用普通 `cache_set()`，那裡本來就是
    last-write-wins、沒有單調要求，不需要多付一次讀的成本。

    `allow_json_fallback` 語意與 `cache_set()` 完全一致（見該函式
    docstring）——**唯一差別**：`skipped` 這個結果只描述「primary/成功
    fallback 的那個 backend 自己判斷的單調結果」，不代表兩個 backend 的
    值彼此同步（本來就是各自獨立儲存，`cache_set()` 的既有限制）。
    """
    backend_name = type(backend).__name__
    try:
        wrote = backend.set_if_newer(key, docs, fetched_at, ttl_seconds=ttl_seconds)
        return CacheWriteResult(
            ok=True, used_fallback=False, backend=backend_name, error=None, skipped=not wrote,
        )
    except Exception as exc:
        err = str(exc)
        print(f"[cache] WARNING: set_if_newer 失敗（backend={backend_name}）：{exc}",
              file=sys.stderr)

    if isinstance(backend, JsonCacheBackend):
        # 同一顆 JsonCacheBackend 剛失敗，換個新實例打同路徑必再失敗，不重試
        return CacheWriteResult(ok=False, used_fallback=False, backend=backend_name, error=err)

    if not _json_fallback_enabled(allow_json_fallback):
        return CacheWriteResult(ok=False, used_fallback=False, backend=backend_name, error=err)

    try:
        wrote = JsonCacheBackend().set_if_newer(key, docs, fetched_at, ttl_seconds=ttl_seconds)
        return CacheWriteResult(
            ok=True, used_fallback=True, backend="JsonCacheBackend", error=err, skipped=not wrote,
        )
    except Exception as exc2:
        print(f"[cache] WARNING: fallback JsonCacheBackend set_if_newer 仍失敗：{exc2}",
              file=sys.stderr)
        return CacheWriteResult(
            ok=False, used_fallback=False, backend=backend_name,
            error=f"{err}; fallback JsonCacheBackend 也失敗：{exc2}",
        )


def _doc_signature(d: dict[str, Any]) -> tuple:
    """把一筆 doc dict 壓成可雜湊的簽章，供合併後「集合是否相等」比對（#56
    doc 層級單調合併用）。只取會影響「這篇內容本身是否變了」的欄位，不含
    會隨每次 fetch 變動的瞬時欄位（簽章本身已含 `ts`，更新時間戳視為變更）。"""
    return (
        str(d.get("id", "")),
        str(d.get("kind", "")),
        str(d.get("source", "")),
        str(d.get("text", "")),
        str(d.get("url", "")),
        float(d.get("ts", 0.0) or 0.0),
        json.dumps(d.get("meta") or {}, sort_keys=True, ensure_ascii=False),
    )


def _docsets_equal(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
    """兩份 doc 清單在「內容」上是否完全一致（忽略順序）。見 `_doc_signature`。"""
    if len(a) != len(b):
        return False
    return {_doc_signature(d) for d in a} == {_doc_signature(d) for d in b}


def merge_docs_monotonic(
    existing_docs: list[dict[str, Any]],
    incoming_docs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """#56 跨快取單調性——doc 層級合併：依 `id` 把 incoming 與既有快取合併，
    個別 doc 的 `ts`（上游發佈時間戳）**只允許變新、不允許變舊**。

    語意（「新 doc ts 不得早於既有快取 doc」）：
      - incoming 的 id 在快取裡不存在 → 收錄（全新內容）。
      - incoming 的 id 已存在，且 `incoming.ts > cached.ts` → 用 incoming
        （更新，內容本就該刷新）。
      - incoming 的 id 已存在，且 `incoming.ts <= cached.ts` → **保留既有較
        新值，不回灌**（供應商回舊版 / 時光倒流被擋下）。這正是 #56 要防的
        根因：上游偶爾會回傳比我們已經存著的還舊的版本（舊 RSS 項目重新
        排到前面、API 回源到舊快照等），若無條件整批覆寫，會把「已經拿到
        的新資料」倒退成舊資料。

    `ts` 相等時取 incoming（內容可能有非時間戳層面的微修正，視為同代刷新，
    不退回既有；與「不得早於」的語意一致——相等不算「早於」）。

    回傳 `(merged_docs, blocked_count)`：`merged_docs` 依「先既有、後新 id」
    的順序拼回（保留既有 doc 的相對位置，新增 id 接在後面）；`blocked_count`
    是被擋下、沒回灌的舊版 doc 數。
    """
    if not existing_docs:
        # 無既有快取：全部 incoming 皆為新內容
        return list(incoming_docs), 0

    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for d in existing_docs:
        did = str(d.get("id", ""))
        if did not in by_id:
            by_id[did] = d
            order.append(did)

    blocked = 0
    for d in incoming_docs:
        did = str(d.get("id", ""))
        cur = by_id.get(did)
        if cur is None:
            by_id[did] = d
            order.append(did)
        else:
            cur_ts = float(cur.get("ts", 0.0) or 0.0)
            new_ts = float(d.get("ts", 0.0) or 0.0)
            if new_ts < cur_ts:
                # 供應商回舊版 doc：新 doc 時間戳比快取舊，禁止回灌，保留既有
                blocked += 1
                continue
            by_id[did] = d  # 更新或相等 → 取 incoming

    merged = [by_id[i] for i in order]
    return merged, blocked


def cache_set_monotonic(
    backend: CacheBackend, key: str, docs: list[dict[str, Any]], fetched_at: float,
    ttl_seconds: float | None = None,
    allow_json_fallback: bool | None = None,
) -> CacheWriteResult:
    """#56 跨快取單調性通用修——`cache_set()` 的 doc 層級單調版本。

    寫入前先讀既有快取、用 `merge_docs_monotonic()` 合併，**個別 doc 的 `ts`
    只允許變新、不允許變舊**：供應商若回傳比快取還舊的版本，該 doc 不被
    回灌、保留既有較新值（構造場景「供應商回舊版 doc」→ 被擋下不回灌）。

    ⚠️ 與 `cache_set_if_newer()`（只比整把 key 的 `fetched_at`，供歷史快照
    用）的不同：本函式是 **doc 層級**的單調保護——genuine 每一篇內容的
    上游時間戳都不回灌，**覆蓋所有拿上游時間戳的來源**（news/social/onchain/
    regulatory/coingecko），不限特定 key 類型。這正是 #56「通用修」的範圍：
    把它接到 `scripts/fetch_scheduler.py::run_once()` 的每個寫入點，所有來源
    的每篇 doc 都自動獲得「時光不倒流」保護。

    行為：
      - 合併後集合與既有快取完全一致（incoming 全被擋、無新 id）→ 視同無
        變化，`skipped=True`，**不**更新 `fetched_at`（避免「這次 fetch 拿
        到 stale 資料」去延長新鮮窗——誠實反映「這輪沒有新東西」）。
      - 否則普通 `cache_set()` 寫入合併結果（fallback / 失敗語意與
        `cache_set()` 完全一致），`monotonic_blocked` 標示被擋下的舊版 doc
        數供觀測。
    """
    existing = cache_get(backend, key)
    existing_docs = (existing or {}).get("docs") or [] if existing else []
    merged, blocked = merge_docs_monotonic(existing_docs, docs)
    if blocked > 0:
        print(
            f"[cache] monotonic: key={key!r} 擋下 {blocked} 篇比快取舊的 doc"
            f"（供應商回舊版 / 時光倒流，已保留較新值）",
            file=sys.stderr,
        )
    if existing_docs and blocked == len(docs) and _docsets_equal(existing_docs, merged):
        # 合併結果與既有快取逐篇一致：這輪沒有新內容（只回收到舊版 doc），
        # 跳過寫入、不刷新 fetched_at（誠實標示「無新資料」）。
        return CacheWriteResult(
            ok=True, used_fallback=False, backend=type(backend).__name__,
            error=None, skipped=True, monotonic_blocked=blocked,
        )
    result = cache_set(
        backend, key, merged, fetched_at, ttl_seconds=ttl_seconds,
        allow_json_fallback=allow_json_fallback,
    )
    return CacheWriteResult(
        ok=result.ok, used_fallback=result.used_fallback, backend=result.backend,
        error=result.error, skipped=result.skipped, monotonic_blocked=blocked,
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
        self.enabled = getattr(wrapped, "enabled", True)
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


def get_freshness_snapshot(
    backend: CacheBackend | None = None,
    source_names: Iterable[str] | None = None,
    coins: Iterable[str] | None = None,
    *,
    strict: bool = False,
    degradation_out: dict[str, bool] | None = None,
    circuit_breaker: bool = False,
) -> list[dict[str, Any]]:
    """`/status` 資料鮮度矩陣用的唯讀 helper：逐 (source, coin) 讀 cache
    `fetched_at`，比對 `stale_after_for(refresh_interval)` 標「新鮮／過期／缺」。

    ⚠️ 純讀：只呼叫既有 `cache_get()`（本身已具備 backend 失敗 fallback 讀
    `JsonCacheBackend` 的語意），**不寫入、不改任何既有快取/寫入邏輯**，也
    **不會**觸發任何真連接器 API 呼叫——跟 `CachedSource.fetch()` 一樣，資料
    只可能來自 `scripts/fetch_scheduler.py` 排程既有寫入的內容，credit-safe。

    `source_names`/`coins` 預設分別為 `DEFAULT_REFRESH_INTERVAL_SECONDS` 的所有
    已知來源、`COIN_POOL` 全部幣種（供測試以較小組合覆寫，避免每個測試都要
    跑滿全量組合）。

    回傳 `[{"source", "coin", "status", "fetched_at", "age_seconds"}, ...]`：
      - `status="missing"`：`cache_get()` 回 `None`（從未成功寫入，或讀取失敗
        已降級）——`fetched_at`/`age_seconds` 皆為 `None`。
      - `status="stale"`：`age_seconds > stale_after_for(refresh_interval)`。
      - `status="fresh"`：`age_seconds <= stale_after_for(refresh_interval)`。
    來源的 refresh 間隔查 `DEFAULT_REFRESH_INTERVAL_SECONDS`（未知來源名 fallback
    `DEFAULT_REFRESH_INTERVAL_FALLBACK_SECONDS`），跟 `CachedSource`/
    `scripts/fetch_scheduler.py` 共用同一份數字，不另外手填第二份。

    `strict`（預設 `False`，opt-in——**SSR `/status` 頁呼叫端不傳這個參數，
    行為逐字不變**，這裡描述的只是 `True` 時的差異）：直接透傳給底層
    `cache_get(..., strict=...)`——`True` 時任一 (source, coin) 讀取「真的
    失敗」（不是單純沒快照）會 `raise CacheReadFailure`，讓呼叫端（如
    `/api/status`）能區分「cache outage 該 502」與「這個來源這個幣沒資料，
    正常標 missing」（見 `cache_get()` docstring、codex 複審 HIGH 根因
    修復）。

    `degradation_out`（預設 `None`，opt-in，不傳不影響任何既有行為）：
    codex 複審 MEDIUM（觀測準確性）——直接透傳給每一次內部 `cache_get()`
    呼叫，讓呼叫端能知道這次鮮度矩陣是不是**曾經**靠 fallback
    `JsonCacheBackend` 頂著才讀到（見 `cache_get()` docstring 的 OR 聚合
    語意：只要矩陣裡任一格用過 fallback，整體就該視為 primary degraded，
    即使其餘格都是 primary 親自答的）。

    `circuit_breaker`（預設 `False`，opt-in，不傳不影響任何既有行為——
    **SSR `/status` 頁呼叫端不傳這個參數，逐字不變**）：codex 複審 HIGH
    （production 安全）——這個矩陣要逐 (source, coin) 迴圈呼叫
    `cache_get()`（`DEFAULT_REFRESH_INTERVAL_SECONDS` × `COIN_POOL` 全量
    組合約 115 格），若 primary（如 DynamoDB）整段 outage，逐格都重新
    嘗試 primary 一次，會：(1) 對已經掛掉的依賴疊加 ~115 倍流量，可能讓
    outage 更嚴重；(2) 就算每次呼叫都會失敗，SDK 預設 timeout/重試疊加
    起來仍可能讓整支請求拖到多分鐘，「degraded 仍回 200」的承諾在實務上
    變成「多分鐘 hang」。`circuit_breaker=True` 時：**同一次呼叫內**一旦
    偵測到任一格用了 fallback（primary 那一下失敗了），後續所有格子改用
    `cache_get(..., skip_primary=True)`——直接跳過 primary、只讀本地
    `JsonCacheBackend`，不再重試已知掛掉的依賴。這個偵測用的正是
    `degradation_out` 訊號；若呼叫端沒傳 `degradation_out`，內部會借用一
    個 local dict 供 circuit breaker 自己追蹤，不影響對外回傳值。
    """
    # 延遲匯入避免模組載入順序造成循環匯入（schema.py 不依賴 ingestion，
    # 理論上不會循環，但沿用專案內其他處延遲匯入的保守慣例）。
    from ..schema import COIN_POOL

    resolved_backend = backend if backend is not None else get_cache_backend()
    names = list(source_names) if source_names is not None else sorted(DEFAULT_REFRESH_INTERVAL_SECONDS)
    coin_list = list(coins) if coins is not None else list(COIN_POOL)

    # circuit breaker 判斷「primary 是否已知失敗」的訊號來源：若呼叫端有給
    # `degradation_out` 就直接共用同一個 dict（沿用既有 OR 聚合契約，
    # 對外回傳值不受影響）；沒給的話（理論上不該發生在真正需要 circuit
    # breaker 的呼叫端，但防呆）借用一個純內部 local dict，只供本函式判斷
    # 是否該跳過 primary 用，不對外流出。`circuit_breaker=False` 時這個
    # dict 完全不使用（見下面 `tracking` 賦值），維持既有行為零改動。
    tracking = degradation_out if degradation_out is not None else ({} if circuit_breaker else None)
    primary_broken = False

    now = time.time()
    snapshot: list[dict[str, Any]] = []
    for name in names:
        interval = DEFAULT_REFRESH_INTERVAL_SECONDS.get(name, DEFAULT_REFRESH_INTERVAL_FALLBACK_SECONDS)
        stale_after = stale_after_for(interval)
        for coin in coin_list:
            entry = cache_get(
                resolved_backend, cache_key(name, coin),
                strict=strict, degradation_out=tracking,
                skip_primary=circuit_breaker and primary_broken,
            )
            if circuit_breaker and not primary_broken and tracking is not None and tracking.get("used_fallback"):
                primary_broken = True
            if entry is None:
                snapshot.append({
                    "source": name, "coin": coin, "status": "missing",
                    "fetched_at": None, "age_seconds": None,
                })
                continue
            fetched_at = float(entry.get("fetched_at", 0.0) or 0.0)
            age = now - fetched_at
            snapshot.append({
                "source": name, "coin": coin,
                "status": "fresh" if age <= stale_after else "stale",
                "fetched_at": fetched_at, "age_seconds": age,
            })
    return snapshot
