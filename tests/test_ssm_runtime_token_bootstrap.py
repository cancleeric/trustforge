"""SSM runtime token bootstrap (PR-A) 回歸測試。

涵蓋 web.py 啟動期 admin token 與 live token 從 trustforge.ssm_params
模組讀取的新邏輯（SSM 優先、env 退回），包含：

- _resolve_bootstrap_token：SSM 提供值時直接採用，否則退回 env
- _compute_admin_token：SSM admin-token 蓋過 env；碰撞偵測（SSM×SSM 與
  SSM×env 兩種情境）皆 fail-closed 且不洩漏 token 明文
- _live_token_matches / _live_token_resolved：config / SSM / env 三層優先序
  與 config 讀取異常時的 fail-closed 行為
- 熱路徑鐵則：啟動期凍結的 _LIVE_TOKEN_SSM_BOOTSTRAP 常數決定後，
  後續 live token 比對完全不應再呼叫 ssm_params.get_runtime_token

本檔案屬於 runtime token SSM 讀取計劃 PR-A 的回歸測試；全程使用
monkeypatch 模擬，不打真 AWS（ssm_params 本身另有獨立測試檔涵蓋
boto3 layer）。
"""

from trustforge import admin_config, ssm_params, web
from trustforge.admin_config import AdminConfig, AdminConfigReadError, hash_live_token

SSM_ADMIN_TOKEN = "ssm-admin-token-aaaaaaaaaaaaaaaa"
SSM_LIVE_TOKEN = "ssm-live-token-bbbbbbbbbbbbbbbb"
ENV_ADMIN_TOKEN = "env-admin-token-cccccccccccccccc"
ENV_LIVE_TOKEN = "env-live-token-dddddddddddddddd"
CONFIG_LIVE_TOKEN = "config-live-token-eeeeeeeeeeeeeeee"


def _no_live_config() -> AdminConfig:
    """live_token_hash 未設定的 AdminConfig（config 層完全未提供 live token）。"""
    return AdminConfig(
        daily_cap_usd=None,
        bedrock_enabled=None,
        live_token_hash=None,
        live_token_last4=None,
        version=0,
        updated_at=None,
        updated_by=None,
        exists=False,
        version_corrupt=False,
    )


def _config_with_live_hash(token: str) -> AdminConfig:
    """live_token_hash 已設定的 AdminConfig（config 層已提供 live token）。"""
    return AdminConfig(
        daily_cap_usd=None,
        bedrock_enabled=None,
        live_token_hash=hash_live_token(token),
        live_token_last4=token[-4:],
        version=1,
        updated_at=None,
        updated_by=None,
        exists=True,
        version_corrupt=False,
    )


# ── 1. _resolve_bootstrap_token ───────────────────────────────────────

def test_resolve_bootstrap_token_ssm_value_takes_precedence(monkeypatch):
    """SSM 回傳非 None 字串 → 直接採用，不管 env 有沒有設。"""
    monkeypatch.setenv("TRUSTFORGE_ADMIN_TOKEN", ENV_ADMIN_TOKEN)
    monkeypatch.setattr(
        ssm_params,
        "get_runtime_token",
        lambda name: SSM_ADMIN_TOKEN if name == "admin-token" else None,
    )
    assert web._resolve_bootstrap_token("admin-token", "TRUSTFORGE_ADMIN_TOKEN") == SSM_ADMIN_TOKEN


def test_resolve_bootstrap_token_ssm_none_falls_to_env(monkeypatch):
    """SSM 回 None → 退回 env（env 有設 → env 值）。"""
    monkeypatch.setenv("TRUSTFORGE_ADMIN_TOKEN", ENV_ADMIN_TOKEN)
    monkeypatch.setattr(ssm_params, "get_runtime_token", lambda name: None)
    assert web._resolve_bootstrap_token("admin-token", "TRUSTFORGE_ADMIN_TOKEN") == ENV_ADMIN_TOKEN


