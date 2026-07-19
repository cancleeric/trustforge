#!/usr/bin/env python3
"""階段2：排程 fetcher — 全專案唯一會在正常營運中打真連接器 API 的地方。

背景：`trustforge.ingestion.base.collect()` 線上模式已改成一律讀
`trustforge.ingestion.cache.CachedSource`（cache-only，見該模組docstring）。
真正打 news/social/onchain/regulatory 這些真連接器 API，改由「本腳本」在
排程（cron / systemd timer）觸發下定時執行，寫入快取；產品每個 request
不再直接打真 API，避免被 rate-limit 甚至封鎖。

用法：
    # 列出所有已知來源名稱
    python3 scripts/fetch_scheduler.py --list-sources

    # 對所有已知來源 x COIN_POOL（BTC/ETH/SOL/BNB/XRP）各跑一次
    # （尊重各來源 DEFAULT_REFRESH_INTERVAL_SECONDS 新鮮度守門：距上次成功
    #  快取未達 refresh 間隔就跳過，避免 cron 誤觸發或手動重跑造成重複打真
    #  API。⚠️ refresh 間隔 << CachedSource 硬過期時限，兩者刻意分開，見
    #  cache.py 模組頂部「codex HIGH-1」說明，不要改到讓兩者相等）
    python3 scripts/fetch_scheduler.py

    # 只跑指定來源 / 幣別（可重複 --source / --coin）
    python3 scripts/fetch_scheduler.py --source coindesk --source reddit-bitcoin
    python3 scripts/fetch_scheduler.py --coin BTC --coin ETH

    # 強制略過新鮮度守門（節制使用，避免 429——尤其 reddit）
    python3 scripts/fetch_scheduler.py --source reddit-cryptocurrency --force

    # 只列出這次會呼叫哪些 (來源, 幣別)，不真的打 API / 不寫快取
    python3 scripts/fetch_scheduler.py --dry-run

    # 切換 cache backend（預設沿用 cache.py 的 CACHE_BACKEND env，
    # dynamodb|sqlite|json；
    # 預設 dynamodb）。primary backend 寫入失敗時，預設**不會**自動 fallback
    # 寫本地 JSON（避免假成功，見 codex HIGH-2）；exit code 非零代表有目標
    # 沒有真的持久化，cron/監控應據此告警。dev/CI 沒有真 AWS、想要一個真正
    # 能用的本地快取時，才明確開 opt-in：
    CACHE_BACKEND=sqlite python3 scripts/fetch_scheduler.py
    # 或維持 CACHE_BACKEND=dynamodb，但允許失敗時 fallback 寫本地 JSON：
    TRUSTFORGE_CACHE_JSON_FALLBACK=1 python3 scripts/fetch_scheduler.py

    # Axis C #1（task #23）：多幣信任快照寫入者——獨立分支，跟上面「打真
    # 連接器 API」的預設模式完全分開、不共用同一條 cron line。對 COIN_POOL
    # 5 幣各跑 1 次 real-off pipeline.run(data_mode="live", llm_mode="off")
    # （純讀既有 cache 運算，$0，不打真連接器、不打 Bedrock），寫入
    # __trust_snapshot__:{coin} 快照 + __trust_overview_html__ 總覽 blob，
    # 建議 cron 每 15 分鐘一次（見 SNAPSHOT_REFRESH_INTERVAL_SECONDS）：
    python3 scripts/fetch_scheduler.py --snapshot
    # 驗證這個分支會跑哪些幣、不真的呼叫 pipeline.run()：
    python3 scripts/fetch_scheduler.py --snapshot --dry-run

部署方式（EC2 cron 或 systemd timer，見 deploy/README.md「排程 fetcher」章節
詳細教學）：各來源 rate limit 不同，用各自間隔的 cron line（或每 5-15 分鐘
跑一次本腳本「全部來源」、靠內建的新鮮度守門自然分散頻率，兩種都可以，
後者更省心）。

Exit code：`0` 全部成功（或本來就沒有目標要跑）；`1` 表示至少一個目標真呼叫
成功、但 cache backend 寫入失敗（沒有真的持久化）——cron/監控應對非零 exit
告警，不要只看程式有沒有當掉。

⚠️ 節制：本腳本才是「唯一打真 API」的地方；本 PR 手動驗證時務必守
「每來源 1-2 次」，reddit 尤其別狂打（cloud IP 本來就容易 403/429）。
"""
from __future__ import annotations

import argparse
import concurrent.futures
import html
import math
import os
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from trustforge.ingestion.base import (  # noqa: E402
    Document,
    Source,
    get_source_enabled,
    sync_source_enabled_from_admin,
)
from trustforge.ingestion.cache import (  # noqa: E402
    COIN_AGNOSTIC_SOURCES,
    COIN_KEYED_BATCH_SOURCES,
    DEFAULT_REFRESH_INTERVAL_FALLBACK_SECONDS,
    DEFAULT_REFRESH_INTERVAL_SECONDS,
    TRUST_OVERVIEW_COIN,
    TRUST_OVERVIEW_SOURCE,
    TRUST_SNAPSHOT_FRESH_WINDOW_SECONDS,
    TRUST_SNAPSHOT_HISTORY_TTL_SECONDS,
    TRUST_SNAPSHOT_REFRESH_INTERVAL_SECONDS,
    TRUST_SNAPSHOT_SOURCE,
    CacheBackend,
    DynamoDBCache,
    JsonCacheBackend,
    SQLiteCacheBackend,
    cache_get,
    cache_key,
    cache_set,
    cache_set_if_newer,
    cache_set_monotonic,
    doc_to_dict,
    get_cache_backend,
    snapshot_history_date,
    stale_after_for,
    trust_snapshot_history_key,
)
from trustforge.ingestion.coingecko import build_coingecko_sources  # noqa: E402
from trustforge.ingestion.news import build_news_sources  # noqa: E402
from trustforge.ingestion.onchain import build_onchain_sources  # noqa: E402
from trustforge.ingestion.regulatory import build_regulatory_sources  # noqa: E402
from trustforge.ingestion.social import build_social_sources  # noqa: E402
from trustforge.ingestion.hoyabit import build_hoyabit_sources, log_hoyabit_startup_status  # noqa: E402
from trustforge.brand_logos import coin_logo_html  # noqa: E402
from trustforge.ledger import DynamoDBLedger, JsonlLedger, get_ledger  # noqa: E402
from trustforge.schema import COIN_POOL, QuestionType  # noqa: E402
from trustforge.scheduler_log import append_scheduler_run  # noqa: E402
from trustforge.replay import capture_source_snapshot  # noqa: E402
from trustforge.source_archive import SourceEventArchive  # noqa: E402
from trustforge.data_quality import validate_documents  # noqa: E402


def build_registry() -> dict[str, Source]:
    """建立「來源名稱 → 真 Source 實例」對照表。純建構，不打任何網路（各
    `Source.__init__` 都不連線，見各連接器實作）。"""
    sources: list[Source] = (
        build_news_sources()
        + build_onchain_sources()
        + build_social_sources()
        + build_regulatory_sources()
        + build_coingecko_sources()
        + build_hoyabit_sources()
    )
    return {s.name: s for s in sources}


def _is_fresh(backend: CacheBackend, name: str, coin: str, interval: float) -> bool:
    """距上次成功寫入快取的時間是否仍在 `interval` 秒內。無快取（從未成功
    寫過）一律視為「不新鮮」，需要真的打一次。"""
    entry = cache_get(backend, cache_key(name, coin))
    if entry is None:
        return False
    fetched_at = float(entry.get("fetched_at", 0.0) or 0.0)
    return (time.time() - fetched_at) <= interval


def _warn_if_fallback_used(label: str, result) -> None:  # noqa: ANN001 — CacheWriteResult，避免循環型別匯入噪音
    if result.used_fallback:
        print(
            f"[fetch_scheduler] WARNING: {label}: primary cache backend 寫入失敗"
            f"（{result.error}），已 fallback 寫入本地 JSON——production 環境應視為"
            "異常（其他 runtime 看不到這份資料），請確認 DynamoDB 憑證/表狀態",
            file=sys.stderr,
        )


# 生產事故修復（coingecko-price 429 風暴，見 run_once() 對 COIN_KEYED_BATCH_
# SOURCES 分支說明 + `--stagger` CLI help）：CoinGecko 逐幣來源（coins/{id}
# 詳情，目前是 coingecko-sentiment/coingecko-dev）keyless 額度只有
# 5-15 req/min，即使一輪只需 5 次真呼叫（每幣 1 次、sentiment/dev 共用
# 快取），5 次呼叫若在極短時間內（如既有預設 --stagger=1 秒）密集發出，
# 瞬間節奏仍可能撞上較保守的滑動窗口限流。這裡對 CoinGecko 逐幣來源設一個
# 額外的呼叫間隔下限，取「使用者傳入的 --stagger」與「這個下限」兩者較大值，
# 其餘來源（reddit 等）不受影響、沿用使用者傳入值。
#
# 後續加固（codex HIGH #1，安全/健康雙審）：這裡的 6 秒只是「同一個 Source
# 內部」逐幣呼叫的間隔，不同 Source（price 與 sentiment/dev）之間完全不
# 共享——排程順序若先跑完 5 幣 dev（0/6/12/18/24s）再跑 1 次 price
# （COIN_KEYED_BATCH，~30s），30 秒內仍發生 6 次真請求（12 次/分鐘），
# keyless 5-15 req/min 的保守下限依然可能撞到。真正的修法已下放到
# `trustforge.ingestion.coingecko._fetch_url` 內部：那裡對「整個 CoinGecko
# host」維護一個共享節流器（不分 Source/端點，任兩次真請求至少間隔 12 秒
# keyless / 2 秒有 key），才是消除 429 的權威保護層。這裡的 6 秒 stagger
# 保留下來只是同一來源內部逐幣呼叫的額外邊際緩衝（belt-and-braces），跟
# coingecko.py 內建的節流器疊加，不衝突，也不是必要條件——即使拿掉這 6 秒
# stagger，coingecko.py 內建的節流器仍會獨立擋住實際的真請求密集發送。
_COINGECKO_STAGGER_FLOOR_SECONDS = 6.0


def _effective_stagger(name: str, stagger: float) -> float:
    """`name` 是 CoinGecko 逐幣來源時，回傳 `max(stagger, 下限)`；其餘來源
    原樣回傳 `stagger`（沿用呼叫端 `--stagger` 設定，不受影響）。"""
    if name.startswith("coingecko-"):
        return max(stagger, _COINGECKO_STAGGER_FLOOR_SECONDS)
    return stagger


def _is_http_429(exc: Exception) -> bool:
    """Return whether a connector failure is an explicit provider rate limit."""
    return isinstance(exc, HTTPError) and exc.code == 429


