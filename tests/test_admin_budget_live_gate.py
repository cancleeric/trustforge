"""admin console PR-3（計劃 §5，🔴 安全相關）：計費 cap 讀取路徑三層化 +
live 閘動態化測試。

涵蓋驗收清單（CEO 親測項的可執行版）：

- cap 三層 fallback（`budget_guard.daily_cap_usd[_resolved]`）：
  config 設 1.0 → 蓋過 env；config 無 → 落 env；env 無 → DEFAULT $3；
  env cap≤0 緊急全關語意逐字保留；config 讀取異常 → 落 env 不炸。
- live 閘（`web._bedrock_allowed`）：config `bedrock_enabled` AND env
  `BEDROCK_MODEL_ID` 雙 fail-closed；config 未設定過 → 只看 env（v0.7.0
  逐字相同）；讀取異常 → fail-closed。
- live token（`web._live_token_matches`）：config hash 有設以它為準
  （env 舊 token 立即失效）；config 未設 → 落 env；皆未設 → False。
- **緊急關閉主路徑立即生效**：`put_config(bedrock_enabled=False)` 的
  write-through 讓同 process 的 `_bedrock_allowed()`/`_is_live_request()`
  **下一次呼叫立刻**看到 False，不等 15s TTL。
- 執行緒安全鐵則：config 讀（`get_config_cached`）發生在
  `BudgetReservation._lock` 之外。
- schema 相容固定驗收：無 config item → 行為與 v0.7.0 逐字相同。
- 雙審合批修（PR #114 複審）：env cap≤0 kill-switch 最高優先凌駕 config
  （harper M2）；config `bedrock_enabled=false` 統一蓋住 online-stance
  側路（harper M1）；budget 路徑 15s 失敗負快取（qa M1）。

⛔ 全程不打真 AWS：一律 monkeypatch `admin_config.get_config` 或注入
mock `_table` 的自建 store（PR-1 慣例）；conftest 的
`_isolate_admin_config_store` 已把預設 store 換成「item 不存在」假表。
"""
from __future__ import annotations

import pytest

from trustforge import admin_config, budget_guard, web
from trustforge.admin_config import (
    AdminConfig,
    AdminConfigReadError,
    AdminConfigStore,
    hash_live_token,
    put_config,
)

# ≥16（`_SHORT_TOKEN_LAST4_THRESHOLD`）且 ≥24（API 下限）的合法樣本 token
CONFIG_TOKEN = "config-live-token-0123456789abcdef"
ENV_TOKEN = "env-live-token-fedcba9876543210"


def _mock_config(monkeypatch, config: AdminConfig) -> None:
    """讓 config 層讀到指定快照（`get_config_cached` 透過模組全域呼叫
    `get_config`，monkeypatch 後即生效；conftest 每測前後清 TTL 快取）。"""
    monkeypatch.setattr(admin_config, "get_config", lambda *a, **k: config)


def _mock_config_read_error(monkeypatch) -> None:
    def boom(*a, **k):
        raise AdminConfigReadError("dynamodb down")

    monkeypatch.setattr(admin_config, "get_config", boom)


# ---------------------------------------------------------------------------
# 1. cap 三層 fallback：config → env → DEFAULT($3)
# ---------------------------------------------------------------------------
def test_cap_config_layer_overrides_env(monkeypatch):
    """config 設 1.0 → 蓋過 env 2.5（驗收固定項）。"""
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "2.5")
    _mock_config(monkeypatch, AdminConfig(daily_cap_usd=1.0, exists=True))
    assert budget_guard.daily_cap_usd() == 1.0
    assert budget_guard.daily_cap_usd_resolved() == (1.0, "config")


def test_cap_config_unset_falls_to_env(monkeypatch):
    """config 無（item 不存在/欄位未設）→ 落 env（既有語意）。"""
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "2.5")
    _mock_config(monkeypatch, AdminConfig())
    assert budget_guard.daily_cap_usd_resolved() == (2.5, "env")


