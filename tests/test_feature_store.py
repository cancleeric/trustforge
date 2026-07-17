from __future__ import annotations

import sqlite3

import pytest

from trustforge.feature_store import TrustFeatureStore


def test_point_in_time_lookup_blocks_future_and_late_available_features(tmp_path) -> None:
    store = TrustFeatureStore(tmp_path / "features.sqlite3")
    store.put_many(feature_set="trust.v1", entity_key="BTC", features={"score": 0.4},
                   event_time=100, available_at=100, snapshot_id="s1", run_id="r1")
    store.put_many(feature_set="trust.v1", entity_key="BTC", features={"score": 0.8},
                   event_time=200, available_at=250, snapshot_id="s2", run_id="r2")
    assert store.get_as_of(feature_set="trust.v1", entity_key="BTC", as_of=199)["score"]["value"] == 0.4
    assert store.get_as_of(feature_set="trust.v1", entity_key="BTC", as_of=225)["score"]["value"] == 0.4
    assert store.get_as_of(feature_set="trust.v1", entity_key="BTC", as_of=250)["score"]["value"] == 0.8


def test_feature_store_is_append_only_and_rejects_impossible_availability(tmp_path) -> None:
    store = TrustFeatureStore(tmp_path / "features.sqlite3")
    with pytest.raises(ValueError):
        store.put_many(feature_set="trust.v1", entity_key="BTC", features={"score": 1},
                       event_time=200, available_at=199)
    [feature_id] = store.put_many(feature_set="trust.v1", entity_key="BTC", features={"score": 1},
                                  event_time=200, available_at=200)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store._conn.execute("DELETE FROM trust_feature_values WHERE feature_id=?", (feature_id,))
