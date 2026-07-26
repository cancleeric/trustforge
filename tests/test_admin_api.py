"""admin console PR-2（計劃 §2/§3，🔴 安全相關）：`/api/admin/*` API + 認證測試。

涵蓋計劃 §9-2「認證繞過測試」（PR-2 驗收門檻）全清單：
無 header→401；錯 token→401；query `?admin_token=`→401（不接受）；
`X-Live-Token` 正確值打 admin→401；`TRUSTFORGE_ADMIN_TOKEN` 未設→404；
admin==live→視同未設；大小寫/前後空白變體→401；401 訊息不可區分原因；
失敗 N 次→429；以及 §9-3 cap 上下界、§9-5 CAS 409、GET 不洩 token。

⛔ 全程不打真 AWS：儲存層（PR-1，`tests/test_admin_config.py` 已獨立測過）
在本檔一律 monkeypatch `web.admin_config` 的模組函式（`get_config`/
`put_config`/`list_audit`）——本檔測的是 API 層（認證、驗證、狀態碼、
回應形狀），不是儲存層。

Handler 呼叫慣例比照 `tests/test_json_api.py::_do_get`（`Handler.__new__`
不開真 socket）。
"""
from __future__ import annotations

import json
import os
import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from email.message import Message
from io import BytesIO
from pathlib import Path

import pytest

from trustforge import web
from trustforge import hermes
from trustforge import analysis_flow
from trustforge.admin_config import (
    AdminConfig,
    AdminConfigReadError,
    AdminConfigWriteError,
    PutConfigResult,
    VersionConflictError,
    VersionCorruptError,
)

# ≥24 可見 ASCII（通過 live_token 長度規則的合法 admin token 樣本）
TEST_ADMIN_TOKEN = "unit-test-admin-token-0123456789abcdef"
TEST_LIVE_TOKEN = "unit-test-live-token-fedcba9876543210"


@pytest.fixture(autouse=True)
def _reset_admin_auth_buckets():
    """認證失敗 per-IP bucket + 全域 backstop 計數測試隔離（比照
    `test_json_api.py` 清 `_status_rate_buckets` 的慣例）。"""
    web._admin_auth_fail_buckets.clear()
    web._admin_auth_global_fails.clear()
    web._UPGRADE_PRINCIPAL_FACTORY = None
    yield
    web._admin_auth_fail_buckets.clear()
    web._admin_auth_global_fails.clear()
    web._UPGRADE_PRINCIPAL_FACTORY = None


@pytest.fixture
def admin_enabled(monkeypatch):
    """啟用管理面：直接設定模組常數（`ADMIN_TOKEN` 刻意是啟動期一次讀取，
    見 `web._compute_admin_token` docstring——測試比照 LIVE_TOKEN 慣例
    monkeypatch 模組常數，另有 `test_compute_admin_token_*` 直接測啟動期
    解析邏輯本身）。"""
    monkeypatch.setattr(web, "ADMIN_TOKEN", TEST_ADMIN_TOKEN)
    return TEST_ADMIN_TOKEN


@pytest.fixture
def admin_disabled(monkeypatch):
    monkeypatch.setattr(web, "ADMIN_TOKEN", "")


def _fake_config(**overrides) -> AdminConfig:
    defaults = dict(
        daily_cap_usd=1.0,
        bedrock_enabled=True,
        live_token_hash="a" * 64,
        live_token_last4="f2a9",
        version=7,
        updated_at="2026-07-07T03:00:00+00:00",
        updated_by="admin@203.0.113.5",
        exists=True,
        version_corrupt=False,
    )
    defaults.update(overrides)
    return AdminConfig(**defaults)


def _request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    headers: dict | None = None,
    body: str | bytes | None = None,
    content_type: str | None = "application/json",
    omit_content_length: bool = False,
    ip: str = "127.0.0.1",
) -> tuple[int, str, dict]:
    """端到端呼叫 `Handler.do_GET`/`do_PUT`（不開真 socket），回傳
    (status, body, response_headers)。"""
    h = web.Handler.__new__(web.Handler)
    h.client_address = (ip, 12345)
    h.path = path
    h.wfile = BytesIO()
    msg = Message()
    if token is not None:
        msg["X-Admin-Token"] = token
    for name, val in (headers or {}).items():
        msg[name] = val
    if body is not None:
        raw = body if isinstance(body, bytes) else body.encode("utf-8")
        if content_type is not None and "Content-Type" not in msg:
            msg["Content-Type"] = content_type
        if not omit_content_length and "Content-Length" not in msg:
            msg["Content-Length"] = str(len(raw))
        h.rfile = BytesIO(raw)
    h.headers = msg

    captured: list[int] = []
    sent_headers: dict = {}
    h.send_response = lambda code: captured.append(code)
    h.send_header = lambda name, val: sent_headers.__setitem__(name, val)
    h.end_headers = lambda: None

    if method == "GET":
        h.do_GET()
    elif method == "PUT":
        h.do_PUT()
    elif method == "POST":
        h.do_POST()
    else:
        raise AssertionError(f"unsupported method {method}")
    return captured[0], h.wfile.getvalue().decode("utf-8"), sent_headers


def _put_config(
    payload_json: str,
    *,
    token: str | None = TEST_ADMIN_TOKEN,
    ip: str = "127.0.0.1",
    **kw,
) -> tuple[int, str, dict]:
    return _request(
        "PUT", "/api/admin/config", token=token, body=payload_json, ip=ip, **kw
    )


# ---------------------------------------------------------------------------
# 啟動期 token 解析（未設全關 / admin==live 碰撞拒用）
# ---------------------------------------------------------------------------

def test_compute_admin_token_unset_is_disabled(monkeypatch):
    monkeypatch.delenv("TRUSTFORGE_ADMIN_TOKEN", raising=False)
    # PR-A Critical 修正後 `_compute_admin_token` 改吃 `live_bootstrap` 參數
    # （由呼叫端在模組層級「只讀一次」live-token bootstrap 值後傳入，見
    # web.py `_LIVE_TOKEN_BOOTSTRAP_RESOLVED` 的初始化順序），這裡比照該
    # 路徑：SSM 未設（本測試未動 TRUSTFORGE_TOKEN_SSM_PREFIX）→ 直接用
    # env `TRUSTFORGE_LIVE_TOKEN` 當 live_bootstrap。
    assert web._compute_admin_token(os.getenv("TRUSTFORGE_LIVE_TOKEN", "")) == ""


