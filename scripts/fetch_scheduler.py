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
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from trustforge.ingestion.base import Source  # noqa: E402
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
    args = parser.parse_args(argv)

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
