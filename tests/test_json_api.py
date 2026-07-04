"""前後端分離 Phase 1（task #28，docs/PLAN-frontend-backend-split.md）：純新增
JSON API 端點測試。

⛔ credit-safe 鐵律：`/api/status`／`/api/overview`／`/api/costs`／
`/api/history` 只讀既有 cache/ledger——`CACHE_BACKEND=json` 隔離到
tmp_path，不打真 DynamoDB；`/api/analyze` 沿用 `_do_analyze`/`_do_comparison`
既有 offline/real-off 預設路徑，不觸發真 Bedrock。

本檔額外驗證「絕不改動既有 SSR HTML 渲染」鐵律：對照既有 `/`、`/status`、
`/costs`、`/analyze`、`/analyze.json` 幾條路由的既有測試斷言仍然成立（見
`test_ssr_routes_untouched_by_json_api_addition`），且本次 diff 本身純新增
（`git diff --stat` 只有 insertions，見 PR 描述/CTO 回報）。
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from email.message import Message
from io import BytesIO
from unittest.mock import MagicMock

import pytest

from trustforge import pipeline as pipeline_module
from trustforge import web
from trustforge.ingestion.cache import (
    DynamoDBCache,
    JsonCacheBackend,
    TRUST_SNAPSHOT_SOURCE,
    cache_key,
    cache_set_if_newer,
    trust_snapshot_history_key,
)
from trustforge import ledger as ledger_module
from trustforge.ledger import JsonlLedger
from trustforge.schema import COIN_POOL


def _stop_overview_bg_thread_for_test() -> None:
    """`test_ssr_routes_untouched_by_json_api_addition` 會呼叫 `_do_get("/")`
    間接觸發首頁總覽背景 daemon thread（`_ensure_overview_bg_thread_started()`）
    ——比照 `tests/test_web.py`／`tests/test_home_overview.py` 既有慣例，每個
    測試前後徹底停掉，避免背景 thread 在 `tmp_path` 已被清掉後還嘗試讀取。"""
    stop_event = web._overview_bg_stop_event
    if stop_event is not None:
        stop_event.set()
    thread = web._overview_bg_thread
    if thread is not None:
        thread.join(timeout=3.0)
    web._overview_bg_thread = None
    web._overview_bg_stop_event = None
    web._overview_html = None
    web._overview_expiry_epoch = 0.0


@pytest.fixture(autouse=True)
def _reset_shared_module_state():
    """`/api/status`／`/api/overview`／`/api/costs`／`/api/history` 皆重用
    `_check_status_rate_limit`，共用 `_status_rate_buckets`（比照
    `tests/test_status_page.py` 既有慣例，每個測試前後重置，避免跨測試
    互相汙染）。"""
    web._status_rate_buckets.clear()
    _stop_overview_bg_thread_for_test()
    yield
    web._status_rate_buckets.clear()
    _stop_overview_bg_thread_for_test()


@pytest.fixture(autouse=True)
def _reset_analyze_dedup_state():
    """#51 `/api/analyze` server-side idempotency：`_analyze_dedup_cache`/
    `_analyze_dedup_inflight` 是 module-level 狀態（同一把 key 60 秒內共用
    結果），本檔許多測試共用相同的 (coin, query, type) 組合（如
    `coin=BTC, q="test"`）——不清乾淨會讓後面的測試誤命中前一個測試留下
    的快取結果/事件，而不是真的呼叫到當次測試 monkeypatch 的
    `web.run`/`web.run_comparison`。"""
    web._analyze_dedup_cache.clear()
    web._analyze_dedup_inflight.clear()
    yield
    web._analyze_dedup_cache.clear()
    web._analyze_dedup_inflight.clear()


@pytest.fixture
def json_cache_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_BACKEND", "json")
    monkeypatch.setenv("TRUSTFORGE_CACHE_DIR", str(tmp_path))
    return JsonCacheBackend()


def _envelope(body: str) -> dict:
    parsed = json.loads(body)
    assert "ok" in parsed
    return parsed


def _do_get(path: str) -> tuple[int, str]:
    """比照 `tests/test_web.py::_do_get` 既有慣例，端到端呼叫
    `Handler.do_GET`（不開真 socket），回傳 (status_code, body)。本檔獨立
    維護一份（`tests/` 不是套件，無法穩定 import 另一個測試模組的
    helper），比照 `test_status_page.py` 等既有各自維護一份的慣例。"""
    h = web.Handler.__new__(web.Handler)
    h.client_address = ("127.0.0.1", 12345)
    h.path = path
    h.wfile = BytesIO()
    h.headers = Message()

    captured = []
    h.send_response = lambda code: captured.append(code)
    h.send_header = lambda name, val: None
    h.end_headers = lambda: None

    h.do_GET()

    body = h.wfile.getvalue().decode("utf-8")
    return captured[0], body


def _real_broken_backend_with_dead_fallback(monkeypatch, tmp_path) -> DynamoDBCache:
    """codex 複審 HIGH（根因修復）：組出一個「真的會讓 `cache_get()` 底層
    primary+fallback 都失敗」的環境，供下面幾個測試打真正的
    `cache_get(..., strict=True)` 讀取路徑（不是 monkeypatch `cache_get`/
    `get_trust_history`/`get_freshness_snapshot` 這些 helper 本身，見 codex
    這輪明確要求「非 replace helper，真 backend 降級」）。

    primary：真 `DynamoDBCache()` 實例，只 mock 掉 `_get_table()`（比照
    `tests/test_connector_cache.py::test_cache_get_falls_back_to_json_on_broken_dynamodb_backend`
    既有慣例，避免打到真 AWS，但 `.get()` 走的是原本真正的程式碼路徑）。
    fallback：`cache_get()` 內部失敗時會另外 instantiate 一顆全新的
    `JsonCacheBackend()`——這裡把 `JsonCacheBackend.get`（類別方法，會影響
    所有實例）也 monkeypatch 成一律拋例外，模擬「連本地磁碟 fallback 都讀
    不了」，讓 strict 讀取真的沒有任何退路可以矇混回 `None`。"""
    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_PATH", str(tmp_path / "fallback_cache.json"))
    broken = DynamoDBCache()
    monkeypatch.setattr(
        broken, "_get_table",
        MagicMock(side_effect=RuntimeError("no aws credentials / table not found")),
    )
    monkeypatch.setattr(
        JsonCacheBackend, "get",
        lambda self, key: (_ for _ in ()).throw(OSError("磁碟也壞了")),
    )
    return broken


# ---------------------------------------------------------------------------
# 信封格式共用斷言
# ---------------------------------------------------------------------------

def test_ok_envelope_helper():
    body = web._json_envelope_ok({"x": 1})
    parsed = json.loads(body)
    assert parsed == {"ok": True, "data": {"x": 1}}


def test_err_envelope_helper():
    body = web._json_envelope_err("bad_request", "訊息")
    parsed = json.loads(body)
    assert parsed == {"ok": False, "error": {"code": "bad_request", "message": "訊息"}}


# ---------------------------------------------------------------------------
# /api/health
# ---------------------------------------------------------------------------

def test_api_health_ok():
    code, body = web._handle_api_health()
    assert code == 200
    parsed = _envelope(body)
    assert parsed["ok"] is True
    assert parsed["data"]["status"] == "ok"
    assert "version" in parsed["data"]
    assert parsed["data"]["uptime_seconds"] >= 0


def test_do_get_api_health_route():
    code, body = _do_get("/api/health")
    assert code == 200
    parsed = _envelope(body)
    assert parsed["ok"] is True


# ---------------------------------------------------------------------------
# /api/analyze
# ---------------------------------------------------------------------------

def test_api_analyze_single_coin_envelope_and_extra_fields():
    code, body = web._handle_api_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}, client_ip="10.0.0.1"
    )
    assert code == 200
    parsed = _envelope(body)
    assert parsed["ok"] is True
    data = parsed["data"]
    assert data["report"]["coin"] == "BTC"
    assert isinstance(data["evidence"], list) and data["evidence"]
    # 三個新補欄位——輸入皆已在 evidence 裡，純渲染層再彙總
    assert "trust_radar" in data
    assert "trust_components_aggregate" in data
    assert "price_provenance" in data
    assert isinstance(data["trust_radar"], dict)
    assert isinstance(data["trust_components_aggregate"], dict)
    assert isinstance(data["price_provenance"], dict)
    # 跟純資料函式直接呼叫的結果一致（同一份 evidence，同一個彙總函式）
    from trustforge.agent.orchestrator import aggregate_trust_by_kind

    evidence = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]}
    )[1]
    assert set(data["trust_radar"].keys()) == set(aggregate_trust_by_kind(evidence).keys())


def test_api_analyze_comparison_envelope():
    code, body = web._handle_api_analyze(
        {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["BTC vs ETH"]},
        client_ip="10.0.0.2",
    )
    assert code == 200
    parsed = _envelope(body)
    data = parsed["data"]
    assert data["report_a"]["coin"] and data["report_b"]["coin"]
    for suffix in ("_a", "_b"):
        assert f"trust_radar{suffix}" in data
        assert f"trust_components_aggregate{suffix}" in data
        assert f"price_provenance{suffix}" in data


def test_api_analyze_invalid_coin_returns_400_generic_message():
    code, body = web._handle_api_analyze(
        {"coin": ["DOGE"], "type": ["multi_source"], "q": ["x"]}, client_ip="10.0.0.3"
    )
    assert code == 400
    parsed = _envelope(body)
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "bad_request"
    for coin in COIN_POOL:
        assert coin in parsed["error"]["message"]
    # 不洩露內部參數語法／stack trace
    assert "Traceback" not in body


def test_api_analyze_invalid_type_returns_400():
    code, body = web._handle_api_analyze(
        {"coin": ["BTC"], "type": ["bogus"], "q": ["x"]}, client_ip="10.0.0.4"
    )
    assert code == 400
    parsed = _envelope(body)
    assert parsed["ok"] is False


def test_api_analyze_do_analyze_failure_returns_502_no_leak(monkeypatch):
    """`/api/analyze` 既有的 `except Exception` 邊界（涵蓋 `_do_analyze()`
    本身＋雷達/元件彙總/信封序列化整段）——codex 複審巡查 6 個端點時本端點
    確認已包好，這裡補一個明確的降級測試鎖住行為，避免未來改動不小心
    移出 try 範圍。"""

    def _boom(qs, *, client_ip=""):
        raise RuntimeError("Bedrock InternalServerException：秘密內部訊息，/etc/aws/creds")

    monkeypatch.setattr(web, "_do_analyze", _boom)
    code, body = web._handle_api_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["x"]}, client_ip="10.0.0.9"
    )
    assert code == 502
    parsed = _envelope(body)
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "upstream_error"
    assert "Traceback" not in body
    assert "秘密內部訊息" not in body
    assert "/etc/aws/creds" not in body
    assert "RuntimeError" not in body


def test_api_analyze_dependency_valueerror_returns_502_not_400(monkeypatch):
    """codex 複審 HIGH #2：真實降級測試——`_do_analyze()` 內部呼叫的
    `pipeline.run()`，在 offline 樣本資料缺失時會 `raise ValueError("無資料：
    ...")`（見 `pipeline.py::run()` 該行、`_do_analyze` docstring 「pipeline
    無資料」一項）。這是**依賴/上游資料缺失**，不是使用者輸入錯——輸入本身
    （coin=BTC 在白名單內、query 長度合法）完全合法，所以必須是 502，
    絕不能被舊版那種「一律 except ValueError → 400」邏輯誤判成使用者輸入
    錯誤。這裡直接注入 `_do_analyze` 實際呼叫的 `web.run`（`pipeline.run`）
    來重現這個真實例外型別/訊息，而不是憑空造一個假例外。"""
    real_message = "無資料：offline 請確認 demo/sample_data 與 data/，線上請接連接器"

    def _boom(*args, **kwargs):
        raise ValueError(real_message)

    monkeypatch.setattr(web, "run", _boom)
    code, body = web._handle_api_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["x"]}, client_ip="10.0.0.10"
    )
    assert code == 502, f"依賴 ValueError 被誤判成 400，body={body}"
    parsed = _envelope(body)
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "upstream_error"
    assert "Traceback" not in body
    assert real_message not in body
    assert "demo/sample_data" not in body


def test_api_analyze_comparison_dependency_valueerror_returns_502_not_400(monkeypatch):
    """同上，comparison 分支：注入 `_do_comparison` 實際呼叫的
    `web.run_comparison`（`pipeline.run_comparison`），驗證合法輸入
    （BTC,ETH 兩個相異白名單幣種）下的依賴 ValueError 一樣走 502，不是 400。"""
    real_message = "無資料：offline 請確認 demo/sample_data 與 data/，線上請接連接器"

    def _boom(*args, **kwargs):
        raise ValueError(real_message)

    monkeypatch.setattr(web, "run_comparison", _boom)
    code, body = web._handle_api_analyze(
        {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["x"]}, client_ip="10.0.0.11"
    )
    assert code == 502, f"依賴 ValueError 被誤判成 400，body={body}"
    parsed = _envelope(body)
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "upstream_error"
    assert "Traceback" not in body
    assert real_message not in body


def test_api_analyze_pre_validation_still_400_before_touching_dependency(monkeypatch):
    """反向確認：真正的使用者輸入錯（幣種不在白名單）在**呼叫任何依賴之前**
    就已經被純驗證擋掉。用呼叫計數器（而非直接 raise）驗證——若改成 raise，
    该例外會被 handler 自己的 `except Exception` 接住變成 502，反而測不出
    「根本沒被呼叫」這件事，所以改用計數器在 try 區塊外明確斷言呼叫次數
    為 0。"""
    calls = {"n": 0}

    def _mine(*args, **kwargs):
        calls["n"] += 1
        raise RuntimeError("不該被呼叫到")

    monkeypatch.setattr(web, "run", _mine)
    monkeypatch.setattr(web, "run_comparison", _mine)

    code, body = web._handle_api_analyze(
        {"coin": ["DOGE"], "type": ["multi_source"], "q": ["x"]}, client_ip="10.0.0.12"
    )
    assert code == 400
    parsed = _envelope(body)
    assert parsed["error"]["code"] == "bad_request"
    assert calls["n"] == 0, "非法幣種不該碰到 web.run（依賴），應在純驗證階段就被擋下"

    code, body = web._handle_api_analyze(
        {"coin": ["BTC,BTC"], "type": ["comparison"], "q": ["x"]}, client_ip="10.0.0.13"
    )
    assert code == 400
    parsed = _envelope(body)
    assert parsed["error"]["code"] == "bad_request"
    assert calls["n"] == 0, "重複幣種不該碰到 web.run_comparison（依賴），應在純驗證階段就被擋下"


def test_api_analyze_json_serialisable_and_matches_analyze_json_report():
    """`/api/analyze` 的 report/evidence 內容與既有 `/analyze.json` 的
    `dataclasses.asdict`/`ev.to_dict()` 輸出邏輯完全一致（只是多包了信封 +
    三個新欄位），不是另一套資料表示。"""
    import dataclasses

    report, evidence, log = web._do_analyze(
        {"coin": ["SOL"], "type": ["multi_source"], "q": ["x"]}
    )
    code, body = web._handle_api_analyze(
        {"coin": ["SOL"], "type": ["multi_source"], "q": ["x"]}, client_ip="10.0.0.5"
    )
    parsed = _envelope(body)
    data = parsed["data"]
    assert data["report"]["market_judgment"] == report.market_judgment
    assert len(data["evidence"]) == len(evidence)


def test_do_get_api_analyze_route_sets_json_content_type():
    h = web.Handler.__new__(web.Handler)
    h.client_address = ("127.0.0.1", 1)
    h.path = "/api/analyze?coin=BTC&type=multi_source&q=test"
    from io import BytesIO
    from email.message import Message

    h.wfile = BytesIO()
    h.headers = Message()
    captured_headers = {}
    captured = []
    h.send_response = lambda code: captured.append(code)
    h.send_header = lambda name, val: captured_headers.setdefault(name, val)
    h.end_headers = lambda: None
    h.do_GET()
    assert captured[0] == 200
    assert captured_headers["Content-Type"] == "application/json; charset=utf-8"
    assert captured_headers["X-Content-Type-Options"] == "nosniff"
    body = h.wfile.getvalue().decode("utf-8")
    parsed = json.loads(body)
    assert parsed["ok"] is True


# ---------------------------------------------------------------------------
# #51 /api/analyze server-side idempotency（防重複送出，Bedrock 開通前
# 最後 prereq）—— in-flight dedup ＋ 60 秒短期結果快取：
#   - 相同 (type, coin[,coin2], query, live/real/token) 的並行/連續重複
#     請求，真正呼叫 `pipeline.run`/`pipeline.run_comparison` 的次數應遠
#     少於請求數（理想剛好 1 次）。
#   - 不同參數組合各自獨立跑，不誤 dedup。
#   - 全程監測用 `monkeypatch.setattr(web, "run", ...)`（single-coin）／
#     `monkeypatch.setattr(web, "run_comparison", ...)`（comparison）包一層
#     呼叫計數器，內部仍轉呼叫真正的 `trustforge.pipeline.run`/
#     `run_comparison`（offline 樣本資料路徑，$0），確保拿到的是真實、
#     可序列化的 Report/Evidence，而不是憑空捏造的假物件。
# ---------------------------------------------------------------------------

class _CallCounter:
    """執行緒安全的呼叫計數器——並行測試裡多個 worker thread 會同時遞增，
    純 `dict`/`int` 的 `+= 1` 不是原子操作，需要鎖保護才不會漏算。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.n = 0

    def hit(self):
        with self._lock:
            self.n += 1


