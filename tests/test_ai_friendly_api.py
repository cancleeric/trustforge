"""第三輪 PR#4「AI 友善急起直追」：`docs/api/openapi.yaml` + `llms.txt` +
`GET /api/openapi.yaml` + `GET /llms.txt` 的一致性測試。

⛔ 刻意不引入 PyYAML/jsonschema 依賴（`pyproject.toml` 沒有、也不打算加）——
比照任務要求的「簡化驗證」：本檔用 stdlib 字串/關鍵字斷言 + 直接呼叫
`_handle_api_*` 系列函式做欄位存在性/型別斷言，驗證 spec 與真實回應形狀是否
一致；不做完整 JSON-Schema 驗證。spec 本身是否是合法 YAML，另外用一次性
venv（不進本 repo 相依）人工檢查過，見 CTO 回報。

⛔ credit-safe 鐵律（比照 `tests/test_json_api.py` 既有慣例）：本檔只呼叫
`_handle_api_*` 系列函式（`CACHE_BACKEND=json` 隔離到 tmp_path，不打真
DynamoDB），`/api/analyze` 走 `sample=1` 離線示範沙盒，不觸發真 Bedrock。
"""
from __future__ import annotations

import inspect
import json
import re
from email.message import Message
from io import BytesIO
from pathlib import Path

import pytest

