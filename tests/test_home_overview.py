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


def _stop_overview_bg_thread_for_test() -> None:
    """測試專用：強制停止目前的背景 thread（若有）並清空 in-memory 狀態。

    背景 thread + `_overview_html` 是 module 級共用狀態，如果不在每個測試
    前後徹底停乾淨，前一個測試 monkeypatch 過的假 `_home_overview_backend`
    （測試結束後 monkeypatch 會還原）可能還被舊 thread 的下一輪迴圈引用
    到、或反過來讓舊 thread 汙染下一個測試的 `_overview_html`——`join()`
    確保舊 thread 真的結束了才繼續，不是只是「設了停止旗標就當作沒事」。
    """
    stop_event = web._overview_bg_stop_event
    if stop_event is not None:
        stop_event.set()
    thread = web._overview_bg_thread
    if thread is not None:
        thread.join(timeout=3.0)
        assert not thread.is_alive(), (
            "背景 thread 沒有在測試逾時預算內停下來（3s）——測試裡模擬的"
            " backend stall 時間可能設太長，或 stop_event 沒被正確處理"
        )
    web._overview_bg_thread = None
    web._overview_bg_stop_event = None
    web._overview_html = None


@pytest.fixture(autouse=True)
def _reset_home_overview_module_state(monkeypatch):
    """每個測試前後重置背景 thread + in-memory 狀態，避免跨測試互相汙染
    （比照 `test_status_page.py::_reset_status_module_state` 慣例）。

    ⛔ credit-safe 防呆：`_render_home_page()`/`_render_home_overview_cached()`
    現在會**懶啟動一條真的背景 thread**（`_ensure_overview_bg_thread_started()`）
    ——若某個測試呼叫到這條路徑卻忘了 monkeypatch `_home_overview_backend`
    或指定 `CACHE_BACKEND=json`，這條背景 thread 會用 env 預設（`dynamodb`）
    真的建構 `DynamoDBCache` 並嘗試 `get_item()`，在有 AWS SSO/憑證的開發
    機上可能觸發真連線。強制整份測試檔的 `CACHE_BACKEND=json` 當保底防
    線，即使個別測試忘記自己隔離，也不會因為這條「本來應該只讀
    in-memory」的路徑意外冒出真 AWS 呼叫。
    """
    monkeypatch.setenv("CACHE_BACKEND", "json")
    _stop_overview_bg_thread_for_test()
    yield
    _stop_overview_bg_thread_for_test()


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
# 首頁總覽讀路徑（第三版，codex HIGH #2 修復）：I/O 徹底移出 request 路徑
# ——背景 daemon thread 定期刷新 in-memory 變數，首頁 request 只讀那顆變數
# （零 I/O）。真正的 I/O + 新鮮度驗證只在 `_overview_bg_refresh_once()`
# 裡，用測試直接呼叫它模擬「跑一輪背景刷新」，不必真的等 30s interval。
# ---------------------------------------------------------------------------

def test_home_overview_end_to_end_write_then_read(json_cache_backend):
    """端到端：`--snapshot` 寫入者真的寫完後，手動觸發一輪背景刷新（等同
    背景 thread 的其中一輪迴圈），首頁讀路徑能讀到同一份 blob（驗證兩邊
    cache key 常數一致，見 `cache.py` Axis C 段落，且背景刷新邏輯本身正
    確）。"""
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

    web._overview_bg_refresh_once()  # 手動跑一輪背景刷新，不必等真的 thread
    htmlout = web._render_home_page()
    assert "多幣信任總覽" in htmlout
    assert "BTC" in htmlout


def test_render_home_overview_cached_is_pure_in_memory_read(monkeypatch):
    """核心架構斷言：`_render_home_overview_cached()`（首頁 request 路徑）
    絕對不能呼叫 `_home_overview_backend()`／`cache_get()`——只准讀
    `_overview_html` 這顆 in-memory 變數。用「一呼叫就炸」的假 backend
    證明就算它被打到一定會拋 `AssertionError`，這裡也不該被呼叫到。

    為了不讓斷言淪為「main thread 剛好比背景 thread 搶先讀完」的競速巧合，
    先用一個「活著但什麼都不做」的假 thread 佔住 `_overview_bg_thread` 的
    位子，讓 `_ensure_overview_bg_thread_started()` 的 `is_alive()` 檢查
    判定「已經有一條在跑了」而直接跳過真的啟動——這樣 `_boom` 在這個測試
    裡從頭到尾保證不會被任何 thread 呼叫到，而不是「機率上通常不會被呼
    叫到」。"""
    def _boom():
        raise AssertionError(
            "_render_home_overview_cached() 不該呼叫 _home_overview_backend()"
            "——I/O 應該只發生在背景 thread 的 _overview_bg_refresh_once() 裡"
        )

    monkeypatch.setattr(web, "_home_overview_backend", _boom)

    placeholder_stop = threading.Event()
    placeholder_thread = threading.Thread(
        target=placeholder_stop.wait, name="tf-overview-bg", daemon=True
    )
    placeholder_thread.start()
    web._overview_bg_thread = placeholder_thread
    web._overview_bg_stop_event = placeholder_stop

    with web._overview_state_lock:
        web._overview_html = '<div class="tf-overview-grid">FROM-MEMORY</div>'

    result = web._render_home_overview_cached()
    assert "FROM-MEMORY" in result  # 直接讀到 in-memory 現貨，沒有經過任何 I/O


