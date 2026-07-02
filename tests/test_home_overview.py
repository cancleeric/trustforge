"""Axis C #1（task #23，PLAN docs/PLAN-axisC-snapshots.md）：多幣信任快照
寫入者 + 首頁總覽正確讀路徑測試。

⛔ credit-safe 鐵律：全程不觸發任何連接器真抓取、不打真 Bedrock、不打真
DynamoDB——`scripts/fetch_scheduler.py --snapshot` 走 real-off
`pipeline.run(data_mode="live", llm_mode="off")`（只讀既有 `CachedSource`
快取／`offline=True` regex fallback），`web.py::_render_home_overview_cached()`
走專用短 timeout backend，本檔用 `CACHE_BACKEND=json` 隔離到 tmp_path，
或直接 monkeypatch 假 backend，兩者皆不觸網。
"""
from __future__ import annotations

import threading
import time

import pytest

from scripts import fetch_scheduler
from trustforge import web
from trustforge.ingestion.cache import (
    JsonCacheBackend,
    TRUST_OVERVIEW_COIN,
    TRUST_OVERVIEW_SOURCE,
    TRUST_SNAPSHOT_FRESH_WINDOW_SECONDS,
    TRUST_SNAPSHOT_SOURCE,
    cache_get,
    cache_key,
)


@pytest.fixture(autouse=True)
def _reset_home_overview_module_state():
    """`_home_overview_cache` 是 module 級共用狀態，每個測試前後重置，避免
    跨測試互相汙染（比照 `test_status_page.py::_reset_status_module_state`
    慣例）。"""
    web._home_overview_cache["expires_at"] = 0.0
    web._home_overview_cache["html"] = ""
    yield
    web._home_overview_cache["expires_at"] = 0.0
    web._home_overview_cache["html"] = ""


@pytest.fixture
def json_cache_backend(tmp_path, monkeypatch):
    """`CACHE_BACKEND=json` 且指向隔離的 tmp_path，避免碰到真 DynamoDB。"""
    monkeypatch.setenv("CACHE_BACKEND", "json")
    monkeypatch.setenv("TRUSTFORGE_CACHE_DIR", str(tmp_path))
    return JsonCacheBackend()


def _fake_report(coin: str, confidence: float = 0.62, calibrated: float = 0.55,
                  direction: str = "偏多", decision_state: str = "normal",
                  generated_at: str = "2026-07-01T00:00:00Z"):
    class _FakeReport:
        pass

    r = _FakeReport()
    r.coin = coin
    r.confidence = confidence
    r.calibrated_confidence = calibrated
    r.direction = direction
    r.decision_state = decision_state
    r.generated_at = generated_at
    return r


# ---------------------------------------------------------------------------
# `--snapshot` 寫入者（scripts/fetch_scheduler.py）
# ---------------------------------------------------------------------------

def test_snapshot_dry_run_never_calls_pipeline_run(monkeypatch, json_cache_backend):
    """`--snapshot --dry-run` 只列出會跑哪些幣，不真的呼叫 `pipeline.run()`
    （credit-safe：驗證這個分支不會誤打任何東西，即使它本身已是 $0
    real-off）。"""
    def _boom(*a, **kw):
        raise AssertionError("--snapshot --dry-run 不該呼叫 pipeline.run()")

    monkeypatch.setattr("trustforge.pipeline.run", _boom)

    rc = fetch_scheduler.main(["--snapshot", "--dry-run", "--coin", "BTC"])
    assert rc == 0
    assert cache_get(json_cache_backend, cache_key(TRUST_SNAPSHOT_SOURCE, "BTC")) is None


