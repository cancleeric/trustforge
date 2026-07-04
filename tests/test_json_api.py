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


def test_api_analyze_dedup_comparison_preserves_request_order_not_swapped(monkeypatch):
    """codex HIGH 複審修正：comparison dedup key **不能**排序 coin pair——
    `/api/analyze` 的 `report_a`/`evidence_a`/`price_provenance_a` 是描述
    `coin_a` 的**有序**欄位，`_b` 系列描述 `coin_b`；`coin=BTC,ETH`
    （coin_a=BTC）與 `coin=ETH,coin2=BTC`（coin_a=ETH）是 A/B 對調、語意
    不同的兩份報告，絕不能共用同一份快取——否則後者會被 dedup 成前者的
    快取 body，「report_a」實際卻描述 BTC 而非請求的 ETH，A/B 對應錯。

    這裡直接斷言每個回應的 `report_a.coin`／`report_b.coin` 對應**自己
    請求的順序**，且兩個反序請求各自真的呼叫了 `pipeline.run_comparison`
    （不是被誤 dedup 成 1 次）。"""
    counter = _CallCounter()
    _wrap_counting_run_comparison(monkeypatch, counter)

    qs_forward = {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["cmp-order-test"]}
    qs_reversed = {"coin": ["ETH"], "coin2": ["BTC"], "type": ["comparison"], "q": ["cmp-order-test"]}

    code_fwd, body_fwd = web._handle_api_analyze(qs_forward, client_ip="10.1.1.5")
    code_rev, body_rev = web._handle_api_analyze(qs_reversed, client_ip="10.1.1.6")

    assert code_fwd == 200 and code_rev == 200
    data_fwd = _envelope(body_fwd)["data"]
    data_rev = _envelope(body_rev)["data"]
    assert data_fwd["report_a"]["coin"] == "BTC" and data_fwd["report_b"]["coin"] == "ETH"
    assert data_rev["report_a"]["coin"] == "ETH" and data_rev["report_b"]["coin"] == "BTC", (
        "反序請求（coin=ETH,coin2=BTC）的 report_a 應描述 ETH，"
        "不能被排序過的 dedup key 誤判成正序請求的快取 body（A/B 對調）"
    )
    assert counter.n == 2, (
        f"順序不同的兩個 comparison 請求語意不同（A/B 對調），應各自真的呼叫 "
        f"pipeline.run_comparison，實際只呼叫 {counter.n} 次"
    )


def test_api_analyze_dedup_comparison_same_order_still_deduped(monkeypatch):
    """同順序（同一個 coin_a,coin_b 序列）的重複 comparison 請求仍應正常
    dedup（只跑 1 次）——上一條測試確認的是「順序不同不誤 dedup」，這條
    反向確認「順序相同該 dedup 的還是有 dedup 到」，避免修正 codex HIGH
    時矯枉過正變成完全不 dedup comparison。"""
    counter = _CallCounter()
    _wrap_counting_run_comparison(monkeypatch, counter)

    qs = {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["cmp-same-order-test"]}
    code1, body1 = web._handle_api_analyze(qs, client_ip="10.1.1.11")
    code2, body2 = web._handle_api_analyze(qs, client_ip="10.1.1.12")

    assert code1 == 200 and code2 == 200
    assert counter.n == 1, f"同順序重複請求應共用同一把 key，實際呼叫 {counter.n} 次"
    assert body1 == body2