def test_compute_admin_token_empty_is_disabled(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_ADMIN_TOKEN", "")
    assert web._compute_admin_token(os.getenv("TRUSTFORGE_LIVE_TOKEN", "")) == ""


def test_compute_admin_token_collision_with_live_token_disables(monkeypatch, caplog):
    """§3.2-4：admin==live（非空且相等）→ ERROR log + 視同未設（全關）。"""
    monkeypatch.setenv("TRUSTFORGE_ADMIN_TOKEN", TEST_LIVE_TOKEN)
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", TEST_LIVE_TOKEN)
    with caplog.at_level("ERROR"):
        assert web._compute_admin_token(os.getenv("TRUSTFORGE_LIVE_TOKEN", "")) == ""
    assert any("TRUSTFORGE_ADMIN_TOKEN" in r.message for r in caplog.records)


def test_compute_admin_token_normal(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_ADMIN_TOKEN", TEST_ADMIN_TOKEN)
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", TEST_LIVE_TOKEN)
    assert web._compute_admin_token(os.getenv("TRUSTFORGE_LIVE_TOKEN", "")) == TEST_ADMIN_TOKEN


# ---------------------------------------------------------------------------
# fail-closed：未設定 → 管理面全關（§3.2-3、§9-2）
# ---------------------------------------------------------------------------

def test_admin_disabled_get_returns_generic_404(admin_disabled):
    """未設 → GET /api/admin/* 落到既有 404 頁，與任何不存在路徑
    byte-identical（連端點存在性都不暴露）。"""
    code_admin, body_admin, _ = _request("GET", "/api/admin/config")
    code_other, body_other, _ = _request("GET", "/no-such-path-xyz")
    assert code_admin == 404
    assert code_other == 404
    assert body_admin == body_other  # byte-identical，無法區分


def test_admin_disabled_get_404_even_with_any_token(admin_disabled):
    code, body, _ = _request("GET", "/api/admin/config", token="whatever-token-123456789")
    assert code == 404
    assert "找不到頁面" in body  # 一般 404 HTML 頁，不是 JSON 401


def test_admin_disabled_put_returns_same_405_as_any_path(admin_disabled):
    """未設 → PUT admin 路徑回與其他一切路徑相同的 405（PUT 對不存在路徑
    本來就是 405；特別回 404 反而突出成 oracle，見 do_PUT docstring）。"""
    code_admin, body_admin, _ = _put_config('{"daily_cap_usd": 1, "expected_version": 0}')
    code_other, body_other, _ = _request(
        "PUT", "/status", body='{"x": 1}'
    )
    assert code_admin == 405
    assert code_other == 405
    assert body_admin == body_other


def test_admin_disabled_audit_404(admin_disabled):
    code, _, _ = _request("GET", "/api/admin/audit")
    assert code == 404


# ---------------------------------------------------------------------------
# 認證（§3.2、§9-2 繞過測試全清單）
# ---------------------------------------------------------------------------

def _mock_get_config(monkeypatch, config=None):
    monkeypatch.setattr(
        web.admin_config, "get_config",
        lambda *a, **k: (config if config is not None else _fake_config()),
    )


def test_no_header_401(admin_enabled):
    code, body, headers = _request("GET", "/api/admin/config")
    assert code == 401
    parsed = json.loads(body)
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "unauthorized"
    # harper CISO L-2：`/api/admin/*` 一律 no-store，即使是認證失敗（401）
    # 的回應——瀏覽器/中介快取都不該留存管理面回應內容。
    assert headers.get("Cache-Control") == "no-store"


def test_cache_control_no_store_on_success_and_unknown_subpath(admin_enabled, monkeypatch):
    """harper CISO L-2：`_send()` 這個唯一出口對 `/api/admin/` 下的每條路徑
    （成功 200、未知子路徑 404）都補 `Cache-Control: no-store`，不管跑的是
    哪份 nginx conf——app 層是最後一道防線。"""
    _mock_get_config(monkeypatch)
    code, _, headers = _request("GET", "/api/admin/config", token=TEST_ADMIN_TOKEN)
    assert code == 200
    assert headers.get("Cache-Control") == "no-store"

    code, _, headers = _request("GET", "/api/admin/nope", token=TEST_ADMIN_TOKEN)
    assert code == 404
    assert headers.get("Cache-Control") == "no-store"


def test_cache_control_no_store_not_applied_outside_admin_path(admin_enabled, monkeypatch):
    """反向驗證：這個新加的 no-store 邏輯只認 `/api/admin/` 前綴，不該
    誤傷其他路徑（避免過度寬鬆的字串比對意外擴大範圍）。"""
    code, _, headers = _request("GET", "/healthz")
    assert code == 200
    assert headers.get("Cache-Control") != "no-store"


def test_wrong_token_401(admin_enabled):
    code, body, _ = _request("GET", "/api/admin/config", token="wrong-token-abcdefghijklmnop")
    assert code == 401


def test_401_message_indistinguishable(admin_enabled):
    """401 訊息不可區分「未帶」vs「帶錯」（不給 oracle）。"""
    _, body_missing, _ = _request("GET", "/api/admin/config")
    web._admin_auth_fail_buckets.clear()  # 隔離兩次失敗計數
    _, body_wrong, _ = _request("GET", "/api/admin/config", token="x" * 30)
    assert body_missing == body_wrong


def test_query_admin_token_not_accepted(admin_enabled):
    """token 走 query `?admin_token=` → 401（無 query fallback，token 不進
    URL/access log）。"""
    code, _, _ = _request("GET", f"/api/admin/config?admin_token={TEST_ADMIN_TOKEN}")
    assert code == 401


def test_live_token_header_cannot_open_admin(admin_enabled, monkeypatch):
    """`X-Live-Token` 正確值打 admin → 401（live token 權限層級不同）。"""
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", TEST_LIVE_TOKEN)
    code, _, _ = _request(
        "GET", "/api/admin/config", headers={"X-Live-Token": TEST_LIVE_TOKEN}
    )
    assert code == 401


def test_token_whitespace_and_case_variants_401(admin_enabled):
    """前後空白/大小寫變體一律 401（不做任何 normalize，精確比對）。"""
    for variant in (
        f" {TEST_ADMIN_TOKEN}",
        f"{TEST_ADMIN_TOKEN} ",
        f"  {TEST_ADMIN_TOKEN}  ",
        TEST_ADMIN_TOKEN.upper(),
    ):
        web._admin_auth_fail_buckets.clear()
        code, _, _ = _request("GET", "/api/admin/config", token=variant)
        assert code == 401, f"variant {variant!r} 不該通過認證"


def test_correct_token_passes(admin_enabled, monkeypatch):
    _mock_get_config(monkeypatch)
    code, body, _ = _request("GET", "/api/admin/config", token=TEST_ADMIN_TOKEN)
    assert code == 200
    assert json.loads(body)["ok"] is True


def test_auth_failure_lockout_429(admin_enabled, monkeypatch):
    """失敗 10 次 → 第 11 次 429（§3.2-6）。lockout-first：鎖定期間連正確
    token 也 429（否則限流對暴力猜測是裝飾性的——猜中那次仍會 200，見
    `_admin_auth_check` docstring）；其他 IP 不受影響。"""
    _mock_get_config(monkeypatch)
    for _ in range(web._ADMIN_AUTH_FAIL_MAX):
        code, _, _ = _request("GET", "/api/admin/config", token="bad-token-1234567890abcdef")
        assert code == 401
    # 超限：錯 token → 429
    code, body, _ = _request("GET", "/api/admin/config", token="bad-token-1234567890abcdef")
    assert code == 429
    assert json.loads(body)["error"]["code"] == "rate_limited"
    # lockout-first：正確 token 在鎖定期間同樣 429（不執行比對）
    code, _, _ = _request("GET", "/api/admin/config", token=TEST_ADMIN_TOKEN)
    assert code == 429
    # 別的 IP 不受影響
    code, _, _ = _request("GET", "/api/admin/config", token=TEST_ADMIN_TOKEN, ip="10.0.0.9")
    assert code == 200


def test_admin_auth_global_failure_backstop_429(admin_enabled, monkeypatch, caplog):
    """縱深防禦 backstop（PR #112 harper M1）：per-IP lockout 仰賴 nginx
    正確設定來源 IP；若攻擊者能偽造來源 IP 輪替（每個 IP 都壓在 per-IP
    門檻之下），個別 IP 永遠不會觸發 per-IP 429。這裡驗證全站（不分 IP）
    累計失敗數達門檻後，*任何*後續請求（含全新、從未失敗過的 IP，也含
    帶正確 token 的請求）一律 429，且有 ERROR 告警 log。
    此 backstop 是縱深、非主防線——主防線仍是 token 恆定時間比對。
    """
    _mock_get_config(monkeypatch)
    caplog.set_level("ERROR")
    per_ip_fail_budget = web._ADMIN_AUTH_FAIL_MAX - 1  # 每個 IP 都不觸發 per-IP lockout
    total_fails = 0
    ip_index = 0
    while total_fails < web._ADMIN_AUTH_GLOBAL_FAIL_MAX:
        ip = f"10.1.0.{ip_index}"
        ip_index += 1
        for _ in range(per_ip_fail_budget):
            if total_fails >= web._ADMIN_AUTH_GLOBAL_FAIL_MAX:
                break
            code, _, _ = _request(
                "GET", "/api/admin/config", token="bad-token-xxxxxxxxxxxxxxxxxxxx", ip=ip
            )
            assert code == 401
            total_fails += 1
    assert total_fails == web._ADMIN_AUTH_GLOBAL_FAIL_MAX
    # 第 100 次失敗當下就該觸發 ERROR 告警（不用等下一次請求才發現）
    assert any("全域失敗率異常" in r.getMessage() for r in caplog.records)

    # 全新、從未失敗過的 IP，帶「正確」token 一樣被擋——這正是全域
    # backstop 的重點：不分是誰、也不看 token 對不對，全站失敗率
    # 異常就先煞車。
    code, body, _ = _request(
        "GET", "/api/admin/config", token=TEST_ADMIN_TOKEN, ip="192.0.2.99"
    )
    assert code == 429
    assert json.loads(body)["error"]["code"] == "rate_limited"


def test_auth_failure_log_has_ip_but_never_token(admin_enabled, caplog):
    with caplog.at_level("WARNING"):
        _request("GET", "/api/admin/config", token="super-secret-wrong-token-xyz")
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "127.0.0.1" in joined
    assert "super-secret-wrong-token-xyz" not in joined


def test_put_requires_auth_too(admin_enabled):
    code, _, _ = _put_config('{"daily_cap_usd": 1, "expected_version": 0}', token=None)
    assert code == 401


def test_authed_unknown_admin_subpath_404_json(admin_enabled):
    code, body, _ = _request("GET", "/api/admin/nope", token=TEST_ADMIN_TOKEN)
    assert code == 404
    assert json.loads(body)["error"]["code"] == "not_found"


def test_constant_time_compare_uses_fixed_length_digest():
    """恆定時間比對（code review 驗收項的可執行版）：比對前兩邊都先
    sha256 成定長 32 bytes，`hmac.compare_digest` 不因輸入長度提早返回。"""
    assert len(web._hash_for_compare("x")) == 32
    assert len(web._hash_for_compare("y" * 500)) == 32


# ---------------------------------------------------------------------------
# GET /api/admin/config（§2.1）
# ---------------------------------------------------------------------------

def test_get_config_shape_and_layers(admin_enabled, monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "2.5")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "anthropic.claude-test")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", TEST_LIVE_TOKEN)
    _mock_get_config(monkeypatch)
    code, body, _ = _request("GET", "/api/admin/config", token=TEST_ADMIN_TOKEN)
    assert code == 200
    data = json.loads(body)["data"]
    # PR-3 回填 PR-2 預留的 effective/source 銜接點：config 層有值 →
    # 三個欄位皆 config 層生效（值取自分析路徑真的在用的同一批函式）
    assert data["daily_cap_usd"] == {
        "config": 1.0, "env": "2.5", "default": 3.0,
        "effective": 1.0, "source": "config",
    }
    assert data["bedrock_enabled"] == {
        "config": True, "bedrock_model_id_set": True,
        "effective": True, "source": "config",
    }
    assert data["live_token"] == {
        "config_configured": True, "config_last4": "f2a9", "env_configured": True,
        # runtime token SSM 讀取計劃 PR-A：未設 TRUSTFORGE_TOKEN_SSM_PREFIX → False
        "ssm_configured": False,
        "effective_configured": True, "source": "config",
    }
    assert data["version"] == 7
    assert data["updated_by"] == "admin@203.0.113.5"


def test_get_config_never_leaks_token_material(admin_enabled, monkeypatch):
    """GET 絕不回 live token 明文/完整 hash（env live token 也只回 bool）。"""
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", TEST_LIVE_TOKEN)
    _mock_get_config(monkeypatch, _fake_config(live_token_hash="deadbeef" * 8))
    code, body, _ = _request("GET", "/api/admin/config", token=TEST_ADMIN_TOKEN)
    assert code == 200
    assert "deadbeef" not in body  # config 層 hash 不外洩
    assert "live_token_hash" not in body
    assert TEST_LIVE_TOKEN not in body  # env 層明文不外洩


def test_get_config_empty_config_env_fallback_view(admin_enabled, monkeypatch):
    """item 不存在（舊部署）→ config 層全 null，env/default 照樣供對照。"""
    monkeypatch.delenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", raising=False)
    _mock_get_config(monkeypatch, AdminConfig())
    code, body, _ = _request("GET", "/api/admin/config", token=TEST_ADMIN_TOKEN)
    data = json.loads(body)["data"]
    assert code == 200
    # PR-3：config/env 皆未設 → effective 落 DEFAULT（$3）、source=default
    assert data["daily_cap_usd"] == {
        "config": None, "env": None, "default": 3.0,
        "effective": 3.0, "source": "default",
    }
    assert data["exists"] is False


def test_get_config_read_error_502(admin_enabled, monkeypatch):
    def boom(*a, **k):
        raise AdminConfigReadError("dynamodb down")
    monkeypatch.setattr(web.admin_config, "get_config", boom)
    code, body, _ = _request("GET", "/api/admin/config", token=TEST_ADMIN_TOKEN)
    assert code == 502
    assert json.loads(body)["error"]["code"] == "upstream_error"
    assert "dynamodb down" not in body  # 不透傳例外細節


# ---------------------------------------------------------------------------
# PUT /api/admin/config（§2.2）
# ---------------------------------------------------------------------------

@pytest.fixture
def put_recorder(monkeypatch):
    calls: dict = {}

    def fake_put(changes, expected_version, actor, *, user_agent=None, **kw):
        calls["changes"] = changes
        calls["expected_version"] = expected_version
        calls["actor"] = actor
        calls["user_agent"] = user_agent
        return PutConfigResult(
            config=_fake_config(version=expected_version + 1), audit_warning=None
        )

    monkeypatch.setattr(web.admin_config, "put_config", fake_put)
    return calls


def test_put_success(admin_enabled, put_recorder, monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "anthropic.claude-test")
    code, body, _ = _put_config('{"daily_cap_usd": 1.0, "expected_version": 7}')
    assert code == 200
    data = json.loads(body)["data"]
    assert data["version"] == 8
    assert data["warnings"] == []
    assert put_recorder["changes"] == {"daily_cap_usd": 1.0}
    assert put_recorder["expected_version"] == 7
    assert put_recorder["actor"] == "admin@127.0.0.1"


@pytest.mark.parametrize("cap_literal,expect", [
    ("0.09", 400),
    ("0.1", 200),
    ("1.0", 200),
    ("50", 200),
    ("50.01", 400),
    ("NaN", 400),        # json.loads 接受 NaN 字面值，isfinite 必須擋
    ("Infinity", 400),
    ("-1", 400),
    ("0", 400),           # 不允許 ≤0（緊急全關語意保留給 env 逃生口）
    ('"abc"', 400),
    ("true", 400),        # bool 是 int 子類，嚴格排除
    ("9" * 400, 400),     # PR #112 qa M1 回歸：巨大整數字面值不得讓
                           # float(cap) 拋未捕捉 OverflowError（曾導致
                           # do_PUT 整個炸掉、連線斷、拿不到乾淨的 400）
])
def test_put_cap_bounds(admin_enabled, put_recorder, cap_literal, expect):
    code, body, _ = _put_config(
        '{"daily_cap_usd": %s, "expected_version": 1}' % cap_literal
    )
    assert code == expect, f"cap={cap_literal} 應回 {expect}，實得 {code}: {body}"
    if expect == 400:
        assert json.loads(body)["error"]["code"] == "invalid_cap"
        assert "changes" not in put_recorder  # 驗證失敗不該碰儲存層


def test_put_cap_null_clears(admin_enabled, put_recorder):
    """null＝清除 config 層 cap（回落 env/DEFAULT），§9-3「刪 config 欄後
    回落 env」的 API 入口。"""
    code, _, _ = _put_config('{"daily_cap_usd": null, "expected_version": 3}')
    assert code == 200
    assert put_recorder["changes"] == {"daily_cap_usd": None}


def test_put_expected_version_required(admin_enabled, put_recorder):
    code, body, _ = _put_config('{"daily_cap_usd": 1.0}')
    assert code == 400
    assert "expected_version" in json.loads(body)["error"]["message"]
    assert "changes" not in put_recorder


@pytest.mark.parametrize("ev_literal", ["-1", "true", '"7"', "1.5", "null"])
def test_put_expected_version_must_be_nonneg_int(admin_enabled, put_recorder, ev_literal):
    code, body, _ = _put_config(
        '{"daily_cap_usd": 1.0, "expected_version": %s}' % ev_literal
    )
    assert code == 400
    assert json.loads(body)["error"]["code"] == "bad_request"  # qa LOW-4：不只驗 status
    assert "changes" not in put_recorder


def test_put_expected_version_zero_creates_fresh_config(admin_enabled, monkeypatch):
    """qa LOW-4 正向測試：`expected_version=0`（全新/空環境，item 尚不
    存在）經 API 層一路傳到 `admin_config.put_config()`，成功建立 config
    item——先前只間接用 `put_recorder` 驗過『0 不會被驗證擋掉』，沒有
    專門驗證 0 這個值本身會原樣送到儲存層（CAS「item_not_exists」語意
    的 API 入口）。"""
    calls: dict = {}

    def fake_put(changes, expected_version, actor, *, user_agent=None, **kw):
        calls["expected_version"] = expected_version
        calls["changes"] = changes
        return PutConfigResult(
            config=_fake_config(version=1, exists=True), audit_warning=None
        )

    monkeypatch.setattr(web.admin_config, "put_config", fake_put)
    code, body, _ = _put_config('{"daily_cap_usd": 1.0, "expected_version": 0}')
    assert code == 200
    data = json.loads(body)["data"]
    assert data["version"] == 1
    assert data["exists"] is True
    assert calls["expected_version"] == 0
    assert calls["changes"] == {"daily_cap_usd": 1.0}


def test_put_no_change_fields_400(admin_enabled, put_recorder):
    code, _, _ = _put_config('{"expected_version": 1}')
    assert code == 400
    assert "changes" not in put_recorder


def test_put_unknown_field_400(admin_enabled, put_recorder):
    code, _, _ = _put_config(
        '{"daily_cap_usd": 1.0, "expected_version": 1, "hack": true}'
    )
    assert code == 400
    assert "changes" not in put_recorder


def test_put_bedrock_enabled_strict_bool(admin_enabled, put_recorder):
    code, _, _ = _put_config('{"bedrock_enabled": "true", "expected_version": 1}')
    assert code == 400
    assert "changes" not in put_recorder


def test_put_bedrock_enabled_without_model_id_writes_with_warning(
    admin_enabled, put_recorder, monkeypatch
):
    """§2.2：true 但 BEDROCK_MODEL_ID 未設 → 仍寫入 + warning（誠實，不造假
    生效狀態）。"""
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    code, body, _ = _put_config('{"bedrock_enabled": true, "expected_version": 2}')
    assert code == 200
    data = json.loads(body)["data"]
    assert any("BEDROCK_MODEL_ID" in w for w in data["warnings"])
    assert put_recorder["changes"] == {"bedrock_enabled": True}  # 仍寫入


@pytest.mark.parametrize("token_literal,expect", [
    ('"%s"' % ("a" * 24), 200),
    ('"%s"' % ("a" * 23), 400),   # <24
    ('"short"', 400),
    ('"%s"' % ("a" * 513), 400),  # 超長
    ('"has space in the middle-1234567"', 400),  # 空白非可見 ASCII
    ('"含中文字的token-abcdefghijklmnop"', 400),  # 非 ASCII
    ("null", 200),                 # null＝清除 runtime token（回落 env）
    ("12345", 400),                # 非字串
])
def test_put_live_token_rules(admin_enabled, put_recorder, monkeypatch, token_literal, expect):
    # harper MEDIUM-1（issue #1）新增的傳輸層檢查跟本測試要驗的格式驗證
    # 規則是兩個不同關注點——這裡固定 TRUST_PROXY=True（模擬有 TLS 反代
    # 保護的安全部署），讓本測試只測格式驗證本身；傳輸層檢查本身另有專屬
    # 測試見 `test_put_live_token_rejected_when_trust_proxy_off` 等。
    monkeypatch.setattr(web, "TRUST_PROXY", True)
    code, body, _ = _put_config(
        '{"live_token": %s, "expected_version": 1}' % token_literal
    )
    assert code == expect, f"live_token={token_literal!r} 應回 {expect}: {body}"
    if expect == 400:
        # qa LOW-4：不只驗 status，也驗 error.code
        assert json.loads(body)["error"]["code"] == "bad_request"


def test_put_live_token_equal_to_admin_token_rejected(admin_enabled, put_recorder, monkeypatch):
    """PR-3：admin/live token 碰撞檢查延伸到 config 層——啟動期檢查只擋
    env live token，config 層若允許設成與 admin token 相同，等於讓分析
    token 與管理 token 合流（§3.2-4 同一條鐵律）→ 400。

    固定 TRUST_PROXY=True：本測試驗的是碰撞檢查，不是 harper MEDIUM-1 的
    傳輸層檢查（後者順序在碰撞檢查之前，TRUST_PROXY=False 會被那道檢查
    先擋下、蓋掉本測試想驗的 400 bad_request）。"""
    monkeypatch.setattr(web, "TRUST_PROXY", True)
    code, body, _ = _put_config(
        '{"live_token": "%s", "expected_version": 1}' % TEST_ADMIN_TOKEN
    )
    assert code == 400
    assert json.loads(body)["error"]["code"] == "bad_request"
    assert "changes" not in put_recorder  # 未寫入


# ── harper MEDIUM-1（issue #1 CISO High 複審）：admin 動態設 live_token 的
#    傳輸層 fail-closed 檢查 ────────────────────────────────────────────────


def test_put_live_token_rejected_when_trust_proxy_off(admin_enabled, put_recorder, monkeypatch):
    """TRUST_PROXY 未開（無法確認有 TLS 反代保護）時，動態設定 live_token
    一律拒絕（跟 main() 啟動期檢查同立場）。"""
    monkeypatch.setattr(web, "TRUST_PROXY", False)
    monkeypatch.delenv("TRUSTFORGE_ALLOW_INSECURE_LIVE_TOKEN", raising=False)
    code, body, _ = _put_config(
        '{"live_token": "%s", "expected_version": 1}' % ("a" * 24)
    )
    assert code == 403
    assert json.loads(body)["error"]["code"] == "insecure_transport"
    assert "changes" not in put_recorder  # 未寫入


def test_put_live_token_allowed_with_explicit_insecure_opt_out(admin_enabled, put_recorder, monkeypatch):
    """明確設 TRUSTFORGE_ALLOW_INSECURE_LIVE_TOKEN=1 opt-out 時放行（本機/
    離線 demo 用），跟 main() 啟動期檢查同一款 opt-out。"""
    monkeypatch.setattr(web, "TRUST_PROXY", False)
    monkeypatch.setenv("TRUSTFORGE_ALLOW_INSECURE_LIVE_TOKEN", "1")
    code, _body, _ = _put_config(
        '{"live_token": "%s", "expected_version": 1}' % ("a" * 24)
    )
    assert code == 200
    assert put_recorder["changes"] == {"live_token": "a" * 24}


def test_put_live_token_clear_not_blocked_when_trust_proxy_off(admin_enabled, put_recorder, monkeypatch):
    """清除 live_token（傳 null）不受本檢查影響——清除只會降低風險，不該被
    擋（本檢查只作用於「payload 真的要設定一個非 null 值」的情境）。"""
    monkeypatch.setattr(web, "TRUST_PROXY", False)
    monkeypatch.delenv("TRUSTFORGE_ALLOW_INSECURE_LIVE_TOKEN", raising=False)
    code, _body, _ = _put_config('{"live_token": null, "expected_version": 1}')
    assert code == 200
    assert put_recorder["changes"] == {"live_token": None}


def test_put_version_conflict_409_with_current_version(admin_enabled, monkeypatch):
    def fake_put(*a, **k):
        raise VersionConflictError(7)
    monkeypatch.setattr(web.admin_config, "put_config", fake_put)
    _mock_get_config(monkeypatch, _fake_config(version=9))
    code, body, _ = _put_config('{"daily_cap_usd": 1.0, "expected_version": 7}')
    assert code == 409
    parsed = json.loads(body)
    assert parsed["error"]["code"] == "version_conflict"
    assert parsed["error"]["current_version"] == 9


def test_put_version_conflict_409_reread_failure_current_version_null(
    admin_enabled, monkeypatch
):
    """qa LOW-4：409 時重讀最新 version 這件事本身也失敗（例如 DynamoDB
    暫時不可用）→ `current_version` 必須是 null，不能讓「附帶資訊讀不到」
    整個升級成 502（既有 `test_put_version_conflict_409_with_current_version`
    只測了重讀成功的分支，這裡補重讀失敗的分支）。"""
    def fake_put(*a, **k):
        raise VersionConflictError(7)

    def fake_get_config(*a, **k):
        raise AdminConfigReadError("dynamodb 暫時不可用")

    monkeypatch.setattr(web.admin_config, "put_config", fake_put)
    monkeypatch.setattr(web.admin_config, "get_config", fake_get_config)
    code, body, _ = _put_config('{"daily_cap_usd": 1.0, "expected_version": 7}')
    assert code == 409
    parsed = json.loads(body)
    assert parsed["error"]["code"] == "version_conflict"
    assert parsed["error"]["current_version"] is None


def test_put_version_corrupt_500_distinct_code(admin_enabled, monkeypatch):
    def fake_put(*a, **k):
        raise VersionCorruptError()
    monkeypatch.setattr(web.admin_config, "put_config", fake_put)
    code, body, _ = _put_config('{"daily_cap_usd": 1.0, "expected_version": 7}')
    assert code == 500
    assert json.loads(body)["error"]["code"] == "version_corrupt"


def test_put_write_error_502(admin_enabled, monkeypatch):
    def fake_put(*a, **k):
        raise AdminConfigWriteError("throttled")
    monkeypatch.setattr(web.admin_config, "put_config", fake_put)
    code, body, _ = _put_config('{"daily_cap_usd": 1.0, "expected_version": 7}')
    assert code == 502
    assert "throttled" not in body


def test_put_config_value_error_maps_to_400(admin_enabled, monkeypatch):
    """qa LOW-4：`admin_config.put_config` 拋裸 `ValueError`（儲存層防禦
    縱深驗證拒絕，理論上已被 API 層驗證擋光，但仍需正確映射）→ 400
    `bad_request`，不透傳內部訊息、不誤判成 502。"""
    def fake_put(*a, **k):
        raise ValueError("internal storage-layer validation detail should not leak")
    monkeypatch.setattr(web.admin_config, "put_config", fake_put)
    code, body, _ = _put_config('{"daily_cap_usd": 1.0, "expected_version": 7}')
    assert code == 400
    parsed = json.loads(body)
    assert parsed["error"]["code"] == "bad_request"
    assert "internal storage-layer validation detail" not in body


def test_put_audit_warning_propagated(admin_enabled, monkeypatch):
    def fake_put(changes, expected_version, actor, *, user_agent=None, **kw):
        return PutConfigResult(
            config=_fake_config(version=8),
            audit_warning="設定已寫入成功，但 DynamoDB 審計紀錄寫入失敗（journal 已留痕）",
        )
    monkeypatch.setattr(web.admin_config, "put_config", fake_put)
    code, body, _ = _put_config('{"daily_cap_usd": 1.0, "expected_version": 7}')
    assert code == 200
    assert any("審計" in w for w in json.loads(body)["data"]["warnings"])


def test_put_response_never_echoes_live_token_plaintext(admin_enabled, put_recorder, monkeypatch):
    """新 token 明文不回顯（比計劃「只出現一次」更保守：出現零次——呼叫端
    本來就持有明文）。固定 TRUST_PROXY=True：本測試驗的是回顯行為，不是
    harper MEDIUM-1 傳輸層檢查。"""
    monkeypatch.setattr(web, "TRUST_PROXY", True)
    plaintext = "brand-new-live-token-0123456789abcdef"
    code, body, _ = _put_config(
        '{"live_token": "%s", "expected_version": 1}' % plaintext
    )
    assert code == 200
    assert plaintext not in body


# ---------------------------------------------------------------------------
# PUT body 邊界（§2.2：4KB 上限、Content-Type、解析失敗）
# ---------------------------------------------------------------------------

def test_put_wrong_content_type_415(admin_enabled, put_recorder):
    code, _, _ = _put_config(
        '{"daily_cap_usd": 1.0, "expected_version": 1}', content_type="text/plain"
    )
    assert code == 415
    assert "changes" not in put_recorder


def test_put_content_type_with_charset_ok(admin_enabled, put_recorder):
    code, _, _ = _put_config(
        '{"daily_cap_usd": 1.0, "expected_version": 1}',
        content_type="application/json; charset=utf-8",
    )
    assert code == 200


def test_put_body_over_4kb_413(admin_enabled, put_recorder):
    huge = '{"daily_cap_usd": 1.0, "expected_version": 1, "pad": "%s"}' % ("x" * 5000)
    code, _, _ = _put_config(huge)
    assert code == 413
    assert "changes" not in put_recorder


def test_put_missing_content_length_411(admin_enabled, put_recorder):
    code, _, _ = _put_config(
        '{"daily_cap_usd": 1.0, "expected_version": 1}', omit_content_length=True
    )
    assert code == 411


def test_put_invalid_json_400(admin_enabled, put_recorder):
    code, _, _ = _put_config('{"daily_cap_usd": ')
    assert code == 400


def test_put_non_object_json_400(admin_enabled, put_recorder):
    code, _, _ = _put_config('[1, 2, 3]')
    assert code == 400


# ---------------------------------------------------------------------------
# PUT 路由分派（do_PUT：僅 /api/admin/config，其餘 405）
# ---------------------------------------------------------------------------

def test_put_non_admin_path_405(admin_enabled):
    code, body, headers = _request("PUT", "/api/status", body='{"x":1}')
    assert code == 405
    assert headers.get("Allow") == "GET"


def test_put_admin_audit_405_when_authed(admin_enabled):
    code, body, headers = _request(
        "PUT", "/api/admin/audit", token=TEST_ADMIN_TOKEN, body='{"x":1}'
    )
    assert code == 405
    assert headers.get("Allow") == "GET"


def test_put_unknown_admin_subpath_404_when_authed(admin_enabled):
    code, body, _ = _request(
        "PUT", "/api/admin/nope", token=TEST_ADMIN_TOKEN, body='{"x":1}'
    )
    assert code == 404


# ---------------------------------------------------------------------------
# GET /api/admin/audit（§2.3）
# ---------------------------------------------------------------------------

@pytest.fixture
def audit_recorder(monkeypatch):
    calls: dict = {}

    def fake_list(limit, **kw):
        calls["limit"] = limit
        return [{"ts": "2026-07-07T03:00:00+00:00", "actor": "admin@1.2.3.4",
                 "changes": [{"field": "daily_cap_usd", "old": 3.0, "new": 1.0}],
                 "version_from": 6, "version_to": 7, "user_agent": None}]

    monkeypatch.setattr(web.admin_config, "list_audit", fake_list)
    return calls


def test_audit_default_limit_50(admin_enabled, audit_recorder):
    code, body, _ = _request("GET", "/api/admin/audit", token=TEST_ADMIN_TOKEN)
    assert code == 200
    assert audit_recorder["limit"] == 50
    data = json.loads(body)["data"]
    assert data["limit"] == 50
    assert data["records"][0]["actor"] == "admin@1.2.3.4"


def test_audit_limit_param(admin_enabled, audit_recorder):
    code, _, _ = _request("GET", "/api/admin/audit?limit=3", token=TEST_ADMIN_TOKEN)
    assert code == 200
    assert audit_recorder["limit"] == 3


def test_audit_limit_clamped_to_200(admin_enabled, audit_recorder):
    code, _, _ = _request("GET", "/api/admin/audit?limit=99999", token=TEST_ADMIN_TOKEN)
    assert code == 200
    assert audit_recorder["limit"] == 200


@pytest.mark.parametrize("bad", ["abc", "0", "-5", "1.5", "9" * 40])
def test_audit_limit_invalid_400(admin_enabled, audit_recorder, bad):
    code, _, _ = _request(
        "GET", f"/api/admin/audit?limit={bad}", token=TEST_ADMIN_TOKEN
    )
    assert code == 400
    assert "limit" not in audit_recorder


def test_audit_requires_auth(admin_enabled, audit_recorder):
    code, _, _ = _request("GET", "/api/admin/audit")
    assert code == 401
    assert "limit" not in audit_recorder


def test_audit_read_error_502(admin_enabled, monkeypatch):
    def boom(limit, **kw):
        raise AdminConfigReadError("query failed")
    monkeypatch.setattr(web.admin_config, "list_audit", boom)
    code, body, _ = _request("GET", "/api/admin/audit", token=TEST_ADMIN_TOKEN)
    assert code == 502
    assert "query failed" not in body


def test_upgrade_gate_requires_auth_and_passed_sandbox(admin_enabled, monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTFORGE_SQLITE_PATH", str(tmp_path / "upgrades.sqlite3"))
    from trustforge.upgrade_ports import AuthenticatedPrincipal
    from trustforge.upgrade_adapters import SandboxAttestationAuthority
    from trustforge.upgrade_queue import UpgradeQueue
    sandbox_authority = SandboxAttestationAuthority(
        tmp_path / "admin-api-sandbox-capabilities.jsonl"
    )
    queue = UpgradeQueue(sandbox_verifier=sandbox_authority)
    queue.sync_diagnostic({"proposals": [{"id": "p", "area": "x", "severity": "high", "tenant_id": "t1"}]})
    binding = queue.resolve_review_instance("p")
    queue.record_reviews({"reviews": [{
        "proposal_id": binding["proposal_id"],
        "payload_sha256": binding["payload_sha256"],
        "verdict": "sandbox_ready",
    }]})
    proposal_id = binding["proposal_id"]
    body = json.dumps({"proposal_id": proposal_id, "decision": "approve", "actor": "qa", "reason": "green"})
    assert _request("POST", "/api/admin/hermes-upgrade-decision", body=body)[0] == 401
    code, response, _ = _request("POST", "/api/admin/hermes-upgrade-decision", token=TEST_ADMIN_TOKEN, body=body)
    assert code == 400
    assert json.loads(response)["error"]["code"] == "bad_request"
    trusted_body = json.dumps({"proposal_id": proposal_id, "decision": "approve", "reason": "green"})
    assert _request("POST", "/api/admin/hermes-upgrade-decision", token=TEST_ADMIN_TOKEN, body=trusted_body)[0] == 403
    principal = AuthenticatedPrincipal(
        "operator", "t1", frozenset({"upgrade:approve"}),
        datetime.now(timezone.utc) + timedelta(hours=1),
    )
    monkeypatch.setattr(web, "_UPGRADE_PRINCIPAL_FACTORY", lambda _headers: principal)
    details = {"candidate": {"family": "analysis", "revision": "abc"}, "tests": 24}
    checksum = hashlib.sha256(json.dumps(details, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    capability = sandbox_authority.issue(
        db_identity=str(queue.path.resolve(strict=False)),
        proposal_id=proposal_id,
        candidate_family="analysis",
        candidate_revision="abc",
        run_id="run-1",
        runner_version="runner/v1",
        artifact_hash="sha256:abc",
        details_checksum=checksum,
        passed=True,
        completed_at=datetime.now(timezone.utc),
        details=details,
    )
    queue.record_sandbox(capability)
    code, response, _ = _request("POST", "/api/admin/hermes-upgrade-decision", token=TEST_ADMIN_TOKEN, body=trusted_body)
    assert code == 200
    assert json.loads(response)["data"]["activated"] is False


def test_admin_upgrade_queue_get_is_authenticated(admin_enabled, monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTFORGE_SQLITE_PATH", str(tmp_path / "upgrades.sqlite3"))
    assert _request("GET", "/api/admin/hermes-upgrades")[0] == 401
    code, response, _ = _request("GET", "/api/admin/hermes-upgrades", token=TEST_ADMIN_TOKEN)
    assert code == 200
    assert json.loads(response)["data"]["durable"] is True


@pytest.mark.parametrize("body", [
    json.dumps({"capability_id": "a" * 64}),
    json.dumps({
        "proposal_id": "p", "passed": True,
        "artifact_hash": "sha256:attacker",
    }),
    json.dumps({"any": "otherwise-valid-payload"}),
])
def test_upgrade_sandbox_endpoint_is_stably_retired(
    admin_enabled, monkeypatch, tmp_path, body
):
    from trustforge.upgrade_adapters import SandboxAttestationAuthority
    from trustforge.upgrade_queue import UpgradeQueue

    queue_path = tmp_path / "queue.sqlite3"
    journal_path = tmp_path / "capabilities.jsonl"
    monkeypatch.setenv("TRUSTFORGE_SQLITE_PATH", str(queue_path))
    monkeypatch.setenv(
        "TRUSTFORGE_SANDBOX_CAPABILITY_JOURNAL", str(journal_path)
    )
    UpgradeQueue()
    journal_path.write_bytes(b"journal-sentinel\n")

    def five_tables():
        with sqlite3.connect(queue_path) as db:
            return tuple(
                tuple(db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall())
                for table in (
                    "upgrade_proposals", "upgrade_reviews",
                    "upgrade_sandbox_runs", "upgrade_decisions",
                    "upgrade_activations",
                )
            )

    before_tables = five_tables()
    before_journal = journal_path.read_bytes()
    code, response, _ = _request(
        "POST", "/api/admin/hermes-upgrade-sandbox",
        token=TEST_ADMIN_TOKEN, body=body,
    )
    assert code == 410
    assert json.loads(response)["error"]["code"] == "sandbox_endpoint_retired"
    assert "capability" not in response.lower()
    assert five_tables() == before_tables
    assert journal_path.read_bytes() == before_journal


def test_openapi_marks_sandbox_endpoint_deprecated_and_410_only():
    spec = (
        Path(__file__).resolve().parents[1] / "docs" / "api" / "openapi.yaml"
    ).read_text(encoding="utf-8")
    section = spec.split(
        "  /api/admin/hermes-upgrade-sandbox:", 1
    )[1].split("  /api/admin/hermes-upgrade-decision:", 1)[0]
    assert "deprecated: true" in section
    assert '"410":' in section
    assert "sandbox_endpoint_retired" in section
    assert "requestBody:" not in section
    assert '"200":' not in section


def test_upgrade_activation_endpoint_is_authenticated_and_explicit(admin_enabled, monkeypatch):
    from trustforge.upgrade_ports import AuthenticatedPrincipal
    from trustforge.upgrade_queue import UpgradeQueue

    called = {}
    principal = AuthenticatedPrincipal(
        "release-operator", "t1", frozenset({"upgrade:activate"}),
        datetime.now(timezone.utc) + timedelta(hours=1),
    )
    monkeypatch.setattr(web, "_UPGRADE_PRINCIPAL_FACTORY", lambda _headers: principal)
    def activate(self, proposal_id, reason, *, principal):
        called.update(proposal_id=proposal_id, actor=principal.subject, reason=reason)
        return {"proposal_id": proposal_id, "state": "activated", "revision": "abc"}
    monkeypatch.setattr(UpgradeQueue, "activate", activate)
    body = json.dumps({"proposal_id": "p", "reason": "reviewed"})
    assert _request("POST", "/api/admin/hermes-upgrade-activate", body=body)[0] == 401
    code, response, _ = _request("POST", "/api/admin/hermes-upgrade-activate", token=TEST_ADMIN_TOKEN, body=body)
    assert code == 200
    assert json.loads(response)["data"]["state"] == "activated"
    assert called == {"proposal_id": "p", "actor": "release-operator", "reason": "reviewed"}


def test_manual_analysis_question_is_allowed_when_hermes_autonomy_disabled(monkeypatch):
    monkeypatch.setattr(hermes, "autonomy_enabled", lambda: (False, "config"))
    monkeypatch.setattr(web, "_check_status_rate_limit", lambda *args, **kwargs: None)
    class Flow:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def submit_manual(self, *_args, **_kwargs): return "question-1", "flow-1"
    monkeypatch.setattr(analysis_flow, "AnalysisFlow", Flow)
    code, body, _ = _request(
        "POST",
        "/api/analysis-question",
        body=json.dumps({"coin": "BTC", "mode": "risk", "question": "test"}),
    )
    payload = json.loads(body)
    assert code == 202
    assert payload["data"] == {"question_id": "question-1", "job_id": "flow-1", "state": "queued", "origin": "manual"}