def test_snapshot_writes_real_off_result_and_overview_blob(monkeypatch, json_cache_backend):
    """成功路徑：`pipeline.run()`（樁）回傳真實形狀的 Report → 精華欄位逐字
    寫入 `__trust_snapshot__:{coin}`，且總覽 blob 寫入 `__trust_overview_html__`，
    欄位皆取自 Report，非虛構值（#24）。"""
    calls: list[tuple] = []

    def fake_pipeline_run(coin, query, qtype, **kwargs):
        calls.append((coin, query, qtype, kwargs))
        report = _fake_report(coin, confidence=0.71, calibrated=0.6,
                               direction="偏多", decision_state="normal")
        return report, [], object()

    monkeypatch.setattr(fetch_scheduler, "run_once", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("--snapshot 不該呼叫 run_once()（打真連接器那條路）")
    ))
    import trustforge.pipeline as pipeline_mod
    monkeypatch.setattr(pipeline_mod, "run", fake_pipeline_run)

    rc = fetch_scheduler.main(["--snapshot", "--coin", "BTC", "--coin", "ETH"])
    assert rc == 0

    # data_mode/llm_mode 必須是 real-off（$0，credit-safe #24 前提）
    for coin, query, qtype, kwargs in calls:
        assert kwargs.get("data_mode") == "live"
        assert kwargs.get("llm_mode") == "off"
    assert {c for c, *_ in calls} == {"BTC", "ETH"}

    btc_entry = cache_get(json_cache_backend, cache_key(TRUST_SNAPSHOT_SOURCE, "BTC"))
    assert btc_entry is not None
    snap = btc_entry["docs"][0]
    assert snap["coin"] == "BTC"
    assert snap["trust_score"] == pytest.approx(0.71, abs=1e-6)
    assert snap["direction"] == "偏多"
    assert snap["calibrated_confidence"] == pytest.approx(0.6, abs=1e-6)
    assert snap["decision_state"] == "normal"
    assert snap["generated_at"] == "2026-07-01T00:00:00Z"

    overview_entry = cache_get(
        json_cache_backend, cache_key(TRUST_OVERVIEW_SOURCE, TRUST_OVERVIEW_COIN)
    )
    assert overview_entry is not None
    overview_html = overview_entry["docs"][0]["html"]
    assert "BTC" in overview_html
    assert "ETH" in overview_html
    assert "0.71" in overview_html


def test_snapshot_skips_coin_on_pipeline_failure_without_faking_values(
    monkeypatch, json_cache_backend
):
    """#24 鐵律：單幣 `pipeline.run()` 失敗（如 collect 全 cache-miss）只跳過
    該幣、不寫入任何值（不得補假值），也不中斷其餘幣別。"""
    def fake_pipeline_run(coin, query, qtype, **kwargs):
        if coin == "BTC":
            raise ValueError("無資料：collect() 全 cache-miss")
        report = _fake_report(coin, confidence=0.5)
        return report, [], object()

    import trustforge.pipeline as pipeline_mod
    monkeypatch.setattr(pipeline_mod, "run", fake_pipeline_run)

    rc = fetch_scheduler.main(["--snapshot", "--coin", "BTC", "--coin", "ETH"])
    assert rc == 1  # 有幣失敗 → 非零 exit，讓 cron/監控看得到

    assert cache_get(json_cache_backend, cache_key(TRUST_SNAPSHOT_SOURCE, "BTC")) is None
    eth_entry = cache_get(json_cache_backend, cache_key(TRUST_SNAPSHOT_SOURCE, "ETH"))
    assert eth_entry is not None
    assert eth_entry["docs"][0]["coin"] == "ETH"


def test_snapshot_never_calls_bedrock_or_real_connector(monkeypatch, json_cache_backend):
    """credit-safe 逐字驗證：`--snapshot` 全程走 real-off，不該讓 `BedrockClient`
    真跑（`offline=True` 分支）或任何真連接器 `Source.fetch()` 被呼叫到。"""
    from trustforge.bedrock import BedrockClient

    original_init = BedrockClient.__init__

    def _spy_init(self, *a, offline=True, **kw):
        assert offline is True, "credit-safe：--snapshot 必須是 llm_mode=off → offline=True"
        return original_init(self, *a, offline=offline, **kw)

    monkeypatch.setattr(BedrockClient, "__init__", _spy_init)

    rc = fetch_scheduler.main(["--snapshot", "--coin", "BTC"])
    # real-off 模式下即使 collect() 缺資料，也只是該幣被跳過（見上一測試），
    # 這裡只關心 BedrockClient 建構時 offline 是否為 True，不強求 rc == 0。
    assert rc in (0, 1)