def test_api_analyze_dedup_sample_vs_real_not_shared(monkeypatch):
    """key 已含 `live`/`real`/`token`——離線示範沙盒（`?sample=1`，opt-in，
    固定樣本資料）跟預設「真資料·$0」檔位（`_is_real_request` 未帶
    `sample`/`live` 時的預設，見該函式 docstring），即使 coin/query/type
    完全相同，資料來源本質不同（固定樣本 vs 即時連接器讀取），絕不能共用
    快取——否則使用者要看樣本 demo 卻拿到（或反過來汙染）即時真資料的
    快取結果。

    直接驗證兩件事：(1) `_analyze_dedup_key()` 算出的 key 確實不同；
    (2) 兩次呼叫各自真的觸發 `pipeline.run`（呼叫次數為 2，不是被 dedup
    成 1 次）。"""
    counter = _CallCounter()
    _wrap_counting_run(monkeypatch, counter)

    qs_sample = {
        "coin": ["BTC"], "type": ["multi_source"], "q": ["sample-vs-real-test"],
        "sample": ["1"],
    }
    qs_real = {"coin": ["BTC"], "type": ["multi_source"], "q": ["sample-vs-real-test"]}

    key_sample = web._analyze_dedup_key(
        qtype=web.QuestionType("multi_source"), coin_key="BTC",
        query="sample-vs-real-test", qs=qs_sample,
    )
    key_real = web._analyze_dedup_key(
        qtype=web.QuestionType("multi_source"), coin_key="BTC",
        query="sample-vs-real-test", qs=qs_real,
    )
    assert key_sample != key_real, "sample 與 real（預設）請求的 dedup key 必須不同"

    code1, _ = web._handle_api_analyze(qs_sample, client_ip="10.1.1.13")
    code2, _ = web._handle_api_analyze(qs_real, client_ip="10.1.1.14")

    assert code1 == 200 and code2 == 200
    assert counter.n == 2, (
        f"sample 與 real（預設）是不同資料來源，不該共用快取，各自都該真的呼叫 "
        f"pipeline.run，實際只呼叫 {counter.n} 次"
    )


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


