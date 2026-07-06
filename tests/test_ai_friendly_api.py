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

import json
from email.message import Message
from io import BytesIO
from pathlib import Path

import pytest

from trustforge import web

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OPENAPI_PATH = _REPO_ROOT / "docs" / "api" / "openapi.yaml"
_LLMS_TXT_ROOT_PATH = _REPO_ROOT / "llms.txt"
_LLMS_TXT_FRONTEND_PATH = _REPO_ROOT / "frontend" / "public" / "llms.txt"

_OVERCLAIM_TERMS = ("保證", "準確率")


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
    """spec 必須逐一列出 `do_GET` 實際會處理的每個 `/api/*` 路徑——防止漏寫
    掉某個端點（code 是 source of truth，不是反過來拿 spec 去 codegen）。"""
    text = _OPENAPI_PATH.read_text(encoding="utf-8")
    for path in (
        "/api/health",
        "/api/status",
        "/api/costs",
        "/api/overview",
        "/api/history",
        "/api/analyze",
        "/api/openapi.yaml",
    ):
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


def test_api_history_matches_documented_shape(json_cache_backend):
    code, body = web._handle_api_history({"coin": ["BTC"], "days": ["7"]}, client_ip="10.9.9.5")
    assert code == 200
    data = json.loads(body)["data"]
    _assert_shape(data, {"coin": str, "days": int, "history": list}, context="/api/history")


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