def test_cap_config_and_env_unset_falls_to_default(monkeypatch):
    monkeypatch.delenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", raising=False)
    _mock_config(monkeypatch, AdminConfig())
    assert budget_guard.daily_cap_usd_resolved() == (3.0, "default")


@pytest.mark.parametrize("bad_env", ["", "  ", "abc", "nan", "inf", "-inf"])
def test_cap_bad_env_falls_to_default(monkeypatch, bad_env):
    """env 層既有壞值容錯逐字保留：空/不可解析/非有限 → DEFAULT。"""
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", bad_env)
    _mock_config(monkeypatch, AdminConfig())
    assert budget_guard.daily_cap_usd_resolved() == (
        budget_guard.DEFAULT_BEDROCK_DAILY_USD_CAP, "default",
    )


@pytest.mark.parametrize("env_val", ["0", "-1"])
def test_cap_env_zero_or_negative_emergency_off_preserved(monkeypatch, env_val):
    """env cap≤0＝緊急全關語意逐字保留（config 未設時）：值原樣生效、
    `daily_cap_exceeded` 直接 True、`try_reserve` 拒絕。"""
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", env_val)
    _mock_config(monkeypatch, AdminConfig())
    val, source = budget_guard.daily_cap_usd_resolved()
    assert (val, source) == (float(env_val), "env")
    assert budget_guard.daily_cap_exceeded() is True
    assert budget_guard.try_reserve_request_budget() is None


def test_cap_config_read_error_falls_to_env_no_crash(monkeypatch, caplog):
    """config **讀取異常**（非「未設定」）→ log warning + 落 env 層，
    分析路徑不因管理面故障掛掉（驗收固定項）。"""
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "2.5")
    _mock_config_read_error(monkeypatch)
    with caplog.at_level("WARNING"):
        assert budget_guard.daily_cap_usd_resolved() == (2.5, "env")
    assert any("admin config 讀取失敗" in r.getMessage() for r in caplog.records)


def test_cap_config_read_error_env_unset_falls_to_default(monkeypatch):
    monkeypatch.delenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", raising=False)
    _mock_config_read_error(monkeypatch)
    assert budget_guard.daily_cap_usd_resolved() == (3.0, "default")


def test_cap_try_reserve_respects_config_layer(monkeypatch):
    """`try_reserve` 用的是三層解析後的 cap：config 層 cap 小於單次保守
    上界（$0.05）→ 直接拒絕預留。"""
    monkeypatch.delenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", raising=False)
    monkeypatch.delenv("TRUSTFORGE_BEDROCK_REQUEST_MAX_USD", raising=False)
    _mock_config(monkeypatch, AdminConfig(daily_cap_usd=0.01, exists=True))
    from trustforge.ledger import JsonlLedger

    ledger = JsonlLedger.__new__(JsonlLedger)
    ledger.read_all = lambda: []  # 今日已花費 $0
    assert budget_guard.try_reserve_request_budget(ledger) is None
    # config 層 cap 夠大 → 放行
    _mock_config(monkeypatch, AdminConfig(daily_cap_usd=1.0, exists=True))
    admin_config._reset_admin_config_cache_for_tests()  # 換 config 後清快取
    amount = budget_guard.try_reserve_request_budget(ledger)
    # 預留金額 = 真實 worst-case 預估（D2.5/#76，取代固定 $0.05 上界）
    assert amount == budget_guard.request_max_cost_usd()
    budget_guard.release_request_budget(amount)


def test_config_read_never_happens_inside_reservation_lock(monkeypatch):
    """執行緒安全鐵則（計劃 §5）：admin config 的（可能慢的）讀取**絕不可**
    發生在 `BudgetReservation._lock` 內——若有人日後把 `daily_cap_usd()`
    挪進 `try_reserve` 的鎖內，本測試會抓到。"""
    seen: list[bool] = []

    def probe(*a, **k):
        # get_config 被呼叫的當下，全域預留鎖必須是未持有狀態
        seen.append(budget_guard._RESERVATION._lock.locked())
        return AdminConfig(daily_cap_usd=1.0, exists=True)

    monkeypatch.setattr(admin_config, "get_config", probe)
    from trustforge.ledger import JsonlLedger

    ledger = JsonlLedger.__new__(JsonlLedger)
    ledger.read_all = lambda: []
    amount = budget_guard.try_reserve_request_budget(ledger)
    budget_guard.release_request_budget(amount)
    assert seen, "try_reserve 應觸發 config 層讀取"
    assert all(locked is False for locked in seen), (
        "admin config 讀取發生在 BudgetReservation._lock 內（違反鎖規則）"
    )


