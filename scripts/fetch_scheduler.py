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
from trustforge.ingestion.news import build_news_sources  # noqa: E402
from trustforge.ingestion.onchain import build_onchain_sources  # noqa: E402
from trustforge.ingestion.regulatory import build_regulatory_sources  # noqa: E402
from trustforge.ingestion.social import build_social_sources  # noqa: E402
from trustforge.ledger import get_ledger  # noqa: E402
from trustforge.schema import COIN_POOL  # noqa: E402


def build_registry() -> dict[str, Source]:
    """建立「來源名稱 → 真 Source 實例」對照表。純建構，不打任何網路（各
    `Source.__init__` 都不連線，見各連接器實作）。"""
    sources: list[Source] = (
        build_news_sources()
        + build_onchain_sources()
        + build_social_sources()
        + build_regulatory_sources()
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
            if stagger > 0:
                time.sleep(stagger)

    return results, failures


_PROBE_SOURCE = "__fetch_scheduler_probe__"
_PROBE_COIN = "PROBE"


def run_probe() -> int:
    """DynamoDB R/W canary probe（codex HIGH-3 修正）。

    背景：`verify_fetch_scheduler`（`deploy/deploy_ec2.sh`）原本只是同步跑一次
    普通排程（`main()` 不帶 `--probe`），但普通排程對每個來源都先過新鮮度
    守門（`_is_fresh()`）——若剛好碰上 cache 全新鮮（如剛部署完、上一輪才
    成功寫過），本次執行對所有來源全部「略過」、0 次真呼叫、0 次 PutItem，
    仍然 `exit 0`。這樣一來，若 IAM 權限被 permission boundary / SCP / table
    resource policy 擋掉（GetItem 還過得去，只有 PutItem 被拒），只要當下
    cache 恰好新鮮，`verify_fetch_scheduler` 就會誤判成功，直到下一輪真的
    需要刷新（cache 過期）時才會開始每次 exit 1——正好繞過本來要防的東西。

    修法：完全不碰任何真連接器 API、不看任何來源的新鮮度，直接對兩個表各做
    一次**保證真的會發生**的 R/W：
      - cache 表：對保留的 canary key（`__fetch_scheduler_probe__:PROBE`，
        不會跟真實來源撞名）做 `set()`（PutItem）→`get()`（GetItem）→比對
        讀回內容是否等於剛寫入的 sentinel。任一步丟例外，或讀回內容對不上
        （可能是背景讀到舊資料、或其實根本沒寫進去卻沒丟例外的邊界情況），
        都視為失敗。
      - cost-ledger 表：對固定 `run_id="__fetch_scheduler_probe__"` 的一筆
        record 做 `append()`（PutItem），驗證寫入本身不丟例外即可（`Ledger`
        介面沒有「依 key 讀單筆」，`read_all()` 是全表 scan + 合併 JSONL
        fallback，拿來當輕量 canary 的讀回驗證太重也太容易被 fallback 掩蓋，
        因此本 probe 只驗證 PutItem，不驗證讀回）。
      - 兩個 canary key 都固定、冪等（重跑覆寫同一筆，不會無限堆積垃圾資料）。

    刻意**不透過** `cache_get()`/`cache_set()` 高階便利函式：它們對讀/寫
    失敗各自有 fallback/降級語意（見 `cache.py` 模組頂部與 `CacheWriteResult`
    docstring），目的是讓「產品路徑」在 primary backend 故障時還能盡量堪用；
    但這正是 probe 要拆穿的東西——probe 要問的是「primary backend（實際配置
    的 `CACHE_BACKEND`/`COST_LEDGER_BACKEND`）本身能不能真的讀寫」，不能被
    這層 fallback 悄悄接住又回報「看起來沒事」。直接呼叫 backend 的低階
    `get()`/`set()`/`append()`，任何例外一律視為 probe 失敗。
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
            entry = cache_backend.get(canary_key)
        except Exception as exc:  # noqa: BLE001
            print(f"[fetch_scheduler] PROBE FAIL：cache GetItem 失敗"
                  f"（backend={type(cache_backend).__name__}）：{exc}", file=sys.stderr)
            ok = False
        else:
            docs = (entry or {}).get("docs") or []
            read_back = docs[0].get("text") if docs else None
            if read_back != sentinel:
                print(f"[fetch_scheduler] PROBE FAIL：cache 讀回內容與剛寫入的不一致"
                      f"（可能讀到舊資料，或寫入其實沒真的落地卻沒丟例外）："
                      f"預期 {sentinel!r}，讀到 {read_back!r}", file=sys.stderr)
                ok = False
            else:
                print(f"[fetch_scheduler] PROBE OK：cache PutItem + GetItem 讀寫一致"
                      f"（backend={type(cache_backend).__name__}）")

    ledger_backend = get_ledger()
    try:
        ledger_backend.append({
            "run_id": _PROBE_SOURCE,
            "total_cost_usd": 0.0,
            "calls": [],
            "note": "fetch_scheduler --probe canary，非真實花費紀錄",
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[fetch_scheduler] PROBE FAIL：cost-ledger PutItem 失敗"
              f"（backend={type(ledger_backend).__name__}）：{exc}", file=sys.stderr)
        ok = False
    else:
        print(f"[fetch_scheduler] PROBE OK：cost-ledger PutItem 成功"
              f"（backend={type(ledger_backend).__name__}）")

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
        help="同一來源內逐幣呼叫之間的間隔秒數，避免瞬間爆量（預設 1 秒；0 關閉）",
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