def _wrap_counting_run(monkeypatch, counter: _CallCounter, *, delay: float = 0.0):
    """把 `web.run` 換成一層計數 wrapper，內部照樣轉呼叫真正的
    `pipeline.run`（offline 樣本資料，$0）——用來驗證 dedup 是否真的擋掉
    了重複呼叫，而不是驗證假造的回傳值。"""
    real_run = pipeline_module.run

    def _counting_run(*args, **kwargs):
        counter.hit()
        if delay:
            time.sleep(delay)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(web, "run", _counting_run)


def _wrap_counting_run_comparison(monkeypatch, counter: _CallCounter, *, delay: float = 0.0):
    real_run_comparison = pipeline_module.run_comparison

    def _counting_run_comparison(*args, **kwargs):
        counter.hit()
        if delay:
            time.sleep(delay)
        return real_run_comparison(*args, **kwargs)

    monkeypatch.setattr(web, "run_comparison", _counting_run_comparison)


def test_api_analyze_dedup_concurrent_identical_requests_call_run_once(monkeypatch):
    """N 個完全相同 (coin,query,type) 的請求並行送出——`pipeline.run` 真正
    只該被呼叫 1 次，其餘全部共用同一份真實結果（同一個 leader 算出來的
    response body）。"""
    counter = _CallCounter()
    _wrap_counting_run(monkeypatch, counter, delay=0.2)

    qs = {"coin": ["BTC"], "type": ["multi_source"], "q": ["dedup-concurrent-test"]}
    n_workers = 20
    barrier = threading.Barrier(n_workers)
    results: list[tuple[int, str]] = []
    results_lock = threading.Lock()

    def _worker():
        barrier.wait()
        code, body = web._handle_api_analyze(qs, client_ip="10.1.1.1")
        with results_lock:
            results.append((code, body))

    threads = [threading.Thread(target=_worker) for _ in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(results) == n_workers
    assert all(code == 200 for code, _ in results), results
    assert counter.n == 1, f"應只有 1 次真的呼叫 pipeline.run，實際 {counter.n} 次"
    bodies = {body for _, body in results}
    assert len(bodies) == 1, "所有並行重複請求應共用同一份真實結果"


def test_api_analyze_dedup_sequential_identical_requests_reuse_60s_cache(monkeypatch):
    """非並行、緊接著的重複請求（同一 key，TTL 60 秒內）：第二次應直接吃
    短期快取，`pipeline.run` 一樣只呼叫 1 次。"""
    counter = _CallCounter()
    _wrap_counting_run(monkeypatch, counter)

    qs = {"coin": ["ETH"], "type": ["multi_source"], "q": ["dedup-sequential-test"]}
    code1, body1 = web._handle_api_analyze(qs, client_ip="10.1.1.2")
    code2, body2 = web._handle_api_analyze(qs, client_ip="10.1.1.3")

    assert code1 == 200 and code2 == 200
    assert counter.n == 1, f"第二次重複請求應直接吃快取，實際呼叫 pipeline.run {counter.n} 次"
    assert body1 == body2, "重複請求應回傳同一份真實結果（可標記為快取，但內容須逐字相同）"


def test_api_analyze_dedup_different_coin_or_query_or_type_not_deduped(monkeypatch):
    """不同 coin/query/type 的請求各自獨立跑，絕不能被誤 dedup 掉。"""
    counter = _CallCounter()
    _wrap_counting_run(monkeypatch, counter)

    combos = [
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["q-a"]},
        {"coin": ["SOL"], "type": ["multi_source"], "q": ["q-a"]},
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["q-b"]},
        {"coin": ["BTC"], "type": ["hypothesis"], "q": ["q-a"]},
    ]
    for qs in combos:
        code, _ = web._handle_api_analyze(qs, client_ip="10.1.1.4")
        assert code == 200

    assert counter.n == len(combos), (
        f"4 組互不相同的請求應各自觸發 pipeline.run，實際只呼叫 {counter.n} 次"
    )