# ---------------------------------------------------------------------------
# 2. live 閘 `_bedrock_allowed`：config AND env，雙 fail-closed
# ---------------------------------------------------------------------------
def test_gate_env_model_unset_always_closed(monkeypatch):
    """env `BEDROCK_MODEL_ID` 未設 → 即使 config 開了也沒模型可打，False。"""
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    _mock_config(monkeypatch, AdminConfig(bedrock_enabled=True, exists=True))
    assert web._bedrock_allowed_resolved() == (False, "env")


def test_gate_config_unset_follows_env_only(monkeypatch):
    """config 未設定過（item 不存在）→ 只看 env，與 v0.7.0 HAS_BEDROCK
    逐字相同（schema 相容固定驗收）。"""
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-bedrock-model")
    _mock_config(monkeypatch, AdminConfig())
    assert web._bedrock_allowed_resolved() == (True, "env")


def test_gate_config_false_closes_even_with_env_model(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-bedrock-model")
    _mock_config(monkeypatch, AdminConfig(bedrock_enabled=False, exists=True))
    assert web._bedrock_allowed_resolved() == (False, "config")


def test_gate_config_true_with_env_model_open(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-bedrock-model")
    _mock_config(monkeypatch, AdminConfig(bedrock_enabled=True, exists=True))
    assert web._bedrock_allowed_resolved() == (True, "config")


def test_gate_config_read_error_fail_closed(monkeypatch, caplog):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-bedrock-model")
    _mock_config_read_error(monkeypatch)
    with caplog.at_level("WARNING"):
        assert web._bedrock_allowed_resolved() == (False, "config_read_error")
    assert any("fail-closed" in r.getMessage() for r in caplog.records)


def test_gate_env_unset_never_reads_config(monkeypatch):
    """零設定部署（BEDROCK_MODEL_ID 未設）不得產生任何 config 讀取
    （零 DynamoDB 呼叫，行為與 v0.7.0 逐字相同）。"""
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)

    def must_not_call(*a, **k):
        raise AssertionError("env 模型未設時不應讀 admin config")

    monkeypatch.setattr(admin_config, "get_config", must_not_call)
    assert web._bedrock_allowed() is False


# ---------------------------------------------------------------------------
# 3. live token：config hash 優先 → env fallback
# ---------------------------------------------------------------------------
def test_token_config_hash_takes_precedence_env_token_rejected(monkeypatch):
    """config token 設定後以它為準：新 token 認、env 舊 token **立即失效**。"""
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", ENV_TOKEN)
    _mock_config(
        monkeypatch,
        AdminConfig(live_token_hash=hash_live_token(CONFIG_TOKEN), exists=True),
    )
    assert web._live_token_matches(CONFIG_TOKEN) is True
    assert web._live_token_matches(ENV_TOKEN) is False
    assert web._live_token_resolved() == (True, "config")


def test_token_config_unset_falls_to_env(monkeypatch):
    """config 未設 → env 比對邏輯與 v0.7.0 逐字相同。"""
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", ENV_TOKEN)
    _mock_config(monkeypatch, AdminConfig())
    assert web._live_token_matches(ENV_TOKEN) is True
    assert web._live_token_matches("wrong-token") is False
    assert web._live_token_resolved() == (True, "env")


def test_token_both_unset_fail_closed(monkeypatch):
    monkeypatch.delenv("TRUSTFORGE_LIVE_TOKEN", raising=False)
    _mock_config(monkeypatch, AdminConfig())
    assert web._live_token_matches("anything") is False
    assert web._live_token_matches("") is False
    assert web._live_token_resolved() == (False, "none")


def test_token_config_read_error_fail_closed(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", ENV_TOKEN)
    _mock_config_read_error(monkeypatch)
    assert web._live_token_matches(ENV_TOKEN) is False
    assert web._live_token_resolved() == (False, "config_read_error")


# ---------------------------------------------------------------------------
# 4. `_is_live_request` 端到端（閘 + token 合成）
# ---------------------------------------------------------------------------
def _live_qs(token: str) -> dict:
    return {"live": ["1"], "token": [token]}


def test_is_live_v070_compat_no_config_item(monkeypatch):
    """schema 相容固定驗收：無 config item（conftest 預設假表＝空 config）
    → 行為與 v0.7.0 逐字相同（env 模型 + env token 即可 live）。"""
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-bedrock-model")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", ENV_TOKEN)
    assert web._is_live_request(_live_qs(ENV_TOKEN)) is True
    assert web._is_live_request(_live_qs("wrong")) is False
    assert web._is_live_request({"live": ["1"]}) is False  # 沒帶 token
    assert web._is_live_request({"token": [ENV_TOKEN]}) is False  # 沒帶 live=1
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    assert web._is_live_request(_live_qs(ENV_TOKEN)) is False  # 閘（env）關


def test_is_live_blocked_when_config_disables_bedrock(monkeypatch):
    """bedrock_enabled=false → 即使 token 全對也強制離線（驗收固定項）。"""
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-bedrock-model")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", ENV_TOKEN)
    _mock_config(monkeypatch, AdminConfig(bedrock_enabled=False, exists=True))
    assert web._is_live_request(_live_qs(ENV_TOKEN)) is False