from trustforge import web
from trustforge.ingestion.cache import (
    TRUST_SNAPSHOT_SOURCE,
    cache_key,
    cache_set_if_newer,
    trust_snapshot_history_key,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OPENAPI_PATH = _REPO_ROOT / "docs" / "api" / "openapi.yaml"
_LLMS_TXT_ROOT_PATH = _REPO_ROOT / "llms.txt"
_LLMS_TXT_FRONTEND_PATH = _REPO_ROOT / "frontend" / "public" / "llms.txt"

_OVERCLAIM_TERMS = (
    "保證",
    "準確率",
    "一定準",
    "100%",
    "百分之百",
    "零風險",
    "保准",
    "穩賺",
    "穩賺不賠",
)


def _real_documented_paths() -> set[str]:
    """從 `web.py::Handler.do_GET` 原始碼推導本次 OpenAPI spec 應該涵蓋的
    路由集合（`if u.path == "/xxx":` 字面比對），不手動硬編一份清單——
    codex 複審警告：先前硬編 `/api/*` 清單時 `/llms.txt` 被漏掉，測試名
    承諾的「涵蓋每個真實路由」實際上沒做到。

    只挑 `/api/*` 與 `/llms.txt`：SSR HTML 頁面路由（`/`、`/status`、
    `/costs`、`/analyze`、`/analyze.json` 等）不是本 OpenAPI spec 的契約
    範圍，本就不該出現在 `docs/api/openapi.yaml` 裡。
    """
    source = inspect.getsource(web.Handler.do_GET)
    all_paths = set(re.findall(r'u\.path == "(/[^"]*)"', source))
    return {p for p in all_paths if p == "/llms.txt" or p.startswith("/api/")}


@pytest.fixture(autouse=True)
def _reset_shared_module_state():
    """比照 `tests/test_json_api.py` 既有慣例：`_status_rate_buckets` 是
    `/api/status` 共用的 module-level 狀態，每個測試前後重置避免互相汙染。"""
    web._status_rate_buckets.clear()
    yield
    web._status_rate_buckets.clear()


@pytest.fixture
def json_cache_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_BACKEND", "json")
    monkeypatch.setenv("TRUSTFORGE_CACHE_DIR", str(tmp_path))


def _do_get(path: str) -> tuple[int, str, dict]:
    """端到端呼叫 `Handler.do_GET`（不開真 socket），回傳
    `(status_code, body, headers)`——比照 `tests/test_json_api.py::_do_get`
    既有慣例，額外多回傳 headers 供本檔驗證 Content-Type。"""
    h = web.Handler.__new__(web.Handler)
    h.client_address = ("127.0.0.1", 12345)
    h.path = path
    h.wfile = BytesIO()
    h.headers = Message()

    captured = []
    headers: dict = {}
    h.send_response = lambda code: captured.append(code)
    h.send_header = lambda name, val: headers.setdefault(name, val)
    h.end_headers = lambda: None

    h.do_GET()

    body = h.wfile.getvalue().decode("utf-8")
    return captured[0], body, headers


# ---------------------------------------------------------------------------
# GET /api/openapi.yaml
# ---------------------------------------------------------------------------


def test_openapi_spec_file_exists_on_disk():
    """spec 檔案必須真的存在於 repo（不是只存在於某個人本機）——部署（Docker
    `COPY docs/api ./docs/api`／EC2 zip `docs/api`）都依賴這份真檔案。"""
    assert _OPENAPI_PATH.is_file(), f"缺少 {_OPENAPI_PATH}"


def test_do_get_openapi_route_returns_200_yaml():
    code, body, headers = _do_get("/api/openapi.yaml")
    assert code == 200
    assert headers["Content-Type"] == "application/yaml; charset=utf-8"
    assert body == _OPENAPI_PATH.read_text(encoding="utf-8")


def test_openapi_spec_covers_every_real_handled_path():
    """spec 必須逐一列出 `do_GET` 實際會處理的每個 `/api/*`／`/llms.txt`
    路徑——防止漏寫掉某個端點（code 是 source of truth，不是反過來拿 spec
    去 codegen）。清單從 `do_GET` 原始碼推導（`_real_documented_paths()`），
    不手動硬編，避免測試名承諾的保護範圍跟真實路由表脫鉤（codex 複審：先前
    硬編清單漏掉 `/llms.txt`）。"""
    real_paths = _real_documented_paths()
    assert "/llms.txt" in real_paths, "推導邏輯本身壞了：/llms.txt 應該要被抓到"
    text = _OPENAPI_PATH.read_text(encoding="utf-8")
    for path in real_paths:
        assert f"{path}:" in text, f"spec 缺少路徑 {path}"


def test_openapi_spec_documents_envelope_and_status_semantics():
    """spec 必須涵蓋 `{ok,data,error}` 信封、429/502/503 語意、dedup/degraded
    欄位——任務明確要求的必備內容，不是選配。"""
    text = _OPENAPI_PATH.read_text(encoding="utf-8")
    for keyword in (
        "OkEnvelope",
        "ErrEnvelope",
        "rate_limited",
        "upstream_error",
        "timeout",
        "\"429\"",
        "\"502\"",
        "\"503\"",
        "degraded",
        "primary_connected",
    ):
        assert keyword in text, f"spec 缺少必備關鍵字：{keyword}"


def test_openapi_spec_documents_missing_key_semantics_for_manip_and_decision_state():
    """任務指定「誠實設計、agent 的關鍵契約」——`manip_score`／
    `manip_score_mean`／`decision_state` 缺鍵語意必須寫清楚。"""
    text = _OPENAPI_PATH.read_text(encoding="utf-8")
    for keyword in (
        "manip_score",
        "manip_score_mean",
        "decision_state",
        "abstain",
        "low_confidence",
        "worst-case",
    ):
        assert keyword in text, f"spec 缺少必備關鍵字：{keyword}"


def test_openapi_spec_has_no_overclaiming_wording():
    text = _OPENAPI_PATH.read_text(encoding="utf-8")
    for term in _OVERCLAIM_TERMS:
        assert term not in text, f"spec 出現誇大詞「{term}」"


# ---------------------------------------------------------------------------
# GET /llms.txt
# ---------------------------------------------------------------------------


def test_llms_txt_file_exists_on_disk():
    assert _LLMS_TXT_ROOT_PATH.is_file(), f"缺少 {_LLMS_TXT_ROOT_PATH}"


def test_do_get_llms_txt_route_returns_200_text_plain():
    code, body, headers = _do_get("/llms.txt")
    assert code == 200
    assert headers["Content-Type"] == "text/plain; charset=utf-8"
    assert body == _LLMS_TXT_ROOT_PATH.read_text(encoding="utf-8")


def test_llms_txt_root_and_frontend_public_copy_are_byte_identical():
    """react/nginx 拓樸下 nginx 直接靜態回應 `frontend/public/llms.txt`，
    不經過 python——兩份檔案必須逐字元一致，否則兩種部署拓樸看到不同內容。"""
    assert _LLMS_TXT_FRONTEND_PATH.is_file(), f"缺少 {_LLMS_TXT_FRONTEND_PATH}"
    root_bytes = _LLMS_TXT_ROOT_PATH.read_bytes()
    frontend_bytes = _LLMS_TXT_FRONTEND_PATH.read_bytes()
    assert root_bytes == frontend_bytes, "llms.txt 與 frontend/public/llms.txt 內容不一致"


def test_llms_txt_covers_required_content():
    """任務要求：一句話定位、逐端點列表、信任分/資訊完整度/棄權/操縱 worst-case
    語意、rate limit、「缺鍵＝未評估，不是零」鐵律——都要出現。"""
    text = _LLMS_TXT_ROOT_PATH.read_text(encoding="utf-8")
    for path in (
        "/api/health",
        "/api/status",
        "/api/costs",
        "/api/overview",
        "/api/history",
        "/api/analyze",
        "/api/openapi.yaml",
        "/llms.txt",
    ):
        assert path in text, f"llms.txt 缺少端點 {path}"
    for keyword in (
        "manip_score",
        "manip_score_mean",
        "calibrated_confidence",
        "decision_state",
        "abstain",
        "low_confidence",
        "缺鍵",
        "worst-case",
    ):
        assert keyword in text, f"llms.txt 缺少必備關鍵字：{keyword}"
    # rate limit 具體數字（任務要求列出限流資訊，不能只寫「有限流」帶過）
    for rate_limit_number in ("60", "30", "5"):
        assert rate_limit_number in text


def test_llms_txt_has_no_overclaiming_wording():
    for path in (_LLMS_TXT_ROOT_PATH, _LLMS_TXT_FRONTEND_PATH):
        text = path.read_text(encoding="utf-8")
        for term in _OVERCLAIM_TERMS:
            assert term not in text, f"{path} 出現誇大詞「{term}」"


# ---------------------------------------------------------------------------
# /api/status 需指向新文件（`/api/help` 未在既有程式碼中實作，這裡以既有
# `/api/status` 作為「AI 友善指引指標」的掛載點，見 web.py::_handle_api_status
# 新增的 `docs` 欄位）
# ---------------------------------------------------------------------------


def test_api_status_points_to_openapi_and_llms_txt(json_cache_backend):
    code, body = web._handle_api_status(client_ip="10.9.9.1")
    assert code == 200
    data = json.loads(body)["data"]
    assert data["docs"]["openapi"] == "/api/openapi.yaml"
    assert data["docs"]["llms_txt"] == "/llms.txt"


# ---------------------------------------------------------------------------
# spec ↔ 真實回應形狀一致性（欄位存在性 + 型別斷言，非完整 JSON-Schema 驗證）
# ---------------------------------------------------------------------------


def _assert_shape(data: dict, required: dict, *, context: str) -> None:
    """簡化版 schema 驗證：`required` 是 `{欄位名: 型別 or tuple of 型別}`，
    逐一斷言欄位存在且型別相符——不驗 `additionalProperties`/`enum`/巢狀完整
    schema，比照任務要求的「簡化驗證：關鍵欄位存在性+型別斷言」。"""
    for key, expected_type in required.items():
        assert key in data, f"{context}: 缺少欄位 {key}（真實回應 keys={sorted(data.keys())}）"
        assert isinstance(data[key], expected_type), (
            f"{context}: 欄位 {key} 型別應為 {expected_type}，實際是 {type(data[key])}"
        )


def test_api_health_matches_documented_shape():
    code, body = web._handle_api_health()
    assert code == 200
    data = json.loads(body)["data"]
    _assert_shape(
        data,
        {"status": str, "version": str, "uptime_seconds": (int, float)},
        context="/api/health",
    )


def test_api_status_matches_documented_shape(json_cache_backend):
    code, body = web._handle_api_status(client_ip="10.9.9.2")
    assert code == 200
    data = json.loads(body)["data"]
    _assert_shape(
        data,
        {
            "version": str,
            "uptime_seconds": (int, float),
            "bedrock_capable": bool,
            "live_token_set": bool,
            "cache_backend": dict,
            "freshness": dict,
            "dedup": dict,
            "docs": dict,
        },
        context="/api/status",
    )
    _assert_shape(
        data["cache_backend"],
        {
            "name": str,
            "connected": bool,
            "primary_connected": bool,
            "active_backend": str,
            "degraded": bool,
        },
        context="/api/status.cache_backend",
    )
    _assert_shape(
        data["freshness"],
        {"fresh": int, "stale": int, "missing": int, "entries": list},
        context="/api/status.freshness",
    )
    if data["freshness"]["entries"]:
        entry = data["freshness"]["entries"][0]
        _assert_shape(
            entry, {"source": str, "coin": str, "status": str}, context="/api/status.freshness.entries[0]"
        )
        # `fetched_at`/`age_seconds`：`[string, null]`/`[number, null]`——
        # spec 明確允許缺鍵時是 `null`，不能用 truthy 斷言直接判斷風險。
        assert entry.get("fetched_at") is None or isinstance(entry["fetched_at"], str)
        assert entry.get("age_seconds") is None or isinstance(entry["age_seconds"], (int, float))


def test_api_costs_matches_documented_shape(json_cache_backend, tmp_path, monkeypatch):
    """比照 `tests/test_json_api.py::test_api_costs_matches_ledger_summary`
    既有慣例：直接 monkeypatch `web.get_ledger`，避免 20 秒 TTL 快取汙染或
    讀到本機真的 `out/cost_ledger.jsonl`。"""
    from trustforge.ledger import JsonlLedger

    monkeypatch.setenv("TRUSTFORGE_COST_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    ledger = JsonlLedger()
    ledger.append({
        "run_id": "r1", "ts": "2026-01-01T00:00:00+00:00",
        "total_cost_usd": 0.0042, "calls": [{"model": "m", "cost_usd": 0.0042}],
    })
    monkeypatch.setattr(web, "get_ledger", lambda: ledger)
    code, body = web._handle_api_costs(client_ip="10.9.9.3")
    assert code == 200
    data = json.loads(body)["data"]
    _assert_shape(
        data,
        {
            "total_cost_usd": (int, float),
            "by_model": dict,
            "by_model_detail": dict,
            "run_count": int,
            "runs": list,
        },
        context="/api/costs",
    )


def test_api_overview_matches_documented_shape(json_cache_backend):
    code, body = web._handle_api_overview(client_ip="10.9.9.4")
    assert code == 200
    data = json.loads(body)["data"]
    _assert_shape(data, {"coins": list}, context="/api/overview")


def test_api_overview_manip_score_present_and_absent_pass_through_not_defaulted(
    json_cache_backend,
):
    """spec 主打的「缺鍵＝未評估，不是零」鐵律，在這之前只用空陣列驗過形狀
    ——完全沒驗過 API 層真的 pass-through 這個語意（codex 複審建議）。這裡
    寫兩筆真快照：BTC 帶 `manip_score`/`manip_score_mean`（正例，值必須
    原封不動穿透，不能被改名/預設成別的數字）；ETH 完全不帶（負例，回應
    裡這兩個鍵必須整個不存在，不能悄悄補 `0`）。"""
    snap_with_manip = {
        "coin": "BTC",
        "trust_score": 0.71,
        "direction": "偏多",
        "calibrated_confidence": 0.6,
        "decision_state": "normal",
        "generated_at": "2026-07-01T00:00:00Z",
        "manip_score": 0.83,
        "manip_score_mean": 0.21,
    }
    snap_without_manip = {
        "coin": "ETH",
        "trust_score": 0.55,
        "direction": "中性",
        "calibrated_confidence": 0.45,
        "decision_state": "low_confidence",
        "generated_at": "2026-07-01T00:00:00Z",
    }
    for coin, snap in (("BTC", snap_with_manip), ("ETH", snap_without_manip)):
        result = cache_set_if_newer(
            json_cache_backend, cache_key(TRUST_SNAPSHOT_SOURCE, coin), [snap],
            fetched_at=1000.0, allow_json_fallback=True,
        )
        assert result.ok

    code, body = web._handle_api_overview(client_ip="10.9.9.41")
    assert code == 200
    coins = {c["coin"]: c for c in json.loads(body)["data"]["coins"]}

    btc = coins["BTC"]
    assert btc["manip_score"] == pytest.approx(0.83), "worst-case manip_score 未原封不動穿透"
    assert btc["manip_score_mean"] == pytest.approx(0.21), "manip_score_mean 未原封不動穿透"

    eth = coins["ETH"]
    assert "manip_score" not in eth, "ETH 本輪無操縱訊號，manip_score 不該悄悄補值"
    assert "manip_score_mean" not in eth, "ETH 本輪無操縱訊號，manip_score_mean 不該悄悄補值"


def test_api_history_matches_documented_shape(json_cache_backend):
    code, body = web._handle_api_history({"coin": ["BTC"], "days": ["7"]}, client_ip="10.9.9.5")
    assert code == 200
    data = json.loads(body)["data"]
    _assert_shape(data, {"coin": str, "days": int, "history": list}, context="/api/history")


def test_api_history_manip_score_present_and_absent_pass_through_not_defaulted(
    json_cache_backend,
):
    """`/api/overview` 那則測試的 `/api/history` 對照版——`HistoryEntry` 的
    `manip_score`/`manip_score_mean` 缺鍵語意也要在 API 層實測，不能只在
    `/api/overview` 驗一次就假設兩條路徑行為一致（`get_trust_history()`
    走的是不同的 cache key/讀取路徑，見 `HistoryEntry` schema 註解）。"""
    import datetime as _dt

    day = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    snap_with_manip = {
        "coin": "BTC",
        "trust_score": 0.71,
        "direction": "偏多",
        "calibrated_confidence": 0.6,
        "decision_state": "normal",
        "generated_at": f"{day}T00:00:00Z",
        "manip_score": 0.9,
        "manip_score_mean": 0.3,
    }
    result = cache_set_if_newer(
        json_cache_backend, trust_snapshot_history_key("BTC", day), [snap_with_manip],
        fetched_at=1000.0, allow_json_fallback=True,
    )
    assert result.ok

    snap_without_manip = {
        "coin": "ETH",
        "trust_score": 0.55,
        "direction": "中性",
        "calibrated_confidence": 0.45,
        "decision_state": "low_confidence",
        "generated_at": f"{day}T00:00:00Z",
    }
    result = cache_set_if_newer(
        json_cache_backend, trust_snapshot_history_key("ETH", day), [snap_without_manip],
        fetched_at=1000.0, allow_json_fallback=True,
    )
    assert result.ok

    code, body = web._handle_api_history({"coin": ["BTC"], "days": ["1"]}, client_ip="10.9.9.51")
    assert code == 200
    history = json.loads(body)["data"]["history"]
    assert len(history) == 1
    assert history[0]["manip_score"] == pytest.approx(0.9)
    assert history[0]["manip_score_mean"] == pytest.approx(0.3)

    code, body = web._handle_api_history({"coin": ["ETH"], "days": ["1"]}, client_ip="10.9.9.52")
    assert code == 200
    history = json.loads(body)["data"]["history"]
    assert len(history) == 1
    assert "manip_score" not in history[0], "ETH 本輪無操縱訊號，manip_score 不該悄悄補值"
    assert "manip_score_mean" not in history[0], "ETH 本輪無操縱訊號，manip_score_mean 不該悄悄補值"


def test_api_history_bad_coin_matches_documented_error_shape(json_cache_backend):
    code, body = web._handle_api_history({"coin": ["NOPE"], "days": ["7"]}, client_ip="10.9.9.6")
    assert code == 400
    parsed = json.loads(body)
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "bad_request"
    assert isinstance(parsed["error"]["message"], str)


def test_api_analyze_single_matches_documented_shape():
    """離線示範沙盒（`sample=1`）——不打真連接器/Bedrock，比照任務 credit-safe
    鐵律；驗證對象是 spec 裡的 `AnalyzeSingleData`/`Report`/`Evidence` 必要
    欄位。"""
    code, body = web._handle_api_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["ai-friendly-test"], "sample": ["1"]},
        client_ip="10.9.9.7",
    )
    assert code == 200
    data = json.loads(body)["data"]
    _assert_shape(
        data,
        {
            "version": str,
            "report": dict,
            "evidence": list,
            "trust_radar": dict,
            "trust_components_aggregate": dict,
            "price_provenance": dict,
            "execution_log": list,
        },
        context="/api/analyze(single).data",
    )
    _assert_shape(
        data["report"],
        {
            "coin": str,
            "question_type": str,
            "question": str,
            "market_judgment": str,
            "facts": list,
            "inferences": list,
            "key_basis": list,
            "confidence": (int, float),
            "limits": list,
            "could_flip": list,
            "contrarian": list,
            "generated_at": str,
            "direction": str,
            "calibrated_confidence": (int, float),
            "decision_state": str,
        },
        context="/api/analyze(single).data.report",
    )
    assert data["report"]["decision_state"] in ("abstain", "low_confidence", "normal")
    if data["evidence"]:
        _assert_shape(
            data["evidence"][0],
            {
                "source": str,
                "fetched_at": str,
                "content_reference": str,
                "related_claim": str,
                "source_url": str,
                "kind": str,
                "trust": (int, float),
                "trust_components": dict,
                "flags": list,
                "info_flags": list,
            },
            context="/api/analyze(single).data.evidence[0]",
        )


