"""#1161 CoinMarketCap secret module 測試 — 仿 test_whale_alert_secret.py，改 SSM
param/env/header 驗證。

重點差異（security，harper 雙審）：
  - key 走 **header** `X-CMC_PRO_API_KEY`（非 URL query param）——斷言 fetcher 收到
    `extra_headers` 含此 header，且 URL 全程乾淨、不含 key。
  - verify_connection 成功條件是 schema 驗證 `data.BTC.quote.USD.price`（非
    whale-alert 的 `result=="success"`）。
  - env / file / SSM param 名稱改 CMC 對應值。
  - 其餘（SecureString put、status 不含明文、ParameterNotFound 回落 env、
    AccessDenied fail-closed、put round-trip drift→RuntimeError、clear 誠實）
    照抄 whale-alert 的測試範式。
"""
from __future__ import annotations

import json

import pytest

from trustforge import cmc_secret
from trustforge.ingestion import cmc


class FakeSSM:
    def __init__(self) -> None:
        self.value: str | None = None
        self.put_calls: list[dict] = []

    def get_parameter(self, **kwargs):
        if self.value is None:
            error = RuntimeError("missing")
            error.response = {"Error": {"Code": "ParameterNotFound"}}  # type: ignore[attr-defined]
            raise error
        return {"Parameter": {"Value": self.value}}

    def put_parameter(self, **kwargs):
        self.put_calls.append(kwargs)
        self.value = kwargs["Value"]

    def delete_parameter(self, **kwargs):
        self.value = None

    def list_tags_for_resource(self, **kwargs):
        return {"TagList": []}

    def add_tags_to_resource(self, **kwargs):
        self.tags = kwargs["Tags"]

    def remove_tags_from_resource(self, **kwargs):
        self.removed_tags = kwargs["TagKeys"]


# CMC verify_connection 成功 fixture：schema 含 data.BTC.quote.USD.price。
_CMC_OK = json.dumps(
    {"data": {"BTC": {"quote": {"USD": {"price": 67000.5, "market_cap": 1.3e12}}}}}
).encode()


def test_write_is_secure_string_and_status_never_contains_plaintext(monkeypatch):
    fake = FakeSSM()
    monkeypatch.setenv(
        "TRUSTFORGE_CMC_SSM_PARAMETER",
        "/trustforge/test/cmc-api-key",
    )
    monkeypatch.setattr(cmc_secret, "_ssm_client", lambda: fake)
    cmc_secret.invalidate_cache()

    secret = "test-cmc-key-1234567890"
    result = cmc_secret.put_api_key(secret).as_dict()

    assert fake.put_calls == [
        {
            "Name": "/trustforge/test/cmc-api-key",
            "Value": secret,
            "Type": "SecureString",
            "Overwrite": True,
            "Tier": "Standard",
        }
    ]
    assert fake.removed_tags == ["TrustForgeLastVerifiedAt"]
    assert result == {
        "configured": True,
        "source": "ssm",
        "last_verified_at": None,
    }
    assert secret not in json.dumps(result)


def test_connection_test_uses_header_not_query_and_status_is_masked(monkeypatch):
    """security（harper 重點）：verify_connection 把 key 放在 header
    `X-CMC_PRO_API_KEY`，**不**放 URL query；URL 全程乾淨、不含 key。"""
    fake = FakeSSM()
    fake.value = "test-cmc-key-1234567890"
    monkeypatch.setenv(
        "TRUSTFORGE_CMC_SSM_PARAMETER",
        "/trustforge/test/cmc-api-key",
    )
    monkeypatch.setattr(cmc_secret, "_ssm_client", lambda: fake)
    cmc_secret.invalidate_cache()
    observed = {}

    def fetcher(url, **kwargs):
        observed["url"] = url
        observed.update(kwargs)
        return _CMC_OK

    result = cmc_secret.verify_connection(fetcher=fetcher).as_dict()

    assert result["configured"] is True
    assert result["last_verified_at"]
    assert observed["timeout"] == 5
    assert observed["max_bytes"] == 64 * 1024
    assert observed["max_redirects"] == 0
    # key 走 header（非 URL query）——security 鐵律。
    assert observed["extra_headers"] == {"X-CMC_PRO_API_KEY": "test-cmc-key-1234567890"}
    assert "test-cmc-key-1234567890" not in observed["url"]
    assert "X-CMC_PRO_API_KEY" not in observed["url"]
    assert "test-cmc-key-1234567890" not in json.dumps(result)