def test_is_live_accepts_config_token(monkeypatch):
    """config live token 設定後 `_is_live_request` 認它（驗收固定項）。"""
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-bedrock-model")
    monkeypatch.delenv("TRUSTFORGE_LIVE_TOKEN", raising=False)
    _mock_config(
        monkeypatch,
        AdminConfig(
            bedrock_enabled=True,
            live_token_hash=hash_live_token(CONFIG_TOKEN),
            exists=True,
        ),
    )
    assert web._is_live_request(_live_qs(CONFIG_TOKEN)) is True
    assert web._is_live_request(_live_qs("wrong")) is False


def test_is_live_without_live_param_never_reads_config(monkeypatch):
    """非 live 請求（絕大多數流量）短路，不觸發任何 config 讀取。"""
    def must_not_call(*a, **k):
        raise AssertionError("非 live 請求不應讀 admin config")

    monkeypatch.setattr(admin_config, "get_config", must_not_call)
    assert web._is_live_request({"coin": ["BTC"]}) is False


# ---------------------------------------------------------------------------
# 5. 緊急關閉主路徑**立即生效**（write-through，不等 15s TTL）
# ---------------------------------------------------------------------------
def _store_with_item(item: dict) -> AdminConfigStore:
    """自建 store + 假表（PR-1 慣例）：get_item 回指定 item、put_item 成功。"""
    store = AdminConfigStore()

    class _Table:
        def get_item(self, **kwargs):
            return {"Item": dict(item)}

        def put_item(self, **kwargs):
            return {}

        def query(self, **kwargs):
            return {"Items": []}

    store._table = _Table()
    return store