def test_resolve_bootstrap_token_ssm_none_env_unset_returns_empty(monkeypatch):
    """SSM 回 None 且 env 也沒設 → 空字串。"""
    monkeypatch.delenv("TRUSTFORGE_ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(ssm_params, "get_runtime_token", lambda name: None)
    assert web._resolve_bootstrap_token("admin-token", "TRUSTFORGE_ADMIN_TOKEN") == ""


# ── 2. _compute_admin_token ───────────────────────────────────────────

def test_compute_admin_token_ssm_overrides_env(monkeypatch):
    """SSM admin-token 有值 → 蓋過 env TRUSTFORGE_ADMIN_TOKEN（即使 env 也設了不同值）。

    PR-A Critical 修正後 `_compute_admin_token` 改吃 `live_bootstrap`
    參數（呼叫端只讀一次 live-token bootstrap 值後傳入，不再由本函式
    內部自己再打一次 SSM）——測試比照這個呼叫慣例，用
    `_resolve_bootstrap_token("live-token", ...)` 算出同一份值再傳入。
    """
    monkeypatch.setenv("TRUSTFORGE_ADMIN_TOKEN", ENV_ADMIN_TOKEN)
    monkeypatch.setattr(
        ssm_params,
        "get_runtime_token",
        lambda name: SSM_ADMIN_TOKEN if name == "admin-token" else None,
    )
    live_bootstrap = web._resolve_bootstrap_token("live-token", "TRUSTFORGE_LIVE_TOKEN")
    assert web._compute_admin_token(live_bootstrap) == SSM_ADMIN_TOKEN


def test_compute_admin_token_ssm_none_uses_env(monkeypatch):
    """SSM 回 None（未設旗標/讀取失敗）→ 落 env，與既有行為相同。"""
    monkeypatch.setenv("TRUSTFORGE_ADMIN_TOKEN", ENV_ADMIN_TOKEN)
    monkeypatch.setattr(ssm_params, "get_runtime_token", lambda name: None)
    live_bootstrap = web._resolve_bootstrap_token("live-token", "TRUSTFORGE_LIVE_TOKEN")
    assert web._compute_admin_token(live_bootstrap) == ENV_ADMIN_TOKEN


# ── 3. 碰撞矩陣 ──────────────────────────────────────────────────────

def test_compute_admin_token_collision_ssm_admin_eq_ssm_live(monkeypatch, caplog):
    """SSM admin-token == SSM live-token → 偵測碰撞回傳空字串，不洩漏明文。"""
    same_token = "colliding-token-ffffffffffffffffffff"
    monkeypatch.setattr(
        ssm_params,
        "get_runtime_token",
        lambda name: same_token if name in ("admin-token", "live-token") else None,
    )
    live_bootstrap = web._resolve_bootstrap_token("live-token", "TRUSTFORGE_LIVE_TOKEN")
    with caplog.at_level("ERROR"):
        assert web._compute_admin_token(live_bootstrap) == ""
    assert any("TRUSTFORGE_ADMIN_TOKEN" in r.message for r in caplog.records)
    assert not any(same_token in r.message for r in caplog.records)


def test_compute_admin_token_collision_ssm_admin_eq_env_live(monkeypatch, caplog):
    """SSM admin-token == env TRUSTFORGE_LIVE_TOKEN（SSM live-token 為 None）→ 偵測碰撞。"""
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", SSM_ADMIN_TOKEN)
    monkeypatch.setattr(
        ssm_params,
        "get_runtime_token",
        lambda name: SSM_ADMIN_TOKEN if name == "admin-token" else None,
    )
    live_bootstrap = web._resolve_bootstrap_token("live-token", "TRUSTFORGE_LIVE_TOKEN")
    with caplog.at_level("ERROR"):
        assert web._compute_admin_token(live_bootstrap) == ""
    assert any("TRUSTFORGE_ADMIN_TOKEN" in r.message for r in caplog.records)
    assert not any(SSM_ADMIN_TOKEN in r.message for r in caplog.records)


# ── 4. _live_token_matches / _live_token_resolved 三層優先序 ────────────

def test_live_token_config_layer_wins_over_ssm_and_env(monkeypatch):
    """config 層 live_token_hash 有設 → 一律用 config，不管 SSM bootstrap 值或 env。"""
    config = _config_with_live_hash(CONFIG_LIVE_TOKEN)
    monkeypatch.setattr(admin_config, "get_config", lambda *a, **k: config)
    monkeypatch.setattr(web, "_LIVE_TOKEN_SSM_BOOTSTRAP", SSM_LIVE_TOKEN)
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", ENV_LIVE_TOKEN)

    assert web._live_token_matches(CONFIG_LIVE_TOKEN) is True
    assert web._live_token_matches(SSM_LIVE_TOKEN) is False
    assert web._live_token_matches(ENV_LIVE_TOKEN) is False
    assert web._live_token_resolved() == (True, "config")


def test_live_token_ssm_layer_when_config_unset(monkeypatch):
    """config 未設 + _LIVE_TOKEN_SSM_BOOTSTRAP 有值 → 用 SSM 值比對。"""
    config = _no_live_config()
    monkeypatch.setattr(admin_config, "get_config", lambda *a, **k: config)
    monkeypatch.setattr(web, "_LIVE_TOKEN_SSM_BOOTSTRAP", SSM_LIVE_TOKEN)
    monkeypatch.delenv("TRUSTFORGE_LIVE_TOKEN", raising=False)

    assert web._live_token_matches(SSM_LIVE_TOKEN) is True
    assert web._live_token_matches("wrong-token-1234567890") is False
    assert web._live_token_resolved() == (True, "ssm")


def test_live_token_env_layer_when_config_and_ssm_unset(monkeypatch):
    """config 未設 + SSM bootstrap 為 None + env 有設 → 落 env（沿用舊行為）。"""
    config = _no_live_config()
    monkeypatch.setattr(admin_config, "get_config", lambda *a, **k: config)
    monkeypatch.setattr(web, "_LIVE_TOKEN_SSM_BOOTSTRAP", None)
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", ENV_LIVE_TOKEN)

    assert web._live_token_matches(ENV_LIVE_TOKEN) is True
    assert web._live_token_matches("wrong-token-1234567890") is False
    assert web._live_token_resolved() == (True, "env")


def test_live_token_none_when_all_layers_unset(monkeypatch):
    """三層都未設 → _live_token_matches 一律 False，resolved 回 (False, "none")。"""
    config = _no_live_config()
    monkeypatch.setattr(admin_config, "get_config", lambda *a, **k: config)
    monkeypatch.setattr(web, "_LIVE_TOKEN_SSM_BOOTSTRAP", None)
    monkeypatch.delenv("TRUSTFORGE_LIVE_TOKEN", raising=False)

    assert web._live_token_matches(ENV_LIVE_TOKEN) is False
    assert web._live_token_matches("anything-1234567890ab") is False
    assert web._live_token_matches("") is False
    assert web._live_token_resolved() == (False, "none")


def test_live_token_config_read_error_fail_closed(monkeypatch):
    """config 讀取異常（AdminConfigReadError）→ fail-closed，不管 SSM bootstrap 值。"""

    def _raise(*a, **k):
        raise AdminConfigReadError("boom")

    monkeypatch.setattr(admin_config, "get_config", _raise)
    monkeypatch.setattr(web, "_LIVE_TOKEN_SSM_BOOTSTRAP", SSM_LIVE_TOKEN)

    assert web._live_token_matches(SSM_LIVE_TOKEN) is False
    assert web._live_token_matches("anything-1234567890ab") is False
    assert web._live_token_resolved() == (False, "config_read_error")


# ── 5. 熱路徑零 SSM 呼叫鐵則 ──────────────────────────────────────────

def test_live_token_hot_path_never_calls_ssm(monkeypatch):
    """_LIVE_TOKEN_SSM_BOOTSTRAP 凍結後，熱路徑連續呼叫 20 次不應觸發 ssm_params.get_runtime_token。"""
    config = _no_live_config()
    monkeypatch.setattr(admin_config, "get_config", lambda *a, **k: config)
    monkeypatch.setattr(web, "_LIVE_TOKEN_SSM_BOOTSTRAP", SSM_LIVE_TOKEN)
    monkeypatch.setattr(
        ssm_params,
        "get_runtime_token",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("熱路徑不該呼叫 SSM")),
    )

    for _ in range(20):
        assert web._live_token_matches(SSM_LIVE_TOKEN) is True
        assert web._live_token_matches("wrong-token-1234567890") is False
        assert web._live_token_resolved() == (True, "ssm")