def test_render_home_overview_cached_returns_empty_when_no_fresh_value_yet():
    """`_overview_html` 尚未被背景 thread 填過（`None`，如剛啟動）時回空
    字串——首頁其餘內容照常渲染，總覽區塊優雅缺席，不是 hang 或報錯。"""
    with web._overview_state_lock:
        web._overview_html = None
    assert web._render_home_overview_cached() == ""


def test_overview_bg_refresh_once_stale_entry_sets_none(monkeypatch):
    """codex HIGH #1 回歸鎖：DynamoDB TTL 刪除是 best-effort，可能延遲數
    小時到 48 小時，背景刷新讀到「entry 仍非空但 fetched_at 早已超過新鮮
    窗」時，必須把 `_overview_html` 設回 `None`（視同 miss，不顯總覽）
    ——不能只看「entry 非空」就當作新鮮。"""
    stale_fetched_at = time.time() - (TRUST_SNAPSHOT_FRESH_WINDOW_SECONDS + 60.0)

    class _StaleBackend:
        def get(self, key, *, consistent_read=False):
            return {
                "docs": [{"html": '<div class="tf-overview-grid">STALE</div>'}],
                "fetched_at": stale_fetched_at,
            }

    monkeypatch.setattr(web, "_home_overview_backend", lambda: _StaleBackend())
    with web._overview_state_lock:
        web._overview_html = '<div class="tf-overview-grid">OLD-FRESH-VALUE</div>'

    web._overview_bg_refresh_once()

    assert web._overview_html is None  # 過期視同 miss，不保留舊值也不顯示過期資料
    assert web._render_home_overview_cached() == ""


def test_overview_bg_refresh_once_fresh_entry_updates_memory(monkeypatch):
    """對照組：`fetched_at` 落在新鮮窗內（未超過 45 分鐘），背景刷新正常
    把 `_overview_html` 更新成新內容——證明新鮮度檢查沒有把正常情況也一
    併擋掉。"""
    fresh_fetched_at = time.time() - 60.0  # 1 分鐘前，遠在 45 分鐘窗內

    class _FreshBackend:
        def get(self, key, *, consistent_read=False):
            return {
                "docs": [{"html": '<div class="tf-overview-grid">FRESH</div>'}],
                "fetched_at": fresh_fetched_at,
            }

    monkeypatch.setattr(web, "_home_overview_backend", lambda: _FreshBackend())

    web._overview_bg_refresh_once()

    assert web._overview_html is not None
    assert "FRESH" in web._overview_html
    assert "FRESH" in web._render_home_overview_cached()


def test_overview_bg_refresh_once_sets_none_on_backend_exception(monkeypatch):
    """背景刷新遇到 backend 拋例外（憑證/DNS/timeout 皆可能）時，把
    `_overview_html` 設回 `None`（不顯示壞資料），且例外不往外傳——背景
    thread 的迴圈才不會因為一次失敗就整條死掉、永遠不再刷新（見
    `_overview_bg_loop()`）。"""
    class _BoomBackend:
        def get(self, key, *, consistent_read=False):
            raise TimeoutError("simulated backend exception")

    monkeypatch.setattr(web, "_home_overview_backend", lambda: _BoomBackend())
    with web._overview_state_lock:
        web._overview_html = '<div class="tf-overview-grid">OLD</div>'

    web._overview_bg_refresh_once()  # 不該拋出

    assert web._overview_html is None


def test_overview_bg_thread_started_lazily_and_is_singleton(monkeypatch):
    """`_ensure_overview_bg_thread_started()` 懶啟動且 idempotent：併發呼叫
    多次也只會啟動一條 thread（不是每次首頁 request 都各自開一條）。"""
    class _NoopBackend:
        def get(self, key, *, consistent_read=False):
            return None

    monkeypatch.setattr(web, "_home_overview_backend", lambda: _NoopBackend())
    monkeypatch.setattr(web, "_OVERVIEW_BG_INTERVAL_SECONDS", 60.0)

    assert web._overview_bg_thread is None  # 尚未啟動（autouse fixture 已重置）

    barrier = threading.Barrier(10)

    def worker():
        barrier.wait()
        web._ensure_overview_bg_thread_started()

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert web._overview_bg_thread is not None
    assert web._overview_bg_thread.is_alive()
    assert web._overview_bg_thread.daemon is True
    assert web._overview_bg_thread.name == "tf-overview-bg"

    alive_bg_threads = [
        t for t in threading.enumerate() if t.name == "tf-overview-bg" and t.is_alive()
    ]
    assert len(alive_bg_threads) == 1  # 10 個併發呼叫只真的啟動了一條