def run_once(
    source_names: list[str] | None,
    coins: list[str],
    backend: CacheBackend,
    force: bool,
    interval_overrides: dict[str, float],
    stagger: float,
    dry_run: bool,
    archive: SourceEventArchive | None = None,
    scheduler_run_id: str | None = None,
) -> tuple[list[tuple[str, int]], list[str]]:
    """對指定來源 x 幣別各跑一次「新鮮度守門 → 真呼叫 → 寫快取」。

    coin-agnostic 來源（`COIN_AGNOSTIC_SOURCES`，如 FNG/SEC，內容不依 coin
    篩選）只真呼叫一次，把同一份結果廣播寫入每個目標幣別的 cache key，
    避免對它們重複打 `len(coins)` 次浪費額度。

    coin-keyed-batch 來源（`COIN_KEYED_BATCH_SOURCES`，目前是
    `coingecko-price`：一次真呼叫的回應本身就涵蓋全部目標幣，且已用
    `Document.meta["coin"]` 明確標示各自歸屬）同樣只真呼叫一次，但**不**
    廣播同一份完整結果——而是依每筆 Document 的 `meta["coin"]` 分流，只把
    屬於該幣的 Document 寫進該幣自己的 cache key（生產事故修復：舊版此來源
    未歸類進任何 batch 集合，落入下面逐幣迴圈被呼叫 `len(coins)` 次；
    `_get_price_data()` 的 process 級記憶體快取雖然讓「成功」時只發生 1 次
    真 HTTP 呼叫，但**呼叫失敗時完全沒有這層保護**——第一幣觸發真呼叫若
    429，記憶體快取仍是 `None`，下一幣又會再觸發一次真呼叫，5 幣依序把
    同一個已經在限流的端點又打了 5 次，正是生產 log 見到
    `coingecko-price[BTC/ETH/SOL/BNB/XRP]` 各自 429 的根因。歸類進
    `COIN_KEYED_BATCH_SOURCES` 後排程器本身只呼叫 `source.fetch()` 一次，
    不論成功失敗都不會重試，徹底消除這個放大效應）。

    單一 (來源, 幣別) 真呼叫失敗（逾時/429/憑證錯/上游故障）只印警告並跳過，
    不中斷整批排程——其他來源/幣別照常繼續（呼應 `base.collect()` 對真連接器
    失敗一貫的優雅降級精神）；但**同時會計入回傳的 `failures`**（codex
    HIGH-1）：真呼叫本身失敗不能被靜默吞掉，否則若連續多輪全部來源都這樣
    失敗，`main()` 仍會回 exit 0、印「完成」，cron/監控看不到任何異常，直到
    cache 硬過期、產品端開始真的斷資料才會被發現。

    真呼叫成功、但寫入 cache backend 失敗（如 DynamoDB 憑證/表故障）同樣**視為
    真失敗**（codex HIGH-2）：一併計入回傳的 `failures`，供 `main()` 決定 exit
    非零，讓 cron/監控看得到——不能因為「至少打到真 API 了」就當這次排程算
    成功。

    coin-agnostic 廣播的新鮮度守門會檢查**所有**目標幣別的 cache key
    （codex MEDIUM-2），不是只看第一個幣：只要任一目標幣缺資料/已過期，就
    視為需要重新真呼叫——反正 coin-agnostic 一次真呼叫本來就會廣播寫入全部
    目標幣，順便就把上一輪部分廣播失敗漏掉的幣補齊，不用等滿一個 refresh
    interval。

    `interval_overrides` 用於新鮮度守門（多久沒刷新就該重打），與 cache
    backend 的硬過期時限（`stale_after_for()` 換算，寫入 DynamoDB 原生 `ttl`
    屬性用）分開計算，見 `cache.py` 模組頂部「codex HIGH-1」說明。

    回傳 `(results, failures)`：
      - `results`：`[(標籤, 寫入文件數), ...]`，只包含「真呼叫 + cache 寫入
        都成功」的目標。
      - `failures`：cache 寫入失敗（或廣播時任一幣別寫入失敗）的標籤清單，
        格式同上（`"{name}"` 或 `"{name}:{coin}"`）。
    """
    registry = build_registry()
    targets = source_names if source_names else sorted(registry)
    results: list[tuple[str, int]] = []
    failures: list[str] = []
    cycle_id = scheduler_run_id or f"scheduler-{uuid.uuid4()}"
    if not dry_run and archive is None:
        archive = SourceEventArchive()

    def archive_fetch(
        source: Source, coin: str, docs: list[Document], now: float,
        stale_after: float, fetch_duration_ms: float,
    ) -> bool:
        """Persist Bronze truth before the mutable latest-value cache projection."""
        assert archive is not None
        archive.append_fetch(
            source_id=source.name,
            source_kind=source.kind,
            coin=coin,
            documents=docs,
            fetched_at=now,
            expires_at=now + stale_after,
            fetch_run_id=f"fetch-{uuid.uuid4()}",
            scheduler_run_id=cycle_id,
            quality_state="accepted" if docs else "empty",
            fetch_duration_ms=fetch_duration_ms,
        )
        return True

    def quality_gate(source: Source, coin: str, docs: list[Document], now: float) -> list[Document]:
        """Quarantine invalid records before Bronze and latest cache projection."""
        assert archive is not None
        accepted, quarantined = validate_documents(docs, now=now)
        for item in quarantined:
            archive.append_quarantine(
                source_id=source.name, coin=coin, fetched_at=now,
                document=item.document, reason_codes=item.reason_codes,
                scheduler_run_id=cycle_id,
            )
        if quarantined:
            print(
                f"[fetch_scheduler] {source.name}[{coin or 'GLOBAL'}]: "
                f"{len(quarantined)} 筆未通過品質閘，已隔離",
                file=sys.stderr,
            )
        return accepted

    for name in targets:
        source = registry.get(name)
        if source is None:
            print(f"[fetch_scheduler] 未知來源：{name!r}（略過；"
                  f"可用：{sorted(registry)}）", file=sys.stderr)
            continue
        if not getattr(source, "enabled", True) or not get_source_enabled(name):
            print(f"[fetch_scheduler] {name}: source disabled，略過")
            continue
        refresh_interval = interval_overrides.get(
            name, DEFAULT_REFRESH_INTERVAL_SECONDS.get(name, DEFAULT_REFRESH_INTERVAL_FALLBACK_SECONDS)
        )
        stale_after = stale_after_for(refresh_interval)

        if name in COIN_AGNOSTIC_SOURCES:
            # codex MEDIUM-2：廣播來源的「新鮮度」不能只看 coins[0]——
            # 若上一輪只有部分幣別廣播寫入成功（例如 cache_set 對某幣失敗），
            # 只查第一個幣會誤判整源新鮮而整批跳過，讓缺的幣要等滿一個
            # refresh interval 才補得回來。這裡改成任一目標幣缺/不新鮮就
            # 視為需要重新真呼叫（反正 coin-agnostic 只呼叫一次、廣播寫入
            # 全部目標幣，本來就會順便把缺的幣補齊）。
            if not force and all(
                _is_fresh(backend, name, c, refresh_interval) for c in coins
            ):
                print(f"[fetch_scheduler] {name}: 未達 refresh 間隔（{refresh_interval:.0f}s），略過")
                continue
            if dry_run:
                print(f"[fetch_scheduler] (dry-run) {name}: 會呼叫 1 次，"
                      f"廣播寫入 {len(coins)} 個幣別 key")
                continue
            try:
                fetch_started = time.perf_counter()
                docs = source.fetch("", coin="")
                fetch_duration_ms = (time.perf_counter() - fetch_started) * 1000.0
            except Exception as exc:  # noqa: BLE001 — 排程任務單點失敗不中斷整批，
                # 但仍要計入 failures（codex HIGH-1）：真呼叫失敗（逾時/429/
                # 憑證錯/上游故障）不能只印警告就當沒事——若全部來源都這樣失敗，
                # main() 必須非零退出，不能讓 cron/監控誤判成功。
                print(f"[fetch_scheduler] {name}: 真呼叫失敗，略過（{exc}）", file=sys.stderr)
                failures.append(name)
                continue
            payload = [doc_to_dict(d) for d in docs]
            now = time.time()
            try:
                original_count = len(docs)
                docs = quality_gate(source, "", docs, now)
                if original_count and not docs:
                    raise ValueError("all fetched documents failed quality gates")
                payload = [doc_to_dict(d) for d in docs]
                archive_fetch(source, "", docs, now, stale_after, fetch_duration_ms)
            except Exception as exc:  # noqa: BLE001 — Bronze 失敗時不可更新 latest projection
                print(f"[fetch_scheduler] {name}: source_events 封存失敗（{exc}）", file=sys.stderr)
                failures.append(name)
                continue
            broadcast_failed: list[str] = []
            for c in coins:
                result = cache_set_monotonic(
                    backend, cache_key(name, c), payload, fetched_at=now, ttl_seconds=stale_after
                )
                if not result.ok:
                    broadcast_failed.append(c)
                    print(f"[fetch_scheduler] {name}[{c}]: cache 寫入失敗"
                          f"（backend={result.backend}）：{result.error}", file=sys.stderr)
                else:
                    _warn_if_fallback_used(f"{name}[{c}]", result)
            if broadcast_failed:
                print(f"[fetch_scheduler] {name}: 廣播寫入失敗（幣別：{broadcast_failed}）",
                      file=sys.stderr)
                failures.append(name)
            else:
                print(f"[fetch_scheduler] {name}: 1 次真呼叫，{len(docs)} 筆文件，"
                      f"廣播寫入 {len(coins)} 個幣別 key")
                results.append((name, len(docs)))
            continue

        if name in COIN_KEYED_BATCH_SOURCES:
            # 新鮮度守門邏輯同 coin-agnostic 分支（codex MEDIUM-2 同款考量）：
            # 任一目標幣缺資料/已過期就視為需要重新真呼叫——反正一次真呼叫
            # 本來就涵蓋全部目標幣，順便把上一輪部分分流寫入失敗漏掉的幣補齊。
            if not force and all(
                _is_fresh(backend, name, c, refresh_interval) for c in coins
            ):
                print(f"[fetch_scheduler] {name}: 未達 refresh 間隔（{refresh_interval:.0f}s），略過")
                continue
            if dry_run:
                print(f"[fetch_scheduler] (dry-run) {name}: 會呼叫 1 次，"
                      f"依 meta['coin'] 分流寫入 {len(coins)} 個幣別 key")
                continue
            try:
                fetch_started = time.perf_counter()
                docs = source.fetch("", coin="")
                fetch_duration_ms = (time.perf_counter() - fetch_started) * 1000.0
            except Exception as exc:  # noqa: BLE001 — 理由同 coin-agnostic 分支（codex HIGH-1）：
                # 只呼叫一次，失敗也只算一次失敗，不會像舊版逐幣迴圈那樣對同一個
                # 已限流的端點重複觸發（見本函式 docstring「生產事故修復」說明）。
                print(f"[fetch_scheduler] {name}: 真呼叫失敗，略過（{exc}）", file=sys.stderr)
                failures.append(name)
                continue
            now = time.time()
            try:
                original_count = len(docs)
                docs = quality_gate(source, "", docs, now)
                if original_count and not docs:
                    raise ValueError("all fetched documents failed quality gates")
                archive_fetch(source, "", docs, now, stale_after, fetch_duration_ms)
            except Exception as exc:  # noqa: BLE001
                print(f"[fetch_scheduler] {name}: source_events 封存失敗（{exc}）", file=sys.stderr)
                failures.append(name)
                continue
            # 依每筆 Document 自帶的 meta["coin"] 分流（不是廣播同一份完整
            # 結果）：非白名單/缺 coin 標記的文件直接捨棄，不落地進任何一個
            # cache key（正常情況下不該發生——來源實作保證回傳的每筆都帶
            # 合法 meta["coin"]，這裡只是防禦性寫法，避免格式意外飄移時把
            # 未知幣別的資料誤塞進某個幣的 cache）。
            docs_by_coin: dict[str, list[Document]] = {c: [] for c in coins}
            for d in docs:
                doc_coin = str(d.meta.get("coin", "")).upper()
                if doc_coin in docs_by_coin:
                    docs_by_coin[doc_coin].append(d)
            broadcast_failed = []
            total_docs = 0
            for c in coins:
                payload = [doc_to_dict(d) for d in docs_by_coin[c]]
                result = cache_set_monotonic(
                    backend, cache_key(name, c), payload, fetched_at=now, ttl_seconds=stale_after
                )
                if not result.ok:
                    broadcast_failed.append(c)
                    print(f"[fetch_scheduler] {name}[{c}]: cache 寫入失敗"
                          f"（backend={result.backend}）：{result.error}", file=sys.stderr)
                else:
                    _warn_if_fallback_used(f"{name}[{c}]", result)
                    total_docs += len(payload)
            if broadcast_failed:
                print(f"[fetch_scheduler] {name}: 分流寫入失敗（幣別：{broadcast_failed}）",
                      file=sys.stderr)
                failures.append(name)
            else:
                print(f"[fetch_scheduler] {name}: 1 次真呼叫，{len(docs)} 筆文件，"
                      f"依 meta['coin'] 分流寫入 {len(coins)} 個幣別 key")
                results.append((name, total_docs))
            continue

        for coin_index, c in enumerate(coins):
            if not force and _is_fresh(backend, name, c, refresh_interval):
                print(f"[fetch_scheduler] {name}[{c}]: 未達 refresh 間隔（{refresh_interval:.0f}s），略過")
                continue
            if dry_run:
                print(f"[fetch_scheduler] (dry-run) {name}[{c}]: 會呼叫真 API")
                continue
            try:
                fetch_started = time.perf_counter()
                docs = source.fetch("", coin=c)
                fetch_duration_ms = (time.perf_counter() - fetch_started) * 1000.0
            except Exception as exc:  # noqa: BLE001 — 單點失敗不中斷整批，
                # 但仍要計入 failures（codex HIGH-1），理由同上方 coin-agnostic 分支。
                print(f"[fetch_scheduler] {name}[{c}]: 真呼叫失敗，略過（{exc}）", file=sys.stderr)
                failures.append(f"{name}:{c}")
                if _is_http_429(exc):
                    deferred: list[str] = []
                    for remaining_coin in coins[coin_index + 1:]:
                        if not force and _is_fresh(
                            backend, name, remaining_coin, refresh_interval
                        ):
                            continue
                        deferred.append(remaining_coin)
                        failures.append(f"{name}:{remaining_coin}")
                    if deferred:
                        print(
                            f"[fetch_scheduler] {name}: HTTP 429 cooldown，"
                            f"本輪停止後續幣別真呼叫（未刷新：{deferred}）",
                            file=sys.stderr,
                        )
                    break
                continue
            now = time.time()
            try:
                original_count = len(docs)
                docs = quality_gate(source, c, docs, now)
                if original_count and not docs:
                    raise ValueError("all fetched documents failed quality gates")
                archive_fetch(source, c, docs, now, stale_after, fetch_duration_ms)
            except Exception as exc:  # noqa: BLE001
                print(f"[fetch_scheduler] {name}[{c}]: source_events 封存失敗（{exc}）", file=sys.stderr)
                failures.append(f"{name}:{c}")
                continue
            result = cache_set_monotonic(
                backend, cache_key(name, c), [doc_to_dict(d) for d in docs],
                fetched_at=now, ttl_seconds=stale_after,
            )
            if not result.ok:
                print(f"[fetch_scheduler] {name}[{c}]: cache 寫入失敗"
                      f"（backend={result.backend}）：{result.error}", file=sys.stderr)
                failures.append(f"{name}:{c}")
            else:
                _warn_if_fallback_used(f"{name}[{c}]", result)
                print(f"[fetch_scheduler] {name}[{c}]: {len(docs)} 筆文件")
                results.append((f"{name}:{c}", len(docs)))
            effective_stagger = _effective_stagger(name, stagger)
            if effective_stagger > 0:
                time.sleep(effective_stagger)

    return results, failures


