"""前後端分離 Phase 1（task #28，docs/architecture/PLAN-frontend-backend-split.md）：純新增
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

import dataclasses
import json
import re
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
    cache_get,
    cache_key,
    cache_set_if_newer,
    trust_snapshot_history_key,
)
from trustforge import ledger as ledger_module
from trustforge.ledger import JsonlLedger
from trustforge.schema import COIN_POOL, Evidence, QuestionType


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
    """#51 `/api/analyze` server-side idempotency：`_analyze_dedup_inflight`
    是唯一的 module-level 狀態（Round 14 codex HIGH 複審之後：single-flight
    `_AnalyzeFlight` 物件本身由呼叫端的區域變數參照決定存活，不再有任何
    以世代編號為鍵的暫存字典/計數器需要清），本檔許多測試共用相同的
    (coin, query, type) 組合（如 `coin=BTC, q="test"`）——不清乾淨會讓
    後面的測試誤命中前一個測試留下的殘留 in-flight entry，而不是真的呼叫
    到當次測試 monkeypatch 的 `web.run`/`web.run_comparison`。"""
    web._analyze_dedup_inflight.clear()
    yield
    web._analyze_dedup_inflight.clear()


@pytest.fixture(autouse=True)
def _isolate_analyze_dedup_cache_backend(request, tmp_path, monkeypatch):
    """test isolation bug 修復（#51/#87 PR1 CEO 追加要求）：`test_api_analyze_
    dedup_*` 系列測試呼叫 `_handle_api_analyze`/`_do_analyze`/`_do_comparison`
    時，多數 qs 都沒帶 `live=1`/`sample=1`，因此落在「真資料·$0」預設檔位
    （`_is_real_request` 判定的 `real=True`），`_do_analyze`/`_do_comparison`
    這條路徑會呼叫 `run(..., data_mode="live", llm_mode="off", ...)`——
    `data_mode="live"` 代表 pipeline 真的透過 `CachedSource` 讀連接器快取，
    預設 backend 是 `get_cache_backend()` 的預設值 `DynamoDBCache`，會打真
    AWS（見上方模組頂部說明；已實測會出現 `ResourceNotFoundException`／
    憑證錯誤，證實這幾個測試在沒有真實 AWS 存取權限/資源時無法穩定通過）。

    這批測試驗證的是 dedup 協調層本身（single-flight、per-key lock、
    follower bounded wait、stale-leader recovery、per-IP 限流交互），跟
    連接器快取實際打哪個 backend 完全無關，不該隱性依賴本機是否有效的
    AWS 憑證/資源才能通過——尤其部分測試（如 stalled leader／stale leader
    recovery）本身就有精細的牆鐘時間預算（`_ANALYZE_DEDUP_LEADER_TIMEOUT_
    SECONDS` 調小成 0.3 秒），真連接器的網路延遲/重試會直接弄亂這些時間
    假設，導致測試結果不只是「跑不過」而是「量測到錯誤的行為」。

    修法：跟既有 `json_cache_backend` fixture／`conftest.py::
    _isolate_connector_cache` 做的事情一致——只對名稱同時含
    `"analyze"`／`"dedup"` 的測試（涵蓋 `test_api_analyze_dedup_*`、
    `test_dedup_analyze_call_*`，以及 #51+#87 PR1 新增、驗證 SSR
    `/analyze`／`/analyze.json` 與跨路由 dedup 的
    `test_ssr_analyze_dedup_*`／`test_analyze_ssr_*` 系列），強制
    `CACHE_BACKEND=json`（本地 JSON 檔案 backend，路徑隔離到
    `tmp_path`，不打任何真雲端服務）。刻意用 `request.node.name` 篩選、
    只作用在這個子集——不改變 `get_cache_backend()` 本身的生產預設值
    （仍是 `dynamodb`），也不影響本檔其餘刻意要測試真 `DynamoDBCache`
    降級行為的測試（如 `_real_broken_backend_with_dead_fallback` 系列，
    那些測試直接建構 `DynamoDBCache()` 實例並自行 monkeypatch，不經過
    這個 env 開關）。
    """
    name = request.node.name
    if "analyze" in name and "dedup" in name:
        monkeypatch.setenv("CACHE_BACKEND", "json")
        monkeypatch.setenv("TRUSTFORGE_CACHE_DIR", str(tmp_path))
    yield


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


# ---------------------------------------------------------------------------
# issue #85：鎖定既有 `trust_radar`/`trust_radar_a`/`trust_radar_b`
# （PR #60、Phase1 JSON API 早已實作、非本 PR 新增）的 SSR↔JSON 同源
# invariant + 操縱旗標覆蓋回歸測試。
# ---------------------------------------------------------------------------

def test_api_analyze_trust_radar_field_equals_aggregate_trust_by_kind_single_and_comparison():
    """`trust_radar`（單幣）／`trust_radar_a`、`trust_radar_b`（比較）都必須是
    `aggregate_trust_by_kind()` 對同一份 `evidence` 算出的結果——彼此逐字
    相等，不會有第二條計算路徑（防未來改動誤植走岔）。"""
    from trustforge.agent.orchestrator import aggregate_trust_by_kind

    code, body = web._handle_api_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["radar test"]},
        client_ip="10.1.1.1",
    )
    assert code == 200
    data = _envelope(body)["data"]
    assert "trust_radar" in data and isinstance(data["trust_radar"], dict)

    evidence = web._do_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["radar test"]}
    )[1]
    assert data["trust_radar"] == aggregate_trust_by_kind(evidence)

    code2, body2 = web._handle_api_analyze(
        {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["radar cmp"]},
        client_ip="10.1.1.2",
    )
    assert code2 == 200
    data2 = _envelope(body2)["data"]
    evidence_a = web._do_comparison(
        {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["radar cmp"]}
    )[1]
    evidence_b = web._do_comparison(
        {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["radar cmp"]}
    )[3]
    assert data2["trust_radar_a"] == aggregate_trust_by_kind(evidence_a)
    assert data2["trust_radar_b"] == aggregate_trust_by_kind(evidence_b)


def test_api_analyze_trust_radar_reflects_manipulation_flag_and_matches_ssr_render(monkeypatch):
    """驗收標準：(1) 同一組輸入下，SSR `/analyze` HTML 內既有的各維度信任
    數值與 `/api/analyze` JSON `trust_radar` 欄位數字逐一相等（同函式同
    `evidence`，防未來走岔計算路徑）；(2) 含操縱旗標（manipulation flag）
    案例，確認 `trust_radar` 正確反映該維度信任值被扣分拉低。

    codex gate 複審（PR #95，測試強度修正）：
    - 操縱旗標的證據不再手塞 `trust`/`flags`，改讓一組「除了操縱關鍵詞外
      其餘（來源／kind／時間戳）完全相同」的 flagged／control 文字各自走
      真正的 `trust.scoring.score()`（經 `agent.orchestrator.
      _scored_to_evidence` 轉成 `Evidence`，跟生產 pipeline 逐字同一條
      路徑）——如果生產 `_manipulation_penalty` 未來被誤刪/弱化，flagged
      分數就不會低於 control，這裡才會真的紅（先前版本手塞 `trust=0.05`
      是套套邏輯，跟 `_manipulation_penalty` 本身正不正確無關）。
    - SSR HTML 的比對改用 `_render_trust_radar()` 新增的
      `data-kind="{kind}"`/`data-trust="{trust:.2f}"` 錨點按 kind 逐列
      精準解析（純測試錨點，不影響顯示樣式/CSP），不再用「數字在整份
      HTML 任何地方出現」的裸 substring 判斷（可能撞到別列/證據明細/
      CSS 而漏掉維度缺失或錯值）。

    monkeypatch `web.run`（`_do_analyze`/SSR `/analyze` 內部共用的同一個
    pipeline 入口），讓兩條各自獨立發出的請求拿到逐字相同的 `evidence`，
    才能在同一次測試裡嚴格比對「同一組輸入」下兩條路由的數字，不受
    pipeline 本身非我們要驗證的變異來源干擾。
    """
    from trustforge.agent.orchestrator import _scored_to_evidence, aggregate_trust_by_kind
    from trustforge.ingestion.base import Document
    from trustforge.trust.scoring import Claim, score as scoring_score

    now = time.time()

    # flagged／control 兩個 claim 除了操縱關鍵詞（`_MANIP_PATTERNS`：暴漲／
    # 百倍／快上車／穩賺）外，來源/kind/時間戳完全相同——分數差異的唯一
    # 自變量是文字內容，不是手動設定的數字。
    flagged_doc = Document(
        id="d-manip", kind="news", source="suspicious-blog",
        text="XX幣即將暴漲百倍，快上車穩賺不賠", url="", ts=now,
    )
    control_doc = Document(
        id="d-control", kind="news", source="suspicious-blog",
        text="XX幣近期價格出現一定幅度變化，成交量略增，走勢待觀察", url="", ts=now,
    )
    flagged_claim = Claim(id="c-manip", text=flagged_doc.text, doc=flagged_doc)
    control_claim = Claim(id="c-control", text=control_doc.text, doc=control_doc)

    [flagged_scored] = scoring_score([flagged_claim], now)
    [control_scored] = scoring_score([control_claim], now)
    flagged_ev = _scored_to_evidence(flagged_scored, related="radar-manip-test")
    control_ev = _scored_to_evidence(control_scored, related="radar-manip-test")

    # 真 `trust.scoring.score()` 算出來的：flagged 確實命中操縱關鍵詞，且
    # 分數確實比除旗標外完全相同的 control 低。
    assert flagged_ev.flags
    assert not control_ev.flags
    assert flagged_ev.trust < control_ev.trust

    real_report, real_evidence, real_log = web.run(
        "BTC", "radar manip test", QuestionType.MULTI_SOURCE, offline=True
    )
    evidence_with_manip = list(real_evidence) + [flagged_ev]
    evidence_without_manip = list(real_evidence) + [control_ev]

    # 除了那一筆證據是否操縱之外，其餘輸入逐字相同 → `aggregate_trust_by_
    # kind` 聚合出的 news 維度信任值也必須是 flagged 版本更低（同一個真
    # 聚合函式算出來的差異，不是斷言手塞的絕對值）。
    dims_with_manip = aggregate_trust_by_kind(evidence_with_manip)
    dims_without_manip = aggregate_trust_by_kind(evidence_without_manip)
    assert dims_with_manip["news"]["has_data"] is True
    assert dims_with_manip["news"]["trust"] < dims_without_manip["news"]["trust"]

    def fake_run(coin, query, qtype, **kwargs):
        return real_report, evidence_with_manip, real_log

    monkeypatch.setattr(web, "run", fake_run)

    ssr_code, ssr_body = _do_get(
        "/analyze?coin=BTC&type=multi_source&q=radar+manip+test"
    )
    assert ssr_code == 200

    api_code, api_body = web._handle_api_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["radar manip test"]},
        client_ip="10.1.1.3",
    )
    assert api_code == 200
    data = _envelope(api_body)["data"]

    expected_dims = dims_with_manip
    assert data["trust_radar"] == expected_dims

    # SSR HTML 按 `data-kind`/`data-trust` 錨點逐列解析，跟 JSON
    # `trust_radar` 的數字逐一相等——不允許任何一邊二次計算產生漂移，
    # 也不會被別列/證據明細/CSS 裡剛好撞同數字誤判過關。
    for kind, dim in expected_dims.items():
        if dim["has_data"]:
            match = re.search(
                rf'data-kind="{re.escape(kind)}"[^>]*data-trust="([\d.]+)"',
                ssr_body,
            )
            assert match, f"SSR /analyze 找不到 {kind} 維度的 data-kind/data-trust 錨點"
            assert match.group(1) == f'{dim["trust"]:.2f}', (
                f"SSR /analyze {kind} 維度數值 {match.group(1)} 與 "
                f"/api/analyze trust_radar 欄位 {dim['trust']:.2f} 不一致"
            )
        else:
            match = re.search(
                rf'data-kind="{re.escape(kind)}"[^>]*data-has-data="false"',
                ssr_body,
            )
            assert match, f"SSR /analyze 找不到 {kind} 維度的無資料錨點"


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


