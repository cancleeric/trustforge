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


def test_backfill_merges_multiple_providers_without_overwriting_same_day(tmp_path):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    boundary = datetime(2021, 7, 1, 23, 59, 59, tzinfo=timezone.utc).timestamp()

    def document(identifier, provider):
        value = {"id": identifier, "text": identifier, "provider": provider, "license": "public-record",
                 "published_at": "2021-07-01T10:00:00Z", "retrieved_at": "2026-07-16T00:00:00Z"}
        value["content_sha256"] = hashlib.sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return value

    first = store_backfilled_source_snapshot(
        backend, "BTC", "2021-07-01", [{"source": "sec-gov", "documents": [document("sec-1", "SEC")]}],
        snapshot_epoch=boundary, provider_manifest={"providers": [{"provider": "SEC", "license": "public-record"}]}, retrieved_at=100.0,
    )
    second = store_backfilled_source_snapshot(
        backend, "BTC", "2021-07-01", [{"source": "alternative-me-fng", "documents": [document("fng-1", "Alternative.me")]}],
        snapshot_epoch=boundary, provider_manifest={"providers": [{"provider": "Alternative.me", "license": "attribution"}]}, retrieved_at=101.0,
    )
    assert first.ok and second.ok
    snapshot = load_source_snapshot(backend, "BTC", "2021-07-01")
    assert snapshot is not None
    assert {source["source"] for source in snapshot["sources"]} == {"sec-gov", "alternative-me-fng"}
    assert {provider["provider"] for provider in snapshot["provider_manifest"]["providers"]} == {"SEC", "Alternative.me"}


def test_backfill_is_isolated_from_same_day_live_snapshot(tmp_path):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    boundary = datetime(2026, 7, 17, 23, 59, 59, tzinfo=timezone.utc).timestamp()
    cache_set(
        backend, cache_key("hoyabit-ticker", "BTC"),
        [{"id": "live", "text": "live only"}], fetched_at=boundary - 10,
    )
    assert capture_source_snapshot(
        backend, "BTC", ["hoyabit-ticker"], captured_at=boundary - 5,
    ).ok
    archive = {
        "id": "archive", "text": "historical", "provider": "Alternative.me",
        "license": "attribution", "published_at": "2026-07-17T00:00:00Z",
        "retrieved_at": "2026-07-18T00:00:00Z",
    }
    archive["content_sha256"] = hashlib.sha256(
        json.dumps(archive, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert store_backfilled_source_snapshot(
        backend, "BTC", "2026-07-17",
        [{"source": "alternative-me-fng", "documents": [archive]}],
        snapshot_epoch=boundary,
        provider_manifest={"providers": [{"provider": "Alternative.me"}]},
    ).ok

    live = load_source_snapshot(backend, "BTC", "2026-07-17")
    backfill = load_source_snapshot(
        backend, "BTC", "2026-07-17", archive_type="backfilled_archive",
    )
    assert live is not None and live.get("archive_type") is None
    assert live["sources"][0]["source"] == "hoyabit-ticker"
    assert backfill is not None and backfill["archive_type"] == "backfilled_archive"
    assert backfill["sources"][0]["source"] == "alternative-me-fng"
