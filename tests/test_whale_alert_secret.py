from __future__ import annotations

import json
from urllib.error import HTTPError

import pytest

from trustforge import whale_alert_secret
from trustforge.ingestion import whale_trades


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


def test_write_is_secure_string_and_status_never_contains_plaintext(monkeypatch):
    fake = FakeSSM()
    monkeypatch.setenv(
        "TRUSTFORGE_WHALE_ALERT_SSM_PARAMETER",
        "/trustforge/test/whale-alert-api-key",
    )
    monkeypatch.setattr(whale_alert_secret, "_ssm_client", lambda: fake)
    whale_alert_secret.invalidate_cache()

    secret = "test-whale-key-1234567890"
    result = whale_alert_secret.put_api_key(secret).as_dict()

    assert fake.put_calls == [
        {
            "Name": "/trustforge/test/whale-alert-api-key",
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


def test_connection_test_is_bounded_and_status_is_masked(monkeypatch):
    fake = FakeSSM()
    fake.value = "test-whale-key-1234567890"
    monkeypatch.setenv(
        "TRUSTFORGE_WHALE_ALERT_SSM_PARAMETER",
        "/trustforge/test/whale-alert-api-key",
    )
    monkeypatch.setattr(whale_alert_secret, "_ssm_client", lambda: fake)
    whale_alert_secret.invalidate_cache()
    observed = {}

    def fetcher(url, **kwargs):
        observed["url"] = url
        observed.update(kwargs)
        return b'{"result":"success","transactions":[]}'

    result = whale_alert_secret.verify_connection(fetcher=fetcher).as_dict()

    assert result["configured"] is True
    assert result["last_verified_at"]
    assert observed["timeout"] == 5
    assert observed["max_bytes"] == 64 * 1024
    assert observed["max_redirects"] == 0
    assert "test-whale-key-1234567890" not in json.dumps(result)


def test_environment_fallback_only_when_ssm_parameter_is_not_enabled(monkeypatch):
    monkeypatch.delenv("TRUSTFORGE_WHALE_ALERT_SSM_PARAMETER", raising=False)
    monkeypatch.delenv("TRUSTFORGE_WHALE_ALERT_API_KEY_FILE", raising=False)
    monkeypatch.setenv("WHALE_ALERT_API_KEY", "local-whale-key-123456")
    whale_alert_secret.invalidate_cache()

    assert whale_alert_secret.resolve_api_key() == (
        "local-whale-key-123456",
        "environment",
    )


def test_secure_local_key_file_is_supported_without_exposing_value(
    monkeypatch, tmp_path
):
    key_file = tmp_path / "whale.alert.apikey"
    key_file.write_text("local-file-whale-key-123456", encoding="utf-8")
    key_file.chmod(0o600)
    monkeypatch.delenv("TRUSTFORGE_WHALE_ALERT_SSM_PARAMETER", raising=False)
    monkeypatch.delenv("WHALE_ALERT_API_KEY", raising=False)
    monkeypatch.setenv("TRUSTFORGE_WHALE_ALERT_API_KEY_FILE", str(key_file))

    value, source = whale_alert_secret.resolve_api_key()

    assert value == "local-file-whale-key-123456"
    assert source == "file"


def test_local_key_file_fails_closed_when_permissions_are_too_broad(
    monkeypatch, tmp_path
):
    key_file = tmp_path / "whale.alert.apikey"
    key_file.write_text("local-file-whale-key-123456", encoding="utf-8")
    key_file.chmod(0o644)
    monkeypatch.delenv("TRUSTFORGE_WHALE_ALERT_SSM_PARAMETER", raising=False)
    monkeypatch.setenv("TRUSTFORGE_WHALE_ALERT_API_KEY_FILE", str(key_file))

    assert whale_alert_secret.resolve_api_key() == (None, "unavailable")


def test_whale_connector_uses_controlled_secret_resolver(monkeypatch):
    observed = {}
    monkeypatch.setattr(
        whale_trades,
        "resolve_api_key",
        lambda: ("controlled-whale-key-123456", "ssm"),
    )

    def fake_fetch(url, extra_headers=None):
        observed["url"] = url
        return b'{"result":"success","transactions":[]}'

    monkeypatch.setattr(whale_trades, "_fetch_url", fake_fetch)

    assert whale_trades.WhaleAlertSource().fetch("BTC") == []
    assert "api_key=controlled-whale-key-123456" in observed["url"]


def test_whale_http_error_is_sanitized_without_key_or_exception_chain(monkeypatch):
    secret = "controlled-whale-key-123456"
    monkeypatch.setattr(whale_trades, "resolve_api_key", lambda: (secret, "environment"))

    def fail(url, extra_headers=None):
        raise HTTPError(url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(whale_trades, "_fetch_url", fail)
    with pytest.raises(RuntimeError, match=r"^Whale Alert request failed: HTTP 429") as caught:
        whale_trades.WhaleAlertSource().fetch("", coin="BTC")

    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


def test_ssm_parameter_not_found_falls_back_to_env(monkeypatch):
    """P1#2：SSM 參數已設（非空）但尚未建立（ParameterNotFound）= 遷移中，
    应回落 env（保既有安裝升級後不靜默斷料），而非 unavailable。"""
    fake = FakeSSM()  # value=None → get_parameter 拋帶 ParameterNotFound 的例外
    monkeypatch.setattr(whale_alert_secret, "_ssm_client", lambda: fake)
    monkeypatch.setenv(
        "TRUSTFORGE_WHALE_ALERT_SSM_PARAMETER", "/trustforge/test/whale-alert-api-key"
    )
    monkeypatch.delenv("TRUSTFORGE_WHALE_ALERT_API_KEY_FILE", raising=False)
    monkeypatch.setenv("WHALE_ALERT_API_KEY", "local-whale-key-123456")
    whale_alert_secret.invalidate_cache()

    assert whale_alert_secret.resolve_api_key() == ("local-whale-key-123456", "environment")


def test_ssm_non_parameter_not_found_error_fails_closed(monkeypatch):
    """P1#2：SSM 拋非 ParameterNotFound 錯誤（AccessDenied/網路/解密失敗）= 真故障，
    fail-closed 回 unavailable，不回落 env。"""
    class AccessDeniedSSM:
        def get_parameter(self, **kwargs):
            error = RuntimeError("denied")
            error.response = {"Error": {"Code": "AccessDenied"}}  # type: ignore[attr-defined]
            raise error

    monkeypatch.setattr(whale_alert_secret, "_ssm_client", lambda: AccessDeniedSSM())
    monkeypatch.setenv(
        "TRUSTFORGE_WHALE_ALERT_SSM_PARAMETER", "/trustforge/test/whale-alert-api-key"
    )
    monkeypatch.delenv("TRUSTFORGE_WHALE_ALERT_API_KEY_FILE", raising=False)
    monkeypatch.setenv("WHALE_ALERT_API_KEY", "local-whale-key-123456")
    whale_alert_secret.invalidate_cache()

    assert whale_alert_secret.resolve_api_key() == (None, "unavailable")


def test_cached_ssm_key_is_rechecked_within_revocation_window(monkeypatch):
    """A separate ingestion process cannot retain a cleared key for five minutes."""
    fake = FakeSSM()
    fake.value = "cached-whale-key-1234567890"
    now = [100.0]
    monkeypatch.setattr(whale_alert_secret, "_ssm_client", lambda: fake)
    monkeypatch.setattr(whale_alert_secret.time, "monotonic", lambda: now[0])
    monkeypatch.setenv(
        "TRUSTFORGE_WHALE_ALERT_SSM_PARAMETER", "/trustforge/test/whale-alert-api-key"
    )
    monkeypatch.delenv("TRUSTFORGE_WHALE_ALERT_API_KEY_FILE", raising=False)
    monkeypatch.delenv("WHALE_ALERT_API_KEY", raising=False)
    whale_alert_secret.invalidate_cache()

    assert whale_alert_secret.resolve_api_key() == (fake.value, "ssm")
    fake.value = None  # another process/admin deleted the parameter
    now[0] += whale_alert_secret._CACHE_TTL_SECONDS - 0.01
    assert whale_alert_secret.resolve_api_key()[0] is not None
    now[0] += 0.02
    assert whale_alert_secret.resolve_api_key() == (None, "unconfigured")
    assert whale_alert_secret._CACHE_TTL_SECONDS <= 15.0


def test_put_api_key_raises_when_round_trip_drifts(monkeypatch):
    """gray 補測：put_api_key 寫入後 resolve 回的值 != 寫入值（SSM 端 drift）
    → RuntimeError，憑證未靜默啟用錯誤值。"""
    class DriftSSM(FakeSSM):
        def get_parameter(self, **kwargs):
            # 永遠回傳與寫入值不同的值 → round-trip 驗證失敗
            return {"Parameter": {"Value": "different-whale-key-1234567890"}}

    drift = DriftSSM()
    monkeypatch.setattr(whale_alert_secret, "_ssm_client", lambda: drift)
    monkeypatch.setenv(
        "TRUSTFORGE_WHALE_ALERT_SSM_PARAMETER", "/trustforge/test/whale-alert-api-key"
    )
    whale_alert_secret.invalidate_cache()

    with pytest.raises(RuntimeError):
        whale_alert_secret.put_api_key("new-whale-key-1234567890")


# ── converge 雙審（codex P1/P2 + harper M1）第二輪：env-fallback 回歸 ──────


def test_resolve_disables_env_fallback_when_requested(monkeypatch):
    """allow_env_fallback_on_missing_ssm=False：SSM ParameterNotFound 時不再
    回落 env（即使 env 有值），直接 fail-closed unavailable。供 put 驗證與
    clear 真實狀態查詢使用。"""
    fake = FakeSSM()  # value=None → get_parameter 拋帶 ParameterNotFound 的例外
    monkeypatch.setattr(whale_alert_secret, "_ssm_client", lambda: fake)
    monkeypatch.setenv(
        "TRUSTFORGE_WHALE_ALERT_SSM_PARAMETER", "/trustforge/test/whale-alert-api-key"
    )
    monkeypatch.delenv("TRUSTFORGE_WHALE_ALERT_API_KEY_FILE", raising=False)
    monkeypatch.setenv("WHALE_ALERT_API_KEY", "local-whale-key-123456")
    whale_alert_secret.invalidate_cache()

    assert whale_alert_secret.resolve_api_key(
        allow_env_fallback_on_missing_ssm=False
    ) == (None, "unavailable")


def test_put_api_key_rejects_env_fallback_as_activation_verification(monkeypatch):
    """harper M1：put 後驗證不可把 env fallback（值恰好相同）當啟用成功。
    put_parameter 成功但 get_parameter 拋 ParameterNotFound（SSM 端未真正
    留存），且 env 值與寫入值相同——舊邏輯會誤判成功；新邏輯因
    allow_env_fallback_on_missing_ssm=False + source!="ssm" → RuntimeError。"""

    class PutOkGetMissingSSM(FakeSSM):
        def get_parameter(self, **kwargs):
            error = RuntimeError("missing after put")
            error.response = {"Error": {"Code": "ParameterNotFound"}}  # type: ignore[attr-defined]
            raise error

    fake = PutOkGetMissingSSM()
    monkeypatch.setattr(whale_alert_secret, "_ssm_client", lambda: fake)
    monkeypatch.setenv(
        "TRUSTFORGE_WHALE_ALERT_SSM_PARAMETER", "/trustforge/test/whale-alert-api-key"
    )
    monkeypatch.delenv("TRUSTFORGE_WHALE_ALERT_API_KEY_FILE", raising=False)
    key = "activation-equals-env-key-0123456789"
    monkeypatch.setenv("WHALE_ALERT_API_KEY", key)  # 與寫入值相同
    whale_alert_secret.invalidate_cache()

    with pytest.raises(RuntimeError):
        whale_alert_secret.put_api_key(key)


def test_clear_api_key_reports_active_env_after_ssm_delete(monkeypatch):
    """clear 只刪 SSM 參數；env 並存時 status 如實揭露仍在現役
    (True, "environment")，不誤報 unconfigured 誤導操作員以為已撤銷。"""
    fake = FakeSSM()
    fake.value = "ssm-stored-whale-key-1234567890"
    monkeypatch.setattr(whale_alert_secret, "_ssm_client", lambda: fake)
    monkeypatch.setenv(
        "TRUSTFORGE_WHALE_ALERT_SSM_PARAMETER", "/trustforge/test/whale-alert-api-key"
    )
    monkeypatch.delenv("TRUSTFORGE_WHALE_ALERT_API_KEY_FILE", raising=False)
    monkeypatch.setenv("WHALE_ALERT_API_KEY", "legacy-env-whale-key-1234567890")
    whale_alert_secret.invalidate_cache()

    result = whale_alert_secret.clear_api_key()

    assert result == whale_alert_secret.SecretStatus(True, "environment", None)


def test_clear_api_key_disables_when_ssm_was_only_source(monkeypatch):
    """only-SSM：clear 後無 env 無檔 → 真正撤銷 (False, "unconfigured")。"""
    fake = FakeSSM()
    fake.value = "ssm-stored-whale-key-1234567890"
    monkeypatch.setattr(whale_alert_secret, "_ssm_client", lambda: fake)
    monkeypatch.setenv(
        "TRUSTFORGE_WHALE_ALERT_SSM_PARAMETER", "/trustforge/test/whale-alert-api-key"
    )
    monkeypatch.delenv("TRUSTFORGE_WHALE_ALERT_API_KEY_FILE", raising=False)
    monkeypatch.delenv("WHALE_ALERT_API_KEY", raising=False)
    whale_alert_secret.invalidate_cache()

    result = whale_alert_secret.clear_api_key()

    assert result == whale_alert_secret.SecretStatus(False, "unconfigured", None)
