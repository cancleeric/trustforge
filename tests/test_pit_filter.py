"""PIT (Point-in-Time) 通用後置過濾器測試（issue #722）。

驗證 `_pit_filter`、`_fetch_with_as_of`、`collect(..., as_of=...)` 的
向後相容與過濾語意。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trustforge.ingestion.base import (
    Document,
    Source,
    _fetch_with_as_of,
    _pit_filter,
    collect,
)


# ── _pit_filter ──────────────────────────────────────────────────────────

def test_pit_filter_none_passes_all() -> None:
    docs = [
        Document(id="a", kind="news", source="x", text="t", ts=1.0),
        Document(id="b", kind="news", source="x", text="t", ts=9999.0),
    ]
    assert _pit_filter(docs, None) == docs


def test_pit_filter_visible_at_epoch_priority() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    past = now - timedelta(days=1)
    future = now + timedelta(days=1)
    docs = [
        Document(id="old", kind="news", source="x", text="t", ts=now.timestamp(),
                 meta={"visible_at_epoch": past.timestamp()}),
        Document(id="new", kind="news", source="x", text="t", ts=past.timestamp(),
                 meta={"visible_at_epoch": future.timestamp()}),
    ]
    result = _pit_filter(docs, now)
    assert len(result) == 1
    assert result[0].id == "old"


def test_pit_filter_fallback_to_ts() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    past = now - timedelta(days=1)
    future = now + timedelta(days=1)
    docs = [
        Document(id="old", kind="news", source="x", text="t", ts=past.timestamp()),
        Document(id="new", kind="news", source="x", text="t", ts=future.timestamp()),
    ]
    result = _pit_filter(docs, now)
    assert len(result) == 1
    assert result[0].id == "old"


def test_pit_filter_excludes_future_docs() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    tomorrow = now + timedelta(days=1)
    docs = [
        Document(id="today", kind="news", source="x", text="t", ts=now.timestamp()),
        Document(id="tomorrow", kind="news", source="x", text="t", ts=tomorrow.timestamp()),
    ]
    result = _pit_filter(docs, now)
    assert [d.id for d in result] == ["today"]


def test_pit_filter_naive_as_of_treated_as_utc() -> None:
    naive = datetime(2026, 1, 1, 12, 0)  # no tzinfo
    ts = naive.replace(tzinfo=timezone.utc).timestamp()
    docs = [
        Document(id="a", kind="news", source="x", text="t", ts=ts),
        Document(id="b", kind="news", source="x", text="t", ts=ts + 10),
    ]
    result = _pit_filter(docs, naive)
    assert [d.id for d in result] == ["a"]


def test_pit_filter_aware_as_of_matches() -> None:
    tokyo = timezone(timedelta(hours=9))
    as_of_tokyo = datetime(2026, 1, 1, 21, 0, tzinfo=tokyo)  # = 12:00 UTC
    ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp()
    docs = [
        Document(id="a", kind="news", source="x", text="t", ts=ts),
        Document(id="b", kind="news", source="x", text="t", ts=ts + 10),
    ]
    result = _pit_filter(docs, as_of_tokyo)
    assert [d.id for d in result] == ["a"]


# ── _fetch_with_as_of ────────────────────────────────────────────────────

def test_fetch_with_as_of_introspection_accepts() -> None:
    class _AsOfSource(Source):
        def __init__(self):
            self.name = "asof-source"
            self.kind = "news"

        def fetch(self, query: str, coin: str = "", *, as_of: datetime | None = None) -> list[Document]:
            return [Document(id="ok", kind="news", source=self.name, text="t", ts=1.0)]

    s = _AsOfSource()
    result = _fetch_with_as_of(s, "q", "BTC", datetime.now(timezone.utc))
    assert len(result) == 1
    assert result[0].id == "ok"


def test_fetch_with_as_of_introspection_rejects() -> None:
    class _NoAsOfSource(Source):
        def __init__(self):
            self.name = "noasof-source"
            self.kind = "news"

        def fetch(self, query: str, coin: str = "") -> list[Document]:
            return [Document(id="ok", kind="news", source=self.name, text="t", ts=1.0)]

    s = _NoAsOfSource()
    result = _fetch_with_as_of(s, "q", "BTC", datetime.now(timezone.utc))
    assert len(result) == 1
    assert result[0].id == "ok"


def test_fetch_with_as_of_none_bypasses_introspection() -> None:
    class _NeverCalledSource(Source):
        def __init__(self):
            self.name = "never"
            self.kind = "news"

        def fetch(self, query: str, coin: str = "") -> list[Document]:
            return []

    s = _NeverCalledSource()
    result = _fetch_with_as_of(s, "q", "BTC", None)
    assert result == []


def test_fetch_with_as_of_typeerror_fallback() -> None:
    class _BoomSource(Source):
        def __init__(self):
            self.name = "boom"
            self.kind = "news"

        def fetch(self, query: str, coin: str = "") -> list[Document]:
            return [Document(id="fallback", kind="news", source=self.name, text="t", ts=1.0)]

    s = _BoomSource()
    # 這個 source 的 fetch 簽名不含 as_of，但 _fetch_with_as_of 會先 inspect
    # 發現不含，所以不傳 as_of，直接呼叫無參數版本。
    result = _fetch_with_as_of(s, "q", "BTC", datetime.now(timezone.utc))
    assert len(result) == 1
    assert result[0].id == "fallback"


# ── collect() backward compatible + as_of ────────────────────────────────

def test_collect_backward_compatible_no_as_of() -> None:
    """不傳 as_of 時行為與改前逐字相同（offline + 假 source）。"""
    class _FakeSource(Source):
        def __init__(self):
            self.name = "fake"
            self.kind = "news"

        def fetch(self, query: str, coin: str = "") -> list[Document]:
            return [Document(id="a", kind="news", source=self.name, text="t", ts=1.0)]

    result = collect("q", coin="BTC", sources=[_FakeSource()], offline=True)
    assert any(d.id == "a" for d in result)


def test_collect_as_of_filters_documents() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    past = now - timedelta(days=1)
    future = now + timedelta(days=1)

    class _FakeSource(Source):
        def __init__(self):
            self.name = "fake"
            self.kind = "news"

        def fetch(self, query: str, coin: str = "") -> list[Document]:
            return [
                Document(id="old", kind="news", source=self.name, text="t", ts=past.timestamp()),
                Document(id="new", kind="news", source=self.name, text="t", ts=future.timestamp()),
            ]

    result = collect("q", coin="BTC", sources=[_FakeSource()], offline=True, as_of=now)
    assert [d.id for d in result] == ["old"]


def test_collect_as_of_with_visible_at_epoch() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    past = now - timedelta(days=1)
    future = now + timedelta(days=1)

    class _FakeSource(Source):
        def __init__(self):
            self.name = "fake"
            self.kind = "news"

        def fetch(self, query: str, coin: str = "") -> list[Document]:
            return [
                Document(id="old", kind="news", source=self.name, text="t", ts=future.timestamp(),
                         meta={"visible_at_epoch": past.timestamp()}),
                Document(id="new", kind="news", source=self.name, text="t", ts=past.timestamp(),
                         meta={"visible_at_epoch": future.timestamp()}),
            ]

    result = collect("q", coin="BTC", sources=[_FakeSource()], offline=True, as_of=now)
    assert [d.id for d in result] == ["old"]


def test_collect_as_of_with_source_that_accepts_as_of() -> None:
    """collect() 透過 _fetch_with_as_of 把 as_of 傳給支援的 source。"""
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    class _AsOfSource(Source):
        def __init__(self):
            self.name = "asof"
            self.kind = "news"

        def fetch(self, query: str, coin: str = "", *, as_of: datetime | None = None) -> list[Document]:
            if as_of is not None:
                return [Document(id="with_as_of", kind="news", source=self.name, text="t", ts=1.0)]
            return [Document(id="without_as_of", kind="news", source=self.name, text="t", ts=1.0)]

    result = collect("q", coin="BTC", sources=[_AsOfSource()], offline=True, as_of=now)
    assert [d.id for d in result] == ["with_as_of"]


def test_collect_as_of_empty_result_when_all_future() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    future = now + timedelta(days=1)

    class _FakeSource(Source):
        def __init__(self):
            self.name = "fake"
            self.kind = "news"

        def fetch(self, query: str, coin: str = "") -> list[Document]:
            return [
                Document(id="new1", kind="news", source=self.name, text="t", ts=future.timestamp()),
                Document(id="new2", kind="news", source=self.name, text="t", ts=future.timestamp()),
            ]

    result = collect("q", coin="BTC", sources=[_FakeSource()], offline=True, as_of=now)
    assert result == []