def test_environment_fallback_only_when_ssm_parameter_is_not_enabled(monkeypatch):
    monkeypatch.delenv("TRUSTFORGE_CMC_SSM_PARAMETER", raising=False)
    monkeypatch.delenv("TRUSTFORGE_CMC_API_KEY_FILE", raising=False)
    monkeypatch.setenv("CMC_PRO_API_KEY", "local-cmc-key-123456")
    cmc_secret.invalidate_cache()

    assert cmc_secret.resolve_api_key() == (
        "local-cmc-key-123456",
        "environment",
    )


def test_cached_ssm_key_is_rechecked_after_bounded_revocation_window(monkeypatch):
    fake = FakeSSM()
    fake.value = "cached-cmc-key-1234567890"
    clock = {"now": 1000.0}
    monkeypatch.setattr(cmc_secret, "_ssm_client", lambda: fake)
    monkeypatch.setattr(cmc_secret.time, "monotonic", lambda: clock["now"])
    monkeypatch.setenv(
        "TRUSTFORGE_CMC_SSM_PARAMETER",
        "/trustforge/test/cmc-api-key",
    )
    cmc_secret.invalidate_cache()

    assert cmc_secret.resolve_api_key() == ("cached-cmc-key-1234567890", "ssm")
    fake.value = None
    assert cmc_secret.resolve_api_key() == ("cached-cmc-key-1234567890", "ssm")

    clock["now"] += cmc_secret._CACHE_TTL_SECONDS + 0.001

    assert cmc_secret.resolve_api_key() == (None, "unconfigured")


def test_secure_local_key_file_is_supported_without_exposing_value(
    monkeypatch, tmp_path
):
    key_file = tmp_path / "cmc.apikey"
    key_file.write_text("local-file-cmc-key-123456", encoding="utf-8")
    key_file.chmod(0o600)
    monkeypatch.delenv("TRUSTFORGE_CMC_SSM_PARAMETER", raising=False)
    monkeypatch.delenv("CMC_PRO_API_KEY", raising=False)
    monkeypatch.setenv("TRUSTFORGE_CMC_API_KEY_FILE", str(key_file))

    value, source = cmc_secret.resolve_api_key()

    assert value == "local-file-cmc-key-123456"
    assert source == "file"


def test_local_key_file_fails_closed_when_permissions_are_too_broad(
    monkeypatch, tmp_path
):
    key_file = tmp_path / "cmc.apikey"
    key_file.write_text("local-file-cmc-key-123456", encoding="utf-8")
    key_file.chmod(0o644)
    monkeypatch.delenv("TRUSTFORGE_CMC_SSM_PARAMETER", raising=False)
    monkeypatch.setenv("TRUSTFORGE_CMC_API_KEY_FILE", str(key_file))

    assert cmc_secret.resolve_api_key() == (None, "unavailable")


def test_cmc_connector_uses_controlled_secret_resolver_via_header(monkeypatch):
    """security：CMC connector 從 cmc_secret.resolve_api_key 取 key，且 key 透過
    header 傳遞（非 URL query）。斷言 fetcher 收到 extra_headers 含
    X-CMC_PRO_API_KEY，URL 不含 key。"""
    observed = {}
    monkeypatch.setattr(
        cmc,
        "resolve_api_key",
        lambda: ("controlled-cmc-key-1234567890", "ssm"),
    )

    def fake_fetch(url, extra_headers=None):
        observed["url"] = url
        observed["extra_headers"] = extra_headers
        return _CMC_OK

    monkeypatch.setattr(cmc, "_fetch_url", fake_fetch)

    docs = cmc.CoinMarketCapPriceSource().fetch("", coin="BTC")
    assert len(docs) == 1
    # key 走 header，URL 全程乾淨。
    assert observed["extra_headers"] == {"X-CMC_PRO_API_KEY": "controlled-cmc-key-1234567890"}
    assert "controlled-cmc-key-1234567890" not in observed["url"]