def run_once_parallel(
    source_names: list[str] | None, coins: list[str], backend: CacheBackend, force: bool,
    interval_overrides: dict[str, float], stagger: float, dry_run: bool, *,
    max_workers: int = 1, total_budget_sec: float = 15 * 60,
) -> tuple[list[tuple[str, int]], list[str]]:
    """Bounded source-level parallel prefetch.

    A source remains the ownership unit: one worker performs all of that
    source's coin/broadcast writes before returning.  Snapshot capture starts
    only after every worker has joined, so no archive can observe a half-finished
    fetch cycle.  Timeout is a supervisor boundary; unfinished work is reported
    as a failed source rather than silently treated as fresh.
    """
    registry = build_registry()
    targets = source_names if source_names else sorted(registry)
    if max_workers <= 1 or len(targets) <= 1:
        return run_once(targets, coins, backend, force, interval_overrides, stagger, dry_run)
    started = time.monotonic()
    results: list[tuple[str, int]] = []
    failures: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="tf-fetch") as pool:
        futures = {
            pool.submit(run_once, [name], coins, backend, force, interval_overrides, stagger, dry_run): name
            for name in targets
        }
        try:
            for future in concurrent.futures.as_completed(futures, timeout=total_budget_sec):
                name = futures[future]
                try:
                    source_results, source_failures = future.result()
                except Exception as exc:  # noqa: BLE001
                    print(f"[fetch_scheduler] {name}: parallel worker failed ({type(exc).__name__})", file=sys.stderr)
                    failures.append(name)
                    continue
                results.extend(source_results)
                failures.extend(source_failures)
        except concurrent.futures.TimeoutError:
            pass
        for future, name in futures.items():
            if not future.done():
                future.cancel()
                failures.append(f"{name}:cycle-timeout")
    elapsed = time.monotonic() - started
    print(f"[fetch_scheduler] parallel cycle: workers={max_workers} elapsed={elapsed:.2f}s budget={total_budget_sec:.0f}s")
    return results, failures


_PROBE_SOURCE = "__fetch_scheduler_probe__"
_PROBE_COIN = "PROBE"
# ledger canary 固定 ts（不像一般 record 交給 DynamoDBLedger.append() 自動填當下
# 時間）：PK=run_id、SK=ts 都固定，才能讓每次 probe 覆寫同一筆，不會無限堆積。
_PROBE_LEDGER_TS = "1970-01-01T00:00:01+00:00"

# codex HIGH（probe 真正有界化，deploy_ec2.sh 的部署 gate 依賴這裡）：
# `get_cache_backend()`/`get_ledger()` 建構 `DynamoDBCache`/`DynamoDBLedger`
# 時**刻意**不帶 timeout（見 cache.py::DynamoDBCache 註解），沿用 boto3/
# botocore 內建預設 connect/read timeout + 標準重試——正常情況數秒等級，
# 但 DynamoDB/DNS/網路降級時可能拖到數分鐘。probe 存在的目的就是要給
# `deploy/deploy_ec2.sh` 一個「有界、快速」的部署 gate，若 client 本身沒有
# 明確 timeout，這個「有界」的前提就不成立——一次降級就可能讓 probe 這支
# SSM 指令卡到遠超部署 gate 的 poll timeout（見 `_probe_cache_backend()`/
# `_probe_ledger_backend()`：只在 probe 這條路徑額外帶入明確短 timeout/
# 重試上限，不影響一般排程路徑既有的容錯空間）。
_PROBE_DYNAMODB_CONNECT_TIMEOUT_SECONDS = 3.0
_PROBE_DYNAMODB_READ_TIMEOUT_SECONDS = 3.0
_PROBE_DYNAMODB_MAX_ATTEMPTS = 2


def _probe_cache_backend() -> CacheBackend:
    """比照 `get_cache_backend()` 的 env 選擇邏輯（`CACHE_BACKEND`），但
    DynamoDB 分支額外帶入明確短 timeout/重試上限，讓 probe 對 cache 表的
    PutItem/GetItem 真正有界（理由見上方 `_PROBE_DYNAMODB_*` 常數註解）。
    """
    backend = os.getenv("CACHE_BACKEND", "dynamodb").strip().lower()
    if backend == "json":
        return JsonCacheBackend()
    if backend == "sqlite":
        return SQLiteCacheBackend()
    return DynamoDBCache(
        connect_timeout=_PROBE_DYNAMODB_CONNECT_TIMEOUT_SECONDS,
        read_timeout=_PROBE_DYNAMODB_READ_TIMEOUT_SECONDS,
        max_attempts=_PROBE_DYNAMODB_MAX_ATTEMPTS,
    )


def _probe_ledger_backend() -> DynamoDBLedger | JsonlLedger:
    """比照 `get_ledger()` 的 env 選擇邏輯（`COST_LEDGER_BACKEND`），但
    DynamoDB 分支同樣帶入明確短 timeout/重試上限，理由同
    `_probe_cache_backend()`。
    """
    backend = os.getenv("COST_LEDGER_BACKEND", "jsonl").strip().lower()
    if backend == "dynamodb":
        return DynamoDBLedger(
            connect_timeout=_PROBE_DYNAMODB_CONNECT_TIMEOUT_SECONDS,
            read_timeout=_PROBE_DYNAMODB_READ_TIMEOUT_SECONDS,
            max_attempts=_PROBE_DYNAMODB_MAX_ATTEMPTS,
        )
    return JsonlLedger()