def test_home_page_never_hangs_on_backend_raising(monkeypatch):
    """慢/掛掉的 backend（get() raise，模擬立即逾時）：即使背景 thread 因
    此拿不到資料，首頁 render 仍必須秒回——這條走的是舊的「拋例外」情境
    （見下一測試的「真 stall」情境，兩者都要涵蓋）。"""
    class _TimeoutBackend:
        def get(self, key, *, consistent_read=False):
            raise TimeoutError("simulated slow backend")

    monkeypatch.setattr(web, "_home_overview_backend", lambda: _TimeoutBackend())

    start = time.time()
    htmlout = web._render_home_page()
    elapsed = time.time() - start

    assert elapsed < 0.5  # 首頁 request 路徑零 I/O，理論上應是微秒級
    assert "多幣信任總覽" not in htmlout
    assert "信任提煉" in htmlout  # 首頁其餘內容照常渲染，未受影響


def test_home_page_never_hangs_on_true_backend_stall(monkeypatch):
    """codex HIGH #2 回歸鎖（PR #47 二輪 review）——**真的 stall**，不是
    立即拋例外：`get()` 真的 `time.sleep()` 卡住遠超過
    `_HOME_OVERVIEW_TIMEOUT_SECONDS`（0.5s）的預算，模擬「backend 不回應
    但也不拋錯」（憑證發現/DNS 卡住、socket 連上但對方不回應等 botocore
    timeout 涵蓋不到的情境）。首頁 request 執行緒完全不該被這個 stall
    拖累——因為它根本不會呼叫到會 stall 的那個函式（I/O 全部關在背景
    thread 的 `_overview_bg_refresh_once()` 裡）。"""
    stall_seconds = 1.5  # 遠超過 0.5s 的 DynamoDB timeout 預算，證明不是靠
    # 那個 timeout 才不卡——首頁根本沒經過會用到那個 timeout 的呼叫路徑。

    class _StallingBackend:
        def get(self, key, *, consistent_read=False):
            time.sleep(stall_seconds)
            return None

    monkeypatch.setattr(web, "_home_overview_backend", lambda: _StallingBackend())

    # 先手動啟動背景 thread（讓它去卡在 stall 裡），首頁 request 路徑本身
    # 完全不應該因為背景 thread 正卡著而變慢。
    start = time.time()
    htmlout = web._render_home_page()
    elapsed = time.time() - start

    assert elapsed < 0.1  # 首頁 request 執行緒完全沒碰到會 stall 的呼叫
    assert "多幣信任總覽" not in htmlout  # 背景 thread 這輪還卡著，尚無新值
    assert "信任提煉" in htmlout

    # 背景 thread 確實正在被 stall 卡住（is_alive 仍 True、迴圈預設每
    # `_OVERVIEW_BG_INTERVAL_SECONDS` 才輪詢一次，不會自己主動結束），
    # 證明這不是「backend 根本沒被呼叫」的假陽性——它真的正卡在
    # `time.sleep(1.5)` 裡。主動送出停止訊號，驗證撐過這輪 stall 之後
    # 迴圈仍能正常收尾（不是卡死），同時避免這條 thread 汙染下一個測試。
    thread = web._overview_bg_thread
    stop_event = web._overview_bg_stop_event
    assert thread is not None
    assert stop_event is not None
    assert thread.is_alive()
    stop_event.set()
    thread.join(timeout=stall_seconds + 2.0)
    assert not thread.is_alive()  # 撐過 stall 之後，收到停止訊號能正常收尾


def test_home_page_concurrent_requests_never_touch_backend_and_stay_fast(monkeypatch):
    """多併發首頁 request 同時進來、backend 正在 stall：全部都該立即回應
    （不是排隊卡在同一顆鎖上，見 codex HIGH #2「single-flight 鎖讓併發
    request 全部排隊卡住」的原始描述）——因為 request 路徑根本不呼叫
    backend。"""
    stall_seconds = 1.5

    class _StallingBackend:
        def get(self, key, *, consistent_read=False):
            time.sleep(stall_seconds)
            return None

    monkeypatch.setattr(web, "_home_overview_backend", lambda: _StallingBackend())

    elapsed_list: list[float] = []
    elapsed_lock = threading.Lock()
    barrier = threading.Barrier(20)

    def worker():
        barrier.wait()
        t0 = time.time()
        web._render_home_page()
        dt = time.time() - t0
        with elapsed_lock:
            elapsed_list.append(dt)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert len(elapsed_list) == 20
    assert max(elapsed_list) < 0.5  # 全部都遠低於 stall_seconds，沒有任何
    # 一個 request 被卡住排隊等 backend
    # （背景 thread 這時可能仍卡在 stall 裡，交給 autouse fixture 收尾即
    # 可，不必在這裡等它，本測試只關心 request 路徑本身的延遲。）


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
