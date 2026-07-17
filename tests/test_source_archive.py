from __future__ import annotations

import json
import sqlite3

import pytest

from scripts import fetch_scheduler
from trustforge.ingestion.base import Document
from trustforge.ingestion.cache import JsonCacheBackend, cache_key
from trustforge.source_archive import SOURCE_EVENT_SCHEMA_VERSION, SourceEventArchive


def test_source_archive_preserves_duplicate_fetches_and_lineage(tmp_path) -> None:
    archive = SourceEventArchive(tmp_path / "archive.sqlite3")
    document = Document(
        id="doc-1", kind="news", source="unit", text="payload", url="https://example.test/a",
        ts=100.0, meta={"coin": "BTC", "etag": "v1", "http_status": 200},
    )
    first = archive.append_fetch(
        source_id="unit", source_kind="news", coin="BTC", documents=[document],
        fetched_at=200.0, expires_at=300.0, fetch_run_id="fetch-1", scheduler_run_id="cycle-1",
        fetch_duration_ms=12.0,
    )
    second = archive.append_fetch(
        source_id="unit", source_kind="news", coin="BTC", documents=[document],
        fetched_at=201.0, expires_at=301.0, fetch_run_id="fetch-2", scheduler_run_id="cycle-2",
        fetch_duration_ms=20.0,
    )
    assert first != second
    assert archive.count(source_id="unit") == 2
    row = archive.get(first)
    assert row is not None
    assert row["schema_version"] == SOURCE_EVENT_SCHEMA_VERSION
    assert row["document_count"] == 1
    assert row["fetch_run_id"] == "fetch-1"
    assert json.loads(row["raw_payload_json"])[0]["schema_version"] == "1.0.0"
    metrics = archive.observability_snapshot(window_seconds=1000, now=250.0)[0]
    assert metrics["fetches"] == 2
    assert metrics["documents"] == 2
    assert metrics["freshness_age_seconds"] == 49.0
    assert metrics["duplicate_fetch_ratio"] == 0.5
    assert metrics["latency_p50_ms"] == 12.0
    assert metrics["latency_p95_ms"] == 20.0


def test_source_archive_rejects_updates_and_deletes(tmp_path) -> None:
    archive = SourceEventArchive(tmp_path / "archive.sqlite3")
    event_id = archive.append_fetch(
        source_id="unit", source_kind="news", coin="BTC", documents=[],
        fetched_at=200.0, expires_at=None,
    )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        archive._conn.execute("UPDATE source_events SET coin = 'ETH' WHERE event_id = ?", (event_id,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        archive._conn.execute("DELETE FROM source_events WHERE event_id = ?", (event_id,))


def test_scheduler_does_not_update_latest_cache_when_bronze_archive_fails(tmp_path, monkeypatch) -> None:
    class Source:
        name = "unit-source"
        kind = "news"

        def fetch(self, query: str, coin: str = "") -> list[Document]:
            return [Document(id="new", kind="news", source=self.name, text="new")]

    class BrokenArchive:
        def append_fetch(self, **kwargs) -> str:
            raise sqlite3.OperationalError("archive unavailable")

    backend = JsonCacheBackend(tmp_path / "cache.json")
    backend.set(cache_key("unit-source", "BTC"), [{"id": "old"}], fetched_at=1.0)
    monkeypatch.setattr(fetch_scheduler, "build_registry", lambda: {"unit-source": Source()})
    results, failures = fetch_scheduler.run_once(
        ["unit-source"], ["BTC"], backend, True, {}, 0.0, False, archive=BrokenArchive()
    )
    assert results == []
    assert failures == ["unit-source:BTC"]
    assert backend.get(cache_key("unit-source", "BTC"))["docs"] == [{"id": "old"}]