def test_api_analyze_comparison_matches_documented_shape():
    code, body = web._handle_api_analyze(
        {
            "coin": ["BTC,ETH"],
            "type": ["comparison"],
            "q": ["ai-friendly-cmp-test"],
            "sample": ["1"],
        },
        client_ip="10.9.9.8",
    )
    assert code == 200
    data = json.loads(body)["data"]
    _assert_shape(
        data,
        {
            "version": str,
            "report_a": dict,
            "evidence_a": list,
            "trust_radar_a": dict,
            "trust_components_aggregate_a": dict,
            "price_provenance_a": dict,
            "report_b": dict,
            "evidence_b": list,
            "trust_radar_b": dict,
            "trust_components_aggregate_b": dict,
            "price_provenance_b": dict,
            "execution_log": list,
        },
        context="/api/analyze(comparison).data",
    )
    for suffix in ("a", "b"):
        assert data[f"report_{suffix}"]["decision_state"] in ("abstain", "low_confidence", "normal")


def test_api_analyze_bad_request_matches_documented_error_shape():
    code, body = web._handle_api_analyze(
        {"coin": ["BTC"], "type": ["not_a_real_type"], "q": ["x"]}, client_ip="10.9.9.9"
    )
    assert code == 400
    parsed = json.loads(body)
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "bad_request"
    assert isinstance(parsed["error"]["message"], str)
