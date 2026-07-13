from datetime import datetime, timezone

from trustforge.ingestion.cache import JsonCacheBackend, cache_key, cache_set
from trustforge.replay import capture_source_snapshot, load_source_snapshot


def test_source_snapshot_preserves_per_source_time_and_explicit_missing_sources(tmp_path):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    cache_set(
        backend, cache_key("sec-edgar", "BTC"), [{"id": "filing-1", "ts": 50.0}], fetched_at=100.0,
    )
    captured = datetime(2026, 7, 13, 12, tzinfo=timezone.utc).timestamp()

    result = capture_source_snapshot(backend, "BTC", ["sec-edgar", "news-feed"], captured_at=captured)

    assert result.ok and not result.used_fallback
    snapshot = load_source_snapshot(backend, "BTC", "2026-07-13")
    assert snapshot is not None
    assert snapshot["snapshot_at"] == "2026-07-13T12:00:00Z"
    assert snapshot["sources"] == [{
        "source": "sec-edgar", "fetched_at": 100.0,
        "documents": [{"id": "filing-1", "ts": 50.0, "published_at": "1970-01-01T00:00:50Z"}],
    }]
    assert snapshot["missing_sources"] == ["news-feed"]


def test_source_snapshot_does_not_reconstruct_missing_history(tmp_path):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    assert load_source_snapshot(backend, "BTC", "2021-06-01") is None


def test_source_snapshot_rejects_same_day_data_captured_after_run_start(tmp_path):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    captured = datetime(2026, 7, 13, 12, tzinfo=timezone.utc).timestamp()
    cache_set(backend, cache_key("sec-edgar", "BTC"), [{"id": "filing-1"}], fetched_at=100.0)
    capture_source_snapshot(backend, "BTC", ["sec-edgar"], captured_at=captured)

    assert load_source_snapshot(
        backend, "BTC", "2026-07-13", at_or_before=captured - 1,
    ) is None
    assert load_source_snapshot(
        backend, "BTC", "2026-07-13", at_or_before=captured,
    ) is not None
