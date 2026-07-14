"""成本會計階段2：`scripts/fetch_scheduler.py::main()` 收尾寫入
`scheduler_log` 的 `source_calls` 逐源計數測試。

credit-safe：全程 monkeypatch `run_once`/`get_cache_backend`，不觸發任何真
連接器 API 呼叫；只驗證「`results` → `source_calls` 分組計數」這段純邏輯、
以及最終真的把 `source_calls` 傳給 `append_scheduler_run()`。
"""
from __future__ import annotations

import time

from scripts import fetch_scheduler


def test_main_writes_source_calls_grouped_by_source(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_run_once(source_names, coins, backend, force, interval_overrides, stagger, dry_run):
        # 模擬：coingecko-price 對 3 幣各成功 1 次；alternative-me-fng 是
        # coin-agnostic，廣播成功寫入 2 幣（標籤不含 ":"，1 筆＝1 次真呼叫）。
        return (
            [
                ("coingecko-price:BTC", 1),
                ("coingecko-price:ETH", 1),
                ("coingecko-price:SOL", 1),
                ("alternative-me-fng", 2),
            ],
            [],
        )

    def fake_append_scheduler_run(record):
        captured.update(record)

    monkeypatch.setattr(fetch_scheduler, "run_once", fake_run_once)
    monkeypatch.setattr(fetch_scheduler, "get_cache_backend", lambda: object())
    monkeypatch.setattr(fetch_scheduler, "append_scheduler_run", fake_append_scheduler_run)
    monkeypatch.setattr(
        fetch_scheduler, "build_registry",
        lambda: {"coingecko-price": object(), "alternative-me-fng": object()},
    )

    rc = fetch_scheduler.main(["--source", "coingecko-price", "--source", "alternative-me-fng"])

    assert rc == 0
    assert captured["source_calls"] == {"coingecko-price": 3, "alternative-me-fng": 1}


def test_main_dry_run_does_not_write_scheduler_run_log(monkeypatch):
    """`--dry-run` 沒有真的呼叫/寫入，不該留下一筆 run record（避免誤導成
    「有一輪真執行」，見 main() 內既有註解）。"""
    calls = {"n": 0}

    def fake_run_once(*args, **kwargs):
        return ([], [])

    def fake_append_scheduler_run(record):
        calls["n"] += 1

    monkeypatch.setattr(fetch_scheduler, "run_once", fake_run_once)
    monkeypatch.setattr(fetch_scheduler, "get_cache_backend", lambda: object())
    monkeypatch.setattr(fetch_scheduler, "append_scheduler_run", fake_append_scheduler_run)
    monkeypatch.setattr(fetch_scheduler, "build_registry", lambda: {"coindesk": object()})

    rc = fetch_scheduler.main(["--dry-run"])

    assert rc == 0
    assert calls["n"] == 0


def test_main_source_calls_empty_when_no_results(monkeypatch):
    captured: dict = {}

    monkeypatch.setattr(fetch_scheduler, "run_once", lambda *a, **k: ([], []))
    monkeypatch.setattr(fetch_scheduler, "get_cache_backend", lambda: object())
    monkeypatch.setattr(
        fetch_scheduler, "append_scheduler_run", lambda record: captured.update(record)
    )
    monkeypatch.setattr(fetch_scheduler, "build_registry", lambda: {"coindesk": object()})

    rc = fetch_scheduler.main([])

    assert rc == 0
    assert captured["source_calls"] == {}


def test_allow_partial_returns_success_when_some_targets_refresh(monkeypatch):
    monkeypatch.setattr(
        fetch_scheduler,
        "run_once",
        lambda *a, **k: ([("coindesk:BTC", 3)], ["reddit-cryptocurrency:BTC"]),
    )
    monkeypatch.setattr(fetch_scheduler, "get_cache_backend", lambda: object())
    monkeypatch.setattr(fetch_scheduler, "append_scheduler_run", lambda _record: None)
    monkeypatch.setattr(
        fetch_scheduler,
        "build_registry",
        lambda: {"coindesk": object(), "reddit-cryptocurrency": object()},
    )

    assert fetch_scheduler.main(["--allow-partial"]) == 0


def test_allow_partial_still_fails_when_nothing_refreshes(monkeypatch):
    monkeypatch.setattr(
        fetch_scheduler,
        "run_once",
        lambda *a, **k: ([], ["coindesk:BTC", "reddit-cryptocurrency:BTC"]),
    )
    monkeypatch.setattr(fetch_scheduler, "get_cache_backend", lambda: object())
    monkeypatch.setattr(fetch_scheduler, "append_scheduler_run", lambda _record: None)
    monkeypatch.setattr(
        fetch_scheduler,
        "build_registry",
        lambda: {"coindesk": object(), "reddit-cryptocurrency": object()},
    )

    assert fetch_scheduler.main(["--allow-partial"]) == 1


def test_parallel_prefetch_runs_source_workers_concurrently(monkeypatch):
    """來源是 ownership 邊界，但不同來源不可悄悄退化為序列執行。"""

    def fake_run_once(source_names, coins, backend, force, interval_overrides, stagger, dry_run):
        time.sleep(0.2)
        return [(source_names[0], 1)], []

    monkeypatch.setattr(fetch_scheduler, "run_once", fake_run_once)
    monkeypatch.setattr(
        fetch_scheduler,
        "build_registry",
        lambda: {"price": object(), "news": object(), "onchain": object()},
    )

    started = time.monotonic()
    results, failures = fetch_scheduler.run_once_parallel(
        ["price", "news", "onchain"], ["BTC"], object(), False, {}, 0, False,
        max_workers=3,
        total_budget_sec=5,
    )
    elapsed = time.monotonic() - started

    assert sorted(results) == [("news", 1), ("onchain", 1), ("price", 1)]
    assert failures == []
    # Three sequential 0.2s source calls need about 0.6s; a three-worker cycle
    # should complete near one worker duration, leaving CI scheduling headroom.
    assert elapsed < 0.45