def test_api_analyze_dedup_stalled_leader_follower_times_out_not_infinite_block(monkeypatch):
    """codex HIGH 複審（follower 無限阻塞資源耗盡，web.py:3353-3354）：leader
    （模擬真連接器/Bedrock）卡住太久時，follower 的 `event.wait()` 必須有
    bounded timeout（`_ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS`），逾時後回
    可重試的 503，而不是無限阻塞該 server thread——重複請求越多，卡住的
    thread 就越多，會把一個單純變慢/掛掉的依賴放大成整個 server 的 thread
    池耗盡。

    這裡把逾時上界調小（0.3 秒），leader 的模擬延遲（1.5 秒）明確大於
    逾時上界；直接量測每個 worker 各自的耗時，斷言 follower 遠早於
    leader 完成前就已經返回（thread 真的被釋放，不是碰巧等到 leader
    做完才順便回來），且逾時的 follower 不會落回自己真的呼叫
    `pipeline.run`（避免放大 Bedrock 花費/thundering herd）。"""
    monkeypatch.setattr(web, "_ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS", 0.3)

    counter = _CallCounter()
    leader_finished = threading.Event()

    def _stalled_run(*args, **kwargs):
        counter.hit()
        time.sleep(1.5)  # 遠大於逾時上界 0.3 秒，模擬 leader hang 住
        result = pipeline_module.run(*args, **kwargs)
        leader_finished.set()
        return result

    monkeypatch.setattr(web, "run", _stalled_run)

    qs = {"coin": ["BTC"], "type": ["multi_source"], "q": ["dedup-stalled-leader-test"]}
    n_workers = 4
    barrier = threading.Barrier(n_workers)
    results: list[tuple[int, str, float]] = []
    results_lock = threading.Lock()

    def _worker():
        barrier.wait()
        t0 = time.time()
        code, body = web._handle_api_analyze(qs, client_ip="10.1.2.1")
        with results_lock:
            results.append((code, body, time.time() - t0))

    threads = [threading.Thread(target=_worker) for _ in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert all(not t.is_alive() for t in threads), "所有 worker thread 都該正常結束，不能無限阻塞"
    assert len(results) == n_workers

    codes = [code for code, _, _ in results]
    assert codes.count(200) == 1, f"應恰好 1 個 leader 成功拿到 200，實際 {codes}"
    assert codes.count(503) == n_workers - 1, (
        f"其餘 follower 應在 bounded wait 逾時後回可重試的 503，實際 {codes}"
    )

    for code, body, elapsed in results:
        if code == 503:
            parsed = _envelope(body)
            assert parsed["error"]["code"] == "timeout"
            # 逾時上界只有 0.3 秒；就算加上排程/GIL 開銷，也該遠早於
            # leader 的 1.5 秒延遲返回——證明 thread 真的被釋放，而不是
            # 恰好等到 leader 做完才「順便」回來。
            assert elapsed < 1.0, f"follower 應在逾時上界內返回，實際耗時 {elapsed:.3f}s"

    leader_finished.wait(timeout=5)
    assert counter.n == 1, (
        f"逾時的 follower 不該落回自己真的呼叫 pipeline.run（會放大 Bedrock 花費），"
        f"實際呼叫 {counter.n} 次"
    )


def test_api_analyze_dedup_stalled_leader_entry_becomes_stale_and_replaced(monkeypatch):
    """codex HIGH 複審（過期/replace stale in-flight entry）：leader
    掛掉/hang 死、且真的完全沒有觸發任何清理程式碼（例如被強制中斷，
    不像一般 Exception 有 `except` 分支負責清理）時，該 key 的 in-flight
    entry 會變成永久卡住的殭屍——後續所有相同請求絕不能永遠 follow 一個
    死掉的 leader（等到 `_ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS` 也沒用，
    因為根本沒有人會 `event.set()`）。

    這裡直接偽造一個「已經存在超過逾時上界、且永遠不會被 set() 的殭屍
    in-flight entry」，斷言下一個進來的請求會偵測到它是 stale、直接取代
    成為新 leader，真的重新觸發一次 `pipeline.run`，而不是掛在殭屍
    entry 上等到天荒地老。"""
    monkeypatch.setattr(web, "_ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS", 0.3)

    counter = _CallCounter()
    _wrap_counting_run(monkeypatch, counter)

    qs = {"coin": ["BTC"], "type": ["multi_source"], "q": ["dedup-stale-leader-test"]}
    coin_key = "BTC"
    query = "dedup-stale-leader-test"
    key = web._analyze_dedup_key(
        qtype=web.QuestionType("multi_source"), coin_key=coin_key, query=query, qs=qs
    )

    zombie_event = threading.Event()  # 永遠不會被 set() ——模擬死掉的 leader
    stale_started_at = time.time() - 10.0  # 遠遠超過 0.3 秒的逾時上界
    zombie_generation = -999  # 刻意用不會跟真正計數器撞號的假世代編號
    web._analyze_dedup_inflight[key] = (zombie_event, stale_started_at, zombie_generation)

    code, body = web._handle_api_analyze(qs, client_ip="10.1.2.2")

    assert code == 200, f"偵測到 stale entry 後應取代成為新 leader、真的跑出結果，實際 {code} {body}"
    assert counter.n == 1, f"應該真的觸發 1 次 pipeline.run，而不是繼續等殭屍 leader，實際 {counter.n} 次"
    # 殭屍 entry 應該已經被新 leader 的 entry 取代／清掉，不會再殘留。
    current = web._analyze_dedup_inflight.get(key)
    assert current is None or current[0] is not zombie_event


def test_dedup_analyze_call_stale_leader_finishing_before_any_replacement_still_published(
    monkeypatch,
):
    """codex HIGH 複審#4（stale-leader 取代新 race，generation fencing）：
    只有真的「被取代」才需要 fencing——若一個 leader 雖然跑得比逾時門檻
    久，但從頭到尾都沒有其他請求真的把它取代掉，它自己完成時仍是這把
    key 唯一、合法的一份結果，必須正常發布/服務，不能因為「超過門檻」
    這件事本身就一律 no-op（那樣會把單純比較慢、但沒被取代的正常情況
    也錯殺，等於每次真連接器/Bedrock 剛好跑超過 45 秒都變成永遠拿不到
    結果）。"""
    monkeypatch.setattr(web, "_ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS", 0.2)
    key = "dedup-stale-not-yet-replaced-test-key"

    def _compute_a():
        time.sleep(0.3)  # 故意超過（縮短過的）逾時門檻，但沒有其他請求進來取代
        return "result-A-slow-but-sole"

    result = web._dedup_analyze_call(key, _compute_a)
    assert result == "result-A-slow-but-sole"

    cached = web._analyze_dedup_cache.get(key)
    assert cached is not None and cached[1] == (True, "result-A-slow-but-sole"), (
        "沒有被任何請求取代的 leader，即使跑得比逾時門檻久，完成後仍應正常發布"
    )
    assert key not in web._analyze_dedup_inflight

    # 佐證：緊接著同一把 key 的新請求命中的是 A 自己剛發布的快取，不會
    # 誤判需要重新跑一次。
    counter = _CallCounter()

    def _compute_should_not_run():
        counter.hit()
        return "should-not-be-called"

    result2 = web._dedup_analyze_call(key, _compute_should_not_run)
    assert result2 == "result-A-slow-but-sole"
    assert counter.n == 0


def test_dedup_analyze_call_stale_leader_finishing_after_replacement_does_not_overwrite(
    monkeypatch,
):
    """codex HIGH 複審#4（stale-leader 取代新 race，generation fencing）：
    這是 codex 這輪要修的核心 bug 場景——leader A 卡住超過逾時門檻，
    新請求偵測到 A 是 stale、取代成為新 leader B，B 很快完成、發布自己
    的結果並清掉 in-flight；**之後**A 才姍姍來遲真的完成（不是真的 hang
    死，只是很慢）。斷言：A 完成後，共用快取仍是 B 的結果，沒有被 A
    覆寫；in-flight 也沒有任何殘留副作用。"""
    monkeypatch.setattr(web, "_ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS", 0.2)
    key = "dedup-stale-race-after-replacement-test-key"

    a_may_finish = threading.Event()
    a_result_holder: dict[str, object] = {}

    def _compute_a():
        a_may_finish.wait(timeout=5)
        return "result-A"

    def _compute_b():
        return "result-B"

    def _worker_a():
        try:
            a_result_holder["value"] = web._dedup_analyze_call(key, _compute_a)
        except Exception as exc:  # pragma: no cover - 只在斷言失敗時才有意義
            a_result_holder["error"] = exc

    a_thread = threading.Thread(target=_worker_a)
    a_thread.start()

    # 等 A 真的成為 leader（in-flight 出現這把 key）。
    for _ in range(100):
        if key in web._analyze_dedup_inflight:
            break
        time.sleep(0.02)
    assert key in web._analyze_dedup_inflight, "leader A 應該已經寫入 in-flight"

    # 等超過（縮短過的）逾時門檻，讓 A 變成 stale。
    time.sleep(0.3)

    # B 進來，偵測到 A 是 stale、取代成為新 leader，並立刻完成、發布。
    result_b = web._dedup_analyze_call(key, _compute_b)
    assert result_b == "result-B"
    cached = web._analyze_dedup_cache.get(key)
    assert cached is not None and cached[1] == (True, "result-B")
    assert key not in web._analyze_dedup_inflight

    # 現在才放 A 完成——A 的 compute() 這時候才真的返回。
    a_may_finish.set()
    a_thread.join(timeout=5)
    assert a_result_holder.get("value") == "result-A", "A 自己應該還是拿到自己真正算出來的結果"

    # 關鍵斷言：A（stale）晚到的完成，不該覆寫 B 已經發布的結果，也不該
    # 誤清 in-flight（此時 in-flight 早就是空的）。
    cached_after = web._analyze_dedup_cache.get(key)
    assert cached_after is not None and cached_after[1] == (True, "result-B"), (
        f"stale leader A 晚到完成不該覆寫 B 已發布的結果，實際 {cached_after}"
    )
    assert key not in web._analyze_dedup_inflight


def test_dedup_analyze_call_stale_leader_finishing_while_replacement_still_running_does_not_overwrite(
    monkeypatch,
):
    """codex HIGH 複審#4（stale-leader 取代新 race，generation fencing）：
    比上一個測試更刁鑽的時序——A 被取代後，在新 leader B **自己都還沒跑
    完**的期間，A 才姍姍來遲完成。fencing 依賴的是「supersession 是否
    已經發生」（B 是否已經領到新的世代編號，這在 B 呼叫自己的
    `compute()` 之前、在鎖內就已經確定），不是「B 自己是否已經跑完」；
    A 這時候一樣必須是 no-op：不能誤清 B 仍在跑的 in-flight entry，也
    不能把快取寫成自己的結果冒充「目前」的結果。最後才放 B 完成，斷言
    最終被發布/服務的是 B 的結果。"""
    monkeypatch.setattr(web, "_ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS", 0.2)
    key = "dedup-stale-race-during-replacement-test-key"

    a_may_finish = threading.Event()
    b_may_finish = threading.Event()
    a_result_holder: dict[str, object] = {}
    b_result_holder: dict[str, object] = {}

    def _compute_a():
        a_may_finish.wait(timeout=5)
        return "result-A"

    def _compute_b():
        b_may_finish.wait(timeout=5)
        return "result-B"

    def _worker(compute, holder):
        try:
            holder["value"] = web._dedup_analyze_call(key, compute)
        except Exception as exc:  # pragma: no cover - 只在斷言失敗時才有意義
            holder["error"] = exc

    a_thread = threading.Thread(target=_worker, args=(_compute_a, a_result_holder))
    a_thread.start()
    for _ in range(100):
        if key in web._analyze_dedup_inflight:
            break
        time.sleep(0.02)
    assert key in web._analyze_dedup_inflight
    initial_generation = web._analyze_dedup_inflight[key][2]

    time.sleep(0.3)  # 讓 A 超過逾時門檻，變成 stale

    b_thread = threading.Thread(target=_worker, args=(_compute_b, b_result_holder))
    b_thread.start()
    # 等 B 真的取代成為新 leader——世代編號跟 A 剛開始那個不同（供
    # `_dedup_analyze_call` 內部 fencing 比對用，不是靠 B 自己跑完）。
    for _ in range(100):
        inflight = web._analyze_dedup_inflight.get(key)
        if inflight is not None and inflight[2] != initial_generation:
            break
        time.sleep(0.02)
    else:
        pytest.fail("B 應該已經取代成為新世代的 leader")

    # 這時候 B 還沒完成（b_may_finish 還沒 set），先放 A 完成。
    a_may_finish.set()
    a_thread.join(timeout=5)
    assert a_result_holder.get("value") == "result-A"

    # A 完成、嘗試發布時，B 早已取代（generation 不同）——A 的發布必須是
    # no-op：不該把 in-flight 清掉（那是 B 的 entry，B 還在跑），也不該
    # 把快取寫成 A 的結果。
    assert key in web._analyze_dedup_inflight, "A 不該誤清掉 B 仍在跑的 in-flight entry"
    cached_while_b_running = web._analyze_dedup_cache.get(key)
    assert cached_while_b_running is None or cached_while_b_running[1] != (True, "result-A"), (
        f"A 被取代後晚到完成，不該把快取寫成自己的結果，實際 {cached_while_b_running}"
    )

    # 現在才放 B 完成——B 才是應該真正發布/被服務的結果。
    b_may_finish.set()
    b_thread.join(timeout=5)
    assert b_result_holder.get("value") == "result-B"

    cached_final = web._analyze_dedup_cache.get(key)
    assert cached_final is not None and cached_final[1] == (True, "result-B"), (
        f"最終應該是 replacement（B）的結果被發布/服務，實際 {cached_final}"
    )
    assert key not in web._analyze_dedup_inflight


def test_api_analyze_dedup_cross_ip_exhausted_ip_blocked_unrelated_ip_unaffected(monkeypatch):
    """codex 複審第二輪 HIGH（dedup×限流交互，方向 2：429-poisoning）：
    IP_A 的 real 限流 bucket 早已用滿，牠自己送 `/api/analyze` 一定要被
    自己的限流擋下（429）；緊接著 IP_B（沒被限流）送出完全相同的請求，
    不該被 IP_A 的 429 影響——因為限流檢查現在搬到 dedup 查找**之前**、
    對每個 caller 各自的 IP 執行（`_analyze_enforce_caller_rate_limit`），
    IP_A 的 429 從頭到尾不會碰到共用的 dedup cache/lock，不可能
    poisoning 到 IP_B。"""
    counter = _CallCounter()
    _wrap_counting_run(monkeypatch, counter)

    ip_a = "10.1.5.1"
    ip_b = "10.1.5.2"
    web._real_rate_buckets[ip_a] = [time.time()] * web._REAL_RATE_MAX
    try:
        qs = {"coin": ["BTC"], "type": ["multi_source"], "q": ["dedup-cross-ip-poisoning-test"]}

        code_a, body_a = web._handle_api_analyze(qs, client_ip=ip_a)
        assert code_a == 429, f"IP_A 自己的限流早已用滿，應該被擋下，實際 {code_a} {body_a}"
        assert _envelope(body_a)["error"]["code"] == "rate_limited"

        code_b, body_b = web._handle_api_analyze(qs, client_ip=ip_b)
        assert code_b == 200, (
            f"IP_B 沒被限流，不該被 IP_A 的 429 poisoning，實際 {code_b} {body_b}"
        )
        assert counter.n == 1, f"只有 IP_B 真的觸發分析，應恰好 1 次，實際 {counter.n} 次"
    finally:
        web._real_rate_buckets.pop(ip_a, None)


def test_api_analyze_dedup_cross_ip_follower_still_enforces_own_rate_limit(monkeypatch):
    """codex 複審第二輪 HIGH（dedup×限流交互，方向 1：繞過限流）：
    leader_ip（沒被限流）先送出請求、進入 compute() 慢慢跑；
    follower_ip（real 限流 bucket 早已用滿）在 leader 還在進行中時送出
    完全相同的請求——即使 follower_ip 跟 leader 同一把 dedup key，也不該
    因為「共用 leader 結果」而繞過自己的限流，應該被自己的限流擋下
    （429），而不是拿到 leader 算出來的 200。

    用 `leader_started` event 確保 leader_ip 先真的進入 compute()（
    leadership 已經確定），才送出 follower_ip 的請求，避免兩邊搶
    leadership 的競速結果影響測試判斷。"""
    counter = _CallCounter()
    leader_started = threading.Event()
    real_run = pipeline_module.run

    def _counting_run(*args, **kwargs):
        counter.hit()
        leader_started.set()
        time.sleep(0.3)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(web, "run", _counting_run)

    leader_ip = "10.1.5.3"
    follower_ip = "10.1.5.4"
    web._real_rate_buckets[follower_ip] = [time.time()] * web._REAL_RATE_MAX
    try:
        qs = {"coin": ["BTC"], "type": ["multi_source"], "q": ["dedup-cross-ip-bypass-test"]}
        results: dict[str, tuple[int, str]] = {}
        results_lock = threading.Lock()

        def _worker(ip):
            code, body = web._handle_api_analyze(qs, client_ip=ip)
            with results_lock:
                results[ip] = (code, body)

        leader_thread = threading.Thread(target=_worker, args=(leader_ip,))
        leader_thread.start()
        assert leader_started.wait(timeout=2), "leader（leader_ip）應該很快就進入 compute()"

        follower_thread = threading.Thread(target=_worker, args=(follower_ip,))
        follower_thread.start()

        leader_thread.join(timeout=10)
        follower_thread.join(timeout=10)

        assert set(results) == {leader_ip, follower_ip}
        assert results[leader_ip][0] == 200
        assert results[follower_ip][0] == 429, (
            f"follower_ip 自己的限流早已用滿，不該靠共用 leader 結果繞過，"
            f"實際 {results[follower_ip]}"
        )
        assert _envelope(results[follower_ip][1])["error"]["code"] == "rate_limited"
        assert counter.n == 1, (
            f"follower_ip 被自己的限流擋下，不該碰 compute()，應恰好 1 次（leader），"
            f"實際 {counter.n} 次"
        )
    finally:
        web._real_rate_buckets.pop(follower_ip, None)


def test_dedup_analyze_call_leader_too_many_requests_not_cached_or_replayed(monkeypatch):
    """codex 複審第二輪 HIGH（dedup×限流交互）：defense-in-depth——就算
    某個 leader 的 `compute()` 過程中真的還是拋出 caller-specific 的
    `TooManyRequests`（在目前架構下理論上不該發生，因為限流已經搬到
    `_handle_api_analyze` 呼叫 `_dedup_analyze_call` 之前的
    `_analyze_enforce_caller_rate_limit()`，且傳給 `compute()` 的
    `_do_analyze`/`_do_comparison` 用 `enforce_rate_limit=False`），
    `_dedup_analyze_call` 本身也不該把這個 429 存進共用快取／replay
    給其他呼叫端——那是 caller-specific 的失敗，不是分析本身的結果。

    直接單元測試 `_dedup_analyze_call`（不透過完整 HTTP handler），
    模擬一個會拋 `TooManyRequests` 的 leader，斷言：(a) leader 自己確實
    收到這個例外；(b) 緊接著同一把 key 換一個「另一個呼叫端」的
    `compute`，不會拿到快取的 429 replay，而是真的被呼叫到一次。"""
    key = "dedup-toomanyrequests-not-cached-test-key"

    def _boom():
        raise web.TooManyRequests("模擬 caller 自己的限流（不該被共用快取）")

    with pytest.raises(web.TooManyRequests):
        web._dedup_analyze_call(key, _boom)

    counter = _CallCounter()

    def _succeed():
        counter.hit()
        return "real-result-for-a-different-caller"

    result = web._dedup_analyze_call(key, _succeed)
    assert result == "real-result-for-a-different-caller", (
        "429 不該被快取／replay，同一把 key 的下一個呼叫端應該真的拿到自己的結果"
    )
    assert counter.n == 1, f"應該真的呼叫到 1 次，而不是複用剛剛那個 429，實際 {counter.n} 次"


def test_api_analyze_dedup_key_distinguishes_token_whitespace_suffix(monkeypatch):
    """codex 複審（token 正規化）：dedup key 對 `token` 必須逐位元組比對
    （不能 strip）——`_is_live_request()` 用 `hmac.compare_digest` 對
    **原始、未 strip** 的 token 做逐位元組比對：`token=<TOKEN>` 合法通過
    （live=True），`token=<TOKEN> `（尾端多一個空白）hmac 比對失敗、回退
    成 real 檔位（live=False）。這兩個請求**實際生效的檔位不同**，若
    key 對 token 做 strip 就會誤判成同一把，讓授權失敗的請求白吃已通過
    驗證的真 Bedrock 結果，或反過來讓合法 token 的請求被腰斬成免費檔位
    的結果——不能共用同一份快取。

    不需要真的觸發真 Bedrock（`web.run` 換成安全的離線 stub，強制走
    樣本資料，$0）；只驗證 dedup key 不同、且兩種請求順序
    （valid-first / whitespace-first）都各自真的呼叫到 `pipeline.run`，
    不是命中彼此的快取。"""
    monkeypatch.setattr(web, "HAS_BEDROCK", True)
    monkeypatch.setattr(web, "LIVE_TOKEN", "secret-token-abc")

    real_run = pipeline_module.run
    qs_valid = {
        "coin": ["BTC"],
        "type": ["multi_source"],
        "q": ["dedup-token-ws-test"],
        "live": ["1"],
        "token": ["secret-token-abc"],
    }
    qs_ws = {
        "coin": ["BTC"],
        "type": ["multi_source"],
        "q": ["dedup-token-ws-test"],
        "live": ["1"],
        "token": ["secret-token-abc "],  # 尾端多一個空白
    }

    key_valid = web._analyze_dedup_key(
        qtype=web.QuestionType("multi_source"), coin_key="BTC", query="dedup-token-ws-test", qs=qs_valid
    )
    key_ws = web._analyze_dedup_key(
        qtype=web.QuestionType("multi_source"), coin_key="BTC", query="dedup-token-ws-test", qs=qs_ws
    )
    assert key_valid != key_ws, "token 差一個尾端空白，實際授權結果不同，dedup key 不該相同"

    # 佐證兩者實際授權結果的確不同：valid token 判 live=True，空白後綴的
    # token hmac 比對失敗、回退成非 live。
    assert web._is_live_request(qs_valid) is True
    assert web._is_live_request(qs_ws) is False

    # 順序 1：valid 先、whitespace 後——各自都該真的呼叫 1 次 pipeline.run。
    counter1 = _CallCounter()

    def _stub_run_1(*args, **kwargs):
        counter1.hit()
        coin, query, qtype = args[0], args[1], args[2]
        return real_run(coin, query, qtype, offline=True)

    monkeypatch.setattr(web, "run", _stub_run_1)
    code1, _ = web._handle_api_analyze(qs_valid, client_ip="10.1.6.1")
    code2, _ = web._handle_api_analyze(qs_ws, client_ip="10.1.6.1")
    assert code1 == 200 and code2 == 200
    assert counter1.n == 2, f"valid/whitespace-suffixed token 應各自呼叫 1 次，實際共 {counter1.n} 次"

    web._analyze_dedup_cache.clear()
    web._analyze_dedup_inflight.clear()

    # 順序 2：whitespace 先、valid 後——順序不影響結論。
    counter2 = _CallCounter()

    def _stub_run_2(*args, **kwargs):
        counter2.hit()
        coin, query, qtype = args[0], args[1], args[2]
        return real_run(coin, query, qtype, offline=True)

    monkeypatch.setattr(web, "run", _stub_run_2)
    code3, _ = web._handle_api_analyze(qs_ws, client_ip="10.1.6.2")
    code4, _ = web._handle_api_analyze(qs_valid, client_ip="10.1.6.2")
    assert code3 == 200 and code4 == 200
    assert counter2.n == 2, f"順序反過來一樣該各自呼叫 1 次，實際共 {counter2.n} 次"


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