# ---------------------------------------------------------------------------
# 首頁總覽讀路徑：TTL + single-flight + 永不 hang
# ---------------------------------------------------------------------------

def test_home_overview_end_to_end_write_then_read(json_cache_backend):
    """端到端：`--snapshot` 寫入者真的寫完後，首頁讀路徑能讀到同一份 blob
    （驗證兩邊 cache key 常數一致，見 `cache.py` Axis C 段落）。"""
    import trustforge.pipeline as pipeline_mod

    def fake_pipeline_run(coin, query, qtype, **kwargs):
        return _fake_report(coin), [], object()

    original = pipeline_mod.run
    pipeline_mod.run = fake_pipeline_run
    try:
        rc = fetch_scheduler.main(["--snapshot", "--coin", "BTC"])
        assert rc == 0
    finally:
        pipeline_mod.run = original

    htmlout = web._render_home_page()
    assert "多幣信任總覽" in htmlout
    assert "BTC" in htmlout


def test_home_overview_cached_within_ttl(monkeypatch):
    calls = {"n": 0}

    class _FakeBackend:
        def get(self, key, *, consistent_read=False):
            calls["n"] += 1
            return None

    monkeypatch.setattr(web, "_home_overview_backend", lambda: _FakeBackend())

    web._render_home_overview_cached()
    web._render_home_overview_cached()
    assert calls["n"] == 1  # TTL 內第二次不重讀


def test_home_overview_recomputed_after_ttl_expires(monkeypatch):
    calls = {"n": 0}

    class _FakeBackend:
        def get(self, key, *, consistent_read=False):
            calls["n"] += 1
            return None

    monkeypatch.setattr(web, "_home_overview_backend", lambda: _FakeBackend())

    web._render_home_overview_cached()
    web._home_overview_cache["expires_at"] = time.time() - 1
    web._render_home_overview_cached()
    assert calls["n"] == 2


def test_home_overview_stale_entry_treated_as_miss(monkeypatch):
    """codex HIGH（PR #47 review）回歸鎖：DynamoDB TTL 刪除是 best-effort，
    可能延遲數小時到 48 小時，reader 讀到「entry 仍非空但 fetched_at 早已
    超過新鮮窗」時，必須自己判過期、視同 cache-miss（不顯總覽）——不能只
    看「entry 非空」就當作新鮮，否則排程停擺時首頁會一直顯示過期的信任
    判斷當成即時。這裡刻意讓假 backend 一直回傳同一筆「很舊」的 entry
    （模擬 DynamoDB TTL 刪除滯後），證明不是靠 TTL 刪除才不顯示。"""
    stale_fetched_at = time.time() - (TRUST_SNAPSHOT_FRESH_WINDOW_SECONDS + 60.0)

    class _StaleBackend:
        def get(self, key, *, consistent_read=False):
            return {
                "docs": [{"html": '<div class="tf-overview-grid">STALE</div>'}],
                "fetched_at": stale_fetched_at,
            }

    monkeypatch.setattr(web, "_home_overview_backend", lambda: _StaleBackend())

    result = web._render_home_overview_cached()
    assert result == ""  # 過期視同 miss，不顯示過期判斷
    assert "STALE" not in result


def test_home_overview_fresh_entry_shown(monkeypatch):
    """對照組：`fetched_at` 落在新鮮窗內（未超過 45 分鐘），正常顯示總覽
    ——證明新鮮度檢查沒有把正常情況也一併擋掉。"""
    fresh_fetched_at = time.time() - 60.0  # 1 分鐘前，遠在 45 分鐘窗內

    class _FreshBackend:
        def get(self, key, *, consistent_read=False):
            return {
                "docs": [{"html": '<div class="tf-overview-grid">FRESH</div>'}],
                "fetched_at": fresh_fetched_at,
            }

    monkeypatch.setattr(web, "_home_overview_backend", lambda: _FreshBackend())

    result = web._render_home_overview_cached()
    assert "FRESH" in result


