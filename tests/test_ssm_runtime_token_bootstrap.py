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
    """SSM admin-token 有值 → 蓋過 env TRUSTFORGE_ADMIN_TOKEN（即使 env 也設了不同值）。"""
    monkeypatch.setenv("TRUSTFORGE_ADMIN_TOKEN", ENV_ADMIN_TOKEN)
    monkeypatch.setattr(
        ssm_params,
        "get_runtime_token",
        lambda name: SSM_ADMIN_TOKEN if name == "admin-token" else None,
    )
    assert web._compute_admin_token() == SSM_ADMIN_TOKEN


def test_compute_admin_token_ssm_none_uses_env(monkeypatch):
    """SSM 回 None（未設旗標/讀取失敗）→ 落 env，與既有行為相同。"""
    monkeypatch.setenv("TRUSTFORGE_ADMIN_TOKEN", ENV_ADMIN_TOKEN)
    monkeypatch.setattr(ssm_params, "get_runtime_token", lambda name: None)
    assert web._compute_admin_token() == ENV_ADMIN_TOKEN


# ── 3. 碰撞矩陣 ──────────────────────────────────────────────────────

def test_compute_admin_token_collision_ssm_admin_eq_ssm_live(monkeypatch, caplog):
    """SSM admin-token == SSM live-token → 偵測碰撞回傳空字串，不洩漏明文。"""
    same_token = "colliding-token-ffffffffffffffffffff"
    monkeypatch.setattr(
        ssm_params,
        "get_runtime_token",
        lambda name: same_token if name in ("admin-token", "live-token") else None,
    )
    with caplog.at_level("ERROR"):
        assert web._compute_admin_token() == ""
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
    with caplog.at_level("ERROR"):
        assert web._compute_admin_token() == ""
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