def run_probe() -> int:
    """DynamoDB R/W canary probe（codex HIGH-3 修正 + 後續 2 個 probe 自身的洞）。

    背景：`verify_fetch_scheduler`（`deploy/deploy_ec2.sh`）原本只是同步跑一次
    普通排程（`main()` 不帶 `--probe`），但普通排程對每個來源都先過新鮮度
    守門（`_is_fresh()`）——若剛好碰上 cache 全新鮮（如剛部署完、上一輪才
    成功寫過），本次執行對所有來源全部「略過」、0 次真呼叫、0 次 PutItem，
    仍然 `exit 0`。這樣一來，若 IAM 權限被 permission boundary / SCP / table
    resource policy 擋掉，只要當下 cache 恰好新鮮，`verify_fetch_scheduler`
    就會誤判成功，直到下一輪真的需要刷新（cache 過期）時才會開始每次
    exit 1——正好繞過本來要防的東西。

    修法：完全不碰任何真連接器 API、不看任何來源的新鮮度，直接對兩個表各做
    一次**保證真的會發生**的 R/W，且寫完都**真的讀回核對**：
      - cache 表：對保留的 canary key（`__fetch_scheduler_probe__:PROBE`，
        不會跟真實來源撞名）做 `set()`（PutItem）→`get(consistent_read=True)`
        （**強一致讀**的 GetItem）→比對讀回內容是否等於剛寫入的 sentinel。
        固定 key 若用預設的最終一致讀，PutItem 之後立刻讀，可能因複寫延遲
        讀到上一輪的舊 sentinel，變成非確定性地誤判「讀回不一致」；用
        `ConsistentRead=True` 保證讀到的就是本次剛寫入的那筆，canary 值
        本身也每次唯一（`pid+timestamp`），雙重確保判定是確定性的。
      - cost-ledger 表：對固定 `(run_id, ts)` 的一筆 record 做 `append()`
        （PutItem），**寫完拆成兩個獨立、互不干擾的驗證**：
        1. `DynamoDBLedger.get_canary(run_id, ts)`——低階「按完整主鍵」
           `GetItem`（`ConsistentRead=True`）核對寫入真的落地。按主鍵查沒有
           `Scan` 的分頁問題（回應 ≤1MB 只回一頁，`FilterExpression` 是掃完
           才套用，表大時 canary 可能剛好落在後面沒掃到的頁），配強一致讀
           也沒有最終一致的問題——不管表多大、複寫延遲多久，都能確定性地
           判斷「這筆到底寫進去了沒」。
        2. `DynamoDBLedger.probe_scan_permission()`——另外單獨呼叫一次
           `Scan`（`Limit=1`），**只驗證 `dynamodb:Scan` 這個 action 本身
           有沒有被拒**，不要求掃到任何特定內容（正式環境的 `/costs`
           端點就是靠 `Scan` 讀，若這個 action 被拒，`/costs` 會整個讀
           失敗）。若拿 `Scan` 結果去核對「有沒有掃到剛寫的 canary」，會
           被最終一致讀 + 分頁問題污染成非確定性誤判——這正是本輪要拆解
           掉的耦合：「驗寫入落地」跟「驗 Scan 權限」分開，兩者都不受
           最終一致/分頁影響。
        非 `DynamoDBLedger`（如本機開發用的 `JsonlLedger`）沒有 IAM/Scan
        這層疑慮，`append()` 沒丟例外就視為成功，不強求上述兩項。
      - 兩個 canary key 都固定、冪等（重跑覆寫同一筆，不會無限堆積垃圾資料）。

    刻意**不透過** `cache_get()`/`cache_set()` 高階便利函式：它們對讀/寫
    失敗各自有 fallback/降級語意（見 `cache.py` 模組頂部與 `CacheWriteResult`
    docstring），目的是讓「產品路徑」在 primary backend 故障時還能盡量堪用；
    但這正是 probe 要拆穿的東西——probe 要問的是「primary backend（實際配置
    的 `CACHE_BACKEND`/`COST_LEDGER_BACKEND`）本身能不能真的讀寫」，不能被
    這層 fallback 悄悄接住又回報「看起來沒事」。直接呼叫 backend 的低階
    `get()`/`set()`/`append()`/`get_canary()`/`probe_scan_permission()`，
    任何例外一律視為 probe 失敗。
    """
    ok = True

    cache_backend = _probe_cache_backend()
    canary_key = cache_key(_PROBE_SOURCE, _PROBE_COIN)
    sentinel = f"probe-{os.getpid()}-{time.time():.6f}"
    try:
        probe_doc = Document(
            id="probe", kind="probe", source=_PROBE_SOURCE, text=sentinel, ts=time.time(),
        )
        cache_backend.set(canary_key, [doc_to_dict(probe_doc)], time.time())
    except Exception as exc:  # noqa: BLE001 — probe 就是要抓「任何」寫入失敗，含被拒
        print(f"[fetch_scheduler] PROBE FAIL：cache PutItem 失敗"
              f"（backend={type(cache_backend).__name__}）：{exc}", file=sys.stderr)
        ok = False
    else:
        try:
            entry = cache_backend.get(canary_key, consistent_read=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[fetch_scheduler] PROBE FAIL：cache GetItem（ConsistentRead）失敗"
                  f"（backend={type(cache_backend).__name__}）：{exc}", file=sys.stderr)
            ok = False
        else:
            docs = (entry or {}).get("docs") or []
            read_back = docs[0].get("text") if docs else None
            if read_back != sentinel:
                print(f"[fetch_scheduler] PROBE FAIL：cache 讀回內容與剛寫入的不一致"
                      f"（ConsistentRead 之下理論上不該發生，代表寫入其實沒真的落地）："
                      f"預期 {sentinel!r}，讀到 {read_back!r}", file=sys.stderr)
                ok = False
            else:
                print(f"[fetch_scheduler] PROBE OK：cache PutItem + GetItem（ConsistentRead）"
                      f"讀寫一致（backend={type(cache_backend).__name__}）")

    ledger_backend = _probe_ledger_backend()
    try:
        ledger_backend.append({
            "run_id": _PROBE_SOURCE,
            "ts": _PROBE_LEDGER_TS,
            "total_cost_usd": 0.0,
            "calls": [],
            "note": "fetch_scheduler --probe canary，非真實花費紀錄",
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[fetch_scheduler] PROBE FAIL：cost-ledger PutItem 失敗"
              f"（backend={type(ledger_backend).__name__}）：{exc}", file=sys.stderr)
        ok = False
    else:
        if isinstance(ledger_backend, DynamoDBLedger):
            try:
                canary_item = ledger_backend.get_canary(_PROBE_SOURCE, _PROBE_LEDGER_TS)
            except Exception as exc:  # noqa: BLE001
                print(f"[fetch_scheduler] PROBE FAIL：cost-ledger GetItem（強一致）讀回失敗"
                      f"（backend={type(ledger_backend).__name__}）：{exc}", file=sys.stderr)
                ok = False
            else:
                if canary_item is None:
                    print("[fetch_scheduler] PROBE FAIL：cost-ledger GetItem（ConsistentRead）"
                          "讀不到剛寫入的 canary（PutItem 沒丟例外，但可能其實沒真的落地）",
                          file=sys.stderr)
                    ok = False
                else:
                    try:
                        ledger_backend.probe_scan_permission()
                    except Exception as exc:  # noqa: BLE001
                        print(f"[fetch_scheduler] PROBE FAIL：cost-ledger Scan 權限檢查失敗"
                              f"（/costs 端點靠 Scan 讀，會整個讀失敗）"
                              f"（backend={type(ledger_backend).__name__}）：{exc}", file=sys.stderr)
                        ok = False
                    else:
                        print(f"[fetch_scheduler] PROBE OK：cost-ledger PutItem + GetItem"
                              f"（強一致）讀回一致 + Scan 權限正常"
                              f"（backend={type(ledger_backend).__name__}）")
        else:
            print(f"[fetch_scheduler] PROBE OK：cost-ledger PutItem 成功"
                  f"（backend={type(ledger_backend).__name__}，非 DynamoDB，不強求 GetItem/Scan）")

    if not ok:
        print("[fetch_scheduler] PROBE 結論：失敗——DynamoDB cache/cost-ledger 至少一項"
              "真的讀寫不通（可能是 IAM 權限被 permission boundary/SCP/table policy 擋，"
              "或表不存在/名稱不對），deploy 不應視為成功", file=sys.stderr)
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Axis C #1（task #23，PLAN docs/archive/plans/PLAN-axisC-snapshots.md）：多幣信任快照寫入者
# + 首頁總覽正確讀路徑。
#
# 背景：`web.py::_render_home_page()` 曾在 Phase 3 短暫加過「多幣總覽」，
# 在首頁 request 當下逐幣讀 DynamoDB，codex 抓出 ThreadPool 孤兒執行緒可用性
# HIGH 風險，整個移除等 Axis C 做對（見該函式 docstring）。這裡是「做對」的
# 寫入者那一半：**獨立**分支（不混進上面 `run_once()` 打真連接器 API 的
# 流程，cadence 也刻意分開），對 5 幣各跑一次 **real-off**
# `pipeline.run(data_mode="live", llm_mode="off")`——`collect()` 線上分支
# 只讀既有 `CachedSource`（cache-miss 就走既有 `_failed` 優雅降級，不打真
# 連接器）、Bedrock `offline=True`（regex fallback，不打真 Bedrock）——純 CPU
# 確定性運算，$0（credit-safe，#24：只寫真 pipeline 結果，不得補假值）。
#
# 精華欄位逐字取自 `Report` 既有欄位（不新造 schema）：
#   - `trust_score` ← `report.confidence`：`trust.scoring.aggregate()` 算出
#     的整體信任分，`web.py::_render_trust_breakdown()` 顯示「信任 X.XX」
#     用的就是這個欄位（見 `_render_report()` 呼叫處），不是新概念。
#   - `direction`/`calibrated_confidence`/`decision_state`/`generated_at`
#     皆是 `Report` dataclass 對應欄位原樣複製。
#
# 單幣 `pipeline.run()` 失敗（如該幣 5 個來源全 cache-miss/已過期，
# `collect()` 回傳空清單觸發 `ValueError`）只印警告、跳過該幣，不寫入任何
# 值（#24 鐵律：不補假值）也不中斷其餘幣別——同 `run_once()` 一貫的「單點
# 失敗不中斷整批」容錯精神。
#
# 5 幣算完後，**順便**把整份總覽組成單一 HTML blob，寫入單一 key
# （`TRUST_OVERVIEW_SOURCE`/`TRUST_OVERVIEW_COIN`，定義於 `cache.py`，跟
# `web.py` 讀路徑共用同一份常數，避免兩處字串各自維護漂移，見該模組
# 「Axis C」段落說明）——首頁 request 只需對這一顆 blob 做**一次**短
# timeout 讀取，不逐幣讀取，見 `web.py::_render_home_overview_cached()`。
# ---------------------------------------------------------------------------

SNAPSHOT_REFRESH_INTERVAL_SECONDS = TRUST_SNAPSHOT_REFRESH_INTERVAL_SECONDS
# = 15 分鐘，建議 cron cadence（獨立 line，不綁在既有「打真連接器 API」的
# 排程節奏內）。快照寫入者本身**不**做新鮮度守門（跟 `run_once()` 對真連接器
# 的節流動機不同：這裡每次都是 real-off `pipeline.run()`，只讀既有 cache
# 純運算，$0、無 429/rate-limit 疑慮，沒有「省額度」的理由；cron 多久觸發
# 一次本身就是唯一的節流）。定義於 `cache.py`（跟 `web.py` 讀路徑的新鮮度
# 自驗共用同一份數字，避免各自定義漂移，見該模組 Axis C 段落 codex HIGH
# 修復說明），本地只是同名 re-export，方便本檔既有程式碼原樣引用。
SNAPSHOT_STALE_AFTER_SECONDS = TRUST_SNAPSHOT_FRESH_WINDOW_SECONDS
# = 45 分鐘，沿用 `cache.py` 既有的 3 倍 margin 換算公式（codex HIGH-1
# 同款考量：cron jitter 或單輪 pipeline.run() 失敗仍要留緩衝，不能讓硬過期
# 等於 refresh 間隔）——同一份數字也是 `web.py` 讀路徑驗證總覽 blob 新鮮度
# 用的窗口，見 `cache.py::TRUST_SNAPSHOT_FRESH_WINDOW_SECONDS`。

# 與 `web.py::_DATE_AGNOSTIC_QUERY_SUFFIX` 組出的預設查詢同文案（"分析該幣種
# 近期市場狀況，整合多源資料"）——刻意不 import web.py（避免這支排程腳本被
# 拉進一堆 web 專用依賴），純字串複製；兩處若之後要改請同步改。
_SNAPSHOT_QUERY = "分析該幣種近期市場狀況，整合多源資料"


def _reputation_summary(evidence: list) -> dict[str, dict]:
    """task #26：從單次 `pipeline.run()` 回傳的 `evidence`（`list[Evidence]`，
    先前呼叫端一律用 `_evidence` 丟棄）擷取 W2 可解釋性 trace 精華。

    背景：`agent.orchestrator._scored_to_evidence()` 在 `dynamic_reputation=
    True`（production 固定開啟，見該模組 `run_agent_pipeline`）時，把
    `trust.scoring.score()` 算出的 `{source, prior, final, agree_n,
    contradict_n, iterations_run}` 併入每筆 `Evidence.trust_components` 的
    `reputation_prior`/`reputation_final`/`reputation_agree_n`/
    `reputation_contradict_n`/`reputation_iterations_run` 欄位——同一來源在
    同一份報告裡的每筆 `Evidence` 攜帶**完全相同**的 trace 值（`scoring.
    score()` 逐 source 建一份、廣播給該 source 所有 claim，見該函式
    `trace_by_source` 實作），這裡依 `source` 去重，只留一份代表值。

    只在真的有 trace 資料時才收錄該來源（`llm_mode=off` 的 real-off
    `--snapshot` 路徑下，`stance_fn` 全部 fail-safe 回 neutral，`agree_n`/
    `contradict_n` 恆為 0、`final == prior`——這是誠實的真結果，不是 bug，
    #24 鐵律：只寫真分析結果，不因為「目前恆為 0」就不寫或造假填別的值）。
    """
    summary: dict[str, dict] = {}
    for ev in evidence:
        tc = getattr(ev, "trust_components", None) or {}
        if "reputation_prior" not in tc or "reputation_final" not in tc:
            continue
        source = getattr(ev, "source", "")
        if not source or source in summary:
            continue  # 同來源多筆 Evidence 的 trace 值相同，取第一筆即可
        prior = float(tc["reputation_prior"])
        final = float(tc["reputation_final"])
        summary[source] = {
            "prior": prior,
            "final": final,
            "delta": round(final - prior, 4),
            "agree_n": int(tc.get("reputation_agree_n", 0)),
            "contradict_n": int(tc.get("reputation_contradict_n", 0)),
        }
    return summary


def _calc_manip_signal(evidence: list, coin: str | None = None) -> tuple[float, float] | None:
    """#86／codex 複審 HIGH 修復：從 `evidence`（`pipeline.run()` 回傳的第二個
    值，同 `_reputation_summary()` 這份）逐筆 `trust_components["manipulation"]`
    算出本輪快照的操縱風險訊號，回傳 `(worst, mean)`。

    codex 複審 HIGH（風險 invariant 定案）：**`worst`（= `max()`，any-hit
    語意）才是主訊號，不是算術平均**。原始實作用平均值當唯一分數，會被
    evidence 筆數稀釋——15 筆裡只要有 1 筆已確認操縱（`manipulation=1.0`），
    平均只剩 0.067，會被 UI 判成「低操縱風險」，把一次確定的操縱訊號洗成
    假安全訊號；來源數量不對等（例如某來源類型灌爆筆數）還會不成比例
    稀釋其他來源的訊號。信任產品的操縱風險徽章必須滿足「只要出現一筆
    已確認操縱，就不可能顯示低風險」這個 invariant，`max()` 是唯一在
    evidence 筆數/來源分布任意變動下都維持這個 invariant 的聚合方式
    （見 `test_calc_manip_signal_single_confirmed_hit_is_not_diluted_by_mean`
    鎖定此不變量）。

    `mean` 一併回傳、寫入快照的 `"manip_score_mean"` 欄位，作**輔助**
    資訊（供人工判讀「這批證據平均而言如何」，不參與徽章分級判斷，見
    `frontend/src/lib/manipRisk.ts::manipRiskDisplay()` 只吃 `manip_score`
    這個 primary 訊號）。

    ⛔ $0／不重算：`trust.scoring.score()` 已把「信譽×0.5 + 佐證×0.25 +
    時效×0.15 − 操縱×0.4」的操縱懲罰分項算好、由
    `agent.orchestrator._scored_to_evidence()` 逐字複製進每筆
    `Evidence.trust_components["manipulation"]`（鍵名對照：issue #86 原始
    描述寫的是 `sc.components["manip"]`，但 `"manip"` 只是權重字典 `w` 的
    鍵，`ScoredClaim.components`/`Evidence.trust_components` 實際存的鍵是
    `"manipulation"`，見 `trust/scoring.py::score()`，兩者不可混用）。這裡
    純粹是對既有結果的重新聚合，不另開一條獨立公式、不重呼叫任何連接器。

    誠實標「無資料」（比照 `_reputation_summary()`／`reputation_trace` 欄位
    同款慣例，也對齊 W2 `single_source`／`has_data` 徽章的誠實原則）：
    `evidence` 為 None／空清單，或逐筆都沒有 `manipulation` 分項（理論上
    只要 `evidence` 非空就一定有——這裡仍防禦式檢查，不假設上游契約永遠
    成立）時回傳 `None`，呼叫端據此完全不寫入 `"manip_score"`／
    `"manip_score_mean"` 這兩個鍵，不用 0.0 冒充「查過、確定無操縱」
    （#24 鐵律）。

    codex 窮舉終審 HIGH 修復（非數值 manipulation 中止整批快照）：原本
    `float(tc["manipulation"])` 對每一筆都直接轉型、沒有例外處理——單筆
    髒資料（`None`、字串、物件、`NaN`/`Infinity`……理論上 `trust.scoring.
    score()` 不該產生，但 `trust_components` 是外部可變的 dict，不假設
    上游契約永遠成立）就會讓 `float()` 拋例外或算出不合法值，往上傳到
    `_snapshot_dict()`，而 `_snapshot_dict()` 呼叫處（`--snapshot` 主
    迴圈）當時只把 `pipeline.run()` 包在 try/except 裡，`_snapshot_dict()`
    本身不在保護範圍內——單筆壞資料會直接把整個 coin loop 炸掉，波及本輪
    其他健康的幣。修法：逐筆驗證改成「只接受 0..1 之間的有限實數」（用
    `math.isfinite()` 擋 `NaN`/`Infinity`，用 `isinstance(x, bool)` 排除
    `True`/`False`——Python 的 `bool` 是 `int` 子類，`isinstance(x, (int,
    float))` 會誤放 `bool` 通過），驗證失敗的單筆一律跳過（不中止整批）
    並印 warning log（帶 `coin` 方便追查是哪一幣、哪個來源寫壞），呼叫端
    （`--snapshot` 主迴圈）另外把 `_snapshot_dict()` 整體納入 per-coin
    try/except 作 defense-in-depth（即使未來又有其他欄位算出時炸例外，也
    只隔離單幣，不會中止整輪排程）。"""
    if not evidence:
        return None
    scores: list[float] = []
    for ev in evidence:
        tc = getattr(ev, "trust_components", None) or {}
        if "manipulation" not in tc:
            continue
        raw = tc["manipulation"]
        # codex 窮舉終審 HIGH 修復：只接受 0..1 之間的有限實數，`bool` 雖是
        # `int` 子類但語意上不是分數，一併排除；`None`/字串/物件/NaN/
        # Infinity 一律視為畸形、跳過該筆並記 warning，不讓單筆壞資料中止
        # 整批快照。
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            print(
                f"[fetch_scheduler] _calc_manip_signal"
                f"{f'({coin})' if coin else ''}: 跳過非數值 manipulation "
                f"分項（型別 {type(raw).__name__}）",
                file=sys.stderr,
            )
            continue
        value = float(raw)
        if not math.isfinite(value) or not (0.0 <= value <= 1.0):
            print(
                f"[fetch_scheduler] _calc_manip_signal"
                f"{f'({coin})' if coin else ''}: 跳過越界/非有限 manipulation "
                f"分項（值 {raw!r}，應為 0..1 之間的有限實數）",
                file=sys.stderr,
            )
            continue
        scores.append(value)
    if not scores:
        return None
    return round(max(scores), 3), round(sum(scores) / len(scores), 3)


# codex vp-engineering 終審 MEDIUM（PR #107）：見 `_collect_authors()`
# docstring／函式內註解——單一快照 `"authors"` 清單的防禦性上限。
_MAX_AUTHORS = 200


def _collect_authors(evidence: list) -> list[str]:
    """W3 前置（資料累積，非偵測）：從 `evidence`（`pipeline.run()` 回傳的
    第二個值）逐筆擷取 `Evidence.author`（見 `agent.orchestrator.
    _scored_to_evidence`，來源平台公開 username 原文，由 ingestion.social/
    news 連接器選填），去重、排序後回傳。

    只累積原始 username 字串本身，**不做任何跨來源/跨平台關聯、不做去
    識別化以外的任何衍生運算、不影響任何 trust 分數**——單純讓帳號維度
    資料開始按日留存，供未來 W3 協同操縱偵測演算法使用（本 PR 不包含該
    演算法）。目前大多數來源（多數 news RSS、onchain、regulatory、
    hoyabit、price）沒有作者概念，`Evidence.author` 恆為 `None`（codex
    vp-engineering 終審 MEDIUM，PR #107：型別已改 `str | None = None`，
    不再用空字串冒充「未知」），該筆直接跳過，回傳空 list 是誠實結果，
    不代表 bug。

    去重排序後若超過 `_MAX_AUTHORS`（200）筆，截斷保留前 `_MAX_AUTHORS`
    筆並記 warning（防禦性上限，避免單一快照撞 DynamoDB item 大小上限，
    見 `_MAX_AUTHORS` 註解）。
    """
    authors: set[str] = set()
    for ev in evidence:
        author = getattr(ev, "author", None) or ""
        if author:
            authors.add(author)
    result = sorted(authors)
    if len(result) > _MAX_AUTHORS:
        # codex vp-engineering 終審 MEDIUM：防禦性上限——單一快照的
        # authors 清單若無上限，理論上可能被大量不同 username 灌爆，
        # 撞上 DynamoDB 單一 item 400KB 上限（快照本身還有其他欄位要塞
        # 進同一個 item）。正常情境（單輪抓取 evidence 通常個位數~幾十
        # 筆）不會觸發，觸發時截斷保留排序後前 `_MAX_AUTHORS` 筆並記
        # warning，不讓單一快照無限膨脹。
        print(
            f"[fetch_scheduler] _collect_authors: 去重後 authors 數"
            f"（{len(result)}）超過上限 {_MAX_AUTHORS}，截斷保留前"
            f" {_MAX_AUTHORS} 筆（排序後）",
            file=sys.stderr,
        )
        result = result[:_MAX_AUTHORS]
    return result


def _snapshot_dict(coin: str, report, evidence: list | None = None) -> dict:
    """`Report`（真 `pipeline.run()` 結果）→ 快照精華 dict。欄位逐字取自
    既有 `Report` dataclass 欄位，不新造（#24：只寫真分析結果）。

    task #26 追加：`evidence`（`pipeline.run()` 回傳的第二個值，之前呼叫端
    直接丟棄）非空時，順便擷取 W2 reputation_trace 精華（見
    `_reputation_summary()`），寫入 `"reputation_trace"` 欄位，供未來 #4
    來源信譽榜使用。沒有 trace 資料（`evidence=None`/空清單，或該幣本輪
    尚未啟用動態信譽）時完全不新增這個鍵，逐字向後相容，也不補假值。

    #86 追加，codex 複審 HIGH 修復（見 `_calc_manip_signal()` docstring）：
    `evidence` 可算出操縱訊號時，多寫兩個鍵——`"manip_score"`（**worst-case
    max**，供首頁跨幣信任排行的操縱風險徽章判斷用的 primary 訊號，「只要
    有一筆已確認操縱就不能顯示低風險」）與 `"manip_score_mean"`（算術
    平均，僅供輔助判讀，不參與徽章分級）。同樣是**追加、非破壞性**欄位
    ——算不出（`evidence` 為 None/空）時完全不新增這兩個鍵，舊格式快照／
    本輪無 evidence 的快照都合法缺席，前端（`OverviewCard.tsx`／
    `ManipRiskBadge`）須顯式標「未評分」（不是悄悄不顯示、更不是假設
    0＝安全）。

    W3 前置（資料累積，非偵測）：`evidence` 裡有筆帶 `author`（見
    `_collect_authors()`）時，多寫 `"authors"` 鍵（去重排序後的 username
    原文 list），讓帳號維度資料開始按日累積。同樣**追加、非破壞性**——
    目前本 PR 不含任何演算法消費這個鍵，也不在任何 UI 顯示；沒有作者資料
    時完全不新增這個鍵，不補空 list、不假裝有資料。"""
    snap = {
        "coin": coin,
        "trust_score": round(float(report.confidence), 4),
        "direction": report.direction,
        "calibrated_confidence": round(float(report.calibrated_confidence), 4),
        "decision_state": report.decision_state,
        "generated_at": report.generated_at,
    }
    if evidence:
        reputation_trace = _reputation_summary(evidence)
        if reputation_trace:
            snap["reputation_trace"] = reputation_trace
        manip_signal = _calc_manip_signal(evidence, coin=coin)
        if manip_signal is not None:
            manip_worst, manip_mean = manip_signal
            snap["manip_score"] = manip_worst
            snap["manip_score_mean"] = manip_mean
        authors = _collect_authors(evidence)
        if authors:
            snap["authors"] = authors
    return snap


def _overview_card_href(coin: str) -> str | None:
    """單張總覽卡的點擊目標：真 `/analyze` 連結（真資料，不帶 `sample=1`），
    點卡直接跑一次該幣的完整多源分析。

    P-2026 生產 UX bug（第二處）：卡片原本是純 `<div>`，桌面版真點下去
    零反應，使用者以為壞掉。世界級 dashboard 卡片理應可點進該幣完整分析。

    `coin` **必須**在 `COIN_POOL` 白名單內才組連結（防呆：`coins` 理論上
    只可能來自 `COIN_POOL` 或 `--coin` 這種操作者輸入的 CLI 參數，非 HTTP
    request 直接控制，但仍不假設呼叫端已驗證——不在白名單就回 `None`，
    呼叫端據此讓卡片保持純 `<div>` 不可點，不組出指向未知/非法幣種、多半
    404 的連結）。查詢文案沿用跟 `_SNAPSHOT_QUERY`／`web.py` 首頁 hero CTA
    （`_hero_analyze_href`）一致的 date-agnostic 句型，只是把幣種換成該卡
    真正對應的幣，而非泛稱「該幣種」。
    """
    if coin not in COIN_POOL:
        return None
    params = {
        "coin": coin,
        "type": QuestionType.MULTI_SOURCE.value,
        "q": f"分析{coin}近期市場狀況，整合多源資料",
    }
    return html.escape(f"/analyze?{urlencode(params)}")


def _render_overview_html(snapshots: list[dict]) -> str:
    """5 卡總覽 HTML（`html.escape` 逐欄）——寫入者這端組好整份字串，首頁
    讀路徑只是把這個 blob 原樣嵌進頁面，request 當下不重新組字串／不逐幣讀
    （見 `web.py::_render_home_overview_cached()`）。

    `snapshots` 為空（本輪全部幣都失敗）回空字串，呼叫端據此判斷不寫總覽
    blob（見 `run_snapshot()`）。CSS 變數沿用 `web.py` 既有 dark/light 主題
    變數名稱（`--tf-border`/`--tf-inset`/`--tf-muted`/`--tf-muted2`），跟
    頁面其餘區塊視覺一致。

    每張卡包成 `<a href="/analyze?...">`（見 `_overview_card_href`），點卡
    直接導向該幣真分析——不再是死 `<div>`（P-2026 生產 UX bug 第二處）。

    P-2026 生產 UX bug（第三處，CEO 真 Chrome 診斷）：卡片內裝飾性子元素
    （幣別 LOGO `<svg>`、信任分/校準信心/時間戳那些純顯示用 `<div>`）在
    瀏覽器裡是各自獨立的點擊目標——滑鼠點在這些子元素上時，事件目標是
    子元素本身而非外層 `<a>`，導致「點卡片沒反應」（`<a>` 本身、
    `a.click()` 程式化觸發、curl 拉 HTML 結構驗證起來都正常，只有「真人
    滑鼠點在子元素上」這個情境會死掉，比對 hero CTA／系統狀態那種文字
    直接包在 `<a>` 裡、沒有子元素分走事件的連結，就不會有這個問題）。
    修法：卡片內每個子元素都加 `pointer-events:none`，讓點擊全部穿透到
    外層 `<a>`；`<a>` 本身維持（顯式標註）`pointer-events:auto`，確保
    整張卡表面都能可靠觸發導航。
    """
    if not snapshots:
        return ""
    e = html.escape
    cards = []
    for snap in snapshots:
        coin_raw = str(snap.get("coin", ""))
        coin = e(coin_raw)
        trust = float(snap.get("trust_score", 0.0) or 0.0)
        direction = e(str(snap.get("direction", "")))
        calibrated = float(snap.get("calibrated_confidence", 0.0) or 0.0)
        decision_state_raw = str(snap.get("decision_state", ""))
        # #1 修復：legacy 快照缺這個 key（`snap.get(...)` 落到預設空字串）或
        # 帶未知字面值時，一律正規化為 "normal" 才拿去顯示——跟 `web.py::
        # _normalize_decision_state()`／前端 `normalizeDecisionState()` 同一套
        # fallback 規則，避免舊快照在副標顯示空白或未知字串。
        decision_state_norm = (
            decision_state_raw if decision_state_raw in ("abstain", "low_confidence", "normal") else "normal"
        )
        decision_state = e(decision_state_norm)
        generated_at = e(str(snap.get("generated_at", "")))
        # #101 主角數字統一：abstain/low_confidence 態主角＝校準後資訊完整度，
        # normal 態主角＝裸均值信任分（`trust`），跟 `web.py::_conf_gauge`／
        # React `OverviewCard`/`ConfidenceGauge` 同一套規則。
        is_low_info = decision_state_norm in ("abstain", "low_confidence")
        hero_value = calibrated if is_low_info else trust
        hero_label = "資訊完整度（校準後）" if is_low_info else "信任分"
        href = _overview_card_href(coin_raw)
        tag_open = (
            f'<a class="tf-overview-card" href="{href}" '
            'style="display:block;text-decoration:none;color:inherit;'
            'pointer-events:auto;'
            'border:1px solid var(--tf-border);border-radius:8px;'
            'padding:.6rem .8rem;background:var(--tf-inset)">'
            if href is not None
            else '<div class="tf-overview-card" style="border:1px solid var(--tf-border);'
            'border-radius:8px;padding:.6rem .8rem;background:var(--tf-inset)">'
        )
        tag_close = '</a>' if href is not None else '</div>'
        # 商業級視覺（Nansen/Messari 級）：幣別旁附官方 LOGO（inline SVG，
        # simple-icons CC0，見 trustforge.brand_logos 模組 docstring）。
        # `coin_raw` 一律經 COIN_POOL 白名單產生（見本函式頂部迴圈），非
        # HTTP request 直接控制的字串；`coin_logo_html` 內部仍只認白名單
        # dict，查無對應幣種回空字串，不會印出破圖或錯誤幣的 LOGO。
        #
        # P-2026 第三處 UX bug 修法：LOGO 是純裝飾（不需要自己被點），用
        # `<span style="pointer-events:none">` 包一層，讓點在 LOGO 上的
        # 滑鼠事件穿透到外層 `<a>`；刻意不動 `trustforge.brand_logos` 共用
        # 的 `_svg()` 輸出本身，那份 SVG 也被非卡片情境的 evidence pill
        # 共用，那邊沒有這個死點擊問題、不該被牽動。
        logo = coin_logo_html(coin_raw)
        logo_html = (
            f'<span style="pointer-events:none">{logo}</span> ' if logo else ""
        )
        cards.append(
            tag_open
            + f'<div style="font-weight:700;pointer-events:none">{logo_html}{coin}</div>'
            f'<div style="font-size:.85rem;color:var(--tf-muted);pointer-events:none">'
            f'{hero_label} {hero_value:.2f} · {direction}</div>'
            f'<div style="font-size:.75rem;color:var(--tf-muted2);pointer-events:none">'
            f'資訊完整度（校準後） {calibrated:.2f}｜裸均值信任分 {trust:.2f} · {decision_state}</div>'
            f'<div style="font-size:.7rem;color:var(--tf-muted2);pointer-events:none">'
            f'{generated_at}</div>'
            + tag_close
        )
    return (
        '<div class="tf-overview-grid" style="display:grid;'
        'grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.6rem">'
        + "".join(cards) + "</div>"
    )


def run_snapshot(coins: list[str], backend: CacheBackend, dry_run: bool) -> int:
    """`--snapshot` 模式：對 `coins` 各跑一次 real-off `pipeline.run()`，把
    精華快照寫入各自 cache key，並把總覽 HTML 寫入單一 blob key。

    `dry_run`：只列出會跑哪些幣、會寫哪些 key，不真的呼叫 `pipeline.run()`
    （跟既有 `run_once()` 的 `--dry-run` 語意一致），供 cron/CI 驗證這個
    分支不會誤打真 API（此模式本來就 $0 real-off，但仍要能驗證「不執行」
    這件事本身正確）。

    回傳 exit code：任一幣快照寫入失敗（或總覽 blob 寫入失敗）計入
    `failures`，只要非空即回 `1`（比照 `main()` 對 `run_once()` failures 的
    既有語意，讓 cron/監控看得到）；全部幣本輪都失敗（0 筆快照）也視為
    失敗，即使沒有寫入任何東西可失敗——因為代表整輪排程根本沒產出任何
    結果，值得被監控看到，而非默默 exit 0。

    task #26：每幣寫完「最新一筆」（`TRUST_SNAPSHOT_SOURCE`，每輪覆寫）後，
    順便多寫一筆**按日**歷史快照（`trust_snapshot_history_key()`，同一天
    多次跑對同一把 key 覆寫＝uPsert，跨日累積成序列），供未來 #26 UI 趨勢
    圖／`get_trust_history()` 讀取。歷史寫入失敗獨立計入
    `failures`（`"{coin}:history"`），不影響已成功寫入的「最新一筆」與
    總覽 blob（那條路徑本來就穩定運作，不該被歷史寫入這個新增功能拖累）。

    codex HIGH（PR #59 review 第三輪，#1 三表示一致性最終閉合）：「最新一筆」
    （`TRUST_SNAPSHOT_SOURCE`）、「按日歷史」（`TRUST_SNAPSHOT_HISTORY_SOURCE`）、
    「總覽 blob」（`TRUST_OVERVIEW_SOURCE`）三個持久化表示**全部**改用
    `cache_set_if_newer()`（單調條件寫入），且**共用同一個 `run_now`**（本函式
    一進來就 `time.time()` 一次，取代先前逐幣各自呼叫、總覽再另外呼叫一次
    `time.time()` 的寫法）。理由：前一輪只把歷史改 monotonic，「最新一筆」跟
    總覽 blob 還是無條件覆寫——run A（較舊）暫停、run B（較新）寫完全部三表
    示、A 恢復繼續寫 → A 會**無條件**把 latest/overview 蓋回舊值，而 history
    因為已經是 monotonic 正確跳過成 B，造成「首頁看到的最新/總覽是 A 的舊
    值、history 卻是 B 的新值」的長期 silent 矛盾（違反 #24：使用者看到的
    「當下」跟「歷史」互相打架）。三者共用同一 `run_now` 這件事本身也很關鍵：
    確保同一輪呼叫的三個寫入判斷用的是**同一把時間戳**跟各自既有值比較，
    不會出現「這輪一部分寫入贏、一部分寫入輸」的內部不一致（同一輪要嘛全部
    覆寫成功、要嘛全部因為比既有值舊而跳過）。跳過（`result.skipped`）皆屬
    正常情況、不計入 `failures`；只有 backend 真的寫入失敗（`result.ok=False`）
    才計入。

    codex HIGH（PR #59 review 第四輪，per-coin all-or-nothing 收窄，非重量級
    跨 key transaction）：latest/history/overview 三個各自的條件寫入雖然都
    是 monotonic，但**彼此不是同一個原子操作**——例如 latest 寫成功後，
    history 那步才發生 error/timeout，會出現「latest 已經是新的，history
    卻還停在舊的」這種單幣局部矛盾。真正的跨 key 原子（DynamoDB
    `TransactWriteItems` 全項單調條件、或 immutable generation + 原子切換
    manifest 指標）屬於重量級架構改動，決定當 follow-up 處理（見
    `docs/plans/OPTIMIZATION-PLAN-weakness.md` 對應段落 + GitHub issue）——歷史
    趨勢 UI 目前還沒建、沒有人讀 history，暫態矛盾影響極小，值得先用一個
    收斂矛盾窗的**低成本收窄**頂著，而不是本輪就上重量級方案。

    本輪收窄做法：**以這一幣 latest 這次的寫入結果為準，串接該幣接下來
    是否處理 history/overview**——
    - latest **skipped**（比既有值舊）→ 同步 `continue` 跳過這一幣的
      history 寫入，也不把它納入這輪的總覽候選（不計入 `failures`，這輪
      本來就不該處理這一幣，交給既有/勝出的那筆資料）。
    - latest **error/失敗**（非 skip）→ 沿用既有的 `continue`，一樣跳過
      history、排除出總覽候選，並計入 `failures`（不會出現「latest 沒寫成
      但 history/overview 還是寫了」）。
    - latest **成功覆寫** → 照常寫 history + 納入總覽候選。
    這把「latest 新但 history 舊」的矛盾窗收到只剩「latest 跟 history 都
    寫成功，但兩個 `cache_set_if_newer()` 呼叫之間程序被砍斷」這種極罕見
    的 transient crash 窗——下一輪排程會自然覆蓋回一致狀態（self-healing），
    不需要跨 key 原子就能把常態下的矛盾視窗壓到最小。

    codex HIGH（PR #59 review 第五輪，#1 durability 最終閉合）：第四輪的
    per-coin 收窄仍有一個方向性缺陷——它是**先寫 latest、才寫 history**。
    若程序剛好在「latest 已經寫成功」跟「history 還沒寫」這兩步之間被砍斷
    （crash／OOM kill／部署中途被殺），且下一輪排程剛好跨過 UTC 日界，
    **前一天的歷史就永久遺失、無法復原**——history 的存在意義正是按日
    累積成 point-in-time 序列，一旦某一天完全沒有任何一次成功寫入，
    之後**沒有任何辦法補回那一天**（不像 latest，latest 弄丟舊值只是
    「暫時舊」，下一輪重跑一定會自然覆蓋回最新，是可自癒的）。「下一輪
    排程自然覆蓋回一致狀態」這個自癒說法，只在「還沒跨過那一天」的前提
    下成立；一旦跨了日界，該幣當天的歷史就是真的、永久地空了一格。

    修法：**重排寫入序，把不可復原的那個（history）放在可自癒的那個
    （latest／overview）前面**——
    1. 先寫 `history`（當日 PIT，一旦這步寫完，這一天這一幣的資料就
       durable 保住了，即使接下來 crash 也不會再遺失）。
    2. `history` 成功後，才接著寫 `latest` + 納入總覽候選（這兩個是
       last-write-wins、下一輪排程一定會自然覆蓋回最新值，可以安心放在
       後面——即使中間又被砍斷，最多只是「latest 暫時顯示舊資料」，不是
       「這一天的歷史永遠消失」）。

    對應把 gating 依據也整個反過來，改成**以 history 這次的 CAS 結果為
    準**（取代第四輪「以 latest 結果為準」的方向）：
    - history **skipped**（當日已有較新或同時的快照）→ 同步跳過這一幣
      的 latest／總覽候選寫入，交給既有/勝出的那筆資料（不計入
      `failures`）。
    - history **error/失敗**（非 skip）→ 一樣跳過 latest／總覽候選，並
      計入 `failures`（不會出現「history 沒寫成但 latest/overview 還是
      寫了」，避免重新引入第四輪修的那種同幣內部矛盾，只是換了個方向）。
    - history **成功覆寫** → 才繼續嘗試 `cache_set_if_newer()` 寫
      `latest`（**仍然是條件寫入**，因為 latest 是全域 key、可能還在跟
      其他天的另一輪排程競爭，不能因為 history 贏了就無條件覆寫
      latest）。latest 本身若又被判定 skip（極罕見：不同天的另一輪已經
      寫入更新的 latest），視為正常、自癒的暫態，不計入 `failures`，也
      不把這筆 stale 資料納入總覽候選（總覽只收「這一幣這一輪真的成為
      目前最新」的資料）；latest 真的寫入失敗（`ok=False`）才計入
      `failures`，同樣排除出總覽候選。
    三表示仍共用同一個 `run_now`；全部幣本輪都合法跳過非失敗誤報的既有
    邏輯（`skipped_coins`）維持不變，只是現在會被兩個地方（history 跳過、
    或 history 成功但 latest 又跳過）都計入。這樣把「crash 在 history 跟
    latest 之間」的殘餘視窗，從「可能永久弄丟一天歷史」收斂成「latest
    暫時顯示舊資料、下一輪自癒」——真正的跨 key 原子仍是 follow-up
    （見 GitHub issue #62、`docs/plans/OPTIMIZATION-PLAN-weakness.md`），但這次
    重排已經把「不可復原」的那一半風險大幅降低。

    codex HIGH（PR #59 review 第六輪，backend-affinity 一致性、#1 最終
    閉合）：上面的「history 先寫」重排隱含一個假設——history 這次真的
    durable 進了 primary backend。但 `cache_set_if_newer()` 本身有
    cross-backend fallback 機制（primary 如 DynamoDB 失敗時，可能改寫本地
    `JsonCacheBackend` 並回傳 `ok=True, used_fallback=True`——見
    `src/trustforge/ingestion/cache.py`）。若 history 這次是靠 fallback
    才寫成功，那份資料**只在本機看得到，沒有真正進 primary**；如果緊接著
    latest/overview 的 CAS 呼叫時 primary 剛好恢復、正常寫進 primary，
    primary 端就會出現「latest/overview 是新的，但 history 完全不存在」
    的跨 backend 分裂——一旦跨過 UTC 日界，primary 那天的 PIT 就永久
    缺角，且因為 `history_result.ok` 是 `True`，這個分裂完全不會被
    `failures` 抓到、run 還是回報 exit 0，形同悄悄發生、沒有人會發現。
    修法：把「history 走 fallback、沒真正進 primary」視同跟
    `history_result.ok=False` 一樣的 gating 失敗——同步跳過這一幣的
    latest/overview（刻意不寫進任何 backend，包含也不寫進 fallback，
    避免又要另外追蹤兩個 backend 各自的一致性），並計入 `failures`
    （逼監控看得到，下一輪 DynamoDB 恢復後才會自然重新整批寫回
    primary，三表示重新對齊）。這確保三個表示要嘛全部落在同一個
    backend 保持一致，要嘛這一幣這一輪整個不處理，不會有「這一幣的
    三個表示分散在不同 backend」這種更難排查的分裂狀態。

    codex HIGH（PR #59 review 第七輪，跨 backend 分裂 class 徹底閉合）：
    第六輪只擋了 history 的 fallback，**latest 跟 overview 這兩個寫入的
    fallback 完全沒擋**——history 成功進 primary 之後，latest 這次呼叫若
    剛好 primary transient 失敗、轉走 JSON fallback，`result.ok` 一樣是
    `True`，程式會照常把它當成功、納入總覽候選；overview 接著若正常寫進
    （已經恢復的）primary，primary 端就會出現「history/overview 是新的，
    但 latest 沒真的更新」的分裂；overview 自己也可能發生一樣的事。用
    「per-key 個別判斷 used_fallback」這種修法，每加一個 key 就要多补一次
    判斷，容易漏，是治標不治本。

    最乾淨的解法：**這三個 `cache_set_if_newer()` 呼叫全部明確傳
    `allow_json_fallback=False`**，從根本上關掉這條路徑的 cross-backend
    fallback（`_json_fallback_enabled()` 收到明確 `False` 時一律直接視為
    停用，不會再嘗試 fallback，不管環境變數 `TRUSTFORGE_CACHE_JSON_FALLBACK`
    是否開啟）——snapshot 這三個表示只認 primary backend 是否真的寫成功；
    primary 失敗就是失敗（`ok=False`），不會有「fallback 成功但沒進
    primary」這種曖昧地帶。一般 cache（連接器資料抓取的快取）呼叫端完全
    不受影響，`allow_json_fallback` 預設值沒變，這裡只是 snapshot 路徑
    明確傳入 `False`。history/latest/overview 三處各自保留的
    `used_fallback` 檢查因此結構上都變成不可能觸發的 defense-in-depth
    （防止未來有人漏改某一處呼叫又重新打開這個 class），三表示要嘛全部
    真的 durable 進 primary、要嘛任何一個環節失敗就整幣跳過並計入
    `failures`，不可能再出現跨 backend 分裂。
    """
    from trustforge.pipeline import run as pipeline_run

    if dry_run:
        for coin in coins:
            print(f"[fetch_scheduler] (dry-run) --snapshot {coin}: 會跑 1 次 "
                  f"real-off pipeline.run()，寫入 cache key "
                  f"{cache_key(TRUST_SNAPSHOT_SOURCE, coin)!r}")
        print(f"[fetch_scheduler] (dry-run) --snapshot: 完成後會寫入總覽 blob "
              f"{cache_key(TRUST_OVERVIEW_SOURCE, TRUST_OVERVIEW_COIN)!r}")
        return 0

    # codex HIGH（PR #59 review 第三輪）：三個持久化表示（latest/history/
    # overview）共用同一個 `run_now`，一進函式就取一次——不要逐幣、逐 key
    # 各自呼叫 `time.time()`。同一輪呼叫用同一把時間戳跟各自既有值比較，
    # 確保這一輪要嘛全部覆寫成功、要嘛全部因為比既有值舊而跳過，不會出現
    # 「這輪一部分寫贏、一部分寫輸」的內部不一致（見本函式 docstring）。
    run_now = time.time()
    snapshots: list[dict] = []
    failures: list[str] = []
    # codex HIGH（PR #59 review 第四輪，per-coin all-or-nothing 收窄）：
    # 「latest 被單調條件寫入判斷為跳過」是**正常、健康**的行為（代表有另一
    # 輪更新的排程已經處理過這幣，見下方 `continue` 分支），不是失敗——
    # 用獨立的 `skipped_coins` 追蹤，跟真正的 `failures`（pipeline 失敗／
    # cache 寫入失敗）分開，讓「這輪全部幣都被更新的並行排程超車而跳過」
    # 不會被回傳碼誤判成「這輪排程失敗」（見下方總覽 blob 段落）。
    skipped_coins: list[str] = []
    source_names = list(build_registry())
    for coin in coins:
        source_capture = capture_source_snapshot(
            backend, coin, source_names, captured_at=run_now,
        )
        if not source_capture.ok or source_capture.used_fallback:
            print(f"[fetch_scheduler] --snapshot {coin}: source snapshot 未 durable 寫入"
                  f"（backend={source_capture.backend}）：{source_capture.error}，略過 trust snapshot",
                  file=sys.stderr)
            failures.append(f"{coin}:source-snapshot")
            continue
        if source_capture.skipped:
            print(f"[fetch_scheduler] --snapshot {coin}: 當日 source snapshot 已有較新資料，略過")
            skipped_coins.append(coin)
            continue
        try:
            report, evidence, _log = pipeline_run(
                coin, _SNAPSHOT_QUERY, QuestionType.MULTI_SOURCE,
                data_mode="live", llm_mode="off",
            )
        except Exception as exc:  # noqa: BLE001 — 單幣失敗（含 collect 全
            # cache-miss/過期時 pipeline.run() 內部 `collect()` 回傳空清單
            # 觸發 ValueError）只跳過該幣、不中斷其餘幣別，且**不寫入任何
            # 值**——#24 鐵律：不得用假值填補失敗的分析結果。
            print(f"[fetch_scheduler] --snapshot {coin}: pipeline.run() 失敗，"
                  f"略過（{exc}）", file=sys.stderr)
            failures.append(coin)
            continue

        # codex 窮舉終審 HIGH 修復（非數值 manipulation 中止整批快照，第二
        # 道防線）：`_calc_manip_signal()` 內部已逐筆驗證、跳過畸形值，
        # 這裡再把 `_snapshot_dict()` 整體納入 per-coin try/except——即使
        # 未來又有其他欄位算出時炸例外，也只隔離這一幣（計入 `failures`、
        # 不寫入任何值，同上方 `pipeline_run()` 失敗的處理方式），不會像
        # 修復前那樣讓單筆壞資料中止整個 coin loop、波及本輪其他健康的幣。
        try:
            snap = _snapshot_dict(coin, report, evidence)
        except Exception as exc:  # noqa: BLE001 — 單幣快照組裝失敗只跳過該
            # 幣、不中斷其餘幣別，且不寫入任何值——#24 鐵律：不得用假值填補
            # 失敗的分析結果。
            print(f"[fetch_scheduler] --snapshot {coin}: _snapshot_dict() 失敗，"
                  f"略過（{exc}）", file=sys.stderr)
            failures.append(coin)
            continue

        # codex HIGH（PR #59 review 第五輪，#1 durability 最終閉合）：
        # **先寫 history、才寫 latest/overview**——history 是「按日累積、
        # 一旦某天完全沒寫到就永久補不回」的不可復原資料（point-in-time
        # 序列），latest/overview 則是 last-write-wins、下一輪排程一定會
        # 自然覆蓋回最新值的可自癒資料。把不可復原的那個放前面寫，才能讓
        # 「history 寫完、latest 還沒寫」之間發生 crash 時，這一天這一幣
        # 的歷史已經 durable 保住，不會因為程序被砍斷而永久遺失（見本函式
        # docstring 完整說明；先前第三、四輪是反過來先寫 latest，若 crash
        # 剛好卡在 latest 寫完、history 寫前，且下一輪已跨過 UTC 日界，
        # 前一天的 history 就會永久遺失，不可復原）。
        #
        # task #26：按日累積歷史——同一天多次跑對同一把 key 覆寫
        # （upsert），跨日才會因日期不同而各自成一筆，天然累積成序列。用
        # 跟 latest/overview 同一個 `run_now`，避免同一輪內兩次
        # `time.time()` 剛好跨過 UTC 午夜造成「同一次真呼叫，兩把 key
        # 寫進不同日期」的邊界亂跳。
        # codex HIGH（PR #59 review 第七輪，跨 backend 分裂 class 徹底
        # 閉合）：明確傳 `allow_json_fallback=False`——snapshot 這三個
        # 表示（history/latest/overview）只認 primary backend 是否真的
        # 寫成功，完全關掉 cross-backend fallback（見本函式 docstring
        # 完整說明）。一般 cache（連接器資料）呼叫端不受影響，`False`
        # 是這裡明確傳入、不是改預設值。
        history_result = cache_set_if_newer(
            backend, trust_snapshot_history_key(coin, snapshot_history_date(run_now)),
            [snap], fetched_at=run_now, ttl_seconds=TRUST_SNAPSHOT_HISTORY_TTL_SECONDS,
            allow_json_fallback=False,
        )
        if not history_result.ok:
            print(f"[fetch_scheduler] --snapshot {coin}: 歷史快照 cache 寫入失敗"
                  f"（backend={history_result.backend}）：{history_result.error}，"
                  f"本輪同步跳過該幣 latest／總覽候選", file=sys.stderr)
            failures.append(f"{coin}:history")
            continue
        if history_result.used_fallback:
            # codex HIGH（PR #59 review 第六輪，backend-affinity 一致性）：
            # primary（DynamoDB）寫入失敗、靠本地 `JsonCacheBackend`
            # fallback 才把 history 寫進去時，`history_result.ok` 仍是
            # `True`（fallback 語意上「有真的持久化成功」），但那份資料
            # **只在本機看得到，沒有真正 durable 進 primary**。若接下來
            # 這一幣的 latest/overview 卻正常寫進 primary（例如 DynamoDB
            # 剛好在兩次呼叫之間恢復），primary 就會出現「latest/overview
            # 是新的，但 history 根本不存在」的跨 backend 分裂——一旦排程
            # 跨過 UTC 日界，那天的 PIT 在 primary 端就永久缺角，而且因為
            # `history_result.ok` 是 `True`，這個分裂完全不會被
            # `failures` 抓到、run 還是 exit 0，不會有人發現。
            #
            # 第七輪起：上面呼叫已經明確傳 `allow_json_fallback=False`，
            # 這個分支結構上**已經不可能再被觸發**（`_json_fallback_enabled`
            # 收到明確 `False` 時一律直接回 `ok=False`，不會嘗試 fallback）
            # ——保留這段檢查純粹是 defense-in-depth：如果未來有人不小心
            # 在上面的呼叫漏掉 `allow_json_fallback=False`，這裡還是能攔住，
            # 不讓「跨 backend 分裂」這個 class 因為一次疏忽就重新打開。
            #
            # 修法：把「history 沒有真正 durable 進 primary」視同 gating
            # 失敗——跟 `not history_result.ok` 一樣處理：跳過這一幣的
            # latest/overview（不寫進任何 backend，避免製造分裂），並計入
            # `failures`（讓監控看得到，逼出 DynamoDB 的問題，下一輪
            # DynamoDB 恢復後會自然重寫回 primary、三表示補齊）。刻意
            # **不**額外把 latest/overview 也寫進 JSON fallback——因為
            # snapshot 三表示本來就設計成三個要嘛同時在同一個 backend
            # 達成一致、要嘛這一幣這一輪整個不處理，不做「history 在
            # fallback、latest 在 fallback」這種本輪各自獨立 fallback、
            # 之後又要對兩個 backend 分別追蹤一致性的複雜度。
            print(f"[fetch_scheduler] --snapshot {coin}: 歷史快照走本地 JSON "
                  f"fallback 寫入（primary 失敗：{history_result.error}），"
                  f"視為未真正 durable 進 primary——本輪同步跳過該幣 "
                  f"latest／總覽候選、計入 failures，避免 primary 出現 "
                  f"history 缺角但 latest/overview 卻已更新的跨 backend 分裂",
                  file=sys.stderr)
            failures.append(f"{coin}:history-fallback")
            continue
        if history_result.skipped:
            # 單調條件寫入判斷 incoming 比當日既有值舊（或一樣新）而主動
            # 跳過——這是正確行為（避免覆蓋較新的值），不是失敗，不計入
            # failures。per-coin all-or-nothing 收窄（第五輪起改以 history
            # 這次的 CAS 結果為準）：history 跳過時，同步跳過這一幣的
            # latest 寫入、也不把它納入這輪的總覽候選，讓三個表示對「這一
            # 幣該不該反映本輪資料」的判斷收斂成同一個答案。
            print(f"[fetch_scheduler] --snapshot {coin}: 歷史快照跳過寫入"
                  f"（當日已有較新或同時的快照，fetched_at={run_now:.0f} 未覆寫，"
                  f"本輪同步跳過該幣 latest／總覽候選）")
            skipped_coins.append(coin)
            continue
        # 走到這裡代表 `history_result.used_fallback` 一定是 `False`
        # （上面已經攔截並 `continue` 掉 fallback 的情況）——history 這次
        # 真的成功 durable 進 primary，不需要再另外呼叫
        # `_warn_if_fallback_used()`。

        # history 已經 durable 保住這一幣這一天的資料，才輪到寫
        # last-write-wins、可自癒的 latest。`latest` 是全域 key（不分日
        # 期），可能還在跟其他天的另一輪排程競爭，即使 history 贏了也不能
        # 無條件覆寫 latest，仍必須是條件寫入。
        result = cache_set_if_newer(
            backend, cache_key(TRUST_SNAPSHOT_SOURCE, coin), [snap],
            fetched_at=run_now, ttl_seconds=SNAPSHOT_STALE_AFTER_SECONDS,
            allow_json_fallback=False,
        )
        if not result.ok:
            print(f"[fetch_scheduler] --snapshot {coin}: cache 寫入失敗"
                  f"（backend={result.backend}）：{result.error}", file=sys.stderr)
            failures.append(coin)
            continue
        if result.used_fallback:
            # codex HIGH（PR #59 review 第七輪，跨 backend 分裂 class
            # 徹底閉合）：history 已經 durable 進 primary，但這一幣的
            # latest 這次卻只走了本地 JSON fallback（沒真正進
            # primary）——若這裡當成功繼續往下納入總覽候選，總覽 blob
            # 若接著正常寫進 primary，primary 端就會出現「history/總覽
            # 是新的，但 latest 沒真的更新」的跨 backend 分裂。上面已經
            # 明確傳 `allow_json_fallback=False`，結構上這裡已經不可能
            # 被觸發（保留純屬 defense-in-depth，見 history 那段同款
            # 注解）。視同失敗處理：不納入總覽候選、計入 failures。
            print(f"[fetch_scheduler] --snapshot {coin}: 最新一筆快照走本地 "
                  f"JSON fallback 寫入（primary 失敗：{result.error}），視為"
                  f"未真正 durable 進 primary——不納入總覽候選、計入 failures",
                  file=sys.stderr)
            failures.append(f"{coin}:latest-fallback")
            continue
        if result.skipped:
            # 極罕見：history 贏了（這一天這一幣目前最新），但 latest
            # 這個全域 key 已經被另一輪（處理不同天、run_now 更新）的排程
            # 搶先寫入更新的值。這是正常、可自癒的暫態（下一輪排程一定會
            # 自然覆蓋回真正最新），不計入 failures；但也不把這筆已經不是
            # 「目前最新」的資料納入總覽候選，避免總覽顯示比 latest 實際
            # 內容還舊的值。
            print(f"[fetch_scheduler] --snapshot {coin}: 最新一筆快照跳過寫入"
                  f"（已有較新或同時的快照，fetched_at={run_now:.0f} 未覆寫，"
                  f"history 已保住當日資料，本輪不納入總覽候選）")
            skipped_coins.append(coin)
            continue
        _warn_if_fallback_used(f"--snapshot {coin}", result)
        print(f"[fetch_scheduler] --snapshot {coin}: trust_score="
              f"{snap['trust_score']:.2f} direction={snap['direction']} 已寫入快取")
        snapshots.append(snap)

    overview_html = _render_overview_html(snapshots)
    if overview_html:
        # codex HIGH（PR #59 review 第三輪）：總覽 blob 也改 `cache_set_if_newer()`
        # 並沿用跟 latest/history 同一個 `run_now`（不再另外呼叫一次
        # `time.time()`）——三者共用同一把時間戳，確保同一輪要嘛三個表示
        # 全部覆寫成功、要嘛全部因為比既有值舊而跳過，不會出現「latest/總覽
        # 被較舊一輪蓋掉、history 卻正確跳過」的表示間矛盾（見本函式
        # docstring）。
        # codex HIGH（PR #59 review 第七輪）：總覽 blob 也明確傳
        # `allow_json_fallback=False`——理由同 history/latest（見本函式
        # docstring），三個 snapshot 表示只認 primary backend 是否真的
        # 寫成功，完全關掉這條路徑的 cross-backend fallback。
        overview_result = cache_set_if_newer(
            backend, cache_key(TRUST_OVERVIEW_SOURCE, TRUST_OVERVIEW_COIN),
            [{"html": overview_html}],
            fetched_at=run_now, ttl_seconds=SNAPSHOT_STALE_AFTER_SECONDS,
            allow_json_fallback=False,
        )
        if not overview_result.ok:
            print(f"[fetch_scheduler] --snapshot: 總覽 blob 寫入失敗"
                  f"（backend={overview_result.backend}）：{overview_result.error}",
                  file=sys.stderr)
            failures.append("__trust_overview_html__")
        elif overview_result.used_fallback:
            # 同上面 latest 那段：`allow_json_fallback=False` 讓這裡結構上
            # 不可能被觸發，純屬 defense-in-depth。總覽走 fallback 代表
            # 沒真正 durable 進 primary——若當成功放過，primary 端就會
            # 出現「latest/history 是新的，但總覽 blob 沒真的更新」的
            # 跨 backend 分裂。視同失敗計入 failures。
            print(f"[fetch_scheduler] --snapshot: 總覽 blob 走本地 JSON "
                  f"fallback 寫入（primary 失敗：{overview_result.error}），"
                  f"視為未真正 durable 進 primary，計入 failures",
                  file=sys.stderr)
            failures.append("__trust_overview_html__:fallback")
        elif overview_result.skipped:
            print(f"[fetch_scheduler] --snapshot: 總覽 blob 跳過寫入"
                  f"（已有較新或同時的總覽，fetched_at={run_now:.0f} 未覆寫）")
        else:
            print(f"[fetch_scheduler] --snapshot: 總覽 blob 已寫入（{len(snapshots)} 幣）")
    elif skipped_coins and not failures:
        # codex HIGH（PR #59 review 第四輪）：本輪 0 幣成功，但**不是失敗**
        # ——全部幣的 latest 都被單調條件寫入判斷為「比既有值舊」而跳過，
        # 代表有另一輪更新的並行排程已經處理過這批幣（見上方 `skipped_coins`
        # 累積邏輯），系統運作正常、資料已經是最新，不該被回傳碼誤判成
        # 「這輪排程失敗」而觸發假警報（尤其排程重疊是這整組修正本來就要
        # 正常處理、不是異常的情境）。
        print(f"[fetch_scheduler] --snapshot: {len(skipped_coins)}/{len(coins)} 幣"
              "本輪跳過寫入（已有更新的並行排程結果，非失敗），跳過總覽 blob 寫入")
    else:
        # 本輪 0 幣成功、且不是「全部因為被更新的並行排程超車而跳過」
        # ——是真的沒有任何一幣產出可用結果（pipeline 失敗／cache 寫入失敗
        # 等），沒有東西可組總覽，不寫入（不留舊 blob 誤導，靠既有 TTL 讓
        # 上一輪殘留的 blob 自然過期）——非 bug，等下一輪（見 PLAN「風險」
        # 段落：冷啟動時 15 分鐘 cadence 若跟 5 幣 collect 同時全部
        # cache-miss，快照本來就該是空，首頁總覽該次不顯示）。
        print("[fetch_scheduler] --snapshot: 0 幣成功，跳過總覽 blob 寫入"
              "（非 bug，等下一輪）", file=sys.stderr)
        failures.append("__trust_overview_html__:no-snapshots")

    print(f"[fetch_scheduler] --snapshot 完成：{len(snapshots)}/{len(coins)} 幣成功寫入快照。")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument(
        "--source", action="append", dest="sources", metavar="NAME",
        help="只跑指定來源（可重複；預設全部已知來源，見 --list-sources）",
    )
    parser.add_argument(
        "--coin", action="append", dest="coins", metavar="COIN",
        help="只跑指定幣別（可重複；預設 COIN_POOL 全部 5 幣）",
    )
    parser.add_argument(
        "--interval", type=float, default=None,
        help="覆寫本次執行目標來源的 refresh 間隔（秒，用於新鮮度守門 + 換算硬過期"
             "時限）；未指定則用各來源 DEFAULT_REFRESH_INTERVAL_SECONDS（見 cache.py）",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="忽略新鮮度守門，強制重新呼叫真 API（節制使用，避免 429，尤其 reddit）",
    )
    parser.add_argument(
        "--stagger", type=float, default=1.0,
        help="同一來源內逐幣呼叫之間的間隔秒數，避免瞬間爆量（預設 1 秒；0 關閉；"
             "CoinGecko 逐幣來源另有 6 秒下限，取兩者較大值，見 "
             "_COINGECKO_STAGGER_FLOOR_SECONDS）",
    )
    parser.add_argument("--parallelism", type=int, default=1, help="bounded concurrent source workers (default: 1)")
    parser.add_argument("--total-budget-sec", type=float, default=15 * 60, help="whole fetch-cycle timeout (max: 900)")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只列出這次會呼叫哪些 (來源, 幣別)，不真的打 API、不寫快取",
    )
    parser.add_argument(
        "--list-sources", action="store_true",
        help="列出所有已知來源名稱後結束（不打任何 API）",
    )
    parser.add_argument(
        "--probe", action="store_true",
        help="DynamoDB cache/cost-ledger 兩表的 R/W canary probe（保留 key，"
             "PutItem→GetItem→驗證讀回，cost-ledger 額外驗 PutItem）；完全不依賴"
             "任何來源新鮮度或外部 API，任一步失敗（含被 IAM 拒絕）即非零退出。"
             "供 deploy 部署後同步健康檢查用，取代『跑一次可能因 cache 全新鮮"
             "而 0 次真呼叫仍 exit 0』的舊驗法（codex HIGH）",
    )
    parser.add_argument(
        "--snapshot", action="store_true",
        help="Axis C #1（task #23）：多幣信任快照寫入者——獨立分支，不打任何"
             "真連接器 API／Bedrock，對 --coin 指定（預設 COIN_POOL 5 幣）各跑"
             "1 次 real-off pipeline.run(data_mode=live, llm_mode=off)（純讀"
             "既有 cache 運算，$0），把精華結果寫入 __trust_snapshot__:{coin}"
             "與總覽 blob __trust_overview_html__，供首頁正確讀路徑使用；建議"
             "獨立 cron line、cadence 見 SNAPSHOT_REFRESH_INTERVAL_SECONDS。"
             "可與 --dry-run 合併使用，只列出會跑哪些幣、不真的呼叫",
    )
    parser.add_argument(
        "--allow-partial", action="store_true",
        help="排程服務模式：至少一個目標成功時將上游部分失敗視為 degraded "
             "success；零成功仍非零退出。production unit 必須先以 --probe "
             "驗證 cache/ledger 基建，避免把 DynamoDB 故障誤當上游降級。",
    )
    args = parser.parse_args(argv)

    if args.parallelism < 1:
        parser.error("--parallelism must be >= 1")
    if not 1 <= args.total_budget_sec <= 15 * 60:
        parser.error("--total-budget-sec must be 1..900")

    if args.probe:
        return run_probe()

    coins = args.coins if args.coins else list(COIN_POOL)

    if args.snapshot:
        backend = get_cache_backend()
        return run_snapshot(coins, backend, args.dry_run)

    registry = build_registry()
    if args.list_sources:
        for name in sorted(registry):
            print(name)
        return 0

    if not args.dry_run:
        # Scheduler is a separate process from the admin web service. Load the
        # durable controls immediately before a real fetch; a read failure is
        # intentionally fatal so an unknown disable state cannot result in
        # external calls or a falsely fresh cache. Introspection and dry-run
        # remain network-free by contract.
        sync_source_enabled_from_admin()
        log_hoyabit_startup_status()
        registry = build_registry()

    interval_overrides: dict[str, float] = {}
    if args.interval is not None:
        target_names = args.sources if args.sources else list(registry)
        interval_overrides = {n: args.interval for n in target_names}

    backend = get_cache_backend()
    results, failures = run_once_parallel(
        args.sources, coins, backend, args.force, interval_overrides, args.stagger, args.dry_run,
        max_workers=args.parallelism, total_budget_sec=args.total_budget_sec,
    )
    total_docs = sum(n for _, n in results)
    print(f"[fetch_scheduler] 完成：{len(results)} 個 (來源,幣別) 目標實際呼叫/廣播成功，"
          f"共 {total_docs} 筆文件寫入快取。")

    # Phase3：收尾寫一筆輕量 run record，供 `/status` 顯示「最近排程執行」。
    # --dry-run 沒有真的呼叫/寫入任何東西，不記錄，避免誤導成「有一輪真執行」。
    # ⚠️ append_scheduler_run() 內部已把所有例外吞掉只印警告，不會 raise——
    # 這裡故意不包 try/except：run log 寫入失敗與否，都不該影響下面依
    # `failures`（真呼叫/cache 寫入是否成功）決定的 exit code 語意。
    if not args.dry_run:
        # 成本會計階段2：逐源呼叫數（供 `/status`「連接器用量」表彙總用）。
        # `results` 標籤是 `"{name}"`（coin-agnostic 廣播，1 筆＝1 次真呼叫）
        # 或 `"{name}:{coin}"`（逐幣，1 筆＝該幣的 1 次真呼叫），資料完全來自
        # 既有 `run_once()` 回傳，不新增任何外呼——純粹把已經拿到的結果重新
        # 依來源名稱分組計數。來源名稱本身不含 ":"（見各 ingestion/*.py
        # `name =` 定義），用 `split(":", 1)[0]` 取來源名稱是安全的。
        source_calls: dict[str, int] = {}
        for label, _ndocs in results:
            src_name = label.split(":", 1)[0]
            source_calls[src_name] = source_calls.get(src_name, 0) + 1

        append_scheduler_run({
            "targets": args.sources if args.sources else sorted(registry),
            "coins": coins,
            "success_count": len(results),
            "failure_count": len(failures),
            "failures": failures,
            "total_docs": total_docs,
            "source_calls": source_calls,
        })

    if failures:
        # codex HIGH-1：failures 現在同時涵蓋「真呼叫本身失敗」（逾時/429/憑證錯/
        # 上游故障）與「真呼叫成功但 cache 寫入失敗」兩種情況——只要有任一目標
        # 這次沒真的刷新成功，就不能讓 exit code 回 0（見上方各 WARNING/錯誤訊息
        # 判斷是哪一種）。
        print(f"[fetch_scheduler] 有 {len(failures)} 個目標本次未成功刷新快取"
              f"（真呼叫失敗或真呼叫成功但 cache 寫入失敗，細節見上方訊息）："
              f"{failures}", file=sys.stderr)
        if args.allow_partial and results:
            print(
                "[fetch_scheduler] 部分來源降級，但已有成功資料寫入；"
                "--allow-partial 回傳成功，完整缺口已保存在 scheduler run log。",
                file=sys.stderr,
            )
            return 0
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
