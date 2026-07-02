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

    # 切換 cache backend（預設沿用 cache.py 的 CACHE_BACKEND env，dynamodb|json；
    # 預設 dynamodb）。primary backend 寫入失敗時，預設**不會**自動 fallback
    # 寫本地 JSON（避免假成功，見 codex HIGH-2）；exit code 非零代表有目標
    # 沒有真的持久化，cron/監控應據此告警。dev/CI 沒有真 AWS、想要一個真正
    # 能用的本地快取時，才明確開 opt-in：
    CACHE_BACKEND=json python3 scripts/fetch_scheduler.py
    # 或維持 CACHE_BACKEND=dynamodb，但允許失敗時 fallback 寫本地 JSON：
    TRUSTFORGE_CACHE_JSON_FALLBACK=1 python3 scripts/fetch_scheduler.py

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
import os
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from trustforge.ingestion.base import Document, Source  # noqa: E402
from trustforge.ingestion.cache import (  # noqa: E402
    COIN_AGNOSTIC_SOURCES,
    COIN_KEYED_BATCH_SOURCES,
    DEFAULT_REFRESH_INTERVAL_FALLBACK_SECONDS,
    DEFAULT_REFRESH_INTERVAL_SECONDS,
    CacheBackend,
    cache_get,
    cache_key,
    cache_set,
    doc_to_dict,
    get_cache_backend,
    stale_after_for,
)
from trustforge.ingestion.coingecko import build_coingecko_sources  # noqa: E402
from trustforge.ingestion.news import build_news_sources  # noqa: E402
from trustforge.ingestion.onchain import build_onchain_sources  # noqa: E402
from trustforge.ingestion.regulatory import build_regulatory_sources  # noqa: E402
from trustforge.ingestion.social import build_social_sources  # noqa: E402
from trustforge.ledger import DynamoDBLedger, get_ledger  # noqa: E402
from trustforge.schema import COIN_POOL  # noqa: E402
from trustforge.scheduler_log import append_scheduler_run  # noqa: E402


def build_registry() -> dict[str, Source]:
    """建立「來源名稱 → 真 Source 實例」對照表。純建構，不打任何網路（各
    `Source.__init__` 都不連線，見各連接器實作）。"""
    sources: list[Source] = (
        build_news_sources()
        + build_onchain_sources()
        + build_social_sources()
        + build_regulatory_sources()
        + build_coingecko_sources()
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


def run_once(
    source_names: list[str] | None,
    coins: list[str],
    backend: CacheBackend,
    force: bool,
    interval_overrides: dict[str, float],
    stagger: float,
    dry_run: bool,
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

    for name in targets:
        source = registry.get(name)
        if source is None:
            print(f"[fetch_scheduler] 未知來源：{name!r}（略過；"
                  f"可用：{sorted(registry)}）", file=sys.stderr)
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
                docs = source.fetch("", coin="")
            except Exception as exc:  # noqa: BLE001 — 排程任務單點失敗不中斷整批，
                # 但仍要計入 failures（codex HIGH-1）：真呼叫失敗（逾時/429/
                # 憑證錯/上游故障）不能只印警告就當沒事——若全部來源都這樣失敗，
                # main() 必須非零退出，不能讓 cron/監控誤判成功。
                print(f"[fetch_scheduler] {name}: 真呼叫失敗，略過（{exc}）", file=sys.stderr)
                failures.append(name)
                continue
            payload = [doc_to_dict(d) for d in docs]
            now = time.time()
            broadcast_failed: list[str] = []
            for c in coins:
                result = cache_set(
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
                docs = source.fetch("", coin="")
            except Exception as exc:  # noqa: BLE001 — 理由同 coin-agnostic 分支（codex HIGH-1）：
                # 只呼叫一次，失敗也只算一次失敗，不會像舊版逐幣迴圈那樣對同一個
                # 已限流的端點重複觸發（見本函式 docstring「生產事故修復」說明）。
                print(f"[fetch_scheduler] {name}: 真呼叫失敗，略過（{exc}）", file=sys.stderr)
                failures.append(name)
                continue
            now = time.time()
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
                result = cache_set(
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

        for c in coins:
            if not force and _is_fresh(backend, name, c, refresh_interval):
                print(f"[fetch_scheduler] {name}[{c}]: 未達 refresh 間隔（{refresh_interval:.0f}s），略過")
                continue
            if dry_run:
                print(f"[fetch_scheduler] (dry-run) {name}[{c}]: 會呼叫真 API")
                continue
            try:
                docs = source.fetch("", coin=c)
            except Exception as exc:  # noqa: BLE001 — 單點失敗不中斷整批，
                # 但仍要計入 failures（codex HIGH-1），理由同上方 coin-agnostic 分支。
                print(f"[fetch_scheduler] {name}[{c}]: 真呼叫失敗，略過（{exc}）", file=sys.stderr)
                failures.append(f"{name}:{c}")
                continue
            result = cache_set(
                backend, cache_key(name, c), [doc_to_dict(d) for d in docs],
                fetched_at=time.time(), ttl_seconds=stale_after,
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


_PROBE_SOURCE = "__fetch_scheduler_probe__"
_PROBE_COIN = "PROBE"
# ledger canary 固定 ts（不像一般 record 交給 DynamoDBLedger.append() 自動填當下
# 時間）：PK=run_id、SK=ts 都固定，才能讓每次 probe 覆寫同一筆，不會無限堆積。
_PROBE_LEDGER_TS = "1970-01-01T00:00:01+00:00"


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

    cache_backend = get_cache_backend()
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

    ledger_backend = get_ledger()
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
    args = parser.parse_args(argv)

    if args.probe:
        return run_probe()

    registry = build_registry()
    if args.list_sources:
        for name in sorted(registry):
            print(name)
        return 0

    coins = args.coins if args.coins else list(COIN_POOL)
    interval_overrides: dict[str, float] = {}
    if args.interval is not None:
        target_names = args.sources if args.sources else list(registry)
        interval_overrides = {n: args.interval for n in target_names}

    backend = get_cache_backend()
    results, failures = run_once(
        args.sources, coins, backend, args.force, interval_overrides, args.stagger, args.dry_run,
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
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