def test_ssm_parameter_not_found_falls_back_to_env(monkeypatch):
    """SSM 參數已設（非空）但尚未建立（ParameterNotFound）= 遷移中，
    应回落 env（保既有安裝升級後不靜默斷料），而非 unavailable。"""
    fake = FakeSSM()  # value=None → get_parameter 拋帶 ParameterNotFound 的例外
    monkeypatch.setattr(cmc_secret, "_ssm_client", lambda: fake)
    monkeypatch.setenv(
        "TRUSTFORGE_CMC_SSM_PARAMETER", "/trustforge/test/cmc-api-key"
    )
    monkeypatch.delenv("TRUSTFORGE_CMC_API_KEY_FILE", raising=False)
    monkeypatch.setenv("CMC_PRO_API_KEY", "local-cmc-key-123456")
    cmc_secret.invalidate_cache()

    assert cmc_secret.resolve_api_key() == ("local-cmc-key-123456", "environment")


def test_ssm_non_parameter_not_found_error_fails_closed(monkeypatch):
    """SSM 拋非 ParameterNotFound 錯誤（AccessDenied/網路/解密失敗）= 真故障，
    fail-closed 回 unavailable，不回落 env。"""

    class AccessDeniedSSM:
        def get_parameter(self, **kwargs):
            error = RuntimeError("denied")
            error.response = {"Error": {"Code": "AccessDenied"}}  # type: ignore[attr-defined]
            raise error

    monkeypatch.setattr(cmc_secret, "_ssm_client", lambda: AccessDeniedSSM())
    monkeypatch.setenv(
        "TRUSTFORGE_CMC_SSM_PARAMETER", "/trustforge/test/cmc-api-key"
    )
    monkeypatch.delenv("TRUSTFORGE_CMC_API_KEY_FILE", raising=False)
    monkeypatch.setenv("CMC_PRO_API_KEY", "local-cmc-key-123456")
    cmc_secret.invalidate_cache()

    assert cmc_secret.resolve_api_key() == (None, "unavailable")


def test_put_api_key_raises_when_round_trip_drifts(monkeypatch):
    """put_api_key 寫入後 resolve 回的值 != 寫入值（SSM 端 drift）→ RuntimeError，
    憑證未靜默啟用錯誤值。"""

    class DriftSSM(FakeSSM):
        def get_parameter(self, **kwargs):
            # 永遠回傳與寫入值不同的值 → round-trip 驗證失敗
            return {"Parameter": {"Value": "different-cmc-key-1234567890"}}

    drift = DriftSSM()
    monkeypatch.setattr(cmc_secret, "_ssm_client", lambda: drift)
    monkeypatch.setenv(
        "TRUSTFORGE_CMC_SSM_PARAMETER", "/trustforge/test/cmc-api-key"
    )
    cmc_secret.invalidate_cache()

    with pytest.raises(RuntimeError):
        cmc_secret.put_api_key("new-cmc-key-1234567890")


def test_resolve_disables_env_fallback_when_requested(monkeypatch):
    """allow_env_fallback_on_missing_ssm=False：SSM ParameterNotFound 時不再
    回落 env（即使 env 有值），直接 fail-closed unavailable。"""
    fake = FakeSSM()  # value=None → get_parameter 拋帶 ParameterNotFound 的例外
    monkeypatch.setattr(cmc_secret, "_ssm_client", lambda: fake)
    monkeypatch.setenv(
        "TRUSTFORGE_CMC_SSM_PARAMETER", "/trustforge/test/cmc-api-key"
    )
    monkeypatch.delenv("TRUSTFORGE_CMC_API_KEY_FILE", raising=False)
    monkeypatch.setenv("CMC_PRO_API_KEY", "local-cmc-key-123456")
    cmc_secret.invalidate_cache()

    assert cmc_secret.resolve_api_key(
        allow_env_fallback_on_missing_ssm=False
    ) == (None, "unavailable")


def test_put_api_key_rejects_env_fallback_as_activation_verification(monkeypatch):
    """put 後驗證不可把 env fallback（值恰好相同）當啟用成功。put_parameter
    成功但 get_parameter 拋 ParameterNotFound（SSM 端未真正留存），且 env 值與
    寫入值相同——新邏輯因 allow_env_fallback_on_missing_ssm=False + source!="ssm"
    → RuntimeError。"""

    class PutOkGetMissingSSM(FakeSSM):
        def get_parameter(self, **kwargs):
            error = RuntimeError("missing after put")
            error.response = {"Error": {"Code": "ParameterNotFound"}}  # type: ignore[attr-defined]
            raise error

    fake = PutOkGetMissingSSM()
    monkeypatch.setattr(cmc_secret, "_ssm_client", lambda: fake)
    monkeypatch.setenv(
        "TRUSTFORGE_CMC_SSM_PARAMETER", "/trustforge/test/cmc-api-key"
    )
    monkeypatch.delenv("TRUSTFORGE_CMC_API_KEY_FILE", raising=False)
    key = "activation-equals-env-key-0123456789"
    monkeypatch.setenv("CMC_PRO_API_KEY", key)  # 與寫入值相同
    cmc_secret.invalidate_cache()

    with pytest.raises(RuntimeError):
        cmc_secret.put_api_key(key)


