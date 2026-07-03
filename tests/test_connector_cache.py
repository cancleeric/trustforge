"""階段2：連接器快取（`ingestion/cache.py`）+ 排程 fetcher
（`scripts/fetch_scheduler.py`）測試。

⛔ 全程不打真連接器 API、不打真 AWS（`DynamoDBCache` 一律用
`unittest.mock` 繞過 boto3，比照 `test_cost_ledger.py` 的 `DynamoDBLedger`
mock 慣例；不引入 moto 或任何新測試依賴）。驗證：
  1. `CachedSource` 命中 cache 時**不**觸發被包裝來源的真 `fetch()`。
  2. cache-miss / 過期 → `CacheMissError`（交 `base.collect()` 既有的
     `_failed` 降級機制接住）。
  3. `JsonCacheBackend` round-trip 正確；`DynamoDBCache` 用 mock Table
     驗證 put/get/TTL 換算，且建構本身不連 AWS。
  4. `cache_get` 對壞掉的 backend 自動 fallback 到本地 `JsonCacheBackend`
     （比照 `ledger.append_run()` 的 fallback 慣例）；`cache_set` 預設**不**
     自動 fallback、明確回傳 `CacheWriteResult`（codex HIGH-2）。
  5. `scripts/fetch_scheduler.py` 是唯一會呼叫真 `Source.fetch()` 的地方
     （用假 source 驗證排程/新鮮度守門/coin-agnostic 廣播/錯誤處理邏輯/
     cache 寫入失敗會讓 exit code 非零，見 codex HIGH-2）。
  6. refresh 間隔（排程多久打一次）與硬過期時限（`CachedSource` 判斷過期）
     刻意分離、硬過期留 margin，構造真實 cron 時間軸驗證任一時點都不會出現
     「排程還沒跑到、cache 已經過期」的空窗（codex HIGH-1）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from trustforge.ingestion.base import Document, Source
from trustforge.ingestion.cache import (
    COIN_AGNOSTIC_SOURCES,
    COIN_KEYED_BATCH_SOURCES,
    DEFAULT_REFRESH_INTERVAL_FALLBACK_SECONDS,
    DEFAULT_REFRESH_INTERVAL_SECONDS,
    DEFAULT_STALE_AFTER_FALLBACK_SECONDS,
    DEFAULT_STALE_AFTER_SECONDS,
    STALE_AFTER_MULTIPLIER,
    CacheBackend,
    CacheMissError,
    CachedSource,
    CacheWriteResult,
    DynamoDBCache,
    JsonCacheBackend,
    cache_get,
    cache_key,
    cache_set,
    doc_from_dict,
    doc_to_dict,
    get_cache_backend,
    get_freshness_snapshot,
    stale_after_for,
)
from trustforge.ledger import DynamoDBLedger, JsonlLedger
from trustforge import scheduler_log

# scripts/ 沒有 __init__.py，用 importlib 依路徑載入，避免污染 sys.path 套件命名空間
# （比照 test_gen_stance_cache.py 的既有作法）。
_REPO = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO / "scripts" / "fetch_scheduler.py"
_spec = importlib.util.spec_from_file_location("fetch_scheduler", _SCRIPT_PATH)
fetch_scheduler = importlib.util.module_from_spec(_spec)
sys.modules["fetch_scheduler"] = fetch_scheduler
_spec.loader.exec_module(fetch_scheduler)


class _FakeSource(Source):
    """可控假來源：記錄呼叫次數 + 回傳固定文件清單。絕不打真網路。"""

    def __init__(self, name: str, kind: str = "news", docs: list[Document] | None = None,
                 raise_exc: Exception | None = None):
        self.name = name
        self.kind = kind
        self._docs = docs if docs is not None else [
            Document(id=f"{name}-1", kind=kind, source=name, text=f"{name} sample", ts=1.0)
        ]
        self._raise_exc = raise_exc
        self.calls: list[tuple[str, str]] = []

    def fetch(self, query: str, coin: str = "") -> list[Document]:
        self.calls.append((query, coin))
        if self._raise_exc is not None:
            raise self._raise_exc
        return list(self._docs)


class _BoomSource(Source):
    """`fetch()` 一被呼叫就炸——用來證明 `CachedSource` 命中快取時完全不
    觸碰被包裝的真來源。"""

    name = "boom"
    kind = "news"

    def fetch(self, query: str, coin: str = "") -> list[Document]:  # pragma: no cover
        raise AssertionError("CachedSource cache-hit 不該呼叫被包裝來源的真 fetch()")


# ---------------------------------------------------------------------------
# cache_key / doc_to_dict / doc_from_dict
# ---------------------------------------------------------------------------

def test_cache_key_normalizes_coin_case_and_blank():
    assert cache_key("coindesk", "btc") == cache_key("coindesk", "BTC")
    assert cache_key("coindesk", None) == cache_key("coindesk", "")
    assert cache_key("coindesk", "BTC") != cache_key("coindesk", "ETH")


def test_doc_roundtrip_preserves_fields():
    doc = Document(id="x1", kind="news", source="coindesk", text="hello",
                    url="https://example.com", ts=123.5, meta={"content_reference": "hi"})
    restored = doc_from_dict(doc_to_dict(doc))
    assert restored == doc


def test_doc_from_dict_tolerates_missing_fields():
    restored = doc_from_dict({})
    assert restored.id == "" and restored.ts == 0.0 and restored.meta == {}


# ---------------------------------------------------------------------------
# CacheBackend：JsonCacheBackend round-trip
# ---------------------------------------------------------------------------

def test_json_backend_get_missing_key_returns_none(tmp_path):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    assert backend.get("nope") is None


def test_json_backend_set_then_get_roundtrip(tmp_path):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    docs = [doc_to_dict(Document(id="a", kind="news", source="coindesk", text="t", ts=1.0))]
    backend.set("coindesk:BTC", docs, fetched_at=1000.0)
    entry = backend.get("coindesk:BTC")
    assert entry is not None
    assert entry["docs"] == docs
    assert entry["fetched_at"] == 1000.0


def test_json_backend_set_overwrites_same_key(tmp_path):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    backend.set("k", [{"id": "old"}], fetched_at=1.0)
    backend.set("k", [{"id": "new"}], fetched_at=2.0)
    entry = backend.get("k")
    assert entry["docs"] == [{"id": "new"}]
    assert entry["fetched_at"] == 2.0


def test_json_backend_survives_corrupt_file(tmp_path):
    p = tmp_path / "cache.json"
    p.write_text("not valid json {{{", encoding="utf-8")
    backend = JsonCacheBackend(p)
    assert backend.get("anything") is None
    # set() 之後應能正常寫入、不被壞檔卡住（覆蓋壞檔）。
    backend.set("k", [{"id": "1"}], fetched_at=1.0)
    assert backend.get("k")["docs"] == [{"id": "1"}]


def test_get_cache_backend_reads_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTFORGE_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("CACHE_BACKEND", "json")
    assert isinstance(get_cache_backend(), JsonCacheBackend)
    monkeypatch.setenv("CACHE_BACKEND", "dynamodb")
    assert isinstance(get_cache_backend(), DynamoDBCache)
    monkeypatch.delenv("CACHE_BACKEND", raising=False)
    assert isinstance(get_cache_backend(), DynamoDBCache)  # 預設 dynamodb


# ---------------------------------------------------------------------------
# DynamoDBCache（mock boto3 Table，不打真 AWS）+ cache_get/cache_set fallback
# ---------------------------------------------------------------------------

def test_dynamodb_cache_is_cache_backend_subclass():
    d = DynamoDBCache()
    assert isinstance(d, CacheBackend)


def test_dynamodb_cache_construction_does_not_touch_aws(monkeypatch):
    """建構只讀 env，不建立 boto3 resource/Table（lazy）——無憑證/未建表環境不炸。"""
    monkeypatch.delenv("TRUSTFORGE_CACHE_TABLE", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    d = DynamoDBCache()
    assert d.table_name == "trustforge-connector-cache"
    assert d.region == "us-east-1"
    assert d._table is None  # 尚未真的碰 AWS SDK


def test_dynamodb_cache_set_calls_put_item_with_decimal_and_ttl():
    d = DynamoDBCache(table_name="fake-table")
    mock_table = MagicMock()
    d._table = mock_table  # 繞過 boto3，模擬已建好的 Table，確保不打真 AWS

    docs = [doc_to_dict(Document(id="a", kind="news", source="coindesk", text="t", ts=1.0))]
    d.set(cache_key("coindesk", "BTC"), docs, fetched_at=1000.0, ttl_seconds=900)

    mock_table.put_item.assert_called_once()
    item = mock_table.put_item.call_args.kwargs["Item"]
    assert item["source_id"] == "coindesk"
    assert item["coin"] == "BTC"
    assert isinstance(item["fetched_at"], Decimal)
    assert item["fetched_at"] == Decimal("1000.0")
    assert isinstance(item["ttl"], int)
    assert item["ttl"] == 1900  # fetched_at + ttl_seconds
    assert json.loads(item["docs_json"]) == docs


def test_dynamodb_cache_set_without_ttl_seconds_uses_fallback_window():
    d = DynamoDBCache(table_name="fake-table")
    mock_table = MagicMock()
    d._table = mock_table

    d.set(cache_key("coindesk", "BTC"), [], fetched_at=1000.0)

    item = mock_table.put_item.call_args.kwargs["Item"]
    assert item["ttl"] == 1000 + DEFAULT_STALE_AFTER_FALLBACK_SECONDS


def test_dynamodb_cache_get_calls_get_item_with_split_key():
    d = DynamoDBCache(table_name="fake-table")
    mock_table = MagicMock()
    d._table = mock_table
    docs = [{"id": "a"}]
    mock_table.get_item.return_value = {
        "Item": {
            "source_id": "coindesk", "coin": "BTC",
            "docs_json": json.dumps(docs), "fetched_at": Decimal("1000.0"),
            "ttl": 1900,
        }
    }

    entry = d.get(cache_key("coindesk", "BTC"))

    mock_table.get_item.assert_called_once_with(
        Key={"source_id": "coindesk", "coin": "BTC"}, ConsistentRead=False
    )
    assert entry == {"docs": docs, "fetched_at": 1000.0}


def test_dynamodb_cache_get_missing_item_returns_none():
    d = DynamoDBCache(table_name="fake-table")
    mock_table = MagicMock()
    d._table = mock_table
    mock_table.get_item.return_value = {}  # 無 "Item" key（DynamoDB 未命中的標準回應）
    assert d.get(cache_key("coindesk", "BTC")) is None


def test_cache_get_falls_back_to_json_on_broken_dynamodb_backend(monkeypatch, tmp_path):
    """dynamodb backend get 失敗（缺憑證/表未建/網路問題）→ fallback 讀
    JsonCacheBackend，不整個炸掉。用 mock 讓 `_get_table()` 直接炸掉，
    確保這裡不會意外打到真 AWS。"""
    fallback_path = tmp_path / "fallback_cache.json"
    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_PATH", str(fallback_path))
    JsonCacheBackend(fallback_path).set(cache_key("coindesk", "BTC"), [{"id": "x"}], fetched_at=5.0)

    broken = DynamoDBCache()
    monkeypatch.setattr(
        broken, "_get_table",
        MagicMock(side_effect=RuntimeError("no aws credentials / table not found")),
    )

    entry = cache_get(broken, cache_key("coindesk", "BTC"))
    assert entry == {"docs": [{"id": "x"}], "fetched_at": 5.0}


def test_cache_set_on_broken_dynamodb_backend_returns_failure_without_opt_in(monkeypatch, tmp_path):
    """codex HIGH-2：預設（沒有明確 opt-in）primary backend 寫入失敗時，
    `cache_set` 必須回傳明確的 `ok=False`，**不**偷偷 fallback 寫本地 JSON
    卻回報成功——否則 production DynamoDB 故障會被本地 fallback 掩蓋成
    「看起來一切正常」。"""
    fallback_path = tmp_path / "fallback_cache.json"
    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_PATH", str(fallback_path))
    monkeypatch.delenv("TRUSTFORGE_CACHE_JSON_FALLBACK", raising=False)

    broken = DynamoDBCache()
    monkeypatch.setattr(
        broken, "_get_table",
        MagicMock(side_effect=RuntimeError("no aws credentials / table not found")),
    )

    result = cache_set(broken, cache_key("coindesk", "BTC"), [{"id": "y"}], fetched_at=9.0, ttl_seconds=900)

    assert result == CacheWriteResult(
        ok=False, used_fallback=False, backend="DynamoDBCache",
        error="no aws credentials / table not found",
    )
    assert not fallback_path.exists()  # 確認真的沒有偷寫到本地 fallback


def test_cache_set_falls_back_to_json_when_explicitly_opted_in_via_kwarg(monkeypatch, tmp_path):
    """明確傳 `allow_json_fallback=True`（dev/CI 情境）時，才允許 fallback 寫
    本地 JSON，且回傳值要誠實標記 `used_fallback=True`（不是跟 primary 成功
    等價的「一切正常」）。"""
    fallback_path = tmp_path / "fallback_cache.json"
    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_PATH", str(fallback_path))

    broken = DynamoDBCache()
    monkeypatch.setattr(
        broken, "_get_table",
        MagicMock(side_effect=RuntimeError("no aws credentials / table not found")),
    )

    result = cache_set(
        broken, cache_key("coindesk", "BTC"), [{"id": "y"}], fetched_at=9.0, ttl_seconds=900,
        allow_json_fallback=True,
    )

    assert result.ok is True
    assert result.used_fallback is True
    assert result.backend == "JsonCacheBackend"
    assert fallback_path.exists()
    entry = JsonCacheBackend(fallback_path).get(cache_key("coindesk", "BTC"))
    assert entry == {"docs": [{"id": "y"}], "fetched_at": 9.0}


def test_cache_set_falls_back_to_json_when_opted_in_via_env(monkeypatch, tmp_path):
    """opt-in 也可以用 env `TRUSTFORGE_CACHE_JSON_FALLBACK=1` 開啟（不用改
    呼叫端 code），效果與明確傳 kwarg 一致。"""
    fallback_path = tmp_path / "fallback_cache.json"
    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_PATH", str(fallback_path))
    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_FALLBACK", "1")

    broken = DynamoDBCache()
    monkeypatch.setattr(
        broken, "_get_table",
        MagicMock(side_effect=RuntimeError("no aws credentials / table not found")),
    )

    result = cache_set(broken, cache_key("coindesk", "BTC"), [{"id": "y"}], fetched_at=9.0, ttl_seconds=900)

    assert result.ok is True
    assert result.used_fallback is True


def test_cache_set_success_returns_ok_true_without_fallback():
    d = DynamoDBCache(table_name="fake-table")
    mock_table = MagicMock()
    d._table = mock_table

    result = cache_set(d, cache_key("coindesk", "BTC"), [{"id": "z"}], fetched_at=1.0, ttl_seconds=900)

    assert result == CacheWriteResult(ok=True, used_fallback=False, backend="DynamoDBCache", error=None)


def test_cache_get_normal_miss_does_not_trigger_fallback(tmp_path):
    """backend 正常回應「沒有這筆」（回 None，不 raise）視為合法 miss，
    不該偷偷轉去問 fallback JsonCacheBackend（語意上 A 沒有 != 應該問 B）。"""
    backend = JsonCacheBackend(tmp_path / "primary.json")
    assert cache_get(backend, "nope") is None


def test_cached_source_hit_with_mocked_dynamodb_backend_does_not_touch_wrapped_fetch():
    """全套鏈路：CachedSource 命中（backend 是 mock 過的 DynamoDBCache）一樣
    不觸發被包裝真來源的 fetch()。"""
    d = DynamoDBCache(table_name="fake-table")
    mock_table = MagicMock()
    d._table = mock_table
    doc = Document(id="d1", kind="news", source="boom", text="cached content", ts=1.0)
    mock_table.get_item.return_value = {
        "Item": {
            "source_id": "boom", "coin": "BTC",
            "docs_json": json.dumps([doc_to_dict(doc)]),
            "fetched_at": Decimal(str(time.time())),
            "ttl": int(time.time()) + 3600,
        }
    }

    cached = CachedSource(_BoomSource(), ttl_seconds=3600, backend=d)
    docs = cached.fetch("任意 query", coin="BTC")
    assert docs == [doc]  # _BoomSource.fetch 若被呼叫會直接炸，跑到這裡代表沒被呼叫


# ---------------------------------------------------------------------------
# CachedSource：命中不打真來源 / miss 與過期一律 CacheMissError
# ---------------------------------------------------------------------------

def test_cached_source_hit_returns_docs_without_touching_wrapped_fetch(tmp_path):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    doc = Document(id="d1", kind="news", source="boom", text="cached content", ts=1.0)
    backend.set(cache_key("boom", "BTC"), [doc_to_dict(doc)], fetched_at=time.time())

    cached = CachedSource(_BoomSource(), ttl_seconds=3600, backend=backend)
    docs = cached.fetch("任意 query 完全不影響 key", coin="BTC")
    assert docs == [doc]  # _BoomSource.fetch 若被呼叫會直接炸，跑到這裡代表沒被呼叫


def test_cached_source_miss_raises_cache_miss_error(tmp_path):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    cached = CachedSource(_BoomSource(), ttl_seconds=3600, backend=backend)
    with pytest.raises(CacheMissError):
        cached.fetch("q", coin="BTC")


def test_cached_source_expired_raises_cache_miss_error(tmp_path):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    backend.set(cache_key("boom", "BTC"), [], fetched_at=time.time() - 7200)  # 2 小時前
    cached = CachedSource(_BoomSource(), ttl_seconds=3600, backend=backend)  # ttl 1 小時
    with pytest.raises(CacheMissError):
        cached.fetch("q", coin="BTC")


def test_cached_source_within_ttl_boundary_hits(tmp_path):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    backend.set(cache_key("boom", "BTC"), [], fetched_at=time.time() - 10)  # 10 秒前
    cached = CachedSource(_BoomSource(), ttl_seconds=3600, backend=backend)
    assert cached.fetch("q", coin="BTC") == []


def test_cached_source_query_ignored_in_cache_key(tmp_path):
    """不同 query 命中同一把 cache（key 只看 source.name + coin）。"""
    backend = JsonCacheBackend(tmp_path / "cache.json")
    doc = Document(id="d1", kind="news", source="boom", text="x", ts=1.0)
    backend.set(cache_key("boom", "ETH"), [doc_to_dict(doc)], fetched_at=time.time())
    cached = CachedSource(_BoomSource(), ttl_seconds=3600, backend=backend)
    assert cached.fetch("問題 A", coin="ETH") == [doc]
    assert cached.fetch("完全不同的問題 B", coin="ETH") == [doc]


def test_cached_source_default_ttl_from_known_and_unknown_name(tmp_path):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    known = CachedSource(_FakeSource("reddit-bitcoin"), backend=backend)
    assert known.ttl_seconds == DEFAULT_STALE_AFTER_SECONDS["reddit-bitcoin"]
    unknown = CachedSource(_FakeSource("some-future-source"), backend=backend)
    assert unknown.ttl_seconds == DEFAULT_STALE_AFTER_FALLBACK_SECONDS


def test_cached_source_default_ttl_is_strictly_greater_than_refresh_interval():
    """codex HIGH-1：CachedSource 的硬過期時限必須明顯大於排程 refresh 間隔，
    否則 refresh 稍微 jitter 或單次失敗，產品讀取就會出現例行空窗。"""
    for name, refresh_interval in DEFAULT_REFRESH_INTERVAL_SECONDS.items():
        stale_after = DEFAULT_STALE_AFTER_SECONDS[name]
        assert stale_after > refresh_interval, name
        assert stale_after == refresh_interval * STALE_AFTER_MULTIPLIER
    assert DEFAULT_STALE_AFTER_FALLBACK_SECONDS > DEFAULT_REFRESH_INTERVAL_FALLBACK_SECONDS
    assert stale_after_for(600) == 600 * STALE_AFTER_MULTIPLIER


def test_refresh_cadence_with_stale_after_margin_has_no_gap(monkeypatch, tmp_path):
    """codex HIGH-1：模擬 cron 排程時間軸——refresh 間隔 600s（10min），硬過期
    依 `STALE_AFTER_MULTIPLIER` 換算為 1800s（30min，3倍）。排程每輪準時刷新
    時，任一時間點讀取都不該因為硬過期判定而降級（不會出現『refresh 間隔 ==
    硬過期』那種例行空窗）。
    """
    import trustforge.ingestion.cache as cache_mod

    refresh_interval = 600.0
    stale_after = stale_after_for(refresh_interval)
    backend = JsonCacheBackend(tmp_path / "cache.json")
    cached = CachedSource(_BoomSource(), ttl_seconds=stale_after, backend=backend)

    fake_now = {"t": 0.0}
    monkeypatch.setattr(cache_mod.time, "time", lambda: fake_now["t"])

    for cycle in range(5):
        fetched_at = cycle * refresh_interval
        backend.set(cache_key("boom", "BTC"), [{"id": f"doc-{cycle}"}], fetched_at=fetched_at)
        # 這一輪刷新後、到下一輪排程理論上該跑的時間點之間，任何時刻讀取都
        # 該命中（不 raise CacheMissError）。
        for offset in (0.0, refresh_interval * 0.5, refresh_interval * 0.99):
            fake_now["t"] = fetched_at + offset
            docs = cached.fetch("", coin="BTC")
            assert [d.id for d in docs] == [f"doc-{cycle}"]


def test_stale_after_margin_tolerates_one_missed_refresh_cycle(monkeypatch, tmp_path):
    """codex HIGH-1：硬過期 = 3x refresh 間隔，代表允許連續 1 次 refresh 失敗
    （中間跳過一輪沒寫入新快取）仍不影響產品讀取，直到第 2 次也失敗才會真的
    觸底降級。"""
    import trustforge.ingestion.cache as cache_mod

    refresh_interval = 600.0
    stale_after = stale_after_for(refresh_interval)  # 1800s
    backend = JsonCacheBackend(tmp_path / "cache.json")
    cached = CachedSource(_BoomSource(), ttl_seconds=stale_after, backend=backend)

    fake_now = {"t": 0.0}
    monkeypatch.setattr(cache_mod.time, "time", lambda: fake_now["t"])

    # t=0 排程成功寫入一次；t=600（第 2 輪）、t=1200（第 3 輪）排程「失敗」
    # （模擬 fetch_scheduler.py 真呼叫失敗，略過寫入，不更新 fetched_at）。
    backend.set(cache_key("boom", "BTC"), [{"id": "doc-0"}], fetched_at=0.0)

    # 第 2 輪該跑但失敗後（t=600~1200 之間）：仍在 1800s 硬過期內，應可讀。
    fake_now["t"] = 900.0
    assert cached.fetch("", coin="BTC")[0].id == "doc-0"

    # 第 3 輪也失敗（t=1200~1800 之間，仍未超過硬過期 1800s）：邊界內仍可讀。
    fake_now["t"] = 1799.0
    assert cached.fetch("", coin="BTC")[0].id == "doc-0"

    # 超過硬過期（連續 3 輪都沒刷新成功）：才真的降級為 CacheMissError。
    fake_now["t"] = 1801.0
    with pytest.raises(CacheMissError):
        cached.fetch("", coin="BTC")


def test_cached_source_preserves_wrapped_name_and_kind(tmp_path):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    wrapped = _FakeSource("coindesk", kind="news")
    cached = CachedSource(wrapped, backend=backend)
    assert cached.name == "coindesk"
    assert cached.kind == "news"


# ---------------------------------------------------------------------------
# base.collect() 注入點：online 分支預設用 CachedSource 包裝
# ---------------------------------------------------------------------------

def test_collect_online_default_wraps_with_cached_source_hit(monkeypatch, tmp_path):
    """collect(offline=False, sources=None) 應把 build_*_sources() 產生的真
    來源包一層 CachedSource；cache 命中時完全不觸發真 fetch()。"""
    from trustforge.ingestion import base
    from trustforge.ingestion import cache as cache_mod

    backend = JsonCacheBackend(tmp_path / "cache.json")
    monkeypatch.setattr(cache_mod, "get_cache_backend", lambda: backend)

    boom = _BoomSource()
    monkeypatch.setattr("trustforge.ingestion.news.build_news_sources", lambda: [boom])
    monkeypatch.setattr("trustforge.ingestion.onchain.build_onchain_sources", lambda: [])
    monkeypatch.setattr("trustforge.ingestion.social.build_social_sources", lambda: [])
    monkeypatch.setattr("trustforge.ingestion.regulatory.build_regulatory_sources", lambda: [])

    doc = Document(id="d1", kind="news", source="boom", text="cached", ts=1.0)
    backend.set(cache_key("boom", "BTC"), [doc_to_dict(doc)], fetched_at=time.time())

    docs = base.collect("q", coin="BTC", offline=False)
    assert doc in docs  # 命中快取，_BoomSource.fetch 沒被呼叫（否則會直接 raise 炸掉整個測試）


def test_collect_online_default_cache_miss_goes_to_failed(monkeypatch, tmp_path):
    from trustforge.ingestion import base
    from trustforge.ingestion import cache as cache_mod

    backend = JsonCacheBackend(tmp_path / "cache.json")
    monkeypatch.setattr(cache_mod, "get_cache_backend", lambda: backend)

    boom = _BoomSource()
    monkeypatch.setattr("trustforge.ingestion.news.build_news_sources", lambda: [boom])
    monkeypatch.setattr("trustforge.ingestion.onchain.build_onchain_sources", lambda: [])
    monkeypatch.setattr("trustforge.ingestion.social.build_social_sources", lambda: [])
    monkeypatch.setattr("trustforge.ingestion.regulatory.build_regulatory_sources", lambda: [])

    failed: list = []
    docs = base.collect("q", coin="BTC", offline=False, _failed=failed)
    assert all(d.kind == "price" for d in docs)  # 只剩 OHLCV 價格事實，news 優雅降級
    assert "boom" in failed


def test_collect_explicit_sources_bypasses_cached_source_wrapping(tmp_path):
    """呼叫端明確傳 `sources=` 時（既有的注入點），不應被包一層
    CachedSource——維持原本可直接注入假來源做單元測試的能力。"""
    from trustforge.ingestion import base

    fake = _FakeSource("direct", kind="news")
    docs = base.collect("q", coin="BTC", sources=[fake], offline=False)
    assert fake.calls == [("q", "BTC")]  # 真的被直接呼叫了，不是被 CachedSource 攔截
    news_docs = [d for d in docs if d.kind == "news"]
    assert len(news_docs) == 1 and news_docs[0].source == "direct"


# ---------------------------------------------------------------------------
# scripts/fetch_scheduler.py：唯一打真 API 的地方
# ---------------------------------------------------------------------------

def _patch_registry(monkeypatch, sources: list[Source]) -> None:
    monkeypatch.setattr(fetch_scheduler, "build_registry", lambda: {s.name: s for s in sources})


def test_scheduler_writes_cache_with_fetched_at(monkeypatch, tmp_path):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    src = _FakeSource("coindesk", kind="news")
    _patch_registry(monkeypatch, [src])
    before = time.time()
    results, failures = fetch_scheduler.run_once(
        ["coindesk"], ["BTC"], backend, force=True, interval_overrides={}, stagger=0, dry_run=False,
    )
    after = time.time()
    assert results == [("coindesk:BTC", 1)]
    assert failures == []
    entry = backend.get(cache_key("coindesk", "BTC"))
    assert entry is not None
    assert before <= entry["fetched_at"] <= after
    assert src.calls == [("", "BTC")]


def test_scheduler_calls_registered_source_fetch(monkeypatch, tmp_path):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    src = _FakeSource("coindesk", kind="news")
    _patch_registry(monkeypatch, [src])
    fetch_scheduler.run_once(
        None, ["BTC", "ETH"], backend, force=True, interval_overrides={}, stagger=0, dry_run=False,
    )
    assert src.calls == [("", "BTC"), ("", "ETH")]


def test_scheduler_coin_agnostic_source_fetches_once_broadcasts_all_coins(monkeypatch, tmp_path):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    name = next(iter(COIN_AGNOSTIC_SOURCES))
    src = _FakeSource(name, kind="onchain")
    _patch_registry(monkeypatch, [src])
    coins = ["BTC", "ETH", "SOL"]
    results, failures = fetch_scheduler.run_once(
        None, coins, backend, force=True, interval_overrides={}, stagger=0, dry_run=False,
    )
    assert src.calls == [("", "")]  # 只真呼叫一次
    assert results == [(name, 1)]
    assert failures == []
    for c in coins:
        entry = backend.get(cache_key(name, c))
        assert entry is not None
        assert entry["docs"] == [doc_to_dict(src._docs[0])]


def test_scheduler_coin_agnostic_broadcast_backfills_partially_missing_coin(monkeypatch, tmp_path):
    """codex MEDIUM-2：coin-agnostic 廣播的新鮮度守門要檢查所有目標幣，不能
    只看 coins[0]。這裡模擬上一輪只有 BTC 廣播寫入成功、ETH 沒寫到（例如上
    一輪部分廣播失敗），下一輪即使 BTC 仍新鮮，也要因為 ETH 缺資料而重新真
    呼叫，並把 ETH 的 key 一併補齊——不能因為 coins[0]（BTC）新鮮就整源跳過，
    讓 ETH 空等一整個 refresh interval。"""
    backend = JsonCacheBackend(tmp_path / "cache.json")
    name = next(iter(COIN_AGNOSTIC_SOURCES))
    # 模擬上一輪：只有 BTC 廣播寫入成功，ETH 完全沒有 cache 資料。
    backend.set(cache_key(name, "BTC"), [], fetched_at=time.time())
    assert backend.get(cache_key(name, "ETH")) is None

    src = _FakeSource(name, kind="onchain")
    _patch_registry(monkeypatch, [src])
    coins = ["BTC", "ETH"]
    results, failures = fetch_scheduler.run_once(
        None, coins, backend, force=False, interval_overrides={name: 3600}, stagger=0, dry_run=False,
    )

    # ETH 缺資料 → 不能被 BTC 的新鮮度誤判整源跳過，必須真的重新呼叫一次。
    assert src.calls == [("", "")]
    assert results == [(name, 1)]
    assert failures == []
    # 廣播會重寫全部目標幣，缺的 ETH 這下補齊了。
    for c in coins:
        entry = backend.get(cache_key(name, c))
        assert entry is not None
        assert entry["docs"] == [doc_to_dict(src._docs[0])]


def test_scheduler_skips_fresh_entry_without_force(monkeypatch, tmp_path):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    backend.set(cache_key("coindesk", "BTC"), [], fetched_at=time.time())  # 剛寫入，很新鮮
    src = _FakeSource("coindesk", kind="news")
    _patch_registry(monkeypatch, [src])
    results, failures = fetch_scheduler.run_once(
        None, ["BTC"], backend, force=False, interval_overrides={"coindesk": 3600}, stagger=0, dry_run=False,
    )
    assert results == []
    assert failures == []
    assert src.calls == []  # 未達間隔，完全沒打


def test_scheduler_force_bypasses_freshness_guard(monkeypatch, tmp_path):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    backend.set(cache_key("coindesk", "BTC"), [], fetched_at=time.time())
    src = _FakeSource("coindesk", kind="news")
    _patch_registry(monkeypatch, [src])
    results, failures = fetch_scheduler.run_once(
        None, ["BTC"], backend, force=True, interval_overrides={"coindesk": 3600}, stagger=0, dry_run=False,
    )
    assert results == [("coindesk:BTC", 1)]
    assert failures == []
    assert src.calls == [("", "BTC")]


def test_scheduler_dry_run_never_calls_fetch_or_writes(monkeypatch, tmp_path):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    src = _FakeSource("coindesk", kind="news")
    _patch_registry(monkeypatch, [src])
    results, failures = fetch_scheduler.run_once(
        None, ["BTC"], backend, force=True, interval_overrides={}, stagger=0, dry_run=True,
    )
    assert results == []
    assert failures == []
    assert src.calls == []
    assert backend.get(cache_key("coindesk", "BTC")) is None


def test_scheduler_single_source_failure_does_not_abort_others(monkeypatch, tmp_path, capsys):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    boom = _FakeSource("coindesk", kind="news", raise_exc=RuntimeError("network down"))
    ok = _FakeSource("decrypt", kind="news")
    _patch_registry(monkeypatch, [boom, ok])
    results, failures = fetch_scheduler.run_once(
        None, ["BTC"], backend, force=True, interval_overrides={}, stagger=0, dry_run=False,
    )
    names = [r[0] for r in results]
    assert "coindesk:BTC" not in names
    assert "decrypt:BTC" in names
    # codex HIGH-1：真呼叫本身失敗（逾時/429/憑證錯/上游故障）不能被靜默吞掉
    # ——雖然不中斷其他來源，但仍要計入 failures，才能讓 main() 的 exit code
    # 反映「這次排程沒有把 coindesk 真的刷新到」這件事。
    assert failures == ["coindesk:BTC"]
    assert backend.get(cache_key("coindesk", "BTC")) is None
    err = capsys.readouterr().err
    assert "coindesk" in err and "network down" in err


def test_scheduler_onchain_invalid_payload_raises_and_preserves_old_cache(monkeypatch, tmp_path, capsys):
    """codex HIGH（#24+robustness，PR #55）驗收：`BlockchairStatsSource` 遇到
    無效 payload（`data` 為 `null`）必須 `raise`，走跟其他來源真呼叫失敗一樣
    的路徑——`run_once()` 計入 `failures`、**完全不覆寫**上一輪還能用的舊
    快取（不是靜靜寫入一筆帶目前時間戳的 "N/A" 假證據）。"""
    from trustforge.ingestion import onchain
    from trustforge.ingestion.onchain import BlockchairStatsSource

    backend = JsonCacheBackend(tmp_path / "cache.json")
    old_docs = [{
        "id": "old-blockchair-doc", "kind": "onchain", "source": "blockchair",
        "text": "上一輪的真實統計：區塊高度=956000", "url": "https://api.blockchair.com/bitcoin/stats",
        "ts": time.time() - 3600, "meta": {},
    }]
    backend.set(cache_key("blockchair", "BTC"), old_docs, time.time() - 3600)

    # 讓真正的 fetch() 邏輯自己走到驗證失敗、自己 raise（不是外部硬塞例外）——
    # 只換掉最底層的 HTTP GET，證明的是新加的驗證程式碼本身，不是測試假設。
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: b'{"data": null, "context": {"code": 200}}')
    src = BlockchairStatsSource()
    _patch_registry(monkeypatch, [src])

    results, failures = fetch_scheduler.run_once(
        ["blockchair"], ["BTC"], backend, force=True, interval_overrides={}, stagger=0, dry_run=False,
    )
    assert results == []
    assert failures == ["blockchair:BTC"]
    entry = backend.get(cache_key("blockchair", "BTC"))
    assert entry is not None
    assert entry["docs"] == old_docs  # 舊快取完全沒被覆蓋
    err = capsys.readouterr().err
    assert "blockchair" in err


def test_scheduler_onchain_bad_best_block_time_raises_and_preserves_old_cache(monkeypatch, tmp_path, capsys):
    """codex MEDIUM（PR #55，鏈上驗證最終閉合）驗收：`best_block_time`
    格式漂移（其餘統計欄位都合法）一樣要 raise，不能用 `time.time()`
    把上游回的歷史/不完整 envelope 偽裝成剛取得的新證據——`run_once()`
    要計入 failures、完全不覆寫舊快取。"""
    from trustforge.ingestion import onchain
    from trustforge.ingestion.onchain import BlockchairStatsSource

    backend = JsonCacheBackend(tmp_path / "cache.json")
    old_docs = [{
        "id": "old-blockchair-doc", "kind": "onchain", "source": "blockchair",
        "text": "上一輪的真實統計：區塊高度=956000", "url": "https://api.blockchair.com/bitcoin/stats",
        "ts": time.time() - 3600, "meta": {},
    }]
    backend.set(cache_key("blockchair", "BTC"), old_docs, time.time() - 3600)

    bad_payload = (
        b'{"data": {"blocks": 1, "difficulty": 2, "mempool_transactions": 3, '
        b'"transactions_24h": 4, "best_block_time": "not-a-date"}, "context": {"code": 200}}'
    )
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: bad_payload)
    src = BlockchairStatsSource()
    _patch_registry(monkeypatch, [src])

    results, failures = fetch_scheduler.run_once(
        ["blockchair"], ["BTC"], backend, force=True, interval_overrides={}, stagger=0, dry_run=False,
    )
    assert results == []
    assert failures == ["blockchair:BTC"]
    entry = backend.get(cache_key("blockchair", "BTC"))
    assert entry is not None
    assert entry["docs"] == old_docs  # 舊快取完全沒被覆蓋
    err = capsys.readouterr().err
    assert "blockchair" in err


def test_scheduler_onchain_sentinel_negative_value_raises_and_preserves_old_cache(monkeypatch, tmp_path, capsys):
    """codex MEDIUM 第 8 輪（PR #55，鏈上驗證最終閉合）驗收：型別合法但
    語意上不可能的 sentinel 值（`blocks: -1`，區塊高度不可能為負）必須
    raise，`run_once()` 計入 failures、完全不覆寫舊快取——不能把不可能
    的數值當真資料、包上新鮮時間戳發布出去覆蓋還能用的舊快取。"""
    from trustforge.ingestion import onchain
    from trustforge.ingestion.onchain import BlockchairStatsSource

    backend = JsonCacheBackend(tmp_path / "cache.json")
    old_docs = [{
        "id": "old-blockchair-doc", "kind": "onchain", "source": "blockchair",
        "text": "上一輪的真實統計：區塊高度=956000", "url": "https://api.blockchair.com/bitcoin/stats",
        "ts": time.time() - 3600, "meta": {},
    }]
    backend.set(cache_key("blockchair", "BTC"), old_docs, time.time() - 3600)

    sentinel_payload = (
        b'{"data": {"blocks": -1, "difficulty": 133869853540305.4, '
        b'"mempool_transactions": 82785, "transactions_24h": 573582, '
        b'"best_block_time": "2026-07-03 09:18:09"}, "context": {"code": 200}}'
    )
    fixed_now = datetime(2026, 7, 3, 9, 30, 0, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: sentinel_payload)
    monkeypatch.setattr(onchain.time, "time", lambda: fixed_now)
    src = BlockchairStatsSource()
    _patch_registry(monkeypatch, [src])

    results, failures = fetch_scheduler.run_once(
        ["blockchair"], ["BTC"], backend, force=True, interval_overrides={}, stagger=0, dry_run=False,
    )
    assert results == []
    assert failures == ["blockchair:BTC"]
    entry = backend.get(cache_key("blockchair", "BTC"))
    assert entry is not None
    assert entry["docs"] == old_docs  # 舊快取完全沒被覆蓋
    err = capsys.readouterr().err
    assert "blockchair" in err


def test_scheduler_onchain_stale_best_block_time_raises_and_preserves_old_cache(monkeypatch, tmp_path, capsys):
    """codex MEDIUM（PR #55，第 5 輪，有界新鮮度窗）驗收：`best_block_time`
    格式正確但超過 6 小時新鮮度窗（重放的陳舊 payload）一樣要 raise，
    `run_once()` 計入 failures、完全不覆寫舊快取。用固定注入的 now，
    不依賴真牆鐘。"""
    from trustforge.ingestion import onchain
    from trustforge.ingestion.onchain import BlockchairStatsSource

    fixed_now = datetime(2026, 7, 3, 9, 30, 0, tzinfo=timezone.utc)
    stale_bbt = (fixed_now - timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")

    backend = JsonCacheBackend(tmp_path / "cache.json")
    old_docs = [{
        "id": "old-blockchair-doc", "kind": "onchain", "source": "blockchair",
        "text": "上一輪的真實統計：區塊高度=956000", "url": "https://api.blockchair.com/bitcoin/stats",
        "ts": fixed_now.timestamp() - 3600, "meta": {},
    }]
    backend.set(cache_key("blockchair", "BTC"), old_docs, fixed_now.timestamp() - 3600)

    stale_payload = (
        b'{"data": {"blocks": 956480, "difficulty": 133869853540305.4, '
        b'"mempool_transactions": 82785, "transactions_24h": 573582, '
        b'"best_block_time": "' + stale_bbt.encode() + b'"}, "context": {"code": 200}}'
    )
    monkeypatch.setattr(onchain, "_fetch_url", lambda url: stale_payload)
    monkeypatch.setattr(onchain.time, "time", lambda: fixed_now.timestamp())
    src = BlockchairStatsSource()
    _patch_registry(monkeypatch, [src])

    results, failures = fetch_scheduler.run_once(
        ["blockchair"], ["BTC"], backend, force=True, interval_overrides={}, stagger=0, dry_run=False,
    )
    assert results == []
    assert failures == ["blockchair:BTC"]
    entry = backend.get(cache_key("blockchair", "BTC"))
    assert entry is not None
    assert entry["docs"] == old_docs  # 舊快取完全沒被覆蓋
    err = capsys.readouterr().err
    assert "blockchair" in err


def test_scheduler_rss_schema_drift_raises_and_preserves_old_cache(monkeypatch, tmp_path, capsys):
    """codex MEDIUM（PR #55，資料密度第二批最終閉合）驗收：共用
    `_parse_rss` 遇到合法 XML 但沒有任何 `<item>`/`<entry>`（schema drift／
    供應商回錯誤頁）必須 raise，`run_once()` 計入 failures、完全不覆寫
    舊快取（不是靜靜用 [] 覆蓋掉還能用的舊新聞）。"""
    from trustforge.ingestion import news
    from trustforge.ingestion.news import CoinDeskRSSSource

    backend = JsonCacheBackend(tmp_path / "cache.json")
    old_docs = [{
        "id": "old-coindesk-doc", "kind": "news", "source": "coindesk",
        "text": "上一輪的真實新聞標題", "url": "https://www.coindesk.com/old-article",
        "ts": time.time() - 3600, "meta": {},
    }]
    backend.set(cache_key("coindesk", "BTC"), old_docs, time.time() - 3600)

    drifted_feed = b'<?xml version="1.0"?><feed xmlns="urn:other"><weird-entry/></feed>'
    monkeypatch.setattr(news, "_fetch_url", lambda url: drifted_feed)
    src = CoinDeskRSSSource()
    _patch_registry(monkeypatch, [src])

    results, failures = fetch_scheduler.run_once(
        ["coindesk"], ["BTC"], backend, force=True, interval_overrides={}, stagger=0, dry_run=False,
    )
    assert results == []
    assert failures == ["coindesk:BTC"]
    entry = backend.get(cache_key("coindesk", "BTC"))
    assert entry is not None
    assert entry["docs"] == old_docs  # 舊快取完全沒被覆蓋
    err = capsys.readouterr().err
    assert "coindesk" in err


def test_scheduler_rss_subfield_drift_raises_and_preserves_old_cache(monkeypatch, tmp_path, capsys):
    """codex HIGH 第 6 輪（PR #55，RSS 資料品質最終閉合）驗收：`<item>`
    容器都在，但供應商把 title/link/description/pubDate 全改名（子欄位
    drift），導致每筆解析成空白 title/空 URL/ts=0——`run_once()` 必須
    計入 failures、完全不覆寫舊快取（不能靜靜用垃圾文件覆蓋掉還能用的
    舊新聞）。"""
    from trustforge.ingestion import news
    from trustforge.ingestion.news import CoinDeskRSSSource

    backend = JsonCacheBackend(tmp_path / "cache.json")
    old_docs = [{
        "id": "old-coindesk-doc", "kind": "news", "source": "coindesk",
        "text": "上一輪的真實新聞標題", "url": "https://www.coindesk.com/old-article",
        "ts": time.time() - 3600, "meta": {},
    }]
    backend.set(cache_key("coindesk", "BTC"), old_docs, time.time() - 3600)

    subfield_drifted_feed = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>CoinDesk</title>
    <item>
      <headline>Bitcoin BTC surges past $70,000</headline>
      <permalink>https://www.coindesk.com/markets/2026/08/01/btc-surge</permalink>
    </item>
  </channel>
</rss>"""
    monkeypatch.setattr(news, "_fetch_url", lambda url: subfield_drifted_feed)
    src = CoinDeskRSSSource()
    _patch_registry(monkeypatch, [src])

    results, failures = fetch_scheduler.run_once(
        ["coindesk"], ["BTC"], backend, force=True, interval_overrides={}, stagger=0, dry_run=False,
    )
    assert results == []
    assert failures == ["coindesk:BTC"]
    entry = backend.get(cache_key("coindesk", "BTC"))
    assert entry is not None
    assert entry["docs"] == old_docs  # 舊快取完全沒被覆蓋
    err = capsys.readouterr().err
    assert "coindesk" in err