def test_emergency_bedrock_off_takes_effect_immediately(monkeypatch):
    """CEO 修正項可執行版：`put_config(bedrock_enabled=False)` 後，同
    process（服務 `/analyze` 的 web process）的下一次 `_bedrock_allowed()`
    ／`_is_live_request()` **立刻**看到 False——write-through，不等 TTL、
    不動時鐘。"""
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-bedrock-model")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", ENV_TOKEN)
    store = _store_with_item(
        {"version": 3, "bedrock_enabled": True, "updated_at": "t", "updated_by": "a"}
    )
    # 預設 store 也指向同一個假表（get_config_cached 走它做 cache-miss 讀）
    monkeypatch.setattr(admin_config, "_default_store_instance", store)

    # 關閉前：閘開、live 成立（且已把「開」的狀態吃進 TTL 快取——證明
    # 之後的翻轉是 write-through 生效，不是碰巧 cache-miss 重讀）
    assert web._bedrock_allowed() is True
    assert web._is_live_request(_live_qs(ENV_TOKEN)) is True

    # 管理面緊急關閉（走真 put_config：CAS 寫入 + write-through）
    put_config(
        {"bedrock_enabled": False}, expected_version=3, actor="admin@test", store=store,
    )

    # 立即生效：不 sleep、不清快取、不動 now_fn
    assert web._bedrock_allowed() is False
    assert web._is_live_request(_live_qs(ENV_TOKEN)) is False


def test_live_token_rotation_takes_effect_immediately(monkeypatch):
    """同理：管理面設定/輪替 config live token 後，同 process 立即以新
    token 為準，env 舊 token 立即失效。"""
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-bedrock-model")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", ENV_TOKEN)
    store = _store_with_item(
        {"version": 5, "bedrock_enabled": True, "updated_at": "t", "updated_by": "a"}
    )
    monkeypatch.setattr(admin_config, "_default_store_instance", store)

    assert web._is_live_request(_live_qs(ENV_TOKEN)) is True  # 輪替前認 env token

    put_config(
        {"live_token": CONFIG_TOKEN}, expected_version=5, actor="admin@test", store=store,
    )

    assert web._is_live_request(_live_qs(CONFIG_TOKEN)) is True
    assert web._is_live_request(_live_qs(ENV_TOKEN)) is False  # env 舊 token 立即失效


def test_cap_change_takes_effect_immediately(monkeypatch):
    """cap 也走同一條 write-through：PUT 後 `daily_cap_usd()` 立即回新值。"""
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "2.5")
    store = _store_with_item({"version": 1, "updated_at": "t", "updated_by": "a"})
    monkeypatch.setattr(admin_config, "_default_store_instance", store)

    assert budget_guard.daily_cap_usd_resolved() == (2.5, "env")  # 設定前落 env

    put_config(
        {"daily_cap_usd": 1.0}, expected_version=1, actor="admin@test", store=store,
    )

    assert budget_guard.daily_cap_usd_resolved() == (1.0, "config")


# ---------------------------------------------------------------------------
# 6. /api/status、/status 頁顯示動態值
# ---------------------------------------------------------------------------
def test_api_status_reflects_dynamic_gate(monkeypatch):
    """/api/status 的 bedrock_capable/live_token_set 反映動態閘（config
    bedrock_enabled=false → capable=False，即使 env 模型有設；config token
    設定 → live_token_set=True，即使 env token 未設）。"""
    import json as _json

    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-bedrock-model")
    monkeypatch.delenv("TRUSTFORGE_LIVE_TOKEN", raising=False)
    _mock_config(
        monkeypatch,
        AdminConfig(
            bedrock_enabled=False,
            live_token_hash=hash_live_token(CONFIG_TOKEN),
            exists=True,
        ),
    )
    code, body = web._handle_api_status()
    assert code == 200
    data = _json.loads(body)["data"]
    assert data["bedrock_capable"] is False
    assert data["live_token_set"] is True


def test_api_status_v070_compat_without_config(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-bedrock-model")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", ENV_TOKEN)
    import json as _json

    _mock_config(monkeypatch, AdminConfig())
    code, body = web._handle_api_status()
    assert code == 200
    data = _json.loads(body)["data"]
    assert data["bedrock_capable"] is True
    assert data["live_token_set"] is True