# ── 6. 模組載入時 live-token SSM 恰讀一次：避免雙讀分岔造成 admin/live token 靜默碰撞 ──


def test_module_init_reads_live_token_ssm_exactly_once_no_split_brain(monkeypatch):
    import importlib

    live_token_call_count = {"count": 0}

    def fake_get_runtime_token(name):
        if name == "admin-token":
            return None
        if name == "live-token":
            live_token_call_count["count"] += 1
            if live_token_call_count["count"] == 1:
                return "seq-first-read-1111111111111111"
            return "seq-second-read-2222222222222222"
        raise AssertionError(f"未預期的 get_runtime_token 呼叫：name={name!r}")

    monkeypatch.setattr(ssm_params, "get_runtime_token", fake_get_runtime_token)
    monkeypatch.setenv("TRUSTFORGE_ADMIN_TOKEN", "seq-second-read-2222222222222222")
    monkeypatch.delenv("TRUSTFORGE_LIVE_TOKEN", raising=False)

    try:
        importlib.reload(web)

        assert live_token_call_count["count"] == 1, (
            f"live-token 在模組載入時應該恰好被呼叫 1 次（修正後單次讀取保證同源），"
            f"實際被呼叫了 {live_token_call_count['count']} 次——若 >1 代表舊版雙讀分岔 bug 回歸"
        )
        assert web._LIVE_TOKEN_SSM_BOOTSTRAP == "seq-first-read-1111111111111111", (
            "_LIVE_TOKEN_SSM_BOOTSTRAP 必須等於唯一一次讀取到的值（第一次讀到的值），"
            f"實際為 {web._LIVE_TOKEN_SSM_BOOTSTRAP!r}"
        )
        assert web.ADMIN_TOKEN == "seq-second-read-2222222222222222", (
            "ADMIN_TOKEN 應正常生效：碰撞檢查比對的對象是唯一一次讀到的第一次值，"
            "與 admin token（= 第二次讀取的值）不相等，故不誤判碰撞；"
            f"實際 ADMIN_TOKEN={web.ADMIN_TOKEN!r}"
        )
        assert web._live_token_matches("seq-second-read-2222222222222222") is False, (
            "關鍵安全斷言：admin token 的值（= 第二次讀取的值）不得同時被視為有效 live token。"
            "若此斷言失敗，代表 _LIVE_TOKEN_SSM_BOOTSTRAP 被第二次讀取的值污染（雙讀分岔），"
            "admin token 意外通過 live token 驗證——即舊版 Critical 漏洞回歸"
        )
    finally:
        monkeypatch.undo()
        monkeypatch.delenv("TRUSTFORGE_ADMIN_TOKEN", raising=False)
        monkeypatch.delenv("TRUSTFORGE_LIVE_TOKEN", raising=False)
        importlib.reload(web)