def test_scheduler_coin_agnostic_fetch_failure_is_counted_into_failures(monkeypatch, tmp_path, capsys):
    """codex HIGH-1：coin-agnostic 來源真呼叫失敗，同樣要計入 failures（不是
    只印警告就當沒事）。"""
    backend = JsonCacheBackend(tmp_path / "cache.json")
    name = next(iter(COIN_AGNOSTIC_SOURCES))
    boom = _FakeSource(name, kind="onchain", raise_exc=RuntimeError("upstream 500"))
    _patch_registry(monkeypatch, [boom])
    results, failures = fetch_scheduler.run_once(
        None, ["BTC", "ETH"], backend, force=True, interval_overrides={}, stagger=0, dry_run=False,
    )
    assert results == []
    assert failures == [name]
    assert backend.get(cache_key(name, "BTC")) is None
    err = capsys.readouterr().err
    assert name in err and "upstream 500" in err


def test_main_returns_nonzero_exit_code_when_all_sources_fetch_fails(monkeypatch, tmp_path):
    """codex HIGH-1 驗收標準：全來源真呼叫失敗，main() 絕不能回 0（否則
    cron/監控會誤判成功，連三輪就會撞上 cache 硬過期、產品斷資料）。"""
    monkeypatch.setenv("TRUSTFORGE_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("CACHE_BACKEND", "json")

    boom1 = _FakeSource("coindesk", kind="news", raise_exc=TimeoutError("timed out"))
    boom2 = _FakeSource("decrypt", kind="news", raise_exc=RuntimeError("429 too many requests"))
    _patch_registry(monkeypatch, [boom1, boom2])

    rc = fetch_scheduler.main([
        "--source", "coindesk", "--source", "decrypt", "--coin", "BTC", "--force",
    ])
    assert rc == 1


def test_scheduler_unknown_source_name_skips_without_crash(tmp_path, capsys):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    results, failures = fetch_scheduler.run_once(
        ["totally-unknown"], ["BTC"], backend, force=True, interval_overrides={}, stagger=0, dry_run=False,
    )
    assert results == []
    assert failures == []
    assert "totally-unknown" in capsys.readouterr().err


def test_scheduler_cache_write_failure_is_reported_not_silently_ok(monkeypatch, tmp_path, capsys):
    """codex HIGH-2：真呼叫成功，但寫入 cache backend 失敗（如 DynamoDB 憑證
    問題）——`run_once()` 必須把這個目標算進 `failures`，不能因為「至少打到
    真 API」就塞進 `results` 當成功。預設也不該偷偷 fallback 寫本地 JSON
    （沒有 opt-in）。"""
    monkeypatch.delenv("TRUSTFORGE_CACHE_JSON_FALLBACK", raising=False)
    fallback_path = tmp_path / "should_not_exist.json"
    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_PATH", str(fallback_path))

    broken = DynamoDBCache(table_name="fake-table")
    monkeypatch.setattr(
        broken, "_get_table",
        MagicMock(side_effect=RuntimeError("no aws credentials / table not found")),
    )
    src = _FakeSource("coindesk", kind="news")
    _patch_registry(monkeypatch, [src])

    results, failures = fetch_scheduler.run_once(
        None, ["BTC"], broken, force=True, interval_overrides={}, stagger=0, dry_run=False,
    )

    assert results == []
    assert failures == ["coindesk:BTC"]
    assert src.calls == [("", "BTC")]  # 真呼叫確實發生了（不是被 API 失敗擋掉）
    assert not fallback_path.exists()  # 沒有偷偷 fallback 寫本地
    err = capsys.readouterr().err
    assert "coindesk" in err and "cache 寫入失敗" in err


def test_scheduler_coin_agnostic_broadcast_write_failure_is_reported(monkeypatch, tmp_path):
    """coin-agnostic 來源廣播寫入時，若任一幣別的 cache 寫入失敗，整個來源
    要算進 failures（不能因為「有些幣別寫成功」就當整體成功）。"""
    monkeypatch.delenv("TRUSTFORGE_CACHE_JSON_FALLBACK", raising=False)
    fallback_path = tmp_path / "should_not_exist.json"
    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_PATH", str(fallback_path))

    broken = DynamoDBCache(table_name="fake-table")
    monkeypatch.setattr(
        broken, "_get_table",
        MagicMock(side_effect=RuntimeError("no aws credentials / table not found")),
    )
    name = next(iter(COIN_AGNOSTIC_SOURCES))
    src = _FakeSource(name, kind="onchain")
    _patch_registry(monkeypatch, [src])

    results, failures = fetch_scheduler.run_once(
        None, ["BTC", "ETH"], broken, force=True, interval_overrides={}, stagger=0, dry_run=False,
    )

    assert results == []
    assert failures == [name]
    assert src.calls == [("", "")]  # 真呼叫確實只發生一次


# ---------------------------------------------------------------------------
# COIN_KEYED_BATCH_SOURCES（生產事故修復：coingecko-price 429 風暴根因）
# ---------------------------------------------------------------------------
#
# 舊版 coingecko-price 未歸類進任何 batch 集合，落入逐幣迴圈被 run_once()
# 呼叫 5 次（每幣一次）；`_get_price_data()` 的 process 級記憶體快取只在
# 「成功」時才寫入，第一幣真呼叫若 429（記憶體快取仍是 None），下一幣又會
# 再觸發一次真呼叫——5 幣依序把同一個已在限流的端點又打了 5 次，正是生產
# log `coingecko-price[BTC/ETH/SOL/BNB/XRP]` 各自 429 的根因。以下驗證
# 歸類進 `COIN_KEYED_BATCH_SOURCES` 後，不論成功/失敗，`run_once()` 對這類
# 來源永遠只呼叫 `source.fetch()` 一次。

def test_coingecko_price_is_registered_as_coin_keyed_batch_source():
    """生產事故修復驗收：`coingecko-price` 必須在 `COIN_KEYED_BATCH_SOURCES`
    裡，才不會落入逐幣迴圈被呼叫 5 次。"""
    assert "coingecko-price" in COIN_KEYED_BATCH_SOURCES


def test_scheduler_coin_keyed_batch_source_fetches_once_demuxes_by_coin(monkeypatch, tmp_path):
    """一次真呼叫回應涵蓋全部目標幣（用 `meta["coin"]` 區分），排程器只呼叫
    `source.fetch()` 一次，並依各 Document 自帶的 `meta["coin"]` 分流寫入
    對應幣別的 cache key——**不是**廣播同一份完整結果（跟 coin-agnostic 不同：
    每個幣的 cache 內容天生只含自己，不會混進其他幣的資料）。"""
    backend = JsonCacheBackend(tmp_path / "cache.json")
    name = next(iter(COIN_KEYED_BATCH_SOURCES))
    coins = ["BTC", "ETH", "SOL"]
    docs = [
        Document(id=f"{name}-{c}", kind="price_live", source=name,
                 text=f"{c} price", ts=1.0, meta={"coin": c})
        for c in coins
    ]
    src = _FakeSource(name, kind="price_live", docs=docs)
    _patch_registry(monkeypatch, [src])

    results, failures = fetch_scheduler.run_once(
        None, coins, backend, force=True, interval_overrides={}, stagger=0, dry_run=False,
    )

    assert src.calls == [("", "")]  # 只真呼叫一次，不是每幣各呼叫一次
    assert results == [(name, 3)]
    assert failures == []
    for c in coins:
        entry = backend.get(cache_key(name, c))
        assert entry is not None
        # 每個幣的 cache 只含自己的那一筆，不含其他幣的資料。
        assert entry["docs"] == [doc_to_dict(d) for d in docs if d.meta["coin"] == c]


def test_scheduler_coin_keyed_batch_source_failure_does_not_retry_per_coin(monkeypatch, tmp_path):
    """生產事故修復核心驗收：真呼叫失敗（模擬 429）時，`run_once()` 對這類
    來源仍然只呼叫 `source.fetch()` 一次（不會像舊版逐幣迴圈那樣對同一個
    已限流的端點重複觸發 `len(coins)` 次）。"""
    backend = JsonCacheBackend(tmp_path / "cache.json")
    name = next(iter(COIN_KEYED_BATCH_SOURCES))
    boom = _FakeSource(name, kind="price_live", raise_exc=RuntimeError("429 too many requests"))
    _patch_registry(monkeypatch, [boom])
    coins = ["BTC", "ETH", "SOL", "BNB", "XRP"]

    results, failures = fetch_scheduler.run_once(
        None, coins, backend, force=True, interval_overrides={}, stagger=0, dry_run=False,
    )

    assert boom.calls == [("", "")], (
        f"真呼叫失敗仍只該呼叫 1 次，實得 {len(boom.calls)} 次：{boom.calls}——"
        "若這裡呼叫次數等於幣數，代表退回了生產事故的行為（同一個限流端點被連續重打）"
    )
    assert results == []
    assert failures == [name]
    for c in coins:
        assert backend.get(cache_key(name, c)) is None


def test_scheduler_coin_keyed_batch_unknown_coin_in_response_is_dropped(monkeypatch, tmp_path):
    """回應裡若混進不在目標幣清單內的 `meta["coin"]`（不應發生，純防禦性
    測試），該筆文件直接被捨棄，不會誤塞進任何一個幣的 cache key。"""
    backend = JsonCacheBackend(tmp_path / "cache.json")
    name = next(iter(COIN_KEYED_BATCH_SOURCES))
    coins = ["BTC", "ETH"]
    docs = [
        Document(id=f"{name}-BTC", kind="price_live", source=name, text="BTC", ts=1.0,
                 meta={"coin": "BTC"}),
        Document(id=f"{name}-DOGE", kind="price_live", source=name, text="DOGE", ts=1.0,
                 meta={"coin": "DOGE"}),  # 非目標幣，須被捨棄
    ]
    src = _FakeSource(name, kind="price_live", docs=docs)
    _patch_registry(monkeypatch, [src])

    results, failures = fetch_scheduler.run_once(
        None, coins, backend, force=True, interval_overrides={}, stagger=0, dry_run=False,
    )

    assert failures == []
    assert results == [(name, 1)]  # 只有 BTC 那 1 筆落地
    assert backend.get(cache_key(name, "BTC"))["docs"] == [doc_to_dict(docs[0])]
    assert backend.get(cache_key(name, "ETH"))["docs"] == []


def test_scheduler_coin_keyed_batch_broadcast_write_failure_is_reported(monkeypatch, tmp_path):
    """分流寫入時若任一幣別的 cache 寫入失敗，整個來源要算進 failures（同
    coin-agnostic 分支的既有慣例）。"""
    monkeypatch.delenv("TRUSTFORGE_CACHE_JSON_FALLBACK", raising=False)
    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_PATH", str(tmp_path / "should_not_exist.json"))

    broken = DynamoDBCache(table_name="fake-table")
    monkeypatch.setattr(
        broken, "_get_table",
        MagicMock(side_effect=RuntimeError("no aws credentials / table not found")),
    )
    name = next(iter(COIN_KEYED_BATCH_SOURCES))
    coins = ["BTC", "ETH"]
    docs = [
        Document(id=f"{name}-{c}", kind="price_live", source=name, text=c, ts=1.0,
                 meta={"coin": c})
        for c in coins
    ]
    src = _FakeSource(name, kind="price_live", docs=docs)
    _patch_registry(monkeypatch, [src])

    results, failures = fetch_scheduler.run_once(
        None, coins, broken, force=True, interval_overrides={}, stagger=0, dry_run=False,
    )

    assert results == []
    assert failures == [name]
    assert src.calls == [("", "")]  # 真呼叫確實只發生一次


def test_scheduler_coin_keyed_batch_dry_run_never_calls_fetch(monkeypatch, tmp_path):
    backend = JsonCacheBackend(tmp_path / "cache.json")
    name = next(iter(COIN_KEYED_BATCH_SOURCES))
    src = _FakeSource(name, kind="price_live", docs=[])
    _patch_registry(monkeypatch, [src])

    results, failures = fetch_scheduler.run_once(
        None, ["BTC", "ETH"], backend, force=True, interval_overrides={}, stagger=0, dry_run=True,
    )
    assert results == []
    assert failures == []
    assert src.calls == []


def test_scheduler_coin_keyed_batch_freshness_guard_needs_all_coins_fresh(monkeypatch, tmp_path):
    """同 coin-agnostic 分支的 codex MEDIUM-2 考量：任一目標幣缺資料就視為
    不新鮮，重新真呼叫一次順便補齊缺的幣。"""
    backend = JsonCacheBackend(tmp_path / "cache.json")
    name = next(iter(COIN_KEYED_BATCH_SOURCES))
    backend.set(cache_key(name, "BTC"), [], fetched_at=time.time())
    assert backend.get(cache_key(name, "ETH")) is None

    docs = [
        Document(id=f"{name}-{c}", kind="price_live", source=name, text=c, ts=1.0,
                 meta={"coin": c})
        for c in ("BTC", "ETH")
    ]
    src = _FakeSource(name, kind="price_live", docs=docs)
    _patch_registry(monkeypatch, [src])

    results, failures = fetch_scheduler.run_once(
        None, ["BTC", "ETH"], backend, force=False, interval_overrides={name: 3600}, stagger=0,
        dry_run=False,
    )
    assert src.calls == [("", "")]
    assert results == [(name, 2)]
    assert failures == []


# ---------------------------------------------------------------------------
# CoinGecko 逐幣來源額外 stagger 下限（生產事故修復：避免 keyless 5-15/min
# 短時間密集呼叫瞬間爆量）
# ---------------------------------------------------------------------------

def test_effective_stagger_floors_coingecko_sources(monkeypatch):
    """CoinGecko 逐幣來源（coingecko-sentiment/coingecko-dev）取
    `max(使用者傳入的 --stagger, 下限)`；使用者傳入更大值時不被下限蓋掉。"""
    assert fetch_scheduler._effective_stagger("coingecko-sentiment", 1.0) == (
        fetch_scheduler._COINGECKO_STAGGER_FLOOR_SECONDS
    )
    assert fetch_scheduler._effective_stagger("coingecko-dev", 0.0) == (
        fetch_scheduler._COINGECKO_STAGGER_FLOOR_SECONDS
    )
    bigger = fetch_scheduler._COINGECKO_STAGGER_FLOOR_SECONDS + 10.0
    assert fetch_scheduler._effective_stagger("coingecko-sentiment", bigger) == bigger


def test_effective_stagger_does_not_affect_other_sources():
    """非 CoinGecko 逐幣來源（如 reddit）原樣沿用呼叫端傳入的 --stagger，
    不受 CoinGecko 下限影響。"""
    assert fetch_scheduler._effective_stagger("reddit-bitcoin", 1.0) == 1.0
    assert fetch_scheduler._effective_stagger("coindesk", 0.0) == 0.0


def test_scheduler_coingecko_percoin_source_sleeps_at_least_floor(monkeypatch, tmp_path):
    """端到端：`run_once()` 對 CoinGecko 逐幣來源（sentiment/dev，未歸類進
    `COIN_KEYED_BATCH_SOURCES`）逐幣呼叫之間的實際 sleep 時間，須套用
    CoinGecko stagger 下限，即使呼叫端傳入的 `--stagger` 遠小於下限。"""
    backend = JsonCacheBackend(tmp_path / "cache.json")
    src = _FakeSource("coingecko-sentiment", kind="sentiment")
    _patch_registry(monkeypatch, [src])

    sleeps: list[float] = []
    monkeypatch.setattr(fetch_scheduler.time, "sleep", lambda s: sleeps.append(s))

    fetch_scheduler.run_once(
        None, ["BTC", "ETH"], backend, force=True, interval_overrides={}, stagger=0.5,
        dry_run=False,
    )
    assert sleeps == [fetch_scheduler._COINGECKO_STAGGER_FLOOR_SECONDS] * 2


def test_main_returns_nonzero_exit_code_when_cache_write_fails(monkeypatch, tmp_path):
    """codex HIGH-2：CLI 層 `main()` 對「真呼叫成功但 cache 寫入失敗」的情況
    要回傳非零 exit code，讓 cron/監控看得到，而不是誤報 exit 0。"""
    monkeypatch.delenv("TRUSTFORGE_CACHE_JSON_FALLBACK", raising=False)
    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_PATH", str(tmp_path / "should_not_exist.json"))
    monkeypatch.setenv("CACHE_BACKEND", "dynamodb")
    monkeypatch.delenv("TRUSTFORGE_CACHE_TABLE", raising=False)

    src = _FakeSource("coindesk", kind="news")
    _patch_registry(monkeypatch, [src])

    def _broken_get_cache_backend():
        broken = DynamoDBCache(table_name="fake-table")
        broken._get_table = MagicMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("no aws credentials / table not found")
        )
        return broken

    monkeypatch.setattr(fetch_scheduler, "get_cache_backend", _broken_get_cache_backend)

    rc = fetch_scheduler.main(["--source", "coindesk", "--coin", "BTC", "--force"])
    assert rc == 1