def test_status_page_shows_dynamic_gate_state(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-bedrock-model")
    monkeypatch.delenv("TRUSTFORGE_LIVE_TOKEN", raising=False)
    _mock_config(monkeypatch, AdminConfig(bedrock_enabled=False, exists=True))
    body = web._render_status_page()
    assert "未啟用" in body
    assert "config 層生效" in body


def test_status_page_env_layer_without_config(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-bedrock-model")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", ENV_TOKEN)
    _mock_config(monkeypatch, AdminConfig())
    body = web._render_status_page()
    assert "已啟用（真 Bedrock 模式可用）" in body
    assert "env 層生效" in body


# ---------------------------------------------------------------------------
# 7. GET /api/admin/config 的 effective/source（source=env 情境；
#    config 層生效情境已在 test_admin_api.py::test_get_config_shape_and_layers）
# ---------------------------------------------------------------------------
def test_admin_view_effective_source_env_layer(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "2.5")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-bedrock-model")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", ENV_TOKEN)
    monkeypatch.setenv("TRUSTFORGE_HERMES_AUTONOMY_ENABLED", "0")
    _mock_config(monkeypatch, AdminConfig())
    view = web._admin_config_view(AdminConfig())
    assert view["daily_cap_usd"]["effective"] == 2.5
    assert view["daily_cap_usd"]["source"] == "env"
    assert view["bedrock_enabled"]["effective"] is True
    assert view["bedrock_enabled"]["source"] == "env"
    assert view["live_token"]["effective_configured"] is True
    assert view["live_token"]["source"] == "env"
    assert view["hermes_autonomy_enabled"]["effective"] is False
    assert view["hermes_autonomy_enabled"]["source"] == "env"


def test_read_error_negative_cache_window(monkeypatch):
    """web 層讀取失敗負快取：失敗後 15s 窗內不重打儲存層（零 AWS 部署的
    /status、/api/status 不因每請求重付失敗讀而變慢），窗內閘門維持
    fail-closed；期滿重試、成功後窗清除。"""
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise AdminConfigReadError("dynamodb down")

    monkeypatch.setattr(admin_config, "get_config", boom)
    fake_now = [1000.0]
    now_fn = lambda: fake_now[0]  # noqa: E731

    assert web._admin_runtime_config(now_fn=now_fn) is web._ADMIN_CFG_READ_ERROR
    assert calls["n"] == 1
    # 窗內：不重打（呼叫次數不變），仍回 sentinel（fail-closed）
    fake_now[0] += 1.0
    assert web._admin_runtime_config(now_fn=now_fn) is web._ADMIN_CFG_READ_ERROR
    assert calls["n"] == 1
    # 窗滿：重試（呼叫次數 +1）
    fake_now[0] += admin_config.CACHE_TTL_SECONDS + 0.1
    assert web._admin_runtime_config(now_fn=now_fn) is web._ADMIN_CFG_READ_ERROR
    assert calls["n"] == 2
    # 恢復：成功讀 → 窗清除、回真 config
    admin_config._reset_admin_config_cache_for_tests()  # 清 TTL 快取讓重讀發生
    _mock_config(monkeypatch, AdminConfig(bedrock_enabled=True, exists=True))
    fake_now[0] += admin_config.CACHE_TTL_SECONDS + 0.1
    cfg = web._admin_runtime_config(now_fn=now_fn)
    assert cfg is not web._ADMIN_CFG_READ_ERROR
    assert cfg.bedrock_enabled is True


# ---------------------------------------------------------------------------
# 8. env cap≤0 kill-switch 最高優先（PR #114 複審 harper M2）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("env_val", ["0", "-1"])
def test_cap_env_killswitch_overrides_config(monkeypatch, env_val):
    """驗收固定項（harper M2）：config 設 1.0 + env 設 ≤0 → 生效 ≤0
    （kill-switch 凌駕 config，「env 設 cap≤0 全關」舊 SOP 在 config 已設
    的部署不得失效）。`daily_cap_exceeded`/`try_reserve` 同步全關。"""
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", env_val)
    _mock_config(monkeypatch, AdminConfig(daily_cap_usd=1.0, exists=True))
    assert budget_guard.daily_cap_usd_resolved() == (float(env_val), "env")
    assert budget_guard.daily_cap_exceeded() is True
    assert budget_guard.try_reserve_request_budget() is None