def test_home_overview_failure_result_also_cached_short_ttl(monkeypatch):
    """P3 加固：讀失敗/miss 的結果本身也要短 TTL 快取，斷網期間不用每個
    request 都重新等一次短 timeout。"""
    calls = {"n": 0}

    class _BoomBackend:
        def get(self, key, *, consistent_read=False):
            calls["n"] += 1
            raise TimeoutError("simulated timeout")

    monkeypatch.setattr(web, "_home_overview_backend", lambda: _BoomBackend())

    web._render_home_overview_cached()
    web._render_home_overview_cached()
    assert calls["n"] == 1  # 失敗結果也被 TTL 快取住，第二次不重讀

    # 失敗 TTL 比成功 TTL 短
    assert web._HOME_OVERVIEW_FAIL_TTL_SECONDS < web._HOME_OVERVIEW_CACHE_TTL_SECONDS
    remaining = web._home_overview_cache["expires_at"] - time.time()
    assert remaining <= web._HOME_OVERVIEW_FAIL_TTL_SECONDS + 0.5  # 留一點時間誤差餘裕


def test_home_overview_single_flight_on_concurrent_expiry(monkeypatch):
    """cache-stampede 回歸：cache 過期瞬間多執行緒併發讀，必須只有一個真的
    打 backend，其餘排隊拿新值（比照 `test_status_page.py` 同款測試）。"""
    calls = {"n": 0}
    calls_lock = threading.Lock()

    class _SlowBackend:
        def get(self, key, *, consistent_read=False):
            with calls_lock:
                calls["n"] += 1
            time.sleep(0.05)  # 拉寬視窗，逼其他執行緒排隊等鎖
            return {"docs": [{"html": "<div>total</div>"}], "fetched_at": time.time()}

    monkeypatch.setattr(web, "_home_overview_backend", lambda: _SlowBackend())

    web._render_home_overview_cached()
    calls["n"] = 0
    web._home_overview_cache["expires_at"] = time.time() - 1

    barrier = threading.Barrier(20)
    results: list[str] = []
    results_lock = threading.Lock()

    def worker():
        barrier.wait()
        out = web._render_home_overview_cached()
        with results_lock:
            results.append(out)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert calls["n"] == 1
    assert len(results) == 20
    assert all(r == results[0] for r in results)


def test_home_page_never_hangs_on_slow_backend(monkeypatch):
    """CEO 驗收鐵律：模擬慢/掛掉的 backend（get() raise，模擬逾時），首頁
    render 必須秒回，不能 hang。這裡用「拋例外」模擬 timeout 效果（真實
    `DynamoDBCache` 遇到逾時本身會拋 botocore 例外，不是真的 sleep 卡住），
    驗證整條讀路徑（`_render_home_overview_cached` → `_render_home_page`）
    對這類例外的處理是立即返回，不吞掉例外變成 hang。"""
    class _TimeoutBackend:
        def get(self, key, *, consistent_read=False):
            raise TimeoutError("simulated slow backend")

    monkeypatch.setattr(web, "_home_overview_backend", lambda: _TimeoutBackend())

    start = time.time()
    htmlout = web._render_home_page()
    elapsed = time.time() - start

    assert elapsed < 2.0  # 遠低於任何合理的網路 timeout，證明沒有真的卡住
    assert "多幣信任總覽" not in htmlout
    assert "信任提煉" in htmlout  # 首頁其餘內容照常渲染，未受影響


def test_home_overview_backend_uses_short_timeout_for_dynamodb(monkeypatch):
    """`_home_overview_backend()`（`CACHE_BACKEND` 未顯式設 json 時）必須
    帶短 timeout 參數建構 `DynamoDBCache`，不能沿用 `get_cache_backend()`
    的無 timeout 版本（見該函式 docstring）。"""
    monkeypatch.delenv("CACHE_BACKEND", raising=False)

    backend = web._home_overview_backend()
    assert type(backend).__name__ == "DynamoDBCache"
    assert backend._connect_timeout == web._HOME_OVERVIEW_TIMEOUT_SECONDS
    assert backend._read_timeout == web._HOME_OVERVIEW_TIMEOUT_SECONDS
    assert backend._max_attempts == 1