def test_main_returns_zero_exit_code_when_all_writes_succeed(monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTFORGE_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("CACHE_BACKEND", "json")

    src = _FakeSource("coindesk", kind="news")
    _patch_registry(monkeypatch, [src])

    rc = fetch_scheduler.main(["--source", "coindesk", "--coin", "BTC", "--force"])
    assert rc == 0


# ---------------------------------------------------------------------------
# Phase3：main() 收尾寫排程 run record（/status「最近排程執行」用），見
# `trustforge/scheduler_log.py`。
# ---------------------------------------------------------------------------

def test_main_persists_scheduler_run_record_on_success(monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTFORGE_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("CACHE_BACKEND", "json")

    src = _FakeSource("coindesk", kind="news")
    _patch_registry(monkeypatch, [src])

    rc = fetch_scheduler.main(["--source", "coindesk", "--coin", "BTC", "--force"])
    assert rc == 0

    record = scheduler_log.get_last_scheduler_run()
    assert record is not None
    assert record["success_count"] == 1
    assert record["failure_count"] == 0
    assert record["failures"] == []


def test_main_dry_run_does_not_persist_scheduler_run_record(monkeypatch, tmp_path):
    """--dry-run 沒真的呼叫/寫入任何東西，不該留下一筆誤導成「有真執行」的紀錄。"""
    monkeypatch.setenv("TRUSTFORGE_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("CACHE_BACKEND", "json")

    src = _FakeSource("coindesk", kind="news")
    _patch_registry(monkeypatch, [src])

    rc = fetch_scheduler.main(["--dry-run", "--source", "coindesk", "--coin", "BTC"])
    assert rc == 0
    assert scheduler_log.get_last_scheduler_run() is None


def test_main_scheduler_run_record_write_failure_does_not_change_exit_code(
    monkeypatch, tmp_path
):
    """run log 寫入失敗（primary backend 掛掉）不該讓排程的 exit code 被誤判——
    真正的成功/失敗語意只看 `run_once()` 算出的 `failures`，run log 只是旁路的
    執行摘要（見 `scheduler_log.append_scheduler_run()` 內部已吞例外的 fallback
    邏輯，這裡用 `fetch_scheduler.main()` 端到端驗證這個邊界）。"""
    monkeypatch.setenv("TRUSTFORGE_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("CACHE_BACKEND", "json")

    src = _FakeSource("coindesk", kind="news")
    _patch_registry(monkeypatch, [src])

    class BrokenPrimaryLog(scheduler_log.SchedulerRunLog):
        def append(self, record):
            raise RuntimeError("scheduler run 表掛了（IAM/建表問題）")

        def read_all(self):
            return []

    monkeypatch.setattr(scheduler_log, "get_scheduler_run_log", lambda: BrokenPrimaryLog())

    rc = fetch_scheduler.main(["--source", "coindesk", "--coin", "BTC", "--force"])
    assert rc == 0  # 真呼叫/cache 寫入都成功，run log 寫入失敗不影響 exit code

    # primary（BrokenPrimaryLog）寫入失敗 → append_scheduler_run() 自動 fallback
    # 寫本地 JsonlSchedulerRunLog，直接讀該 backend 確認 fallback 真的落地。
    fallback_records = scheduler_log.JsonlSchedulerRunLog().read_all()
    assert len(fallback_records) == 1
    assert fallback_records[0]["success_count"] == 1


def test_scheduler_list_sources_cli(capsys):
    rc = fetch_scheduler.main(["--list-sources"])
    assert rc == 0
    out = capsys.readouterr().out
    for name in ("coindesk", "decrypt", "alternative-me-fng", "blockchain-info",
                 "reddit-cryptocurrency", "reddit-bitcoin", "sec-gov"):
        assert name in out


def test_scheduler_cli_dry_run_end_to_end(monkeypatch, tmp_path, capsys):
    """CLI 層 `main(["--dry-run", ...])` 真的完全不打任何真連接器 API。"""
    monkeypatch.setenv("TRUSTFORGE_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("CACHE_BACKEND", "json")

    def _boom_fetch_url(url):  # pragma: no cover
        raise AssertionError(f"--dry-run 不該打真 API：{url}")

    from trustforge.ingestion import news, social, onchain, regulatory
    monkeypatch.setattr(news, "_fetch_url", _boom_fetch_url)
    monkeypatch.setattr(social, "_fetch_url", _boom_fetch_url)
    monkeypatch.setattr(onchain, "_fetch_url", _boom_fetch_url)
    monkeypatch.setattr(regulatory, "_fetch_url", _boom_fetch_url)

    rc = fetch_scheduler.main(["--dry-run", "--source", "coindesk", "--coin", "BTC"])
    assert rc == 0


# ---------------------------------------------------------------------------
# codex HIGH-3：`--probe` canary（不依賴 freshness，deploy 部署後同步驗證用）
# ---------------------------------------------------------------------------

def test_probe_succeeds_when_both_tables_are_writable(monkeypatch, tmp_path):
    """happy path：cache/cost-ledger 都能真的 PutItem+GetItem，probe 回 0。"""
    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_PATH", str(tmp_path / "cache.json"))
    monkeypatch.setenv("TRUSTFORGE_COST_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setenv("CACHE_BACKEND", "json")
    monkeypatch.setenv("COST_LEDGER_BACKEND", "jsonl")

    rc = fetch_scheduler.main(["--probe"])
    assert rc == 0

    # 讀回驗證 canary 真的落地了（不是隨便回 0）。
    backend = get_cache_backend()
    entry = backend.get(cache_key(fetch_scheduler._PROBE_SOURCE, fetch_scheduler._PROBE_COIN))
    assert entry is not None
    assert entry["docs"][0]["text"].startswith("probe-")

    ledger = JsonlLedger()
    records = [r for r in ledger.read_all() if r.get("run_id") == fetch_scheduler._PROBE_SOURCE]
    assert len(records) == 1


def test_probe_cache_backend_uses_bounded_timeout_for_dynamodb(monkeypatch):
    """codex HIGH（probe 真正有界化）：`_probe_cache_backend()`（`CACHE_BACKEND`
    未顯式設 json 時）必須帶明確短 timeout/重試參數建構 `DynamoDBCache`，不能
    沿用 `get_cache_backend()` 的無 timeout 版本——否則 degraded/blackholed
    的 DynamoDB（DNS/網路/節流異常導致連線或讀取長時間不回應）會讓 boto3
    預設 timeout/重試把單次呼叫拖到數分鐘，部署 gate 的「有界」假設就不成立
    （見 `scripts/fetch_scheduler.py::run_probe()` 上方 `_PROBE_DYNAMODB_*`
    常數註解）。比照 `web.py::_home_overview_backend()` 既有的驗證慣例：直接
    斷言建構出來的 backend 實例帶著預期的 timeout/重試參數，不需要真的模擬
    網路 hang（那需要真連線，不是 unit test 該做的事）。"""
    monkeypatch.delenv("CACHE_BACKEND", raising=False)

    backend = fetch_scheduler._probe_cache_backend()
    assert type(backend).__name__ == "DynamoDBCache"
    assert backend._connect_timeout == fetch_scheduler._PROBE_DYNAMODB_CONNECT_TIMEOUT_SECONDS
    assert backend._read_timeout == fetch_scheduler._PROBE_DYNAMODB_READ_TIMEOUT_SECONDS
    assert backend._max_attempts == fetch_scheduler._PROBE_DYNAMODB_MAX_ATTEMPTS


def test_probe_cache_backend_json_branch_has_no_dynamodb_timeout_concept(monkeypatch):
    """`CACHE_BACKEND=json` 時是本機檔案 I/O，本來就不會 hang，`_probe_cache_backend()`
    要回傳一般的 `JsonCacheBackend()`，不該誤帶 DynamoDB 的 timeout 參數（該
    類別也沒有這種建構參數）。"""
    monkeypatch.setenv("CACHE_BACKEND", "json")

    backend = fetch_scheduler._probe_cache_backend()
    assert type(backend).__name__ == "JsonCacheBackend"


def test_probe_ledger_backend_uses_bounded_timeout_for_dynamodb(monkeypatch):
    """同上，`_probe_ledger_backend()`（`COST_LEDGER_BACKEND=dynamodb` 時）
    也要帶一樣的明確短 timeout/重試參數建構 `DynamoDBLedger`，不能沿用
    `get_ledger()` 的無 timeout 版本，理由同 cache 那份。"""
    monkeypatch.setenv("COST_LEDGER_BACKEND", "dynamodb")

    ledger = fetch_scheduler._probe_ledger_backend()
    assert type(ledger).__name__ == "DynamoDBLedger"
    assert ledger._connect_timeout == fetch_scheduler._PROBE_DYNAMODB_CONNECT_TIMEOUT_SECONDS
    assert ledger._read_timeout == fetch_scheduler._PROBE_DYNAMODB_READ_TIMEOUT_SECONDS
    assert ledger._max_attempts == fetch_scheduler._PROBE_DYNAMODB_MAX_ATTEMPTS


def test_probe_ledger_backend_jsonl_branch_default(monkeypatch):
    """`COST_LEDGER_BACKEND` 未設（預設 jsonl）時，`_probe_ledger_backend()`
    要回傳一般的 `JsonlLedger()`，本機檔案 I/O 不需要 timeout 概念。"""
    monkeypatch.delenv("COST_LEDGER_BACKEND", raising=False)

    ledger = fetch_scheduler._probe_ledger_backend()
    assert isinstance(ledger, JsonlLedger)


def test_probe_bounded_timeout_actually_reaches_boto3_config_on_degraded_dynamodb(monkeypatch):
    """codex HIGH（degraded/blackholed DynamoDB mock）：即使 DynamoDB 端已經
    降級（連線/讀取會被 explicit timeout 中止而不是無限等待——這裡直接模擬
    `_get_table()` 建 client 時真的丟出 botocore 風格的連線逾時例外，代表
    「該次呼叫在 bounded 時間內 raise/終止」，而不是被 boto3 預設值拖著空等），
    probe 必須在（模擬的）bounded 時間內拿到終態並正確判定失敗（非零退出），
    不會被卡住、也不會誤判成功。"""
    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_PATH", "unused-cache.json")
    monkeypatch.setenv("CACHE_BACKEND", "dynamodb")

    class _DegradedTable:
        """模擬 degraded/blackholed DynamoDB：呼叫本身不會無限 hang（explicit
        timeout 已經在 botocore 層擋掉），而是很快就丟出逾時類例外——這正是
        `connect_timeout`/`read_timeout`/`max_attempts` 存在的目的。"""

        def put_item(self, **_kwargs):
            raise TimeoutError(
                "Connect timeout on endpoint URL (degraded DynamoDB, "
                "bounded by explicit connect_timeout/read_timeout, not boto3 預設)"
            )

    degraded_backend = DynamoDBCache(table_name="fake-table")
    degraded_backend._table = _DegradedTable()  # 繞過 boto3，直接注入「已降級」的 Table
    monkeypatch.setattr(fetch_scheduler, "_probe_cache_backend", lambda: degraded_backend)

    started = time.monotonic()
    rc = fetch_scheduler.run_probe()
    elapsed = time.monotonic() - started

    assert rc == 1  # 真的失敗（degraded backend），不是誤判成功
    # 這裡的重點不是「跑多快」（mock 本來就是瞬間 raise），而是證明 probe
    # 遇到降級 backend 時會直接往上拋出、被 run_probe() 接住轉成非零退出，
    # 不會卡在某個無界等待點——跟部署 gate 的 90s poll deadline 契約一致。
    assert elapsed < 5.0


def test_probe_fails_nonzero_when_cache_put_item_denied(monkeypatch):
    """codex HIGH-3 核心案例：cache 表 PutItem 被拒（權限被 permission
    boundary/SCP/table policy 擋掉）→ probe 必須非零退出。"""
    broken = DynamoDBCache(table_name="fake-table")
    mock_table = MagicMock()
    mock_table.put_item.side_effect = RuntimeError(
        "AccessDeniedException: User is not authorized to perform: dynamodb:PutItem"
    )
    broken._table = mock_table  # 繞過 boto3，模擬已建好但被拒的 Table

    monkeypatch.setattr(fetch_scheduler, "_probe_cache_backend", lambda: broken)

    rc = fetch_scheduler.run_probe()
    assert rc == 1
    mock_table.put_item.assert_called_once()  # 真的觸發了一次 PutItem，不是被略過


def test_probe_fails_nonzero_when_cache_get_item_denied_after_put_succeeds(monkeypatch):
    """PutItem 過得去、但 GetItem 被拒（或讀回內容跟剛寫的對不上）——這正是
    只驗 PutItem 會漏掉的方向，probe 一樣要非零退出。"""
    broken = DynamoDBCache(table_name="fake-table")
    mock_table = MagicMock()
    mock_table.put_item.return_value = {}
    mock_table.get_item.side_effect = RuntimeError(
        "AccessDeniedException: User is not authorized to perform: dynamodb:GetItem"
    )
    broken._table = mock_table

    monkeypatch.setattr(fetch_scheduler, "_probe_cache_backend", lambda: broken)

    rc = fetch_scheduler.run_probe()
    assert rc == 1
    mock_table.put_item.assert_called_once()
    mock_table.get_item.assert_called_once()


def test_probe_cache_readback_uses_consistent_read(monkeypatch, tmp_path):
    """codex MEDIUM：固定 canary key 若用預設最終一致讀，PutItem 後立刻讀可能
    因複寫延遲讀到上一輪的舊 sentinel，變成非確定性誤判。probe 的讀回必須
    帶 `ConsistentRead=True`。"""
    backend = DynamoDBCache(table_name="fake-table")
    mock_table = MagicMock()
    state: dict[str, str] = {}

    def _fake_put_item(Item):  # noqa: N803 — 對齊 boto3 參數名
        state["docs_json"] = Item["docs_json"]
        return {}

    def _fake_get_item(Key, ConsistentRead=False):  # noqa: N803
        assert ConsistentRead is True, "probe 讀回必須帶 ConsistentRead=True，不能吃預設最終一致讀"
        return {"Item": {"docs_json": state["docs_json"], "fetched_at": time.time()}}

    mock_table.put_item.side_effect = _fake_put_item
    mock_table.get_item.side_effect = _fake_get_item
    backend._table = mock_table

    monkeypatch.setattr(fetch_scheduler, "_probe_cache_backend", lambda: backend)
    monkeypatch.setattr(
        fetch_scheduler, "_probe_ledger_backend", lambda: JsonlLedger(path=tmp_path / "ledger.jsonl")
    )

    rc = fetch_scheduler.run_probe()

    assert rc == 0
    mock_table.get_item.assert_called_once()
    assert mock_table.get_item.call_args.kwargs["ConsistentRead"] is True


def test_probe_fails_nonzero_when_ledger_put_item_denied(monkeypatch, tmp_path):
    """cost-ledger 表 PutItem 被拒 → probe 也要非零退出（即使 cache 表沒問題）。"""
    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_PATH", str(tmp_path / "cache.json"))
    monkeypatch.setenv("CACHE_BACKEND", "json")

    broken_ledger = DynamoDBLedger(table_name="fake-ledger-table")
    mock_table = MagicMock()
    mock_table.put_item.side_effect = RuntimeError(
        "AccessDeniedException: User is not authorized to perform: dynamodb:PutItem"
    )
    broken_ledger._table = mock_table
    monkeypatch.setattr(fetch_scheduler, "_probe_ledger_backend", lambda: broken_ledger)

    rc = fetch_scheduler.run_probe()
    assert rc == 1
    mock_table.put_item.assert_called_once()
    mock_table.get_item.assert_not_called()  # PutItem 都失敗了，不該還去驗讀回
    mock_table.scan.assert_not_called()  # PutItem 都失敗了，不該還去 Scan


def test_probe_fails_nonzero_when_ledger_get_item_does_not_find_canary(monkeypatch, tmp_path):
    """codex HIGH（本輪）：PutItem 沒丟例外，但按完整主鍵 GetItem（強一致）
    讀不到剛寫入的 canary——代表寫入其實沒真的落地。probe 必須非零退出，
    且**不該因此再去驗 Scan 權限**（先驗寫入落地都沒成功，Scan 權限驗了
    也沒意義，兩件事分開驗但仍有先後順序）。"""
    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_PATH", str(tmp_path / "cache.json"))
    monkeypatch.setenv("CACHE_BACKEND", "json")

    ledger = DynamoDBLedger(table_name="fake-ledger-table")
    mock_table = MagicMock()
    mock_table.put_item.return_value = {}
    mock_table.get_item.return_value = {}  # 沒有 "Item" key == 沒讀到
    ledger._table = mock_table
    monkeypatch.setattr(fetch_scheduler, "_probe_ledger_backend", lambda: ledger)

    rc = fetch_scheduler.run_probe()
    assert rc == 1
    mock_table.put_item.assert_called_once()
    mock_table.get_item.assert_called_once()
    mock_table.scan.assert_not_called()


def test_probe_fails_nonzero_when_ledger_scan_permission_denied(monkeypatch, tmp_path):
    """codex HIGH（本輪）：GetItem 已經讀回剛寫入的 canary（寫入確定落地），
    但單獨驗證 `dynamodb:Scan` 權限的那次呼叫被拒——`/costs` 端點靠 Scan
    讀，一樣會整個讀失敗，probe 必須非零退出。"""
    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_PATH", str(tmp_path / "cache.json"))
    monkeypatch.setenv("CACHE_BACKEND", "json")

    ledger = DynamoDBLedger(table_name="fake-ledger-table")
    mock_table = MagicMock()
    mock_table.put_item.return_value = {}
    mock_table.get_item.return_value = {
        "Item": {"run_id": fetch_scheduler._PROBE_SOURCE, "ts": fetch_scheduler._PROBE_LEDGER_TS}
    }
    mock_table.scan.side_effect = RuntimeError(
        "AccessDeniedException: User is not authorized to perform: dynamodb:Scan"
    )
    ledger._table = mock_table
    monkeypatch.setattr(fetch_scheduler, "_probe_ledger_backend", lambda: ledger)

    rc = fetch_scheduler.run_probe()
    assert rc == 1
    mock_table.put_item.assert_called_once()
    mock_table.get_item.assert_called_once()
    mock_table.scan.assert_called_once()


def test_probe_succeeds_when_ledger_get_item_finds_canary_and_scan_permitted(monkeypatch, tmp_path):
    """happy path：PutItem 成功、GetItem（強一致，按完整主鍵）真的讀到剛寫入
    的 canary，且 Scan 呼叫本身沒被拒 → probe 判定成功。刻意讓 Scan 回應是
    空的（不含 canary），證明「驗 Scan 權限」真的不看內容，只要呼叫不被拒
    就算過（不是只驗 PutItem 沒丟例外就算數，也不因 Scan 掃不到東西就誤判
    失敗）。"""
    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_PATH", str(tmp_path / "cache.json"))
    monkeypatch.setenv("CACHE_BACKEND", "json")

    ledger = DynamoDBLedger(table_name="fake-ledger-table")
    mock_table = MagicMock()
    mock_table.put_item.return_value = {}
    mock_table.get_item.return_value = {
        "Item": {"run_id": fetch_scheduler._PROBE_SOURCE, "ts": fetch_scheduler._PROBE_LEDGER_TS}
    }
    mock_table.scan.return_value = {"Items": []}
    ledger._table = mock_table
    monkeypatch.setattr(fetch_scheduler, "_probe_ledger_backend", lambda: ledger)

    rc = fetch_scheduler.run_probe()
    assert rc == 0
    mock_table.put_item.assert_called_once()
    mock_table.get_item.assert_called_once()
    get_item_kwargs = mock_table.get_item.call_args.kwargs
    assert get_item_kwargs["ConsistentRead"] is True
    assert get_item_kwargs["Key"] == {
        "run_id": fetch_scheduler._PROBE_SOURCE, "ts": fetch_scheduler._PROBE_LEDGER_TS,
    }
    mock_table.scan.assert_called_once()
    assert mock_table.scan.call_args.kwargs.get("Limit") == 1


def test_probe_scan_pagination_with_missing_canary_in_first_page_does_not_fail(
    monkeypatch, tmp_path
):
    """codex HIGH（本輪）核心回歸案例：Scan 只回第一頁（`LastEvaluatedKey`
    代表還有下一頁）、第一頁完全沒有 canary、內容也跟 canary 無關——這正是
    舊版「用 Scan 內容核對 canary」在大表上會誤判失敗的情境。新版驗證方式
    下，Scan 只驗權限不驗內容，GetItem 才驗寫入落地，因此不該因此判定
    失敗。"""
    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_PATH", str(tmp_path / "cache.json"))
    monkeypatch.setenv("CACHE_BACKEND", "json")

    ledger = DynamoDBLedger(table_name="fake-ledger-table")
    mock_table = MagicMock()
    mock_table.put_item.return_value = {}
    mock_table.get_item.return_value = {
        "Item": {"run_id": fetch_scheduler._PROBE_SOURCE, "ts": fetch_scheduler._PROBE_LEDGER_TS}
    }
    mock_table.scan.return_value = {
        "Items": [{"run_id": "other-run-1"}, {"run_id": "other-run-2"}],
        "LastEvaluatedKey": {"run_id": "other-run-2", "ts": "2026-01-01T00:00:00+00:00"},
    }
    ledger._table = mock_table
    monkeypatch.setattr(fetch_scheduler, "_probe_ledger_backend", lambda: ledger)

    rc = fetch_scheduler.run_probe()
    assert rc == 0


def test_probe_is_not_fooled_by_fully_fresh_cache_where_normal_run_would_report_success(
    monkeypatch, tmp_path,
):
    """codex HIGH-3 要防的正是這個：一般排程（不帶 --probe）在所有目標都新鮮
    時完全不會呼叫 backend.set()，即使 PutItem 早就被拒也照樣 exit 0。這裡先
    重現這個舊有的假成功，再證明 `--probe` 繞過 freshness、真的抓到同一個被
    拒的 PutItem——deploy 必須跑 `--probe`，跑一般排程不算數。"""

    class DeniedPutAlwaysFreshCache(CacheBackend):
        def __init__(self):
            self.put_calls = 0

        def set(self, key, docs, fetched_at, ttl_seconds=None):
            self.put_calls += 1
            raise RuntimeError("AccessDeniedException: dynamodb:PutItem denied")

        def get(self, key):
            # 每個來源都回「剛剛才新鮮過」，讓一般排程的新鮮度守門全部判定略過。
            return {"docs": [], "fetched_at": time.time()}

    denied_fresh_backend = DeniedPutAlwaysFreshCache()
    # 一般排程路徑（main() 內部）跟 --probe 路徑（run_probe() 內部）現在各自
    # 走不同的 backend 取得函式（見 codex HIGH：probe 真正有界化，run_probe()
    # 改用 `_probe_cache_backend()`/`_probe_ledger_backend()`），兩邊都要
    # monkeypatch 到同一個 denied_fresh_backend 實例，才能驗證這裡要斷言的
    # 「同一個被拒的 PutItem，一般排程繞過去、--probe 抓得到」。
    monkeypatch.setattr(fetch_scheduler, "get_cache_backend", lambda: denied_fresh_backend)
    monkeypatch.setattr(fetch_scheduler, "_probe_cache_backend", lambda: denied_fresh_backend)
    monkeypatch.setattr(
        fetch_scheduler, "get_ledger", lambda: JsonlLedger(path=tmp_path / "ledger.jsonl")
    )
    monkeypatch.setattr(
        fetch_scheduler, "_probe_ledger_backend", lambda: JsonlLedger(path=tmp_path / "ledger.jsonl")
    )

    src = _FakeSource("coindesk", kind="news")
    _patch_registry(monkeypatch, [src])

    # 舊驗法：一般排程，cache 全新鮮 → 0 次真呼叫、0 次 PutItem，仍然 exit 0（假成功）。
    rc_normal_run = fetch_scheduler.main(["--source", "coindesk", "--coin", "BTC"])
    assert rc_normal_run == 0
    assert denied_fresh_backend.put_calls == 0  # 證明真的一次 PutItem 都沒發生過

    # 新驗法：--probe 完全不看 freshness，直接觸發一次真正的 PutItem，抓到被拒。
    rc_probe = fetch_scheduler.main(["--probe"])
    assert rc_probe == 1
    assert denied_fresh_backend.put_calls == 1  # 這次真的觸發了一次 PutItem


# ---------------------------------------------------------------------------
# get_freshness_snapshot()（/status 資料鮮度矩陣用，Phase2）
# ---------------------------------------------------------------------------
# ⛔ 純讀：只呼叫既有 cache_get()，不觸發任何真連接器 API、不改任何寫入邏輯。

def test_freshness_snapshot_missing_when_no_cache_entry(tmp_path):
    backend = JsonCacheBackend(path=tmp_path / "cache.json")
    snapshot = get_freshness_snapshot(
        backend=backend, source_names=["coindesk"], coins=["BTC"]
    )
    assert snapshot == [
        {"source": "coindesk", "coin": "BTC", "status": "missing",
         "fetched_at": None, "age_seconds": None}
    ]


def test_freshness_snapshot_fresh_within_stale_window(tmp_path):
    backend = JsonCacheBackend(path=tmp_path / "cache.json")
    now = time.time()
    backend.set(cache_key("coindesk", "BTC"), [], fetched_at=now)

    snapshot = get_freshness_snapshot(
        backend=backend, source_names=["coindesk"], coins=["BTC"]
    )
    assert len(snapshot) == 1
    assert snapshot[0]["status"] == "fresh"
    assert snapshot[0]["fetched_at"] == now


def test_freshness_snapshot_stale_past_stale_after(tmp_path):
    backend = JsonCacheBackend(path=tmp_path / "cache.json")
    refresh_interval = DEFAULT_REFRESH_INTERVAL_SECONDS["coindesk"]
    stale_after = stale_after_for(refresh_interval)
    old_fetched_at = time.time() - stale_after - 1  # 剛好過期一秒
    backend.set(cache_key("coindesk", "BTC"), [], fetched_at=old_fetched_at)

    snapshot = get_freshness_snapshot(
        backend=backend, source_names=["coindesk"], coins=["BTC"]
    )
    assert snapshot[0]["status"] == "stale"


def test_freshness_snapshot_unknown_source_uses_fallback_interval(tmp_path):
    """未知來源名（不在 DEFAULT_REFRESH_INTERVAL_SECONDS）用
    fallback 間隔換算硬過期，不 raise。"""
    backend = JsonCacheBackend(path=tmp_path / "cache.json")
    fallback_stale_after = stale_after_for(DEFAULT_REFRESH_INTERVAL_FALLBACK_SECONDS)
    backend.set(
        cache_key("some-unknown-source", "BTC"), [],
        fetched_at=time.time() - fallback_stale_after - 1,
    )
    snapshot = get_freshness_snapshot(
        backend=backend, source_names=["some-unknown-source"], coins=["BTC"]
    )
    assert snapshot[0]["status"] == "stale"


def test_freshness_snapshot_default_covers_all_known_sources_and_coin_pool(tmp_path):
    """不傳 source_names/coins 時，預設涵蓋 DEFAULT_REFRESH_INTERVAL_SECONDS
    全部來源 × COIN_POOL 全部幣種（/status 頁面預設呼叫方式）。"""
    from trustforge.schema import COIN_POOL

    backend = JsonCacheBackend(path=tmp_path / "cache.json")
    snapshot = get_freshness_snapshot(backend=backend)
    assert len(snapshot) == len(DEFAULT_REFRESH_INTERVAL_SECONDS) * len(COIN_POOL)
    sources_seen = {row["source"] for row in snapshot}
    coins_seen = {row["coin"] for row in snapshot}
    assert sources_seen == set(DEFAULT_REFRESH_INTERVAL_SECONDS)
    assert coins_seen == set(COIN_POOL)


def test_freshness_snapshot_does_not_call_wrapped_source_fetch(tmp_path):
    """`get_freshness_snapshot()` 純讀 cache backend，絕不呼叫任何真 `Source.fetch()`
    ——即使 cache-miss，也只回傳 `status="missing"`，不會反過來打真 API
    （credit-safe 鐵律，跟 `CachedSource.fetch()` 一樣的邊界）。"""
    calls = []

    class SpySource(Source):
        def __init__(self):
            self.kind = "news"
            self.name = "coindesk"

        def fetch(self, query: str, coin: str = ""):
            calls.append((query, coin))
            raise AssertionError("get_freshness_snapshot 不該呼叫真 Source.fetch()")

    backend = JsonCacheBackend(path=tmp_path / "cache.json")
    snapshot = get_freshness_snapshot(
        backend=backend, source_names=["coindesk"], coins=["BTC"]
    )
    assert snapshot[0]["status"] == "missing"
    assert calls == []  # 從未呼叫過任何真 fetch()