def test_cap_env_killswitch_never_reads_config(monkeypatch):
    """kill-switch 生效期間零 DynamoDB 讀：緊急關閉不依賴管理面存活。"""
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "0")

    def must_not_call(*a, **k):
        raise AssertionError("env kill-switch 生效時不應讀 admin config")

    monkeypatch.setattr(admin_config, "get_config", must_not_call)
    assert budget_guard.daily_cap_usd_resolved() == (0.0, "env")


def test_cap_env_positive_does_not_shortcircuit_config(monkeypatch):
    """反向保護：只有 ≤0 走 kill-switch 短路；env 正值仍照三層順序被
    config 蓋過（不得把整個 env 層都提到 config 之前）。"""
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "0.5")
    _mock_config(monkeypatch, AdminConfig(daily_cap_usd=1.0, exists=True))
    assert budget_guard.daily_cap_usd_resolved() == (1.0, "config")


# ---------------------------------------------------------------------------
# 9. config bedrock_enabled 統一蓋住 online-stance 側路（PR #114 複審
#    harper M1）：緊急關閉後 ?real=1 流量不得再打真 Bedrock
# ---------------------------------------------------------------------------
def _enable_stance_env(monkeypatch) -> None:
    monkeypatch.setenv("TRUSTFORGE_ONLINE_STANCE", "1")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-bedrock-model")


def test_stance_blocked_when_config_disables_bedrock(monkeypatch):
    """驗收固定項（harper M1）：bedrock_enabled=false → online-stance 也
    被擋（緊急關閉統一蓋所有真 Bedrock 路徑，不只 ?live=1 主路徑）。"""
    _enable_stance_env(monkeypatch)
    _mock_config(monkeypatch, AdminConfig(bedrock_enabled=False, exists=True))
    assert budget_guard.online_stance_requested() is False


def test_stance_config_unset_follows_env_only(monkeypatch):
    """schema 相容：config 未設定過（item 不存在）→ 只看 env，行為與
    v0.7.0 逐字相同。"""
    _enable_stance_env(monkeypatch)
    _mock_config(monkeypatch, AdminConfig())
    assert budget_guard.online_stance_requested() is True


def test_stance_config_true_allows(monkeypatch):
    _enable_stance_env(monkeypatch)
    _mock_config(monkeypatch, AdminConfig(bedrock_enabled=True, exists=True))
    assert budget_guard.online_stance_requested() is True


def test_stance_config_read_error_fail_closed(monkeypatch, caplog):
    """config 讀取異常 → stance 側路 fail-closed（無法確認管理面是否已
    緊急關閉，就不放行真 Bedrock 花費），與 `web._bedrock_allowed` 的
    read-error 方向一致。"""
    _enable_stance_env(monkeypatch)
    _mock_config_read_error(monkeypatch)
    with caplog.at_level("WARNING"):
        assert budget_guard.online_stance_requested() is False
    assert any("fail-closed" in r.getMessage() for r in caplog.records)


def test_stance_env_off_never_reads_config(monkeypatch):
    """零設定離線 demo（stance env 開關未設）不得產生任何 config 讀取
    （零 DynamoDB/AWS 呼叫，行為與 v0.7.0 逐字相同）。"""
    monkeypatch.delenv("TRUSTFORGE_ONLINE_STANCE", raising=False)
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-bedrock-model")

    def must_not_call(*a, **k):
        raise AssertionError("stance env 開關未設時不應讀 admin config")

    monkeypatch.setattr(admin_config, "get_config", must_not_call)
    assert budget_guard.online_stance_requested() is False


def test_stance_pipeline_side_path_blocked_when_config_disabled(monkeypatch):
    """pipeline 放行處（`pipeline.run` 的 `_online_stance_wanted`）依賴的
    是同一個 `online_stance_requested()`——config 關閉時 web 限流入口
    `_online_stance_force_offline` 也直接短路回 False（stance 根本不會
    啟用，不需限流）。"""
    _enable_stance_env(monkeypatch)
    _mock_config(monkeypatch, AdminConfig(bedrock_enabled=False, exists=True))
    assert web._online_stance_force_offline("203.0.113.9") is False


