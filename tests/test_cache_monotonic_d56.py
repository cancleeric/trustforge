"""#56 跨快取單調性通用修——doc 層級「時光不倒流」測試。

核心保證：供應商若回傳比快取還舊的 doc（舊版 doc / 時光倒流），該 doc 被
擋下、不回灌；既有的較新值保留。覆蓋：
  1. `merge_docs_monotonic()` 單元：個別 doc 的 `ts` 只允許變新、不允許變舊。
  2. `cache_set_monotonic()`：寫入層確實擋下舊 doc，且不因 stale 資料刷新
     `fetched_at`（誠實標示「無新內容」→ skipped）。
  3. 排程層：`fetch_scheduler.run_once()` 接到 `cache_set_monotonic` 後，
     對一個「先寫過新 doc、再回傳舊 doc」的來源，快取內容不被回灌。
全程不打真連接器 API、不打真 AWS。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from trustforge.ingestion.base import Document
from trustforge.ingestion.cache import (
    JsonCacheBackend,
    cache_set_monotonic,
    doc_to_dict,
    merge_docs_monotonic,
)

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO / "scripts" / "fetch_scheduler.py"
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("fetch_scheduler_d56", _SCRIPT_PATH)
fetch_scheduler = importlib.util.module_from_spec(_spec)
sys.modules["fetch_scheduler_d56"] = fetch_scheduler
_spec.loader.exec_module(fetch_scheduler)


def _doc(did: str, ts: float, text: str = "x") -> dict:
    return doc_to_dict(Document(id=did, kind="news", source="coindesk", text=text, ts=ts))


# ---------------------------------------------------------------------------
# 1. merge_docs_monotonic 單元
# ---------------------------------------------------------------------------
def test_merge_blocks_older_doc_and_keeps_newer():
    existing = [_doc("a", 100.0, "new-a"), _doc("b", 50.0, "b")]
    incoming = [_doc("a", 50.0, "old-a"), _doc("c", 200.0, "c")]
    merged, blocked = merge_docs_monotonic(existing, incoming)
    by_id = {d["id"]: d for d in merged}
    # a 的舊版被擋下，保留既有較新的 "new-a" @ ts=100
    assert by_id["a"]["ts"] == 100.0 and by_id["a"]["text"] == "new-a"
    # b 不在 incoming，保持
    assert by_id["b"]["ts"] == 50.0
    # c 為全新 id，收錄
    assert by_id["c"]["ts"] == 200.0
    assert blocked == 1
    # 順序：既有 (a,b) 在前，新 id (c) 在後
    assert [d["id"] for d in merged] == ["a", "b", "c"]


def test_merge_allows_equal_or_newer_ts():
    existing = [_doc("a", 100.0, "old")]
    incoming = [_doc("a", 100.0, "same-ts-update"), _doc("a", 150.0, "newer")]
    # 兩次呼叫分別：相等取 incoming、更新取 incoming
    merged_eq, blocked_eq = merge_docs_monotonic(existing, [incoming[0]])
    assert merged_eq[0]["text"] == "same-ts-update" and blocked_eq == 0
    merged_newer, blocked_newer = merge_docs_monotonic(existing, [incoming[1]])
    assert merged_newer[0]["ts"] == 150.0 and blocked_newer == 0


def test_merge_no_existing_treats_all_incoming_as_new():
    merged, blocked = merge_docs_monotonic([], [_doc("a", 10.0)])
    assert len(merged) == 1 and blocked == 0


# ---------------------------------------------------------------------------
# 2. cache_set_monotonic 寫入層
# ---------------------------------------------------------------------------
def test_cache_set_monotonic_blocks_stale_doc_and_does_not_refresh_fetched_at(tmp_path):
    backend = JsonCacheBackend(tmp_path / "c.json")
    key = "coindesk:BTC"
    # 第一輪：寫入較新 doc（ts=100），fetched_at=1000
    r1 = cache_set_monotonic(backend, key, [_doc("a", 100.0, "new")], fetched_at=1000.0)
    assert r1.ok and r1.monotonic_blocked == 0 and not r1.skipped

    # 第二輪：供應商回舊版 doc（ts=50），fetched_at=2000（想刷新新鮮窗）
    r2 = cache_set_monotonic(backend, key, [_doc("a", 50.0, "old")], fetched_at=2000.0)
    assert r2.ok and r2.monotonic_blocked == 1 and r2.skipped
    # 快取內容不被回灌
    entry = backend.get(key)
    docs = entry["docs"]
    assert docs[0]["ts"] == 100.0 and docs[0]["text"] == "new"
    # fetched_at 不應被 stale 資料刷新（仍為第一輪的 1000）
    assert entry["fetched_at"] == 1000.0


def test_cache_set_monotonic_writes_newer_doc_and_refreshes(tmp_path):
    backend = JsonCacheBackend(tmp_path / "c2.json")
    key = "coindesk:BTC"
    cache_set_monotonic(backend, key, [_doc("a", 100.0, "old")], fetched_at=1000.0)
    r = cache_set_monotonic(backend, key, [_doc("a", 200.0, "newer")], fetched_at=2000.0)
    assert r.ok and r.monotonic_blocked == 0 and not r.skipped
    entry = backend.get(key)
    assert entry["docs"][0]["ts"] == 200.0 and entry["fetched_at"] == 2000.0


def test_cache_set_monotonic_records_mixed_batch_blocked_count(tmp_path):
    backend = JsonCacheBackend(tmp_path / "c3.json")
    key = "coindesk:BTC"
    cache_set_monotonic(backend, key, [_doc("a", 100.0), _doc("b", 100.0)], fetched_at=1.0)
    # a 回舊版（ts=50），b 回新（ts=150），c 全新
    r = cache_set_monotonic(
        backend, key,
        [_doc("a", 50.0, "old-a"), _doc("b", 150.0, "new-b"), _doc("c", 120.0, "c")],
        fetched_at=2.0,
    )
    assert r.monotonic_blocked == 1  # 只有 a 被擋
    by_id = {d["id"]: d for d in backend.get(key)["docs"]}
    assert by_id["a"]["ts"] == 100.0  # 舊版沒回灌
    assert by_id["b"]["ts"] == 150.0  # 更新成功
    assert "c" in by_id  # 新 id 收錄


# ---------------------------------------------------------------------------
# 3. 排程層整合：run_once 接到 cache_set_monotonic 後不回灌舊 doc
# ---------------------------------------------------------------------------
def test_scheduler_run_once_does_not_regress_stale_docs(monkeypatch, tmp_path):
    """構造：先讓排程把「新 doc（ts=100）」寫進快取；再讓同一來源回傳「舊
    doc（ts=50）」，run_once 應保留 ts=100 的值、不回灌。"""
    from trustforge.ingestion.base import Source as _Source

    class _FakeSource(_Source):
        def __init__(self, docs):
            self.name = "coindesk"
            self.kind = "news"
            self._docs = docs

        def fetch(self, query: str, coin: str = "") -> list:  # noqa: ANN001
            return self._docs

    # 第一輪：寫新 doc
    backend = JsonCacheBackend(tmp_path / "sched.json")
    src_new = _FakeSource([Document(id="a", kind="news", source="coindesk", text="new", ts=100.0)])
    monkeypatch.setattr(fetch_scheduler, "build_registry", lambda: {"coindesk": src_new})
    monkeypatch.setattr(fetch_scheduler, "get_cache_backend", lambda: backend)

    results, failures = fetch_scheduler.run_once(
        ["coindesk"], ["BTC"], backend, force=True,
        interval_overrides={}, stagger=0, dry_run=False,
    )
    assert failures == [] and results == [("coindesk:BTC", 1)]

    # 第二輪：同一來源回舊 doc
    src_old = _FakeSource([Document(id="a", kind="news", source="coindesk", text="old", ts=50.0)])
    monkeypatch.setattr(fetch_scheduler, "build_registry", lambda: {"coindesk": src_old})
    results2, failures2 = fetch_scheduler.run_once(
        ["coindesk"], ["BTC"], backend, force=True,
        interval_overrides={}, stagger=0, dry_run=False,
    )
    assert failures2 == []
    entry = backend.get("coindesk:BTC")
    assert entry["docs"][0]["ts"] == 100.0 and entry["docs"][0]["text"] == "new"