def test_api_analyze_dedup_comparison_coin_order_normalized(monkeypatch):
    """comparison `coin=BTC,ETH` 與「等價但參數順序不同」的
    `coin=ETH&coin2=BTC` 應正規化成同一把 dedup key，只真的跑 1 次
    `pipeline.run_comparison`。"""
    counter = _CallCounter()
    _wrap_counting_run_comparison(monkeypatch, counter, delay=0.1)

    qs_a = {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["cmp-dedup-test"]}
    qs_b = {"coin": ["ETH"], "coin2": ["BTC"], "type": ["comparison"], "q": ["cmp-dedup-test"]}

    code_a, body_a = web._handle_api_analyze(qs_a, client_ip="10.1.1.5")
    code_b, body_b = web._handle_api_analyze(qs_b, client_ip="10.1.1.6")

    assert code_a == 200 and code_b == 200
    assert counter.n == 1, f"順序不同但語意相同的比較請求應共用同一把 key，實際呼叫 {counter.n} 次"
    assert body_a == body_b


def test_api_analyze_dedup_leader_failure_shared_with_followers(monkeypatch):
    """leader 呼叫 `_do_analyze` 失敗時，並行等待中的 follower 應收到同一個
    例外（一律轉 502），而不是各自默默重跑一次——否則遇到失敗就等於沒
    dedup 到，防重複送出的保證形同虛設。"""
    counter = _CallCounter()

    def _boom(*args, **kwargs):
        counter.hit()
        time.sleep(0.2)
        raise ValueError("無資料：offline 請確認 demo/sample_data 與 data/，線上請接連接器")

    monkeypatch.setattr(web, "run", _boom)

    qs = {"coin": ["BTC"], "type": ["multi_source"], "q": ["dedup-failure-test"]}
    n_workers = 6
    barrier = threading.Barrier(n_workers)
    results: list[tuple[int, str]] = []
    results_lock = threading.Lock()

    def _worker():
        barrier.wait()
        code, body = web._handle_api_analyze(qs, client_ip="10.1.1.7")
        with results_lock:
            results.append((code, body))

    threads = [threading.Thread(target=_worker) for _ in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(results) == n_workers
    assert all(code == 502 for code, _ in results), results
    assert counter.n == 1, f"leader 失敗只該真的觸發依賴呼叫 1 次，實際 {counter.n} 次"


def test_api_analyze_dedup_ttl_expiry_allows_refetch(monkeypatch):
    """快取過期（TTL 60 秒）後，下一次重複請求應該重新真的跑一次，而不是
    永久卡住舊結果——直接操控 `web._analyze_dedup_cache` 裡的過期時間，
    不用真的 sleep 60 秒拖慢測試。"""
    counter = _CallCounter()
    _wrap_counting_run(monkeypatch, counter)

    qs = {"coin": ["BNB"], "type": ["multi_source"], "q": ["dedup-ttl-test"]}
    code1, _ = web._handle_api_analyze(qs, client_ip="10.1.1.8")
    assert code1 == 200
    assert counter.n == 1

    # 手動讓快取項目過期（模擬 TTL 60 秒後）。
    assert len(web._analyze_dedup_cache) == 1
    (key, (_expiry, entry)), = web._analyze_dedup_cache.items()
    web._analyze_dedup_cache[key] = (time.time() - 1.0, entry)

    code2, _ = web._handle_api_analyze(qs, client_ip="10.1.1.9")
    assert code2 == 200
    assert counter.n == 2, "TTL 過期後應該重新真的呼叫 pipeline.run，而非永久沿用舊結果"


def test_api_analyze_dedup_not_bypass_pre_validation(monkeypatch):
    """dedup 只包在「純驗證通過之後」——非法幣種仍應在碰任何依賴之前被
    400 擋下，不會被 dedup key 計算或 in-flight 機制誤放行。"""
    counter = _CallCounter()
    _wrap_counting_run(monkeypatch, counter)

    code, body = web._handle_api_analyze(
        {"coin": ["DOGE"], "type": ["multi_source"], "q": ["x"]}, client_ip="10.1.1.10"
    )
    assert code == 400
    assert counter.n == 0
    parsed = _envelope(body)
    assert parsed["error"]["code"] == "bad_request"


# ---------------------------------------------------------------------------
# /api/status
# ---------------------------------------------------------------------------

def test_api_status_ok(json_cache_backend):
    code, body = web._handle_api_status(client_ip="10.0.1.1")
    assert code == 200
    parsed = _envelope(body)
    data = parsed["data"]
    assert "version" in data
    assert "uptime_seconds" in data
    assert data["cache_backend"]["connected"] is True
    # codex 複審 MEDIUM（觀測準確性）：primary 正常時（沒動用 fallback）
    # 三個新欄位要如實反映「沒有降級」。
    assert data["cache_backend"]["primary_connected"] is True
    assert data["cache_backend"]["degraded"] is False
    assert data["cache_backend"]["active_backend"] == type(json_cache_backend).__name__
    assert set(data["freshness"].keys()) == {"fresh", "stale", "missing", "entries"}
    assert data["freshness"]["missing"] >= 0


def test_api_status_primary_fail_fallback_success_returns_200_not_502(monkeypatch, tmp_path):
    """codex 複審 HIGH（最終閉合，關鍵回歸）：先前這裡有個獨立、繞過
    `cache_get()` fallback 機制的 probe（`cache_backend.get(...)` 原始
    呼叫，回傳值沒被使用），只要 primary 拋例外就立刻 502，即使本地
    `JsonCacheBackend` fallback 其實讀得到——跟 overview/history
    「primary+fallback 都失敗才 502」的 outage 定義不一致，會把單純的
    transient DynamoDB 失敗（fallback 正常）誤判成整個依賴掛掉。

    移除冗餘 probe 後，502 判定完全交給 `get_freshness_snapshot(...,
    strict=True)`（天生 fallback-aware，見 `cache_get()` docstring）：
    primary 失敗但 fallback（真實本地 `JsonCacheBackend`，這裡指向乾淨
    tmp_path，讀不到任何資料只是合法 miss，不是例外）讀得到 → 不該 502，
    必須正常回 200 + 該格標 `missing`（合法「暫時查無資料」，不是
    「依賴不可用」）。這是本輪 codex 要的關鍵回歸測試。

    codex 複審 MEDIUM（觀測準確性，最終閉合）：光回 200 還不夠——這裡
    **同時**驗證回應不再謊報 `connected:true`/隱瞞降級：primary 全部靠
    fallback 頂住時，必須如實回報 `primary_connected:false`、
    `degraded:true`、`active_backend:"JsonCacheBackend"`（不是原本 primary
    的類名），`connected` 欄位語意收斂成跟 `primary_connected` 一致，也
    不能是 `True`。"""
    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_PATH", str(tmp_path / "fallback_cache.json"))

    broken = DynamoDBCache()
    monkeypatch.setattr(
        broken, "_get_table",
        MagicMock(side_effect=RuntimeError("transient DynamoDB throttling：秘密內部訊息")),
    )
    monkeypatch.setattr(web, "_status_cache_backend", lambda: broken)

    code, body = web._handle_api_status(client_ip="10.0.1.2")
    assert code == 200, f"primary 失敗但 fallback 正常，卻被誤判成 502，body={body}"
    parsed = _envelope(body)
    assert parsed["ok"] is True
    data = parsed["data"]
    assert data["freshness"]["missing"] >= 0
    assert data["cache_backend"]["primary_connected"] is False, (
        f"primary 明明拋例外，卻謊報 primary_connected:true，body={body}"
    )
    assert data["cache_backend"]["degraded"] is True
    assert data["cache_backend"]["active_backend"] == "JsonCacheBackend"
    assert data["cache_backend"]["connected"] is False, (
        f"connected 欄位不該無條件硬寫 True，掩蓋 primary outage，body={body}"
    )
    # 就算最終回 200，primary 拋出的原始例外訊息也不該外洩到回應 body
    # （只在 server log 看得到，harper CISO must-have #3 這裡仍然適用）。
    assert "秘密內部訊息" not in body
    assert "Traceback" not in body


def test_api_status_freshness_dependency_failure_returns_502(monkeypatch, json_cache_backend):
    """codex 複審 HIGH（對齊 502 契約）：cache probe 成功，但
    `get_freshness_snapshot()` 這個依賴讀取失敗（真實 cache_get 路徑
    注入）——同樣不得 swallow 成空鮮度 + 200，必須 502。"""
    import trustforge.ingestion.cache as cache_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("DynamoDB Scan 逾時：秘密內部訊息")

    monkeypatch.setattr(web, "_status_cache_backend", lambda: json_cache_backend)
    monkeypatch.setattr(cache_mod, "get_freshness_snapshot", _boom)
    code, body = web._handle_api_status(client_ip="10.0.1.8")
    assert code == 502, f"freshness 依賴失敗被誤判成 200，body={body}"
    parsed = _envelope(body)
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "upstream_error"
    assert "Traceback" not in body
    assert "秘密內部訊息" not in body


def test_api_status_freshness_real_backend_outage_returns_502_not_200(monkeypatch, tmp_path):
    """codex 複審 HIGH（根因修復）：打真正的 `get_freshness_snapshot()` →
    `cache_get()` 讀取路徑——primary（真 `DynamoDBCache`，mock `_get_table`
    模擬憑證/連線壞掉）**和** fallback（本地 `JsonCacheBackend`，
    monkeypatch 模擬磁碟也讀不了）**都**真的失敗，驗證 `/api/status` 用
    `strict=True` 接住了：回 502，**不是**悄悄回 200 + 全部標成 `missing`
    的空鮮度矩陣（這才是 outage，而不是單純沒資料）。"""
    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_PATH", str(tmp_path / "fallback_cache.json"))
    monkeypatch.setattr(
        JsonCacheBackend, "get",
        lambda self, key: (_ for _ in ()).throw(OSError("磁碟也壞了")),
    )

    broken = DynamoDBCache()
    monkeypatch.setattr(
        broken, "_get_table",
        MagicMock(side_effect=RuntimeError("no aws credentials / table not found")),
    )
    monkeypatch.setattr(web, "_status_cache_backend", lambda: broken)

    code, body = web._handle_api_status(client_ip="10.0.1.9")
    assert code == 502, f"cache outage 被誤判成 200，body={body}"
    parsed = _envelope(body)
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "upstream_error"
    assert "Traceback" not in body
    assert "no aws credentials" not in body


def test_api_status_backend_construction_failure_returns_502_no_leak(monkeypatch):
    """codex 複審 HIGH：`_status_cache_backend()` **建構本身**（不是 `.get()`
    探測）失敗——例如 config/憑證錯誤——先前會直接穿透 `do_GET` 吐 traceback。
    現在整段回通用 502，不洩露例外訊息。"""

    def _boom():
        raise RuntimeError("DynamoDB config 壞了：秘密內部路徑 /etc/secret.cfg")

    monkeypatch.setattr(web, "_status_cache_backend", _boom)
    code, body = web._handle_api_status(client_ip="10.0.1.9")
    assert code == 502
    parsed = _envelope(body)
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "upstream_error"
    assert "Traceback" not in body
    assert "秘密內部路徑" not in body
    assert "/etc/secret.cfg" not in body
    assert "RuntimeError" not in body


def test_api_status_slow_primary_circuit_breaker_bounded_latency_single_attempt(
    monkeypatch, tmp_path
):
    """codex 複審 HIGH（production 安全，circuit breaker + 短 timeout）：
    `get_freshness_snapshot()` 逐 (source, coin) 約 115 格，若每格都重新
    嘗試一次 primary，primary 慢/掛掉時會 (1) 對已掛的依賴疊加 ~115 倍
    流量、(2) 讓整支請求延遲隨格數線性放大（可拖到多分鐘）。

    這裡用一個「第一次呼叫就慢 + 拋例外」的 fake backend（模擬 DynamoDB
    outage 時 primary 本身也慢的最壞情境）驗證：
      1. **primary 全程只被嘗試一次**——circuit breaker 生效後，後續格子
         直接跳過 primary、只讀 fallback，不會被 ~115 格線性放大成
         ~115 次呼叫。
      2. **整支請求延遲 bounded**——遠低於「格數 × 單次慢速時間」量級
         （0.05s × 115 ≈ 5.75s），證明沒有逐格重試。
      3. 仍然回 200 + degraded（fallback 服務本身正常，只是繞去讀本地
         備援）——跟上一輪定義的「fallback 成功仍 200」語意一致。
    """
    monkeypatch.setenv("TRUSTFORGE_CACHE_JSON_PATH", str(tmp_path / "fallback_cache.json"))

    calls = {"n": 0}

    class _SlowThenFailBackend:
        def get(self, key, *, consistent_read=False):
            calls["n"] += 1
            time.sleep(0.05)  # 模擬單次 primary 嘗試本身就慢（即使有短
            # timeout，這一下仍要花掉一些時間；重點是這個時間不會被乘上
            # 115 格）。
            raise TimeoutError("simulated DynamoDB primary read timeout")

        def set(self, key, docs, fetched_at, ttl_seconds=None):
            raise AssertionError("不該被呼叫（唯讀路徑）")

    monkeypatch.setattr(web, "_status_cache_backend", lambda: _SlowThenFailBackend())

    start = time.monotonic()
    code, body = web._handle_api_status(client_ip="10.0.1.20")
    elapsed = time.monotonic() - start

    assert code == 200, f"primary 慢/失敗但 fallback 正常，應回 200 degraded，body={body}"
    parsed = _envelope(body)
    assert parsed["ok"] is True
    data = parsed["data"]
    assert data["cache_backend"]["primary_connected"] is False
    assert data["cache_backend"]["degraded"] is True
    assert data["cache_backend"]["active_backend"] == "JsonCacheBackend"

    assert calls["n"] <= 1, (
        f"circuit breaker 沒生效：primary 被嘗試了 {calls['n']} 次，"
        "應該只嘗試一次、後續格子直接跳去 fallback"
    )
    assert elapsed < 2.0, (
        f"/api/status 延遲隨格數線性放大（circuit breaker 沒生效），"
        f"elapsed={elapsed:.2f}s"
    )


def test_api_status_rate_limited_after_threshold(json_cache_backend):
    ip = "10.0.1.3"
    for _ in range(web._STATUS_RATE_MAX):
        code, _ = web._handle_api_status(client_ip=ip)
        assert code == 200
    code, body = web._handle_api_status(client_ip=ip)
    assert code == 429
    parsed = _envelope(body)
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "rate_limited"


# ---------------------------------------------------------------------------
# /api/costs
# ---------------------------------------------------------------------------

def test_api_costs_matches_ledger_summary(json_cache_backend, monkeypatch, tmp_path):
    """`_get_ledger_summary()` 以 `get_ledger` 函式物件本身 keyed 20 秒 TTL
    快取（見該函式 docstring）——比照 `tests/test_cost_ledger.py` 既有慣例，
    用 `monkeypatch.setattr(web, "get_ledger", ...)` 換一顆全新 fake 工廠，
    確保這裡讀到的一定是本測試自己寫入的帳本內容，不會被其他測試在同一
    個 20 秒視窗內留下的快取值汙染。"""
    monkeypatch.setenv("TRUSTFORGE_COST_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    ledger = JsonlLedger()
    ledger.append({
        "run_id": "r1", "ts": "2026-01-01T00:00:00+00:00",
        "total_cost_usd": 0.0042, "calls": [{"model": "m", "cost_usd": 0.0042}],
    })
    monkeypatch.setattr(web, "get_ledger", lambda: ledger)
    code, body = web._handle_api_costs(client_ip="10.0.2.1")
    assert code == 200
    parsed = _envelope(body)
    data = parsed["data"]
    assert data["total_cost_usd"] == pytest.approx(0.0042)
    assert "by_model" in data and "runs" in data
    assert data["run_count"] == 1


def test_api_costs_large_ledger_response_is_bounded_not_full_scan(
    json_cache_backend, monkeypatch, tmp_path
):
    """codex 複審 HIGH（成本端點可擴展性）：帳本無論多大，`/api/costs` 回應都
    不能序列化整份 `runs`——`run_count` 反映真實總筆數，`runs` 只回最近
    `ledger.SUMMARY_RECENT_RUNS_CAP`（50）筆，不隨帳本大小線性增長。"""
    monkeypatch.setenv("TRUSTFORGE_COST_LEDGER_PATH", str(tmp_path / "big_ledger.jsonl"))
    ledger = JsonlLedger()
    total_records = 500
    for i in range(total_records):
        ledger.append({
            "run_id": f"r{i}",
            "ts": f"2026-01-01T00:{i % 60:02d}:00+00:00",
            "coin": "BTC",
            "question_type": "multi_source",
            "offline": False,
            "total_cost_usd": 0.001,
            "calls": [{"model": "m", "cost_usd": 0.001, "tokens_in": 1, "tokens_out": 1}],
        })
    monkeypatch.setattr(web, "get_ledger", lambda: ledger)

    code, body = web._handle_api_costs(client_ip="10.0.2.2")

    assert code == 200
    parsed = _envelope(body)
    data = parsed["data"]
    assert data["run_count"] == total_records
    assert len(data["runs"]) == ledger_module.SUMMARY_RECENT_RUNS_CAP
    assert data["total_cost_usd"] == pytest.approx(total_records * 0.001)
    # 回應體積跟帳本大小脫鉤：body 長度不會隨 500 筆記錄線性膨脹到能塞下全部
    # runs（每筆記錄序列化後遠大於這個門檻，能通過代表沒有整份塞進去）。
    assert len(body) < 20_000


def test_api_costs_ledger_read_failure_returns_502_no_leak(monkeypatch):
    """codex 複審 HIGH：`_get_ledger_summary()`（含其內部 fallback）萬一整段
    仍炸出例外，handler 層要接住，回通用 502，不洩露例外訊息/內部路徑。"""

    def _boom():
        raise RuntimeError("ledger 檔案毀損：/private/secret/ledger.jsonl")

    monkeypatch.setattr(web, "_get_ledger_summary", _boom)
    code, body = web._handle_api_costs(client_ip="10.0.2.9")
    assert code == 502
    parsed = _envelope(body)
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "upstream_error"
    assert "Traceback" not in body
    assert "/private/secret/ledger.jsonl" not in body
    assert "RuntimeError" not in body


# ---------------------------------------------------------------------------
# /api/overview
# ---------------------------------------------------------------------------

def test_api_overview_backend_construction_failure_returns_502_no_leak(monkeypatch):
    """codex 複審 HIGH：`_home_overview_backend()` 建構本身出錯（憑證/DNS/
    config 問題）——先前沒包例外邊界，會直接穿透 `do_GET` 吐 traceback。"""

    def _boom():
        raise RuntimeError("AccessDeniedException：秘密內部訊息，/var/secret")

    monkeypatch.setattr(web, "_home_overview_backend", _boom)
    code, body = web._handle_api_overview(client_ip="10.0.3.9")
    assert code == 502
    parsed = _envelope(body)
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "upstream_error"
    assert "Traceback" not in body
    assert "AccessDeniedException" not in body
    assert "秘密內部訊息" not in body
    assert "/var/secret" not in body


def test_api_overview_cache_read_failure_returns_502_no_leak(monkeypatch, json_cache_backend):
    """codex 複審 HIGH：backend 建構成功，但逐幣 `cache_get()` 讀取炸例外
    （如 DynamoDB 讀取失敗）——同一段 try 也要接住。"""

    def _boom_backend():
        return json_cache_backend

    def _boom_cache_get(*args, **kwargs):
        raise RuntimeError("DynamoDB ProvisionedThroughputExceededException：內部訊息")

    monkeypatch.setattr(web, "_home_overview_backend", _boom_backend)
    import trustforge.ingestion.cache as cache_mod

    monkeypatch.setattr(cache_mod, "cache_get", _boom_cache_get)
    code, body = web._handle_api_overview(client_ip="10.0.3.8")
    assert code == 502
    parsed = _envelope(body)
    assert parsed["ok"] is False
    assert "Traceback" not in body
    assert "ProvisionedThroughputExceededException" not in body


def test_api_overview_real_backend_outage_returns_502_not_200_empty(monkeypatch, tmp_path):
    """codex 複審 HIGH（根因修復）：上面那個測試是 monkeypatch `cache_get`
    這個 helper 本身，codex 明確指出「沒測到 `cache_get` 真正吞例外的行
    為」——這裡改成打真正的 `cache_get()` 讀取路徑（真 backend、primary+
    fallback 都真的失敗），驗證 `/api/overview` 現在用 `strict=True` 接住
    了：回 502，**不是**悄悄回 200 + 空/部分 coins 陣列（那樣監控/使用者
    會誤以為只是剛好沒資料，而非 cache 依賴真的掛了）。"""
    broken = _real_broken_backend_with_dead_fallback(monkeypatch, tmp_path)
    monkeypatch.setattr(web, "_home_overview_backend", lambda: broken)

    code, body = web._handle_api_overview(client_ip="10.0.3.7")
    assert code == 502, f"cache outage 被誤判成 200，body={body}"
    parsed = _envelope(body)
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "upstream_error"
    assert "Traceback" not in body
    assert "no aws credentials" not in body


def test_api_overview_reads_per_coin_snapshot(json_cache_backend):
    snap = {
        "coin": "BTC",
        "trust_score": 0.71,
        "direction": "偏多",
        "calibrated_confidence": 0.6,
        "decision_state": "normal",
        "generated_at": "2026-07-01T00:00:00Z",
    }
    result = cache_set_if_newer(
        json_cache_backend, cache_key(TRUST_SNAPSHOT_SOURCE, "BTC"), [snap],
        fetched_at=1000.0, allow_json_fallback=True,
    )
    assert result.ok

    code, body = web._handle_api_overview(client_ip="10.0.3.1")
    assert code == 200
    parsed = _envelope(body)
    coins = parsed["data"]["coins"]
    assert len(coins) == 1
    assert coins[0]["coin"] == "BTC"
    assert coins[0]["trust_score"] == pytest.approx(0.71)
    assert coins[0]["fetched_at_epoch"] == 1000.0


def test_api_overview_empty_when_no_snapshots_written(json_cache_backend):
    code, body = web._handle_api_overview(client_ip="10.0.3.2")
    assert code == 200
    parsed = _envelope(body)
    assert parsed["data"]["coins"] == []


# ---------------------------------------------------------------------------
# /api/history
# ---------------------------------------------------------------------------

def test_api_history_returns_sorted_daily_series(json_cache_backend):
    # 用「相對今天」的日期（而非寫死的曆法日期）當測資：`_handle_api_history()`
    # 沒有（也不該有，endpoint 邏輯本輪不能動）`end_date` 覆寫參數，`days=5` 窗
    # 一律是相對呼叫當下 UTC 今天往回算。寫死曆法日期（如 2026-06-29）只在
    # 特定時間窗內落在「近 5 天」內，時間一過測資就跑出窗外變成 time bomb
    # （codex 複審：`assert ('2026-06-29' in ['2026-06-30'])` 失敗）。改成
    # `today - N 天`，不管哪天執行都保證落在 5 天窗內。
    today = datetime.now(timezone.utc).date()
    day1 = (today - timedelta(days=2)).isoformat()
    day2 = (today - timedelta(days=1)).isoformat()
    for day, score in ((day1, 0.5), (day2, 0.6)):
        snap = {"coin": "ETH", "trust_score": score, "direction": "中性",
                "calibrated_confidence": score, "decision_state": "normal",
                "generated_at": f"{day}T00:00:00Z"}
        result = cache_set_if_newer(
            json_cache_backend, trust_snapshot_history_key("ETH", day), [snap],
            fetched_at=1000.0, allow_json_fallback=True,
        )
        assert result.ok

    code, body = web._handle_api_history(
        {"coin": ["ETH"], "days": ["5"]}, client_ip="10.0.4.1"
    )
    assert code == 200
    parsed = _envelope(body)
    data = parsed["data"]
    assert data["coin"] == "ETH"
    assert data["days"] == 5
    dates = [h["date"] for h in data["history"]]
    assert dates == sorted(dates)
    assert day1 in dates and day2 in dates


def test_api_history_rejects_bad_coin(json_cache_backend):
    code, body = web._handle_api_history(
        {"coin": ["DOGE"], "days": ["5"]}, client_ip="10.0.4.2"
    )
    assert code == 400
    parsed = _envelope(body)
    assert parsed["error"]["code"] == "bad_request"


@pytest.mark.parametrize("days_val", ["0", "-1", "abc", "99999", "9999999999999"])
def test_api_history_rejects_bad_days(json_cache_backend, days_val):
    code, body = web._handle_api_history(
        {"coin": ["BTC"], "days": [days_val]}, client_ip="10.0.4.3"
    )
    assert code == 400
    parsed = _envelope(body)
    assert parsed["ok"] is False


def test_api_history_missing_coin_defaults_to_400_not_500(json_cache_backend):
    code, body = web._handle_api_history({"days": ["5"]}, client_ip="10.0.4.4")
    assert code == 400


def test_api_history_backend_construction_failure_returns_502_no_leak(monkeypatch):
    """codex 複審 HIGH：`get_trust_history()` 內部呼叫的
    `_home_overview_backend()` 建構失敗——既有 try/except 已包住這條路徑，
    這裡補一個明確的降級測試鎖住行為，確保回通用 502、不洩露細節。"""

    def _boom():
        raise RuntimeError("AccessDeniedException：秘密內部訊息")

    monkeypatch.setattr(web, "_home_overview_backend", _boom)
    code, body = web._handle_api_history(
        {"coin": ["BTC"], "days": ["5"]}, client_ip="10.0.4.9"
    )
    assert code == 502
    parsed = _envelope(body)
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "upstream_error"
    assert "Traceback" not in body
    assert "AccessDeniedException" not in body
    assert "秘密內部訊息" not in body


def test_api_history_real_backend_outage_returns_502_not_200_empty(monkeypatch, tmp_path):
    """codex 複審 HIGH（根因修復）：打真正的 `get_trust_history()` →
    `cache_get()` 讀取路徑（真 backend，primary+fallback 都真的失敗），
    驗證 `/api/history` 用 `strict=True` 接住了：回 502，**不是**悄悄回
    200 + 空 history 陣列（那樣會被誤判成「這幾天剛好都沒排程寫過快照」，
    而非 cache 依賴真的掛了）。"""
    broken = _real_broken_backend_with_dead_fallback(monkeypatch, tmp_path)
    monkeypatch.setattr(web, "_home_overview_backend", lambda: broken)

    code, body = web._handle_api_history(
        {"coin": ["BTC"], "days": ["5"]}, client_ip="10.0.4.7"
    )
    assert code == 502, f"cache outage 被誤判成 200，body={body}"
    parsed = _envelope(body)
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "upstream_error"
    assert "Traceback" not in body
    assert "no aws credentials" not in body


def test_api_history_pure_miss_still_returns_200_empty_history(json_cache_backend):
    """對照組：backend 正常運作、只是這幾天沒排程寫過快照（合法 miss，
    不是 outage）——`strict=True` 不該把這個也變成 502，仍要回 200 +
    空 history 陣列（既有行為，`strict` 只影響「讀取真的失敗」分支）。"""
    code, body = web._handle_api_history(
        {"coin": ["BTC"], "days": ["5"]}, client_ip="10.0.4.8"
    )
    assert code == 200
    parsed = _envelope(body)
    assert parsed["data"]["history"] == []


# ---------------------------------------------------------------------------
# SSR 不變回歸鎖：既有 HTML 路由行為維持不動
# ---------------------------------------------------------------------------

def test_ssr_routes_untouched_by_json_api_addition(json_cache_backend):
    code, body = _do_get("/")
    assert code == 200
    assert "信任提煉" in body

    code, body = _do_get("/status")
    assert code == 200
    assert "系統狀態" in body

    code, body = _do_get("/costs")
    assert code == 200
    assert "累計花費" in body or "尚無" in body or "成本" in body

    code, body = _do_get("/analyze?coin=BTC&type=multi_source&q=test")
    assert code == 200
    assert "市場判斷" in body

    code, body = _do_get("/analyze.json?coin=BTC&type=multi_source&q=test")
    assert code == 200
    parsed = json.loads(body)
    assert "report" in parsed and "ok" not in parsed  # 既有 /analyze.json 沒有信封，逐字不變
