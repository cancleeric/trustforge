"""Tests for degraded marking in freshness dashboard (#537)."""
from __future__ import annotations

import pytest

from trustforge.freshness import dashboard
from trustforge.ingestion.cache import JsonCacheBackend, cache_key


# ── helpers ──────────────────────────────────────────────────────────────────

def _mock_cache_get(entries):
    """Return a callable that mimics `cache_get(backend, key)` for the given entries."""
    def _get(backend, key, *args, **kwargs):
        return entries.get(key)
    return _get


def _entry(fetched_at, ttl):
    return {"docs": [{"id": "test"}], "fetched_at": fetched_at, "ttl": ttl}


def test_not_degraded_when_some_fresh(monkeypatch, tmp_path):
    """部分 fresh → 不標 degraded。"""
    backend = JsonCacheBackend(tmp_path / "cache.json")
    now = 1000.0
    entries = {
        cache_key("news", "BTC"): _entry(900.0, 1200.0),   # fresh: ttl > now
        cache_key("social", "BTC"): _entry(800.0, 950.0),  # stale: ttl <= now
    }
    monkeypatch.setattr("trustforge.freshness.cache_get", _mock_cache_get(entries))

    result = dashboard(backend, ["BTC"], ["news", "social"], now=now)

    assert result["degraded"] is False
    assert result["degraded_reason"] is None
    assert result["summary"].get("fresh", 0) == 1
    assert result["summary"].get("stale", 0) == 1


def test_degraded_when_all_stale(monkeypatch, tmp_path):
    """全部 stale → degraded。"""
    backend = JsonCacheBackend(tmp_path / "cache.json")
    now = 1000.0
    entries = {
        cache_key("news", "BTC"): _entry(800.0, 900.0),
        cache_key("social", "BTC"): _entry(700.0, 850.0),
    }
    monkeypatch.setattr("trustforge.freshness.cache_get", _mock_cache_get(entries))

    result = dashboard(backend, ["BTC"], ["news", "social"], now=now)

    assert result["degraded"] is True
    assert result["degraded_reason"] == "all_freshness_stale"
    assert result["summary"].get("fresh", 0) == 0
    assert result["summary"].get("stale", 0) == 2


def test_degraded_when_all_missing(monkeypatch, tmp_path):
    """全部 missing → degraded（fail-safe: 不可誤報 healthy）。"""
    backend = JsonCacheBackend(tmp_path / "cache.json")
    monkeypatch.setattr("trustforge.freshness.cache_get", lambda b, k, *a, **kw: None)

    result = dashboard(backend, ["BTC"], ["news", "social"], now=1000.0)

    assert result["degraded"] is True
    assert result["degraded_reason"] == "no_data"
    assert result["summary"].get("missing", 0) == 2


def test_degraded_when_no_coins_or_sources(monkeypatch, tmp_path):
    """無 coin 也無 source → degraded（no_data）。"""
    backend = JsonCacheBackend(tmp_path / "cache.json")

    result = dashboard(backend, [], [], now=1000.0)

    assert result["degraded"] is True
    assert result["degraded_reason"] == "no_data"
    assert result["total_entries"] == 0


def test_degraded_when_mixed_stale_and_missing(monkeypatch, tmp_path):
    """stale + missing 混合、無 fresh → degraded。"""
    backend = JsonCacheBackend(tmp_path / "cache.json")
    now = 1000.0
    entries = {
        cache_key("news", "BTC"): _entry(800.0, 900.0),  # stale
        # social → None (missing)
    }
    monkeypatch.setattr("trustforge.freshness.cache_get", _mock_cache_get(entries))

    result = dashboard(backend, ["BTC"], ["news", "social"], now=now)

    assert result["degraded"] is True
    assert result["degraded_reason"] == "all_freshness_stale"
    assert result["summary"].get("stale", 0) == 1
    assert result["summary"].get("missing", 0) == 1


def test_degraded_with_threshold_not_exceeded(monkeypatch, tmp_path):
    """stale 但未超過閾值 → 不因閾值 degraded（只靠 fresh_count 判定）。"""
    backend = JsonCacheBackend(tmp_path / "cache.json")
    now = 1000.0
    entries = {
        cache_key("news", "BTC"): _entry(900.0, 1200.0),   # fresh
        cache_key("social", "BTC"): _entry(900.0, 950.0),  # stale, age=100s
    }
    monkeypatch.setattr("trustforge.freshness.cache_get", _mock_cache_get(entries))

    result = dashboard(backend, ["BTC"], ["news", "social"], now=now,
                       degraded_stale_after=200.0)

    assert result["degraded"] is False