# ---------------------------------------------------------------------------
# 10. budget 路徑失敗負快取（PR #114 複審 qa M1）：DynamoDB 持續故障時
#     不得讓每個 analyze（含純離線）都重付一次有界 timeout 的失敗讀
# ---------------------------------------------------------------------------
def test_budget_path_negative_cache_no_flood(monkeypatch):
    """讀取失敗後 15s 窗內，`daily_cap_usd_resolved()` 不再重打儲存層
    （呼叫次數不增），且照樣落 env（fail-safe 行為不變）。"""
    monkeypatch.setenv("TRUSTFORGE_BEDROCK_DAILY_USD_CAP", "2.5")
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise AdminConfigReadError("dynamodb down")

    monkeypatch.setattr(admin_config, "get_config", boom)
    assert budget_guard.daily_cap_usd_resolved() == (2.5, "env")
    assert calls["n"] == 1
    # 失敗窗內：不重打儲存層，直接落 env
    assert budget_guard.daily_cap_usd_resolved() == (2.5, "env")
    assert budget_guard.daily_cap_usd_resolved() == (2.5, "env")
    assert calls["n"] == 1
    # stance 側路共用同一個負快取窗：也不重打，fail-closed
    monkeypatch.setenv("TRUSTFORGE_ONLINE_STANCE", "1")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-bedrock-model")
    assert budget_guard.online_stance_requested() is False
    assert calls["n"] == 1


def test_failsoft_window_expiry_and_recovery(monkeypatch):
    """`get_config_cached_failsoft` 窗語意：失敗窗內 raise 不打儲存層；
    期滿重試；成功清窗、回真 config（恢復延遲 ≤15s）。"""
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise AdminConfigReadError("dynamodb down")

    monkeypatch.setattr(admin_config, "get_config", boom)
    fake_now = [1000.0]
    now_fn = lambda: fake_now[0]  # noqa: E731

    with pytest.raises(AdminConfigReadError):
        admin_config.get_config_cached_failsoft(now_fn=now_fn)
    assert calls["n"] == 1
    # 窗內：raise 但不重打（呼叫次數不變）
    fake_now[0] += 1.0
    with pytest.raises(AdminConfigReadError):
        admin_config.get_config_cached_failsoft(now_fn=now_fn)
    assert calls["n"] == 1
    # 窗滿：重試（仍失敗 → 續窗）
    fake_now[0] += admin_config.CACHE_TTL_SECONDS + 0.1
    with pytest.raises(AdminConfigReadError):
        admin_config.get_config_cached_failsoft(now_fn=now_fn)
    assert calls["n"] == 2
    # 恢復：儲存層修好 → 窗滿重試成功、清窗、回真 config
    _mock_config(monkeypatch, AdminConfig(daily_cap_usd=1.0, exists=True))
    fake_now[0] += admin_config.CACHE_TTL_SECONDS + 0.1
    cfg = admin_config.get_config_cached_failsoft(now_fn=now_fn)
    assert cfg.daily_cap_usd == 1.0
    # 窗已清：下一次呼叫不被負快取擋（TTL 正快取直接回，同樣不 raise）
    cfg2 = admin_config.get_config_cached_failsoft(now_fn=now_fn)
    assert cfg2.daily_cap_usd == 1.0


def test_admin_view_never_contains_token_material(monkeypatch):
    """effective/source 擴充後仍不得洩漏 token 值/hash（縱深重申）。"""
    import json as _json

    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", ENV_TOKEN)
    cfg = AdminConfig(
        live_token_hash=hash_live_token(CONFIG_TOKEN),
        live_token_last4=CONFIG_TOKEN[-4:],
        exists=True,
    )
    _mock_config(monkeypatch, cfg)
    serialized = _json.dumps(web._admin_config_view(cfg))
    assert CONFIG_TOKEN not in serialized
    assert ENV_TOKEN not in serialized
    assert hash_live_token(CONFIG_TOKEN) not in serialized
    assert "live_token_hash" not in serialized