def test_api_analyze_dedup_sequential_identical_requests_each_fresh(monkeypatch):
    """codex HIGH 複審 Round 12（結果 staleness，#51 最終收斂：移除共用的
    60 秒 TTL 結果快取，只留 in-flight coalescing）：非並行、緊接著送出
    的兩個完全相同（coin/query/type/mode 都一樣）的請求，第二個**不該**
    吃到第一個的快取結果——加密市場資料時效敏感，使用者刻意重送相同
    query 通常是要最新資料，"request 內容相等" 不等於 "同一個邏輯操作"。

    這裡用一個會依呼叫次數回傳**不同** `market_judgment` 的 stub 取代
    `web.run`（模擬市場資料在兩次請求之間變動），斷言：(1) `pipeline.run`
    真的被呼叫 2 次（而不是第二次直接複用快取）；(2) 第二個回應的
    `market_judgment` 反映的是第二次呼叫**更新後**的資料，跟第一次不同
    ——不是沿用第一次的舊報告。"""
    counter = _CallCounter()
    real_run = pipeline_module.run

    def _varying_run(*args, **kwargs):
        counter.hit()
        report, evidence, log = real_run(*args, **kwargs)
        report.market_judgment = f"市場判斷版本-{counter.n}"
        return report, evidence, log

    monkeypatch.setattr(web, "run", _varying_run)

    qs = {"coin": ["ETH"], "type": ["multi_source"], "q": ["dedup-sequential-test"]}
    code1, body1 = web._handle_api_analyze(qs, client_ip="10.1.1.2")
    code2, body2 = web._handle_api_analyze(qs, client_ip="10.1.1.3")

    assert code1 == 200 and code2 == 200
    assert counter.n == 2, (
        f"循序（非並行）的重複請求應各自 fresh 觸發 pipeline.run，"
        f"實際只呼叫 {counter.n} 次"
    )
    judgment1 = _envelope(body1)["data"]["report"]["market_judgment"]
    judgment2 = _envelope(body2)["data"]["report"]["market_judgment"]
    assert judgment1 == "市場判斷版本-1"
    assert judgment2 == "市場判斷版本-2", (
        "第二個循序請求應該拿到第二次呼叫更新後的資料，而不是第一次的舊報告"
    )
    assert body1 != body2, "兩次循序請求的回應內容不該逐字相同（各自反映當下最新資料）"


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
    """同順序（同一個 coin_a,coin_b 序列）的**並行**重複 comparison 請求
    仍應正常 in-flight dedup（只跑 1 次）——上一條測試確認的是「順序不同
    不誤 dedup」，這條反向確認「順序相同、同時送出的請求該 dedup 的還是
    有 dedup 到」，避免修正 codex HIGH 時矯枉過正變成完全不 dedup
    comparison。（Round 12 之後：dedup 只發生在 in-flight 期間，這裡改用
    並行送出而不是循序送出兩次——循序送出在 Round 12 架構下本來就該各自
    fresh，不再是這條測試要驗證的行為，見
    `test_api_analyze_dedup_sequential_identical_requests_each_fresh`。）
    """
    counter = _CallCounter()
    _wrap_counting_run_comparison(monkeypatch, counter, delay=0.2)

    qs = {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["cmp-same-order-test"]}
    n_workers = 5
    barrier = threading.Barrier(n_workers)
    results: list[tuple[int, str]] = []
    results_lock = threading.Lock()

    def _worker():
        barrier.wait()
        code, body = web._handle_api_analyze(qs, client_ip="10.1.1.11")
        with results_lock:
            results.append((code, body))

    threads = [threading.Thread(target=_worker) for _ in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(results) == n_workers
    assert all(code == 200 for code, _ in results), results
    assert counter.n == 1, f"同順序、並行送出的重複請求應共用同一把 key，實際呼叫 {counter.n} 次"
    bodies = {body for _, body in results}
    assert len(bodies) == 1, "並行重複請求應共用同一份真實結果"


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


def test_dedup_analyze_call_leader_failure_not_written_to_ttl_cache():
    """codex HIGH 複審#5（快取暫時性失敗把短暫故障變 60 秒故障，原始
    版本）／Round 12（#51 最終收斂，移除共用的 60 秒 TTL 結果快取，只留
    in-flight coalescing）：直接單元測試 `_dedup_analyze_call`——leader
    失敗後，in-flight entry 應該被清乾淨，而不是卡在一個「已經失敗」卻
    還留著的舊 leader entry 上，讓下一個請求可以真的重新判斷、重新
    嘗試（此時已經沒有任何供全新請求複用的共用快取可查，`key` 本身
    也不再是任何結果暫存區的鍵）。"""
    key = "dedup-failure-not-in-ttl-cache-test-key"

    def _boom():
        raise ValueError("模擬依賴暫時性失敗（連接器 timeout）")

    with pytest.raises(ValueError):
        web._dedup_analyze_call(key, _boom)

    assert key not in web._analyze_dedup_inflight, (
        "leader 失敗後應該清掉 in-flight entry，讓下一個請求可以 fresh retry"
    )


def test_api_analyze_dedup_leader_failure_not_cached_fresh_retry_after_recovery(monkeypatch):
    """codex HIGH 複審#5（快取暫時性失敗把短暫故障變 60 秒故障，原始
    版本）／Round 12（#51 最終收斂，移除共用的 60 秒 TTL 結果快取，只留
    in-flight coalescing）：leader 失敗時，例外只共用給當下這批
    in-flight follower，絕不留存給任何之後才進來的全新請求——後面循序
    送出的下一個請求，不管依賴是否早就恢復，都會 fresh 重新真的呼叫一次
    （這是 Round 12 之後所有循序請求的預設行為，不只是「失敗恢復」這個
    特例）。

    這裡驗證兩件事（銜接上一個測試 `..._leader_failure_shared_with_
    followers` 的斷言，這裡額外加上「恢復後立即重試」）：
    1. leader 第一次失敗（模擬依賴**當下仍然故障**）時，並行等待中的
       follower 共用同一次失敗（都拿到 502，依賴只被真的呼叫 1 次，不是
       依序各自輪流重試）。
    2. 緊接著（同一個 process 內，不 sleep、不手動竄改任何內部狀態）送出
       的下一個請求——模擬依賴已經恢復——立刻真的重試並成功，不被剛才那
       次失敗卡住。"""
    counter = _CallCounter()
    dependency_recovered = threading.Event()
    real_run = pipeline_module.run

    def _flaky(*args, **kwargs):
        counter.hit()
        if not dependency_recovered.is_set():
            time.sleep(0.2)
            raise ValueError("依賴暫時性失敗（模擬連接器 timeout）")
        return real_run(*args, **kwargs)

    monkeypatch.setattr(web, "run", _flaky)

    qs = {
        "coin": ["ETH"],
        "type": ["multi_source"],
        "q": ["dedup-transient-failure-recovery-test"],
    }
    n_workers = 4
    barrier = threading.Barrier(n_workers)
    results: list[tuple[int, str]] = []
    results_lock = threading.Lock()

    def _worker():
        barrier.wait()
        code, body = web._handle_api_analyze(qs, client_ip="10.1.1.20")
        with results_lock:
            results.append((code, body))

    threads = [threading.Thread(target=_worker) for _ in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(results) == n_workers
    assert all(code == 502 for code, _ in results), results
    assert counter.n == 1, (
        f"依賴仍故障期間，當下 {n_workers} 個並行 follower 應該共用同一次失敗，"
        f"依賴只該真的被打 1 次（不該依序各自輪流重試），實際 {counter.n} 次"
    )

    # 模擬依賴恢復，緊接著（不 sleep、不動任何快取內部狀態）送出下一個
    # 請求——必須立刻真的重試並成功，而不是被剛才那次失敗的結果卡住。
    dependency_recovered.set()
    code, body = web._handle_api_analyze(qs, client_ip="10.1.1.21")
    assert code == 200, (
        f"依賴恢復後，下一個請求應該立刻真的重試並成功，不該被舊的失敗結果卡住；"
        f"實際 code={code}, body={body!r}"
    )
    assert counter.n == 2, (
        f"依賴恢復後的下一個請求應該真的觸發依賴呼叫（fresh retry），"
        f"不是命中被快取的舊失敗，實際依賴總共被呼叫 {counter.n} 次"
    )


# codex HIGH 複審 Round 12（#51 最終收斂）：原本這裡有一個
# `test_api_analyze_dedup_ttl_expiry_allows_refetch`，測「TTL 過期後
# 允許重新 fetch」——該測試的前提（存在一個 60 秒 TTL 結果快取）已經
# 隨著本輪移除 TTL 快取而不再成立，且它最終要驗證的行為（過期/後續請求
# 會 fresh 重新呼叫 pipeline.run）現在是
# `test_api_analyze_dedup_sequential_identical_requests_each_fresh`
# 保證的**預設**行為，不需要再手動竄改任何內部快取狀態去模擬「過期」，
# 故整條測試移除，避免重複且對已不存在的內部結構做白盒操控。


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


def test_api_analyze_dedup_zombie_leader_entry_not_replaced_fresh_caller_just_waits_and_503s(
    monkeypatch,
):
    """codex HIGH 複審 Round 14（single-flight 物件 + 參照，#51 最終收斂，
    移除 stale-leader 取代機制）：先前版本裡，一個「已經存在超過逾時上界、
    且永遠不會被 `event.set()` 的殭屍 in-flight entry」會被下一個進來的
    請求偵測為 stale、取而代之成為新 leader，真的重新觸發一次
    `pipeline.run`。Round 14 之後**刻意移除**這個「取代」行為——leader
    （不論是真的 hang 死，還是單純很慢）不會再被任何後到的請求取代，
    所有後到的請求（不管是不是「fresh」）都只是老老實實去 join 這個
    唯一的 `_AnalyzeFlight`、bounded wait 到自己的 deadline，等不到就回
    503（見模組頂部大段說明「不需要 replace stale leader」的完整理由：
    這個簡化能成立，前提是 leader 的 `compute()` 本身已經受
    `bedrock.py` 的硬性 timeout 限制，不會真的無限期 hang）。

    這裡直接偽造一個「早就存在、且永遠不會被 set() 的殭屍 `_AnalyzeFlight`」
    ，斷言：(1) 後到的請求**不會**取代它、也**不會**真的觸發
    `pipeline.run`；(2) 後到的請求只是 join 這個殭屍 Flight，bounded wait
    到自己的（縮短過的）逾時上界後正常回 503；(3) 殭屍 entry 原封不動留著
    ——不像 Round 12～13 那樣被清掉/取代。

    codex HIGH 複審 Round 15 補充：這裡的殭屍 Flight 剛建立
    （`started_at` ≈ 現在），**還沒**超過 `_ANALYZE_DEDUP_STALE_LEADER_SECONDS`
    （90 秒）的 stale 門檻，所以不觸發本輪新增的 stale-entry recovery——
    這個測試驗證的是「年輕、只是恰好卡住的 leader 不會被輕易取代」，不是
    「entry 無論多老都永遠不會被取代」；後者已不成立，見下方
    `test_api_analyze_dedup_stale_leader_entry_recovered_by_fresh_request_after_90s`
    ——真的活超過 90 秒才會被接手。"""
    monkeypatch.setattr(web, "_ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS", 0.3)

    counter = _CallCounter()
    _wrap_counting_run(monkeypatch, counter)

    qs = {"coin": ["BTC"], "type": ["multi_source"], "q": ["dedup-zombie-leader-not-replaced-test"]}
    coin_key = "BTC"
    query = "dedup-zombie-leader-not-replaced-test"
    key = web._analyze_dedup_key(
        qtype=web.QuestionType("multi_source"), coin_key=coin_key, query=query, qs=qs
    )

    zombie_flight = web._AnalyzeFlight()  # event 永遠不會被 set() ——模擬死掉的 leader
    web._analyze_dedup_inflight[key] = zombie_flight

    t0 = time.time()
    code, body = web._handle_api_analyze(qs, client_ip="10.1.2.2")
    elapsed = time.time() - t0

    assert code == 503, (
        f"殭屍 leader 不該被取代，後到的請求應該乖乖 bounded wait 到自己的逾時上界後回 503，"
        f"實際 {code} {body}"
    )
    assert _envelope(body)["error"]["code"] == "timeout"
    assert counter.n == 0, (
        f"後到的請求不該取代殭屍 leader、不該真的觸發 pipeline.run，實際呼叫 {counter.n} 次"
    )
    assert elapsed < 3.0, f"應在 bounded wait 上界附近就返回，實際耗時 {elapsed:.3f}s"

    current = web._analyze_dedup_inflight.get(key)
    assert current is zombie_flight, (
        "殭屍 entry 應該原封不動留著，不該被後到的請求取代或清掉"
    )


def test_dedup_analyze_call_delayed_follower_reads_from_held_flight_reference_not_ttl(
    monkeypatch,
):
    """codex HIGH 複審 Round 14（single-flight 物件 + 參照，收斂 5 秒
    wall-clock TTL 讓延遲 follower miss 結果→重複 compute 這個問題）：

    Round 12～13 的設計裡，follower 醒來（`event.wait()` 返回）後，是靠
    **重新查一個以世代編號為鍵的字典**（`_analyze_dedup_follower_result`，
    有 `_ANALYZE_DEDUP_FOLLOWER_RESULT_GRACE_SECONDS=5.0` 秒的寬限期）
    才能拿到 leader 的結果——若這個 follower 的執行緒在「event 被 set()」
    到「真的執行到查字典那行程式碼」之間，被作業系統排程延遲超過 5 秒
    （GIL 競爭、系統忙碌等都可能發生，不需要惡意攻擊），字典裡的暫存
    結果已經被那個寬限期回收，這個 follower 會誤判成「沒有結果可撿」、
    落回協調 loop 頂端重新判斷——多半會誤判成全新請求、自己再真的
    `compute()` 一次，造成重複的真實依賴呼叫（重複花費）。

    Round 14 把這個「醒來後去哪裡找結果」的機制，從「查一個有 TTL 的
    字典」改成「直接讀自己一早就握在手上的 `_AnalyzeFlight` 物件參照」
    ——這個物件的存活由 Python reference counting 決定，跟牆鐘時間完全
    無關，結構上不可能再發生「TTL 到期、結果不見了」這件事。

    這裡透過**monkeypatch 這個 follower 實際 join 到的那個 Flight 物件的
    `event.wait`**，在底層真正的 `wait()` 回傳之後，人為再插入一段刻意
    設得比舊 TTL（5 秒）更長的延遲（6 秒）才讓呼叫端拿回控制權——精準
    模擬「follower 真正被喚醒（leader 已經寫好 `ok`/`payload`）到它真正
    執行後續讀取程式碼之間，被排程延遲超過舊 TTL」這個確切情境。斷言：
    即使經過這段刻意延長到超過舊 5 秒 TTL 的延遲，這個 follower 仍然拿到
    正確的結果，且全程只有 leader 那 1 次真的觸發 `compute()`——延遲
    follower 不會因此誤判成全新請求、不會自己再重複呼叫一次。"""
    key = "dedup-delayed-follower-flight-reference-test-key"
    leader_may_finish = threading.Event()
    counter = _CallCounter()

    def _compute_leader():
        counter.hit()
        leader_may_finish.wait(timeout=10)
        return "result-from-leader"

    leader_result_holder: dict[str, object] = {}

    def _worker_leader():
        leader_result_holder["value"] = web._dedup_analyze_call(key, _compute_leader)

    leader_thread = threading.Thread(target=_worker_leader)
    leader_thread.start()

    # 等 leader 真的建立好 in-flight entry（成為這把 key 唯一的 leader）。
    flight = None
    for _ in range(200):
        flight = web._analyze_dedup_inflight.get(key)
        if flight is not None:
            break
        time.sleep(0.01)
    assert flight is not None, "leader 應該已經寫入 in-flight"

    # 在這個 Flight 物件的 event 上動手腳：底層真正的 wait() 一旦真的
    # 回傳（代表 leader 已經寫好 ok/payload 並呼叫過 event.set()），刻意
    # 讓呼叫端（follower）多等 6 秒才真的拿回控制權——模擬「follower 真正
    # 被喚醒到它真的執行到下一行程式碼」之間，被作業系統排程延遲超過
    # 舊 5 秒 TTL 寬限期的情境。這段延遲刻意設在**底層 wait() 回傳之後**
    # 才發生，不佔用/展延 follower 自己的 `deadline` 預算判斷（那是在
    # `_dedup_analyze_call` 呼叫這個 wait() **之前**，用來算
    # `remaining` 參數的，不受這裡影響）。
    real_wait = flight.event.wait

    def _delayed_wait(timeout=None):
        completed = real_wait(timeout=timeout)
        if completed:
            time.sleep(6.0)  # 刻意比舊的 5 秒 TTL 寬限期更長
        return completed

    flight.event.wait = _delayed_wait

    def _compute_follower_if_mistakenly_treated_as_fresh():
        # 若這個延遲 follower 誤判成全新請求、自己落回去重新 compute()，
        # 才會呼叫到這裡——代表舊的 TTL-miss race 又發生了。
        counter.hit()
        return "should-not-be-called-delayed-follower-mistaken-for-fresh"

    # 把 leader 標記完成（在 follower 開始等待之前先讓它就緒，確保
    # follower 真的走到「join → wait → 延遲 → 讀取」這條路，而不是自己
    # 變成 leader）。follower 需要給自己夠長的 deadline，蓋過 6 秒延遲。
    monkeypatch.setattr(web, "_ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS", 20.0)

    follower_result_holder: dict[str, object] = {}

    def _worker_follower():
        follower_result_holder["value"] = web._dedup_analyze_call(
            key, _compute_follower_if_mistakenly_treated_as_fresh
        )

    follower_thread = threading.Thread(target=_worker_follower)
    follower_thread.start()
    time.sleep(0.2)  # 確保 follower 真的先走到 event.wait() 上

    leader_may_finish.set()
    leader_thread.join(timeout=10)
    follower_thread.join(timeout=15)

    assert leader_result_holder.get("value") == "result-from-leader"
    assert follower_result_holder.get("value") == "result-from-leader", (
        "延遲超過舊 TTL 的 follower，仍應從自己持有的 Flight 參照正確讀到 "
        "leader 的結果，而不是誤判成全新請求"
    )
    assert counter.n == 1, (
        f"全程只該有 leader 真的呼叫 1 次 compute()，延遲 follower 不該因為 "
        f"read-after-wake 的延遲而誤觸發第二次，實際呼叫 {counter.n} 次"
    )
    assert key not in web._analyze_dedup_inflight


def test_bedrock_runtime_client_has_hard_read_and_connect_timeout(monkeypatch):
    """#51 codex HIGH 複審 Round 14（Bedrock 主敘事 hard timeout，dedup
    「leader compute() 有牆鐘時間上界」這個簡化的正確性前提）：
    `BedrockClient._runtime()` 先前完全沒有 `Config`/timeout，boto3 預設
    等於無限期等待。這裡直接 monkeypatch `boto3.client`，斷言真正建置
    client 時傳入的 `config` 確實帶有預期的
    `read_timeout=60`/`connect_timeout=10`/`retries={"total_max_attempts":
    1}`——純本地 mock，不連真 AWS，不花任何錢。"""
    from trustforge import bedrock as bedrock_module

    captured: dict[str, object] = {}

    class _FakeBoto3Module:
        @staticmethod
        def client(service_name, region_name=None, config=None):
            captured["service_name"] = service_name
            captured["region_name"] = region_name
            captured["config"] = config
            return MagicMock()

    monkeypatch.setitem(__import__("sys").modules, "boto3", _FakeBoto3Module())

    client = bedrock_module.BedrockClient(offline=False, stance_offline=True)
    client._runtime()

    assert captured["service_name"] == "bedrock-runtime"
    cfg = captured["config"]
    assert cfg is not None, "應該傳入明確的 Config，不能沿用 boto3 預設（無限期等待）"
    assert cfg.read_timeout == bedrock_module._NARRATIVE_READ_TIMEOUT_SEC == 60
    assert cfg.connect_timeout == bedrock_module._NARRATIVE_CONNECT_TIMEOUT_SEC == 10
    assert cfg.retries == {"total_max_attempts": 1}


def test_dedup_analyze_call_leader_bounded_by_bedrock_style_timeout_follower_gets_503(
    monkeypatch,
):
    """#51 codex HIGH 複審 Round 14（leader hang（Bedrock 逾時）→503，
    dedup×`bedrock.py` timeout 整合）：模擬「leader 的 `compute()` 內部
    呼叫到一個真的有 `read_timeout` 上界的 Bedrock 連線，該連線卡住直到
    命中 timeout 才拋出 `botocore.exceptions.ReadTimeoutError`」這個情境
    ——只 mock 這個行為本身（延遲＋拋出 timeout 專用例外類別），不連真
    AWS、不花任何錢、不需要真的等到 60 秒。

    斷言兩件事：(1) follower 自己的 dedup bounded wait（縮短過的
    `_ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS`）比這個模擬的 Bedrock
    timeout 短，會在 leader 的模擬呼叫還沒真的逾時完成之前就先回
    `_AnalyzeDedupTimeout`（503）——不會被 leader 這條 hang 住的 thread
    拖著一起等；(2) leader 的模擬呼叫本身**確實有牆鐘時間上界**，最終
    會拋出 `ReadTimeoutError`（而不是真的無限期 hang 住），驗證「有硬性
    timeout 的依賴呼叫」這個前提成立——這正是 Round 14 移除 stale-leader
    取代機制時，用來替代它的正確性保證。"""
    from botocore.exceptions import ReadTimeoutError

    monkeypatch.setattr(web, "_ANALYZE_DEDUP_LEADER_TIMEOUT_SECONDS", 0.3)
    key = "dedup-leader-bedrock-style-timeout-test-key"

    simulated_bedrock_read_timeout_sec = 1.5  # 遠比 0.3 秒 follower 逾時上界長
    leader_finished = threading.Event()

    def _compute_leader_simulating_hung_bedrock_call():
        # 模擬「卡在一個有 read_timeout 上界的 Bedrock 連線」：睡到模擬的
        # timeout 值才拋出 timeout 專用例外——代表這條呼叫本身有界，不是
        # 真的無限期 hang。
        time.sleep(simulated_bedrock_read_timeout_sec)
        leader_finished.set()
        raise ReadTimeoutError(endpoint_url="https://bedrock-runtime.mock.invalid/")

    leader_exc_holder: dict[str, object] = {}

    def _worker_leader():
        try:
            web._dedup_analyze_call(key, _compute_leader_simulating_hung_bedrock_call)
        except Exception as exc:  # noqa: BLE001 -- 要能捕捉/斷言確切的例外型別
            leader_exc_holder["exc"] = exc

    t0 = time.time()
    leader_thread = threading.Thread(target=_worker_leader)
    leader_thread.start()

    # 等 leader 真的建立好 in-flight entry。
    for _ in range(200):
        if key in web._analyze_dedup_inflight:
            break
        time.sleep(0.01)
    else:
        pytest.fail("leader 應該已經寫入 in-flight")

    def _compute_follower_should_not_be_called():
        raise AssertionError("follower 不該落回自己真的呼叫 compute()")

    with pytest.raises(web._AnalyzeDedupTimeout):
        web._dedup_analyze_call(key, _compute_follower_should_not_be_called)
    follower_elapsed = time.time() - t0

    assert follower_elapsed < simulated_bedrock_read_timeout_sec, (
        f"follower 應該在 leader 模擬的 Bedrock timeout（{simulated_bedrock_read_timeout_sec}s）"
        f"真的觸發之前，就先因為自己的 dedup 逾時上界（0.3s）回 503，"
        f"實際耗時 {follower_elapsed:.3f}s"
    )
    assert not leader_finished.is_set(), (
        "follower 拿到 503 的當下，leader 模擬的 Bedrock 呼叫應該仍在進行中"
        "（還沒真的命中它自己的 timeout），佐證 follower 沒有等 leader 完成"
    )

    leader_thread.join(timeout=5)
    assert leader_finished.is_set(), "leader 的模擬 Bedrock 呼叫最終應該真的觸發（有界，不是無限期 hang）"
    assert isinstance(leader_exc_holder.get("exc"), ReadTimeoutError), (
        f"leader 應該拿到自己模擬呼叫拋出的 ReadTimeoutError，實際 {leader_exc_holder!r}"
    )
    assert key not in web._analyze_dedup_inflight




def test_dedup_analyze_call_normal_no_stale_still_single_flight(monkeypatch):
    """codex HIGH 複審#5（thundering herd）修復後的正面對照組：完全沒有
    stale leader 涉入的正常並行重複請求，仍然只觸發 1 次 compute()——
    確認協調 loop 的重寫沒有改壞既有、最基本的 single-flight 行為。"""
    key = "dedup-thundering-herd-normal-control-test-key"
    counter = _CallCounter()

    def _compute():
        counter.hit()
        time.sleep(0.2)
        return "result-normal"

    n_callers = 6
    holders: list[dict[str, object]] = [{} for _ in range(n_callers)]
    barrier = threading.Barrier(n_callers)
    threads = []

    def _worker(idx):
        barrier.wait()
        holders[idx]["value"] = web._dedup_analyze_call(key, _compute)

    for i in range(n_callers):
        t = threading.Thread(target=_worker, args=(i,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=5)

    assert counter.n == 1, f"沒有 stale leader 涉入時，仍應只觸發 1 次 compute()，實際 {counter.n} 次"
    for idx, holder in enumerate(holders):
        assert holder.get("value") == "result-normal", f"呼叫端 {idx} 應共用同一份結果，實際 {holder}"


def test_api_analyze_dedup_stale_leader_entry_recovered_by_fresh_request_after_90s(monkeypatch):
    """codex HIGH 複審 Round 15（慢 leader 無 end-to-end deadline、持 key
    整段 503-storm）：`bedrock.py` 的 `read_timeout`/`connect_timeout` 只
    bound 單次 Bedrock 呼叫，`compute()` 整體（可能循序打好幾次連接器/
    Bedrock）沒有 end-to-end 上界，慢 leader 可能把這把 key 佔住遠超過
    follower 願意等的 45 秒——這段期間所有同 key 的請求全部卡死變 503，
    直到這個慢 leader 自己結束為止。

    對比 Round 14 的 zombie-not-replaced 測試（那裡的殭屍 Flight 剛建立、
    還沒超過 90 秒門檻）：這裡的殭屍 Flight 已經活超過
    `_ANALYZE_DEDUP_STALE_LEADER_SECONDS`（90 秒），後到的 fresh 請求應該
    偵測到並「接手」成為新 leader——真的呼叫一次 `pipeline.run` 拿到
    200，而不是傻傻卡到自己的 45 秒逾時上界才 503；key 因此不會被一個慢
    leader 永久卡死。"""
    counter = _CallCounter()
    _wrap_counting_run(monkeypatch, counter)

    qs = {
        "coin": ["BTC"],
        "type": ["multi_source"],
        "q": ["dedup-stale-leader-recovered-after-90s-test"],
    }
    coin_key = "BTC"
    query = "dedup-stale-leader-recovered-after-90s-test"
    key = web._analyze_dedup_key(
        qtype=web.QuestionType("multi_source"), coin_key=coin_key, query=query, qs=qs
    )

    stale_flight = web._AnalyzeFlight()  # event 永遠不會被 set() ——模擬慢/掛掉的 leader
    stale_flight.started_at = time.time() - (web._ANALYZE_DEDUP_STALE_LEADER_SECONDS + 1.0)
    web._analyze_dedup_inflight[key] = stale_flight

    t0 = time.time()
    code, body = web._handle_api_analyze(qs, client_ip="10.1.2.4")
    elapsed = time.time() - t0

    assert code == 200, (
        f"存活超過 90 秒的 stale entry 應該被 fresh 請求接手成為新 leader，實際 {code} {body}"
    )
    assert counter.n == 1, f"接手成為新 leader 後應該真的觸發一次 pipeline.run，實際 {counter.n} 次"
    assert elapsed < 5.0, (
        f"不該卡到 follower 自己的 45 秒逾時上界才回應，應該直接接手 fresh 計算，實際耗時 {elapsed:.3f}s"
    )
    assert key not in web._analyze_dedup_inflight, "接手的新 leader 完成後應清掉自己的 in-flight entry"


def test_dedup_analyze_call_orphaned_stale_leader_finishing_after_replacement_does_not_overwrite_new_leader(
    monkeypatch,
):
    """codex HIGH 複審 Round 15（fencing 天生，不需要 generation counter）：
    一個原本的 leader（flight1）因為存活超過 90 秒被 fresh 請求取代（產生
    flight2、自己成為新 leader）之後，flight1 背後真正的 `compute()`（被
    orphan 掉，但仍在真的執行）之後完成時，只應該寫**自己手上那個
    flight1 物件**，清 in-flight 字典 entry 前必須先確認字典裡現在還是不是
    自己（identity 比對）——不能把新 leader（flight2）還在使用中的 entry
    誤刪掉。這裡讓 flight1、flight2 都刻意卡住（各自用一個 Event 控制何時
    完成），驗證兩者完成的先後順序下彼此都不會互相干擾。"""
    key = "dedup-round15-fencing-orphaned-leader-test-key"

    leader1_ready = threading.Event()
    leader1_may_finish = threading.Event()

    def leader1_compute():
        leader1_ready.set()
        assert leader1_may_finish.wait(timeout=5), "leader1 應該在測試主體釋放前保持卡住"
        return "leader1-result"

    result1_holder: dict[str, object] = {}

    def _run_leader1():
        result1_holder["value"] = web._dedup_analyze_call(key, leader1_compute)

    t1 = threading.Thread(target=_run_leader1)
    t1.start()
    assert leader1_ready.wait(timeout=5), "leader1 應該先進入 compute() 卡住"

    flight1 = web._analyze_dedup_inflight.get(key)
    assert flight1 is not None, "leader1 應該已經把自己的 Flight 裝進 in-flight 字典"
    # 人為把它「餵老」到超過 stale 門檻，不用真的等 90 秒。
    flight1.started_at = time.time() - (web._ANALYZE_DEDUP_STALE_LEADER_SECONDS + 1.0)

    leader2_ready = threading.Event()
    leader2_may_finish = threading.Event()

    def leader2_compute():
        leader2_ready.set()
        assert leader2_may_finish.wait(timeout=5), "leader2 應該在測試主體釋放前保持卡住"
        return "leader2-result"

    result2_holder: dict[str, object] = {}

    def _run_leader2():
        result2_holder["value"] = web._dedup_analyze_call(key, leader2_compute)

    t2 = threading.Thread(target=_run_leader2)
    t2.start()
    assert leader2_ready.wait(timeout=5), "leader2（接手者）應該偵測到 stale 並自己開始 compute()"

    flight2 = web._analyze_dedup_inflight.get(key)
    assert flight2 is not None
    assert flight2 is not flight1, "stale entry 應該已經被換成全新的 `_AnalyzeFlight`"

    # 讓（已被取代的）orphaned leader1 先完成——它只應該寫自己手上的
    # flight1，且清理時發現字典裡已經不是自己，不應該動到 leader2 還在
    # 使用中的 entry。
    leader1_may_finish.set()
    t1.join(timeout=5)
    assert not t1.is_alive(), "leader1 應該已經完成"
    assert result1_holder["value"] == "leader1-result"
    assert flight1.ok is True and flight1.payload == "leader1-result"
    assert web._analyze_dedup_inflight.get(key) is flight2, (
        "orphaned 舊 leader 完成清理時，不應該覆寫或誤刪新 leader 還在使用中的 in-flight entry"
    )

    # 收尾：讓新 leader（flight2）也完成，確認它自己的 entry 正常被清掉，
    # 不受 leader1 的 identity-fencing 影響。
    leader2_may_finish.set()
    t2.join(timeout=5)
    assert not t2.is_alive(), "leader2 應該已經完成"
    assert result2_holder["value"] == "leader2-result"
    assert key not in web._analyze_dedup_inflight, "新 leader 完成後應清掉自己的 in-flight entry"


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


def _call_ssr_analyze(path_and_query: str, client_ip: str) -> tuple[int, str]:
    """透過 `web.Handler.do_GET` 模擬打 SSR `/analyze`／`/analyze.json`
    路由，不開真 socket（比照 test_security.py 既有 fake handler 慣例：
    `web.Handler.__new__` + 手動接上 `send_response`/`send_header`/
    `end_headers`/`wfile`，繞過 `BaseHTTPRequestHandler.__init__` 真正的
    socket 交握）。回傳 `(status_code, body)`——`body` 是 `.json` 變體回傳
    的原始 JSON 字串，或一般 `/analyze` 回傳的 HTML（含 429 錯誤卡）。"""
    buf = BytesIO()
    h = web.Handler.__new__(web.Handler)
    h.client_address = (client_ip, 12345)
    h.path = path_and_query
    h.headers = {}
    h.wfile = buf

    captured_status: list[int] = []
    h.send_response = lambda code: captured_status.append(code)
    h.send_header = lambda name, val: None
    h.end_headers = lambda: None

    h.do_GET()

    status = captured_status[-1] if captured_status else None
    return status, buf.getvalue().decode("utf-8")


def test_ssr_analyze_dedup_cross_ip_follower_still_enforces_own_rate_limit(monkeypatch):
    """#93（harper CISO PR #92 審查附帶條件 #2）：SSR `/analyze`／
    `/analyze.json` 路由跟 `/api/analyze` 共用同一套 in-flight dedup
    （`_analyze_dedup_key`/`_dedup_analyze_call`，見 `do_GET` 內
    `_analyze_dedup_coin_key` 那段），先前只有 `/api/analyze` 有對應的
    跨 IP 限流測試（見前一個
    `test_api_analyze_dedup_cross_ip_follower_still_enforces_own_rate_limit`），
    SSR 路由缺一條端到端測試鎖死同樣的行為。

    模式跟前一個 API 版測試完全對稱：leader_ip（沒被限流）先打
    `/analyze.json`、進入 compute() 慢慢跑；follower_ip（real 限流
    bucket 早已用滿）在 leader 還在進行中時打完全相同的 SSR 請求——
    即使 follower_ip 跟 leader 同一把 dedup key，也不該因為「共用
    leader 結果」繞過自己的限流，應該被自己的限流擋下（429），而不是
    拿到 leader 算出來的 200。"""
    counter = _CallCounter()
    leader_started = threading.Event()
    real_run = pipeline_module.run

    def _counting_run(*args, **kwargs):
        counter.hit()
        leader_started.set()
        time.sleep(0.3)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(web, "run", _counting_run)

    leader_ip = "10.1.5.7"
    follower_ip = "10.1.5.8"
    web._real_rate_buckets[follower_ip] = [time.time()] * web._REAL_RATE_MAX
    try:
        path = "/analyze.json?coin=BTC&type=multi_source&q=ssr-dedup-cross-ip-bypass-test"
        results: dict[str, tuple[int, str]] = {}
        results_lock = threading.Lock()

        def _worker(ip):
            code, body = _call_ssr_analyze(path, ip)
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
        assert results[leader_ip][0] == 200, (
            f"leader_ip 沒被限流，應該正常拿到 200，實際 {results[leader_ip]}"
        )
        assert results[follower_ip][0] == 429, (
            f"follower_ip 自己的限流早已用滿，不該靠共用 leader 結果（同一條 "
            f"SSR dedup key）繞過，實際 {results[follower_ip]}"
        )
        assert counter.n == 1, (
            f"follower_ip 被自己的限流擋下，不該碰 compute()，應恰好 1 次"
            f"（leader），實際 {counter.n} 次"
        )
    finally:
        web._real_rate_buckets.pop(follower_ip, None)


def test_api_analyze_dedup_key_captures_online_stance_force_offline_per_caller(monkeypatch):
    """codex HIGH 複審 Round 11（key 漏 caller-specific online-stance
    降級）：real-mode 執行還依賴 `_online_stance_force_offline(client_ip)`
    ——per-IP 的 online-stance 專用限流耗盡與否，決定這次請求會不會被
    degrade（`force_stance_offline=True`）。dedup key 若沒捕捉這個變數，
    會讓配額狀態不同的兩個 IP 命中同一把 key、彼此污染（甚至讓耗盡的
    IP 白拿一份繞過自己配額限制的正常結果），而且哪個先到決定另一方
    拿到什麼結果。

    這裡驗證兩件事：
    1. IP_A（online-stance 配額早已耗盡）跟 IP_B（配額充裕）送出完全
       相同的 real-mode 請求，各自拿到跟自己狀態相符的結果——A 的
       `run()` 呼叫帶 `force_stance_offline=True`、B 的不帶（`False`）
       ——依賴各被真的呼叫 1 次，不共用同一把 key、不互相污染、不依
       送出順序而定。
    2. 同一個 IP（狀態不變）循序再送一次完全相同的請求 → Round 12 之後
       循序請求一律 fresh 重新真的呼叫 `run()`（不再命中任何共用快取），
       但因為配額狀態沒變，一樣正確算出 `force_stance_offline=True`——
       這條「per-caller 狀態決定誰被 degrade」的判斷不受 TTL 快取移除
       影響，本來就該每次都獨立、正確地反映當下狀態。
    """
    monkeypatch.setattr(web, "online_stance_requested", lambda: True)
    real_run = pipeline_module.run
    calls: list[dict] = []

    def _capturing_run(coin, query, qtype, offline=False, data_dir=None,
                        data_mode=None, llm_mode=None, **kwargs):
        calls.append({"force_stance_offline": kwargs.get("force_stance_offline", False)})
        return real_run(coin, query, qtype, offline=True)

    monkeypatch.setattr(web, "run", _capturing_run)

    ip_a = "10.1.6.1"
    ip_b = "10.1.6.2"
    web._online_stance_rate_buckets[ip_a] = [time.time()] * web._ONLINE_STANCE_RATE_MAX
    try:
        qs = {
            "coin": ["BTC"],
            "type": ["multi_source"],
            "q": ["dedup-online-stance-cross-ip-test"],
            "real": ["1"],
        }

        code_a, body_a = web._handle_api_analyze(qs, client_ip=ip_a)
        code_b, body_b = web._handle_api_analyze(qs, client_ip=ip_b)

        assert code_a == 200 and code_b == 200, (code_a, body_a, code_b, body_b)
        assert len(calls) == 2, (
            f"IP_A（配額耗盡）跟 IP_B（配額充裕）狀態不同，不該共用同一把 "
            f"dedup key，依賴應該各被真的呼叫 1 次，實際共 {len(calls)} 次：{calls}"
        )
        assert calls[0]["force_stance_offline"] is True, (
            f"IP_A 配額耗盡，應該被 degrade（force_stance_offline=True），"
            f"實際 {calls[0]}"
        )
        assert calls[1]["force_stance_offline"] is False, (
            f"IP_B 配額充裕，不該被 degrade，實際 {calls[1]}"
        )

        # Round 12 之後：同一個 IP 循序再送一次，in-flight 早已清空，會
        # fresh 重新真的呼叫一次 run()（不再命中任何共用快取）——但因為
        # IP_A 的配額狀態沒變，這次呼叫一樣該正確算出
        # `force_stance_offline=True`，不會因為快取移除而回歸算錯。
        code_a2, body_a2 = web._handle_api_analyze(qs, client_ip=ip_a)
        assert code_a2 == 200, (code_a2, body_a2)
        assert len(calls) == 3, (
            f"循序（非並行）的重複請求應該 fresh 重新真的呼叫依賴，"
            f"實際共 {len(calls)} 次：{calls}"
        )
        assert calls[2]["force_stance_offline"] is True, (
            f"IP_A 配額狀態沒變，這次 fresh 呼叫一樣該正確算出 "
            f"force_stance_offline=True，實際 {calls[2]}"
        )
    finally:
        web._online_stance_rate_buckets.pop(ip_a, None)
        web._online_stance_rate_buckets.pop(ip_b, None)


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


def test_api_analyze_dedup_key_live_ignores_sample_real_bypass(monkeypatch):
    """codex HIGH 複審（key 構造正確 canonicalization，收斂前幾輪
    token/query 糾結）：`live=1`（token 驗證通過）生效時，`sample`/`real`
    完全不影響 `_do_analyze` 實際呼叫 `pipeline.run` 的方式（live 優先，
    見 `_is_sample_request`/`_is_real_request`）——但先前版本的
    `_analyze_dedup_key` 把這些原始（會被忽略的）值原封不動塞進 key，
    導致使用者只要任意變動一個「反正會被忽略」的 `sample`/`real` 參數，
    就能繞過 dedup、讓語意上完全相同的一次真 Bedrock 呼叫被拆成好幾次
    獨立 compute()——重複花費繞過 dedup，正是 #51 要防的事。

    修復後：`_analyze_dedup_key` 改用 `_analyze_effective_mode(qs)`
    算出的單一 `"live"/"real"/"sample"` canonical 欄位取代原始
    sample/real/token 四個欄位。本測試end-to-end驗證：同一個 query，
    三個**並行**送出、`live=1` + 合法 token 的請求，只是各自帶不同
    （理應被忽略）的 `sample`/`real` 值，應該只觸發 **1 次**
    `pipeline.run`（正確 in-flight dedup、不能被繞過）。（Round 12
    之後：dedup 只發生在 in-flight 期間，這裡改用並行送出而不是循序
    送出三次。）

    同時驗證反面：sample/real/live 三種**不同** effective_mode 的請求
    （即使 query 相同）必須各自獨立 compute()，不能被錯誤地 dedup 在一起
    ——canonicalization 只收斂「同 mode 內被忽略的雜訊欄位」，不能連
    「真正不同的 mode」都誤判成同一把 key。
    """
    monkeypatch.setattr(web, "HAS_BEDROCK", True)
    monkeypatch.setattr(web, "LIVE_TOKEN", "bypass-test-live-token")

    real_run = pipeline_module.run
    query = "dedup-effective-mode-bypass-test"

    # --- (1) live=1 + 不同的 sample/real 原始值——理應共用同一把 key，
    #     只呼叫 1 次 pipeline.run（不能靠變動被忽略的參數繞過 dedup）。
    counter_live = _CallCounter()

    def _stub_run_live(*args, **kwargs):
        counter_live.hit()
        coin, q, qtype_arg = args[0], args[1], args[2]
        time.sleep(0.2)  # 拉長一點，確保三個並行 worker 真的重疊在一起
        # 用真正的 pipeline.run 跑一次真正的離線 offline 分析，$0、
        # 不觸發任何真 Bedrock/AWS 呼叫——這裡只是要驗證 dedup 的呼叫
        # 次數，不是要驗證 pipeline 本身的輸出內容。
        return real_run(coin, q, qtype_arg, offline=True)

    monkeypatch.setattr(web, "run", _stub_run_live)

    qs_live_a = {
        "coin": ["BTC"],
        "type": ["multi_source"],
        "q": [query],
        "live": ["1"],
        "token": ["bypass-test-live-token"],
        "sample": ["1"],
    }
    qs_live_b = {
        "coin": ["BTC"],
        "type": ["multi_source"],
        "q": [query],
        "live": ["1"],
        "token": ["bypass-test-live-token"],
        "real": ["1"],
    }
    qs_live_c = {
        "coin": ["BTC"],
        "type": ["multi_source"],
        "q": [query],
        "live": ["1"],
        "token": ["bypass-test-live-token"],
        "sample": ["some-other-ignored-value"],
        "real": ["another-ignored-value"],
    }

    key_live_a = web._analyze_dedup_key(
        qtype=web.QuestionType("multi_source"), coin_key="BTC", query=query, qs=qs_live_a
    )
    key_live_b = web._analyze_dedup_key(
        qtype=web.QuestionType("multi_source"), coin_key="BTC", query=query, qs=qs_live_b
    )
    key_live_c = web._analyze_dedup_key(
        qtype=web.QuestionType("multi_source"), coin_key="BTC", query=query, qs=qs_live_c
    )
    assert key_live_a == key_live_b == key_live_c, (
        "live=1 生效時，sample/real 的原始值差異理應被忽略、共用同一把 "
        f"key，實際：a={key_live_a!r}, b={key_live_b!r}, c={key_live_c!r}"
    )

    barrier = threading.Barrier(3)
    live_results: list[int] = []
    live_results_lock = threading.Lock()

    def _worker(qs):
        barrier.wait()
        code, _ = web._handle_api_analyze(qs, client_ip="10.1.7.1")
        with live_results_lock:
            live_results.append(code)

    threads = [
        threading.Thread(target=_worker, args=(qs,))
        for qs in (qs_live_a, qs_live_b, qs_live_c)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(live_results) == 3 and all(code == 200 for code in live_results), live_results
    assert counter_live.n == 1, (
        f"live=1 + 不同（理應被忽略）的 sample/real 值，並行送出應該正確 "
        f"in-flight dedup、只呼叫 1 次 pipeline.run，實際呼叫了 "
        f"{counter_live.n} 次——代表可以靠變動被忽略的參數繞過 dedup、"
        f"重複觸發真 Bedrock 呼叫"
    )

    web._analyze_dedup_inflight.clear()

    # --- (2) 反面驗證：sample / real / live 三種**不同**
    #     effective_mode（即使 query 相同）必須各自獨立 compute()，
    #     不能被誤判成同一把 key。
    counter_modes = _CallCounter()

    def _stub_run_modes(*args, **kwargs):
        counter_modes.hit()
        coin, q, qtype_arg = args[0], args[1], args[2]
        return real_run(coin, q, qtype_arg, offline=True)

    monkeypatch.setattr(web, "run", _stub_run_modes)

    qs_sample_mode = {
        "coin": ["BTC"],
        "type": ["multi_source"],
        "q": [query],
        "sample": ["1"],
    }
    qs_real_mode = {
        "coin": ["BTC"],
        "type": ["multi_source"],
        "q": [query],
    }
    qs_live_mode = {
        "coin": ["BTC"],
        "type": ["multi_source"],
        "q": [query],
        "live": ["1"],
        "token": ["bypass-test-live-token"],
    }

    key_sample_mode = web._analyze_dedup_key(
        qtype=web.QuestionType("multi_source"), coin_key="BTC", query=query, qs=qs_sample_mode
    )
    key_real_mode = web._analyze_dedup_key(
        qtype=web.QuestionType("multi_source"), coin_key="BTC", query=query, qs=qs_real_mode
    )
    key_live_mode = web._analyze_dedup_key(
        qtype=web.QuestionType("multi_source"), coin_key="BTC", query=query, qs=qs_live_mode
    )
    assert len({key_sample_mode, key_real_mode, key_live_mode}) == 3, (
        f"sample/real/live 三種不同 effective_mode 應該各自產生不同 key，"
        f"實際：sample={key_sample_mode!r}, real={key_real_mode!r}, "
        f"live={key_live_mode!r}"
    )

    code_s, _ = web._handle_api_analyze(qs_sample_mode, client_ip="10.1.7.2")
    code_r, _ = web._handle_api_analyze(qs_real_mode, client_ip="10.1.7.2")
    code_l, _ = web._handle_api_analyze(qs_live_mode, client_ip="10.1.7.2")
    assert code_s == 200 and code_r == 200 and code_l == 200
    assert counter_modes.n == 3, (
        f"sample/real/live 三種不同 effective_mode（即使 query 相同）應該"
        f"各自獨立呼叫 1 次 pipeline.run，實際共呼叫了 {counter_modes.n} 次"
    )


def test_api_analyze_dedup_key_distinguishes_query_whitespace_variant(monkeypatch):
    """codex 複審 MEDIUM（query key⟺實際執行必須一致）：`_analyze_dedup_key`
    先前對 `query` 做 `.strip()`，但 `_do_analyze`/`_do_comparison` 內部
    重新讀 `qs.get("q", [...])[0]` 傳給 `pipeline.run` 時**不 strip**——
    `"foo"` 跟 `" foo "` 會被 key 誤判成同一把（因為都 strip 成 `"foo"`），
    但兩者傳給 pipeline 的 prompt 其實不同（有無頭尾空白）。若共用同一個
    in-flight/快取 entry，先到的請求會決定「共用」的實際執行內容，後到
    的另一個字串不同的請求卻拿到別人 prompt 跑出來的答案——跟先前修
    `token` 的 strip 問題同一個道理。

    修法：key 移除 `query.strip()`，跟 pipeline 實際收到的原始 query
    位元組一致。驗證：dedup key 不同、且兩種到達順序（"foo" 先到 /
    " foo " 先到）都各自真的呼叫到 `pipeline.run`、且各自拿到對應**自己
    query** 的執行內容，不共用、不被先到者決定。"""
    qs_plain = {
        "coin": ["BTC"],
        "type": ["multi_source"],
        "q": ["foo"],
    }
    qs_spaced = {
        "coin": ["BTC"],
        "type": ["multi_source"],
        "q": [" foo "],
    }

    key_plain = web._analyze_dedup_key(
        qtype=web.QuestionType("multi_source"), coin_key="BTC", query="foo", qs=qs_plain
    )
    key_spaced = web._analyze_dedup_key(
        qtype=web.QuestionType("multi_source"), coin_key="BTC", query=" foo ", qs=qs_spaced
    )
    assert key_plain != key_spaced, "query 頭尾空白不同、傳給 pipeline 的 prompt 不同，dedup key 不該相同"

    real_run = pipeline_module.run

    # 順序 1："foo" 先到、" foo " 後到——各自都該真的呼叫 1 次 pipeline.run，
    # 且各自收到的 query 要對應自己送出的那個（不是共用先到者的）。
    seen_queries_1: list[str] = []

    def _stub_run_1(coin, query, qtype, *args, **kwargs):
        seen_queries_1.append(query)
        return real_run(coin, query, qtype, offline=True)

    monkeypatch.setattr(web, "run", _stub_run_1)
    code1, _ = web._handle_api_analyze(qs_plain, client_ip="10.1.7.1")
    code2, _ = web._handle_api_analyze(qs_spaced, client_ip="10.1.7.1")
    assert code1 == 200 and code2 == 200
    assert seen_queries_1 == ["foo", " foo "], (
        f"應各自呼叫 1 次、各自帶自己的 query，實際觀察到 {seen_queries_1!r}"
    )

    web._analyze_dedup_inflight.clear()

    # 順序 2：" foo " 先到、"foo" 後到——順序反過來，結論不變。
    seen_queries_2: list[str] = []

    def _stub_run_2(coin, query, qtype, *args, **kwargs):
        seen_queries_2.append(query)
        return real_run(coin, query, qtype, offline=True)

    monkeypatch.setattr(web, "run", _stub_run_2)
    code3, _ = web._handle_api_analyze(qs_spaced, client_ip="10.1.7.2")
    code4, _ = web._handle_api_analyze(qs_plain, client_ip="10.1.7.2")
    assert code3 == 200 and code4 == 200
    assert seen_queries_2 == [" foo ", "foo"], (
        f"順序反過來一樣該各自呼叫 1 次、各自帶自己的 query，實際觀察到 {seen_queries_2!r}"
    )


def test_api_analyze_dedup_key_no_delimiter_injection_collision(monkeypatch):
    """codex 複審 HIGH（key delimiter 注入碰撞）：`_analyze_dedup_key` 先前用
    `"\\x1f".join(...)` 固定分隔字元串接所有欄位——但每一段都是 user 可控
    的原始輸入（`query`/`sample`/`token`… 皆可含任意位元組，包括 `\\x1f`
    本身），未逃逸的固定分隔字元串接不保證「不同語意的欄位組合 ⇒ 不同
    字串」這個 key 必須成立的不變量：只要某個欄位的內容剛好吸收了本該屬於
    另一個欄位的分隔位元組，兩組完全不同的 (query, sample, live, real,
    token) 組合就可能被串接成一模一樣的位元組序列，共用同一把 dedup key
    ——導致其中一個請求的 in-flight/快取結果被另一個完全不相干的請求
    冒用，且該跑的那次計算被錯誤地抑制掉。

    驗證方法上的誠實揭露：codex 複審訊息裡舉的示意例子（`q=x%1F1`
    無 `sample` 參數 vs `q=x&sample=1`）經實際逐位元組驗算/程式驗證
    （見下方 `_legacy_vulnerable_join` 重建），**在目前這個固定 7 欄位
    join 下並不會真的字串相等**（差恰好 1 個尾端分隔位元組，屬於示意性
    簡化說法，非本函式實際位元組行為的精確重現）；真正可程式驗證、
    確實會碰撞的一組具體反例是本測試使用的 `("x\\x1f1", sample="")`
    vs `("x", sample="1\\x1f")`——兩者串接後位元組序列完全相同（已用
    `_legacy_vulnerable_join` 重建驗證），足以證明「未逃逸固定分隔字元
    串接不是 collision-resistant」這個 codex 抓到的根本問題類別確實存在
    且可被實際觸發，不論示意例子本身是否逐位元組精確。

    修法：key 改用 `json.dumps(...)` 序列化，JSON 字串逃逸使 user 輸入的
    `\\x1f`／逗號／引號都無法偽造成別的欄位邊界，兩個真正不同的欄位組合
    在任何情況下都不可能序列化成同一把 key。"""
    qtype = web.QuestionType("multi_source")

    def _legacy_vulnerable_join(coin_key: str, query: str, qs: dict) -> str:
        """重建修復前 `_analyze_dedup_key` 的 `"\\x1f".join(...)` 邏輯
        （不呼叫 production code），僅用於證明「舊格式確實會碰撞」。"""
        sample_raw = qs.get("sample", [""])[0] or ""
        live_raw = qs.get("live", [""])[0] or ""
        real_raw = qs.get("real", [""])[0] or ""
        token_raw = qs.get("token", [""])[0] or ""
        return "\x1f".join(
            (qtype.value, coin_key, query, sample_raw, live_raw, real_raw, token_raw)
        )

    # --- (1) 真實可驗證的碰撞對：query 含 \x1f（無 sample）vs query 乾淨
    #     但 sample 欄位本身帶了一個尾端 \x1f 位元組——兩者是完全不同的
    #     原始輸入（不同 query 字串、不同 sample 原始值），舊格式下卻串接
    #     成同一把 key。
    qs_a = {"coin": ["BTC"], "type": ["multi_source"], "q": ["x\x1f1"]}
    qs_b = {"coin": ["BTC"], "type": ["multi_source"], "q": ["x"], "sample": ["1\x1f"]}

    legacy_key_a = _legacy_vulnerable_join("BTC", "x\x1f1", qs_a)
    legacy_key_b = _legacy_vulnerable_join("BTC", "x", qs_b)
    assert legacy_key_a == legacy_key_b, (
        "重現修復前的問題：這組具體反例在舊版 \\x1f 串接下本該碰撞成同一把 key"
        f"（legacy_a={legacy_key_a!r}, legacy_b={legacy_key_b!r}）"
    )

    key_a = web._analyze_dedup_key(qtype=qtype, coin_key="BTC", query="x\x1f1", qs=qs_a)
    key_b = web._analyze_dedup_key(qtype=qtype, coin_key="BTC", query="x", qs=qs_b)
    assert key_a != key_b, (
        "修復後：這組會讓舊格式碰撞的具體反例，用現行 JSON 序列化不該再碰撞"
        f"（key_a={key_a!r}, key_b={key_b!r}）"
    )

    # --- (2) codex 複審訊息裡舉的示意例子（無 sample vs 乾淨 sample=1）：
    #     實測（見上方 docstring）在舊格式下並不真的字串相等，但仍額外
    #     驗證修復後的 key 保持不同（不因為修復而意外變成相同，屬於基本
    #     回歸保護，非本輪漏洞的核心重現）。
    qs_c = {"coin": ["BTC"], "type": ["multi_source"], "q": ["x\x1f1"]}
    qs_d = {"coin": ["BTC"], "type": ["multi_source"], "q": ["x"], "sample": ["1"]}
    key_c = web._analyze_dedup_key(qtype=qtype, coin_key="BTC", query="x\x1f1", qs=qs_c)
    key_d = web._analyze_dedup_key(qtype=qtype, coin_key="BTC", query="x", qs=qs_d)
    assert key_c != key_d

    # --- (3) 端對端行為驗證：透過 `_handle_api_analyze` 真的送出 (1) 的
    #     那組真實碰撞反例，斷言各自獨立呼叫 pipeline.run、各自帶對應
    #     自己 query 的內容，不共用 in-flight/快取、不互相覆寫/抑制對方
    #     的計算。
    real_run = pipeline_module.run
    seen_calls: list[dict] = []

    def _stub_run(coin, query, qtype_arg, *args, **kwargs):
        seen_calls.append({"query": query, "kwargs": dict(kwargs)})
        return real_run(coin, query, qtype_arg, *args, **kwargs)

    monkeypatch.setattr(web, "run", _stub_run)

    code_a, _ = web._handle_api_analyze(qs_a, client_ip="10.1.9.1")
    code_b, _ = web._handle_api_analyze(qs_b, client_ip="10.1.9.2")

    assert code_a == 200 and code_b == 200
    assert len(seen_calls) == 2, (
        f"兩個原本會被舊格式誤判成同一把 key 的不同請求，該各自真的呼叫 1 次 "
        f"pipeline.run（不共用快取/碰撞成 1 次），實際觀察到 {len(seen_calls)} 次："
        f"{seen_calls!r}"
    )
    call_a, call_b = seen_calls[0], seen_calls[1]
    assert call_a["query"] == "x\x1f1", f"實際觀察到 {call_a['query']!r}"
    assert call_b["query"] == "x", f"實際觀察到 {call_b['query']!r}"


def test_api_analyze_dedup_key_json_serialization_broadly_collision_resistant(monkeypatch):
    """codex 複審 HIGH（key delimiter 注入碰撞 + effective_mode
    canonicalization，兩輪修復疊加後的延伸驗證）。

    codex HIGH 複審（key 構造正確 canonicalization）之後，key 已經從
    `(type, coin, query, sample_raw, live_raw, real_raw, token_raw)` 收斂
    成 `(type, coin, query, effective_mode)`——這個測試先前（本輪之前）
    斷言「所有 (query,sample,token) 組合都各自得到獨一無二的 key」，但
    這個舊斷言本身就是本輪要修的那個 bug 的鏡像：多數 sample/token 原始
    值差異對 `effective_mode` 而言根本是**被忽略的雜訊**（例如
    `live=1` 生效時 sample/real 完全不影響實際執行；`real` 這個 query
    參數從來不被 `_is_real_request` 讀取），沒道理各自產生不同 key、各自
    觸發一次獨立 compute()——那正是 codex 本輪抓到的「改 sample=a/b 產生
    不同 key 卻跑相同 work、繞過 dedup 重複花費」。

    改成驗證**兩個**互補的不變量，覆蓋 delimiter-injection
    collision-resistance（前一輪修復）跟「同一 effective_mode 不因忽略
    欄位變動而碎片化」（本輪修復）：

    1. **同 query + 同 effective_mode ⟹ 同一把 key**：對每個
       `tricky_queries` 裡的 query，分別構造一批「預期都落在 sample /
       real / live 同一個 effective_mode」但在被忽略的欄位上刻意帶
       `\\x1f`／逗號／雙引號／反斜線等 tricky 內容的 qs 組合，斷言同一
       (query, mode) 底下產生的所有 key 完全相同（一個都不該意外
       分裂出去）。
    2. **不同 (query, effective_mode) ⟹ 不同 key**：抽樣每個
       (query, mode) 各自的代表 key，斷言這批代表 key 兩兩相異，驗證
       JSON 序列化在 `effective_mode` 這個新的、縮減後的欄位集合下仍然
       collision-resistant（不會因為兩個不同的 query 或不同的 mode
       意外序列化成同一把 key）。
    """
    monkeypatch.setattr(web, "HAS_BEDROCK", True)
    monkeypatch.setattr(web, "LIVE_TOKEN", "broad-test-live-token")
    qtype = web.QuestionType("multi_source")

    tricky_queries = ["x", "x\x1f1", "\x1f", "x,y", 'x"y', "x\\y", "foo\x1fbar\x1f", ""]

    def _sample_mode_variants() -> list[dict]:
        # sample 精確等於 "1" 才會生效——被忽略的欄位（real/token）刻意
        # 帶 tricky 內容，理應完全不影響 effective_mode="sample"。
        return [
            {"sample": ["1"]},
            {"sample": ["1"], "real": ["1"]},
            {"sample": ["1"], "real": ["0"]},
            {"sample": ["1"], "token": ["a\x1fb"]},
            {"sample": ["1"], "token": ['a"b'], "real": ["x,y"]},
        ]

    def _real_mode_variants() -> list[dict]:
        # 沒有 live、sample 不精確等於 "1"——都落在預設 real 檔位；
        # sample/real/token 的具體 tricky 內容全部理應被忽略。
        return [
            {},
            {"real": ["1"]},
            {"sample": ["0"]},
            {"sample": ["yes"]},
            {"sample": ["1\x1f"]},
            {"sample": ["\x1f1"], "token": ["a\x1fb"]},
            {"real": ["1"], "token": ['a"b'], "sample": [","]},
        ]

    def _live_mode_variants() -> list[dict]:
        # live=1 + 正確 token——sample/real 不管帶什麼 tricky 內容都該被
        # 忽略（live 優先），這正是本輪修復要堵住的「改 sample/real 繞過
        # dedup 重複花費真 Bedrock」場景。
        return [
            {"live": ["1"], "token": ["broad-test-live-token"]},
            {"live": ["1"], "token": ["broad-test-live-token"], "sample": ["1"]},
            {"live": ["1"], "token": ["broad-test-live-token"], "real": ["1"]},
            {
                "live": ["1"],
                "token": ["broad-test-live-token"],
                "sample": ["anything\x1fweird"],
                "real": ['also"weird,stuff'],
            },
        ]

    mode_variant_builders = {
        "sample": _sample_mode_variants,
        "real": _real_mode_variants,
        "live": _live_mode_variants,
    }

    representative_keys: dict[tuple[str, str], str] = {}

    for query in tricky_queries:
        for mode_name, build_variants in mode_variant_builders.items():
            group_keys: list[str] = []
            for extra in build_variants():
                qs = {"coin": ["BTC"], "type": ["multi_source"], "q": [query], **extra}
                assert web._analyze_effective_mode(qs) == mode_name, (
                    f"測試資料本身設計錯誤：{qs!r} 應該落在 {mode_name!r} 檔位，"
                    f"實際 {web._analyze_effective_mode(qs)!r}"
                )
                key = web._analyze_dedup_key(qtype=qtype, coin_key="BTC", query=query, qs=qs)
                group_keys.append(key)

            assert len(set(group_keys)) == 1, (
                f"query={query!r} mode={mode_name!r} 底下，{len(group_keys)} 組只在"
                f"「被忽略欄位」不同的 qs 變體，理應共用同一把 key（同一 "
                f"effective_mode），實際卻產生了 {len(set(group_keys))} 種不同的 "
                f"key：{group_keys!r}——代表這些被忽略欄位的變動仍會不當 "
                f"fragment dedup key。"
            )
            representative_keys[(query, mode_name)] = group_keys[0]

    all_repr_keys = list(representative_keys.values())
    assert len(set(all_repr_keys)) == len(all_repr_keys), (
        f"存在碰撞：{len(all_repr_keys)} 組不同的 (query,effective_mode) 只產生了 "
        f"{len(set(all_repr_keys))} 把不同的 key，代表 JSON 序列化在縮減後的欄位"
        f"集合下仍有 collision"
    )


# ---------------------------------------------------------------------------
# #51 + #87（issue 併軌）：SSR `/analyze`／`/analyze.json` 防重複計費——套用
# 跟上方 `/api/analyze` 完全同一套 in-flight dedup（`_analyze_dedup_key`/
# `_dedup_analyze_call`/`_AnalyzeFlight`），且跟 `/api/analyze` 共用同一把
# dedup key space（`_analyze_dedup_coin_key`，見 `do_GET` 對應段落）。
# ---------------------------------------------------------------------------

def _do_get_from_ip(path: str, ip: str) -> tuple[int, str]:
    """比照上方 `_do_get`，但允許指定 `client_address`——SSR 路由的 dedup
    需要驗證「每個 caller 各自過自己的限流」（見 `_analyze_enforce_caller_
    rate_limit`），跨 IP 測試需要能各自指定不同的來源 IP。"""
    h = web.Handler.__new__(web.Handler)
    h.client_address = (ip, 12345)
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


def test_ssr_analyze_dedup_concurrent_identical_requests_call_run_once(monkeypatch):
    """驗收標準 #1：同參數 `/analyze` 並行連發 3 次，真正呼叫 `pipeline.run`
    應只有 1 次，其餘 in-flight follower 共用同一份真實結果（回應 HTML
    body 逐字相同）——跟 `test_api_analyze_dedup_concurrent_identical_
    requests_call_run_once` 驗證同一件事，換成走 SSR `/analyze` 路由。"""
    counter = _CallCounter()
    _wrap_counting_run(monkeypatch, counter, delay=0.2)

    path = "/analyze?coin=BTC&type=multi_source&q=ssr-dedup-concurrent-test"
    n_workers = 3
    barrier = threading.Barrier(n_workers)
    results: list[tuple[int, str]] = []
    results_lock = threading.Lock()

    def _worker():
        barrier.wait()
        code, body = _do_get_from_ip(path, "10.2.1.1")
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
    assert len(bodies) == 1, "並行重複請求應共用同一份真實結果"


def test_ssr_analyze_json_dedup_concurrent_identical_requests_call_run_once(monkeypatch):
    """同上，換成 `/analyze.json`（裸 JSON 匯出路由）。"""
    counter = _CallCounter()
    _wrap_counting_run(monkeypatch, counter, delay=0.2)

    path = "/analyze.json?coin=ETH&type=multi_source&q=ssr-json-dedup-concurrent-test"
    n_workers = 3
    barrier = threading.Barrier(n_workers)
    results: list[tuple[int, str]] = []
    results_lock = threading.Lock()

    def _worker():
        barrier.wait()
        code, body = _do_get_from_ip(path, "10.2.1.2")
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
    assert len(bodies) == 1, "並行重複請求應共用同一份真實結果"


def test_ssr_analyze_dedup_sequential_requests_each_fresh_after_previous_completes(monkeypatch):
    """驗收標準 #2：視窗外（前一次已完成後）的下一次請求應正常觸發新執行
    ——跟 `/api/analyze` 一致，dedup 只做 in-flight coalescing、**不含**
    TTL 結果快取（見 `_dedup_analyze_call` docstring Round 12 codex 複審：
    加密市場資料時效敏感，TTL 快取會 replay 過時分析結果，已被移除）。
    每一次前一個請求真的執行完成後，之後**循序**進來的下一個請求（不管
    是 1 秒後還是 30 秒後）一律 fresh 重新呼叫，不吃殘留的 in-flight
    entry（見 autouse fixture `_reset_analyze_dedup_state`：每個測試前後
    都會清空 `_analyze_dedup_inflight`，這裡額外直接驗證『同一個 process
    內連續兩次』這個更貼近真實使用情境的路徑）。"""
    counter = _CallCounter()
    _wrap_counting_run(monkeypatch, counter)

    path = "/analyze?coin=SOL&type=multi_source&q=ssr-dedup-sequential-test"
    code1, _ = _do_get_from_ip(path, "10.2.1.3")
    code2, _ = _do_get_from_ip(path, "10.2.1.3")

    assert code1 == 200 and code2 == 200
    assert counter.n == 2, (
        f"視窗外（前一次執行完成後）的下一次請求應正常觸發新執行，"
        f"實際 pipeline.run 只被呼叫 {counter.n} 次"
    )


def test_analyze_ssr_and_api_share_same_cross_route_dedup_key_space(monkeypatch):
    """驗收標準 #3：跨路由——`/analyze`（SSR）與 `/api/analyze`（JSON）同
    參數並行送出，也該共用同一把 dedup key、只觸發 1 次真實呼叫。這是
    #87 對 #51 既有機制的擴充要求：不只 `/api/analyze` 內部去重，`/analyze`
    ／`/analyze.json`／`/api/analyze` 三條路由要共用同一把 dedup key
    space（見 `_analyze_dedup_coin_key` docstring）。"""
    counter = _CallCounter()
    _wrap_counting_run(monkeypatch, counter, delay=0.3)

    ssr_path = "/analyze?coin=BTC&type=multi_source&q=cross-route-dedup-test"
    api_qs = {"coin": ["BTC"], "type": ["multi_source"], "q": ["cross-route-dedup-test"]}

    results: list[int] = []
    results_lock = threading.Lock()
    barrier = threading.Barrier(2)

    def _worker_ssr():
        barrier.wait()
        code, _ = _do_get_from_ip(ssr_path, "10.2.1.4")
        with results_lock:
            results.append(code)

    def _worker_api():
        barrier.wait()
        code, _ = web._handle_api_analyze(api_qs, client_ip="10.2.1.5")
        with results_lock:
            results.append(code)

    t_ssr = threading.Thread(target=_worker_ssr)
    t_api = threading.Thread(target=_worker_api)
    t_ssr.start()
    t_api.start()
    t_ssr.join(timeout=10)
    t_api.join(timeout=10)

    assert len(results) == 2
    assert all(code == 200 for code in results), results
    assert counter.n == 1, (
        f"`/analyze` 與 `/api/analyze` 同參數並行請求應共用同一把 dedup key，"
        f"真正呼叫 pipeline.run 應只有 1 次，實際 {counter.n} 次"
    )


def test_analyze_ssr_dedup_coin_key_matches_api_analyze_coin_key_comparison():
    """`_analyze_dedup_coin_key`（SSR 路由用）跟 `_handle_api_analyze` 內建構
    `coin_key` 的既有邏輯，comparison 題型下算出的 `coin_key` 必須逐字
    相同，這是跨路由共用 dedup key space 成立的前提（見
    `_analyze_dedup_coin_key` docstring）。"""
    qtype = web.QuestionType("comparison")
    qs = {"coin": ["ETH"], "coin2": ["BTC"], "type": ["comparison"], "q": ["x"]}
    coin_key = web._analyze_dedup_coin_key(qtype, qs, "x")
    assert coin_key == "ETH,BTC", (
        "comparison coin_key 應依請求原始順序（coin_a,coin_b）組成，"
        f"不能排序，實際 {coin_key!r}"
    )


def test_analyze_ssr_dedup_coin_key_invalid_coin_returns_none_fail_safe(monkeypatch):
    """`_analyze_dedup_coin_key` 找不到合法幣種時回傳 `None`（不 raise）
    ——SSR `/analyze` 路由據此判斷「這次不套用 dedup」，直接落回原始呼叫
    方式，讓 `_do_analyze` 既有的 `ValueError` 錯誤訊息原封不動顯示。這裡
    直接端到端驗證：非法幣種（`DOGE`，非白名單）打 `/analyze` 仍然正常回
    400 錯誤頁，不會因為新增的 dedup 分支而行為改變，也不會誤觸真依賴
    呼叫。"""
    counter = _CallCounter()
    _wrap_counting_run(monkeypatch, counter)

    qtype = web.QuestionType("multi_source")
    assert web._analyze_dedup_coin_key(qtype, {"coin": ["DOGE"]}, "x") is None

    code, body = _do_get_from_ip(
        "/analyze?coin=DOGE&type=multi_source&q=ssr-invalid-coin-test", "10.2.1.6"
    )
    assert code == 400, body
    assert counter.n == 0, "非法幣種不該碰任何依賴呼叫"


def test_ssr_analyze_json_dedup_timeout_returns_json_not_html(monkeypatch):
    """codex MEDIUM 複審修復：`/analyze.json` 是 JSON 端點契約（成功時回
    `application/json`），dedup follower bounded wait 逾時
    （`_AnalyzeDedupTimeout`）先前這裡的 except 分支不分路由一律回
    `page(...)` HTML，導致 `/analyze.json` 違約收到 HTML 而非 JSON。這裡
    直接監控逾時情境：monkeypatch `web._dedup_analyze_call` 讓它 raise
    `_AnalyzeDedupTimeout`（模擬 leader 執行太久、follower 等到逾時），
    斷言 `/analyze.json` 回應的 status、Content-Type、body 三者都符合
    JSON 端點契約——`/analyze`（HTML 版本）維持原行為不受影響（見下方
    對照斷言）。"""

    def _raise_timeout(key, compute):
        raise web._AnalyzeDedupTimeout("模擬 leader 執行逾時，请稍後重試")

    monkeypatch.setattr(web, "_dedup_analyze_call", _raise_timeout)

    def _do_get_capture(path: str, ip: str) -> tuple[int, dict, str]:
        h = web.Handler.__new__(web.Handler)
        h.client_address = (ip, 12345)
        h.path = path
        h.wfile = BytesIO()
        h.headers = Message()
        captured_headers: dict = {}
        captured = []
        h.send_response = lambda code: captured.append(code)
        h.send_header = lambda name, val: captured_headers.setdefault(name, val)
        h.end_headers = lambda: None
        h.do_GET()
        body = h.wfile.getvalue().decode("utf-8")
        return captured[0], captured_headers, body

    code, headers, body = _do_get_capture(
        "/analyze.json?coin=BTC&type=multi_source&q=ssr-json-dedup-timeout-test",
        "10.2.1.7",
    )
    assert code == 503, body
    assert headers["Content-Type"] == "application/json; charset=utf-8", headers
    parsed = json.loads(body)  # 契約：body 必須是可被 json.loads 解析的合法 JSON
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "timeout"

    # 對照組：`/analyze`（HTML 版本）在同樣的逾時情境下維持原行為（品牌化
    # HTML 錯誤卡），確認這次修復只精準命中 `/analyze.json`，沒有動到
    # `/analyze` 既有的 HTML 錯誤頁行為。
    code_html, headers_html, body_html = _do_get_capture(
        "/analyze?coin=BTC&type=multi_source&q=ssr-html-dedup-timeout-test",
        "10.2.1.8",
    )
    assert code_html == 503, body_html
    assert headers_html["Content-Type"] == "text/html; charset=utf-8", headers_html
    assert "服務忙碌中" in body_html


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


# ---------------------------------------------------------------------------
# harper CISO 隱私審查附條件修復（PR #107）：公開 JSON 端點不得洩漏
# Evidence.author / 快照 authors 鍵；內部 cache/快照原樣保留供未來 W3 用。
# ---------------------------------------------------------------------------

def _real_evidence_plus_authored(coin: str, query: str) -> tuple:
    """回傳一組真實跑過 `web.run()` 的 (report, evidence, log)，並在
    evidence 尾端多附一筆帶 `author` 的 `Evidence`——模擬「連接器真的抓到
    來源平台公開 username」的情境，供三個公開端點測試共用。"""
    report, evidence, log = web.run(coin, query, QuestionType.MULTI_SOURCE, offline=True)
    authored_ev = Evidence(
        source="reddit-bitcoin",
        fetched_at="2026-07-06T00:00:00Z",
        content_reference="ref-leak-test",
        related_claim=query,
        author="/u/leak_test_user",
    )
    return report, list(evidence) + [authored_ev], log


def test_api_analyze_public_response_excludes_author(monkeypatch):
    """CEO must-have #1：`/api/analyze` 對外回應的每筆 evidence dict 不得
    含 `author` 鍵，即便底層 evidence 真的有 author。"""
    report, evidence, log = _real_evidence_plus_authored("BTC", "author leak test")

    def fake_run(coin, query, qtype, **kwargs):
        return report, evidence, log

    monkeypatch.setattr(web, "run", fake_run)

    code, body = web._handle_api_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["author leak test"]},
        client_ip="10.2.0.1",
    )
    assert code == 200
    data = _envelope(body)["data"]
    assert any(ev.get("content_reference") == "ref-leak-test" for ev in data["evidence"])
    assert all("author" not in ev for ev in data["evidence"])


def test_analyze_json_route_excludes_author(monkeypatch):
    """`/analyze.json`（SSR 頁旁的裸 JSON 匯出路由，同樣免認證公開）也不得
    洩漏 author——跟 `/api/analyze` 是各自獨立的序列化路徑，需分開驗證。"""
    report, evidence, log = _real_evidence_plus_authored("BTC", "author leak json route")

    def fake_run(coin, query, qtype, **kwargs):
        return report, evidence, log

    monkeypatch.setattr(web, "run", fake_run)

    code, body = _do_get("/analyze.json?coin=BTC&type=multi_source&q=author+leak+json+route")
    assert code == 200
    parsed = json.loads(body)
    assert any(ev.get("content_reference") == "ref-leak-test" for ev in parsed["evidence"])
    assert all("author" not in ev for ev in parsed["evidence"])


def test_api_analyze_comparison_public_response_excludes_author(monkeypatch):
    """codex vp-engineering 終審 LOW（PR #107）：`/api/analyze`
    `type=comparison` 分支（`evidence_a`/`evidence_b`）同樣不得洩漏
    author——先前 CEO must-have #1 的測試只涵蓋單幣分支，comparison 分支
    需分開驗證，不能假設「單幣測過等於 comparison 也測過」。"""
    report_a, evidence_a, report_b, evidence_b, log = web.run_comparison(
        "BTC", "ETH", "comparison author leak test", offline=True,
    )
    authored_ev = Evidence(
        source="reddit-bitcoin",
        fetched_at="2026-07-06T00:00:00Z",
        content_reference="ref-cmp-leak-test",
        related_claim="comparison author leak test",
        author="/u/cmp_leak_test_user",
    )
    evidence_a = list(evidence_a) + [authored_ev]

    def fake_run_comparison(coin_a, coin_b, query, **kwargs):
        return report_a, evidence_a, report_b, evidence_b, log

    monkeypatch.setattr(web, "run_comparison", fake_run_comparison)

    code, body = web._handle_api_analyze(
        {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["comparison author leak test"]},
        client_ip="10.2.0.4",
    )
    assert code == 200
    data = _envelope(body)["data"]
    assert any(
        ev.get("content_reference") == "ref-cmp-leak-test" for ev in data["evidence_a"]
    )
    assert all("author" not in ev for ev in data["evidence_a"])
    assert all("author" not in ev for ev in data["evidence_b"])


def test_analyze_json_route_comparison_excludes_author(monkeypatch):
    """`/analyze.json?type=comparison`（SSR 頁旁的裸 JSON 匯出路由）同樣
    需要獨立驗證 comparison 分支不洩漏 author。"""
    report_a, evidence_a, report_b, evidence_b, log = web.run_comparison(
        "BTC", "ETH", "analyze.json comparison author leak", offline=True,
    )
    authored_ev = Evidence(
        source="reddit-bitcoin",
        fetched_at="2026-07-06T00:00:00Z",
        content_reference="ref-cmp-json-leak",
        related_claim="analyze.json comparison author leak",
        author="/u/cmp_json_leak_user",
    )
    evidence_a = list(evidence_a) + [authored_ev]

    def fake_run_comparison(coin_a, coin_b, query, **kwargs):
        return report_a, evidence_a, report_b, evidence_b, log

    monkeypatch.setattr(web, "run_comparison", fake_run_comparison)

    code, body = _do_get(
        "/analyze.json?coin=BTC,ETH&type=comparison&q=analyze.json+comparison+author+leak"
    )
    assert code == 200
    parsed = json.loads(body)
    assert any(
        ev.get("content_reference") == "ref-cmp-json-leak" for ev in parsed["evidence_a"]
    )
    assert all("author" not in ev for ev in parsed["evidence_a"])
    assert all("author" not in ev for ev in parsed["evidence_b"])


def test_evidence_field_gatekeeper_all_fields_classified():
    """欄位守門測試（codex vp-engineering 終審 LOW，PR #107，已實測 H1
    為活案例）：`dataclasses.fields(Evidence)` 的每個欄位都必須被明確
    分類進 `web._EVIDENCE_PUBLIC_FIELDS` 或 `web._EVIDENCE_FILTERED_FIELDS`
    其中一個——這是 blocklist 過濾模式失效的結構性防線。新增 Evidence
    欄位卻忘了分類時，這個測試會紅，逼迫開發者明確決定該欄位的對外
    可見性，而不是預設「未分類 = 直接對外洩漏」。"""
    field_names = {f.name for f in dataclasses.fields(Evidence)}
    classified = web._EVIDENCE_PUBLIC_FIELDS | web._EVIDENCE_FILTERED_FIELDS
    unclassified = field_names - classified
    assert not unclassified, f"新欄位未分類（需歸入 public 或 filtered）：{unclassified}"
    assert not (web._EVIDENCE_PUBLIC_FIELDS & web._EVIDENCE_FILTERED_FIELDS), (
        "同一欄位不該同時出現在 public 與 filtered 兩個集合"
    )
    assert classified == field_names, (
        "分類集合的合集必須恰等於 Evidence 全部欄位，不多不少"
    )


def test_api_overview_public_response_excludes_authors_but_cache_retains_it(
    json_cache_backend,
):
    """CEO must-have #1：`/api/overview` 對外回應不得含 `authors` 鍵；同一
    份底層快照本身（cache 原始內容）必須維持原樣，供未來 W3 偵測讀取——
    過濾只發生在對外序列化邊界，不是閹割資料源頭。"""
    snap = {
        "coin": "BTC",
        "trust_score": 0.6,
        "authors": ["/u/leak_test_user", "reddit_mod_42"],
    }
    result = cache_set_if_newer(
        json_cache_backend, cache_key(TRUST_SNAPSHOT_SOURCE, "BTC"), [snap],
        fetched_at=1000.0, allow_json_fallback=True,
    )
    assert result.ok

    code, body = web._handle_api_overview(client_ip="10.2.0.2")
    assert code == 200
    data = _envelope(body)["data"]
    btc = next(c for c in data["coins"] if c["coin"] == "BTC")
    assert "authors" not in btc

    # 內部快照本身沒被動到——原始 cache 內容仍完整含 authors。
    raw = cache_get(json_cache_backend, cache_key(TRUST_SNAPSHOT_SOURCE, "BTC"))
    assert raw["docs"][0]["authors"] == ["/u/leak_test_user", "reddit_mod_42"]


def test_api_history_public_response_excludes_authors_but_cache_retains_it(
    json_cache_backend,
):
    """CEO must-have #1：`/api/history` 對外回應每日序列同樣不得含
    `authors` 鍵；底層按日快照 cache 內容不受影響。"""
    day = (datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    snap = {
        "coin": "ETH",
        "trust_score": 0.5,
        "authors": ["/u/history_leak_user"],
    }
    key = trust_snapshot_history_key("ETH", day)
    result = cache_set_if_newer(
        json_cache_backend, key, [snap], fetched_at=1000.0, allow_json_fallback=True,
    )
    assert result.ok

    code, body = web._handle_api_history(
        {"coin": ["ETH"], "days": ["1"]}, client_ip="10.2.0.3"
    )
    assert code == 200
    data = _envelope(body)["data"]
    assert data["history"], "測資應至少含今天一筆"
    assert all("authors" not in day_entry for day_entry in data["history"])

    raw = cache_get(json_cache_backend, key)
    assert raw["docs"][0]["authors"] == ["/u/history_leak_user"]


def test_public_evidence_dict_helper_strips_author_keeps_other_fields():
    """`_public_evidence_dict()` 單元測試：只拿掉 `author`，其餘欄位（含
    `trust`/`flags`/`content_reference`）原樣保留。"""
    ev = Evidence(
        source="reddit-bitcoin",
        fetched_at="2026-07-06T00:00:00Z",
        content_reference="ref",
        related_claim="claim",
        trust=0.42,
        author="/u/someone",
    )
    d = web._public_evidence_dict(ev)
    assert "author" not in d
    assert d["trust"] == 0.42
    assert d["content_reference"] == "ref"
    # 原始 Evidence 物件本身不受影響（helper 回傳的是新 dict，非原地修改）。
    assert ev.author == "/u/someone"


def test_public_snapshot_dict_helper_strips_authors_keeps_other_fields():
    """`_public_snapshot_dict()` 單元測試：只拿掉 `authors`，其餘欄位原樣
    保留；不修改呼叫端傳入的原始 dict。"""
    snap = {"coin": "BTC", "trust_score": 0.7, "authors": ["/u/a", "/u/b"]}
    d = web._public_snapshot_dict(snap)
    assert "authors" not in d
    assert d["coin"] == "BTC"
    assert d["trust_score"] == 0.7
    # 原始 dict 不被就地修改。
    assert snap["authors"] == ["/u/a", "/u/b"]
