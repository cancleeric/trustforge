from datetime import datetime, timezone
import hashlib
import json

import pytest

from trustforge.ingestion.cache import JsonCacheBackend, cache_key, cache_set
from trustforge.replay import capture_source_snapshot, load_source_snapshot, store_backfilled_source_snapshot


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


def test_backfill_requires_full_provenance_and_valid_content_hash(tmp_path):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    boundary = datetime(2021, 7, 1, tzinfo=timezone.utc).timestamp()
    document = {
        "id": "archive-1", "text": "historical announcement", "provider": "example",
        "license": "public-record", "published_at": "2021-06-30T10:00:00Z",
        "retrieved_at": "2026-07-14T00:00:00Z",
    }
    document["content_sha256"] = hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    result = store_backfilled_source_snapshot(
        backend, "BTC", "2021-07-01", [{"source": "government", "documents": [document]}],
        snapshot_epoch=boundary, provider_manifest={"providers": [{"provider": "example"}]},
    )
    assert result.ok
    snapshot = load_source_snapshot(backend, "BTC", "2021-07-01", at_or_before=boundary)
    assert snapshot is not None
    assert snapshot["archive_type"] == "backfilled_archive"


def test_backfill_rejects_missing_provenance(tmp_path):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    boundary = datetime(2021, 7, 1, tzinfo=timezone.utc).timestamp()
    with pytest.raises(ValueError, match="content_sha256"):
        store_backfilled_source_snapshot(
            backend, "BTC", "2021-07-01", [{"source": "government", "documents": [{"published_at": "2021-06-30T10:00:00Z"}]}],
            snapshot_epoch=boundary, provider_manifest={"providers": []},
        )