def test_clear_api_key_reports_active_env_after_ssm_delete(monkeypatch):
    """clear 只刪 SSM 參數；env 並存時 status 如實揭露仍在現役
    (True, "environment")，不誤報 unconfigured 誤導操作員以為已撤銷。"""
    fake = FakeSSM()
    fake.value = "ssm-stored-cmc-key-1234567890"
    monkeypatch.setattr(cmc_secret, "_ssm_client", lambda: fake)
    monkeypatch.setenv(
        "TRUSTFORGE_CMC_SSM_PARAMETER", "/trustforge/test/cmc-api-key"
    )
    monkeypatch.delenv("TRUSTFORGE_CMC_API_KEY_FILE", raising=False)
    monkeypatch.setenv("CMC_PRO_API_KEY", "legacy-env-cmc-key-1234567890")
    cmc_secret.invalidate_cache()

    result = cmc_secret.clear_api_key()

    assert result == cmc_secret.SecretStatus(True, "environment", None)


def test_clear_api_key_disables_when_ssm_was_only_source(monkeypatch):
    """only-SSM：clear 後無 env 無檔 → 真正撤銷 (False, "unconfigured")。"""
    fake = FakeSSM()
    fake.value = "ssm-stored-cmc-key-1234567890"
    monkeypatch.setattr(cmc_secret, "_ssm_client", lambda: fake)
    monkeypatch.setenv(
        "TRUSTFORGE_CMC_SSM_PARAMETER", "/trustforge/test/cmc-api-key"
    )
    monkeypatch.delenv("TRUSTFORGE_CMC_API_KEY_FILE", raising=False)
    monkeypatch.delenv("CMC_PRO_API_KEY", raising=False)
    cmc_secret.invalidate_cache()

    result = cmc_secret.clear_api_key()

    assert result == cmc_secret.SecretStatus(False, "unconfigured", None)


def test_verify_connection_rejects_bad_schema(monkeypatch):
    """verify_connection 成功條件是 schema 驗證 data.BTC.quote.USD.price；
    回應缺此結構（如憑證被拒回的錯誤 body）→ RuntimeError，不造假成功。"""
    fake = FakeSSM()
    fake.value = "test-cmc-key-1234567890"
    monkeypatch.setenv(
        "TRUSTFORGE_CMC_SSM_PARAMETER", "/trustforge/test/cmc-api-key"
    )
    monkeypatch.setattr(cmc_secret, "_ssm_client", lambda: fake)
    cmc_secret.invalidate_cache()

    def fetcher(url, **kwargs):
        # CMC 憑證無效時的典型錯誤回應（無 data.BTC.quote.USD.price）。
        return b'{"status": {"error_code": 1001, "error_message": "Invalid API key"}}'

    with pytest.raises(RuntimeError):
        cmc_secret.verify_connection(fetcher=fetcher)


def test_ssm_cache_rechecks_within_revocation_window(monkeypatch):
    """Separate scheduler process cache must stop using an SSM key after 15s."""
    fake = FakeSSM()
    fake.value = "cached-cmc-key-1234567890"
    clock = {"now": 1000.0}
    monkeypatch.setenv(
        "TRUSTFORGE_CMC_SSM_PARAMETER", "/trustforge/test/cmc-api-key"
    )
    monkeypatch.setattr(cmc_secret, "_ssm_client", lambda: fake)
    monkeypatch.setattr(cmc_secret.time, "monotonic", lambda: clock["now"])
    monkeypatch.delenv("TRUSTFORGE_CMC_API_KEY_FILE", raising=False)
    monkeypatch.delenv("CMC_PRO_API_KEY", raising=False)
    cmc_secret.invalidate_cache()

    assert cmc_secret.resolve_api_key() == ("cached-cmc-key-1234567890", "ssm")
    fake.value = None
    clock["now"] += 14.9
    assert cmc_secret.resolve_api_key() == ("cached-cmc-key-1234567890", "ssm")

    clock["now"] += 0.2
    assert cmc_secret.resolve_api_key() == (None, "unconfigured")