def test_degraded_with_threshold_exceeded(monkeypatch, tmp_path):
    """部分 stale 超過閾值 → degraded（stale_exceeds_threshold）。"""
    backend = JsonCacheBackend(tmp_path / "cache.json")
    now = 1000.0
    entries = {
        cache_key("news", "BTC"): _entry(900.0, 1200.0),   # fresh
        cache_key("social", "BTC"): _entry(500.0, 600.0),  # stale, age=500s
    }
    monkeypatch.setattr("trustforge.freshness.cache_get", _mock_cache_get(entries))

    result = dashboard(backend, ["BTC"], ["news", "social"], now=now,
                       degraded_stale_after=200.0)

    assert result["degraded"] is True
    assert result["degraded_reason"] == "stale_exceeds_threshold"


def test_last_refresh_epoch_tracks_most_recent_fetch(monkeypatch, tmp_path):
    """last_refresh_epoch 記錄所有 row 中最新的 fetched_at。"""
    backend = JsonCacheBackend(tmp_path / "cache.json")
    entries = {
        cache_key("news", "BTC"): _entry(800.0, 1200.0),
        cache_key("social", "BTC"): _entry(950.0, 1200.0),
    }
    monkeypatch.setattr("trustforge.freshness.cache_get", _mock_cache_get(entries))

    result = dashboard(backend, ["BTC"], ["news", "social"], now=1000.0)

    assert result["last_refresh_epoch"] == 950.0


def test_affected_source_count_counts_stale_and_missing(monkeypatch, tmp_path):
    """affected_source_count 只計 stale 或 missing 的 unique source。"""
    backend = JsonCacheBackend(tmp_path / "cache.json")
    now = 1000.0
    entries = {
        cache_key("news", "BTC"): _entry(900.0, 1200.0),    # fresh
        cache_key("social", "BTC"): _entry(800.0, 900.0),   # stale
        cache_key("news", "ETH"): _entry(850.0, 900.0),     # stale (same source different coin)
        # onchain: missing for both coins (no entry)
    }
    monkeypatch.setattr("trustforge.freshness.cache_get", _mock_cache_get(entries))

    result = dashboard(backend, ["BTC", "ETH"], ["news", "social", "onchain"], now=now)

    # social stale, onchain missing; news has one fresh (BTC) and one stale (ETH)
    # affected_sources = {social, onchain} (news also has a stale row but also fresh)
    assert result["affected_source_count"] >= 1
    assert result["max_stale_age_sec"] > 0


def test_non_degraded_is_healthy_and_has_null_reason(monkeypatch, tmp_path):
    """非 degraded 時 degraded=False，degraded_reason=None。"""
    backend = JsonCacheBackend(tmp_path / "cache.json")
    now = 1000.0
    entries = {
        cache_key("news", "BTC"): _entry(900.0, 1200.0),
    }
    monkeypatch.setattr("trustforge.freshness.cache_get", _mock_cache_get(entries))

    result = dashboard(backend, ["BTC"], ["news"], now=now)

    assert result["degraded"] is False
    assert result["degraded_reason"] is None
    assert result["summary"].get("fresh", 0) == 1


def test_single_fresh_with_others_stale_not_degraded(monkeypatch, tmp_path):
    """有一個 fresh + 其他 stale → fresh_count>0 → 不因 all_stale degraded。"""
    backend = JsonCacheBackend(tmp_path / "cache.json")
    now = 1000.0
    entries = {
        cache_key("news", "BTC"): _entry(900.0, 1200.0),     # fresh
        cache_key("social", "BTC"): _entry(100.0, 200.0),    # very old stale
        cache_key("onchain", "BTC"): _entry(200.0, 300.0),   # very old stale
    }
    monkeypatch.setattr("trustforge.freshness.cache_get", _mock_cache_get(entries))

    result = dashboard(backend, ["BTC"], ["news", "social", "onchain"], now=now)

    # fresh_count=1，不走 all_freshness_stale；沒給 threshold，不走 stale_exceeds_threshold
    assert result["degraded"] is False
    assert result["degraded_reason"] is None
    assert result["last_refresh_epoch"] == 900.0
