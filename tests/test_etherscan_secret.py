from __future__ import annotations

import json

import pytest

from trustforge import etherscan_secret
from trustforge.ingestion import etherscan


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
        "TRUSTFORGE_ETHERSCAN_SSM_PARAMETER",
        "/trustforge/test/etherscan-api-key",
    )
    monkeypatch.setattr(etherscan_secret, "_ssm_client", lambda: fake)
    etherscan_secret.invalidate_cache()

    secret = "test-etherscan-key-1234567890"
    result = etherscan_secret.put_api_key(secret).as_dict()

    assert fake.put_calls == [
        {
            "Name": "/trustforge/test/etherscan-api-key",
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
    fake.value = "test-etherscan-key-1234567890"
    monkeypatch.setenv(
        "TRUSTFORGE_ETHERSCAN_SSM_PARAMETER",
        "/trustforge/test/etherscan-api-key",
    )
    monkeypatch.setattr(etherscan_secret, "_ssm_client", lambda: fake)
    etherscan_secret.invalidate_cache()
    observed = {}

    def fetcher(url, **kwargs):
        observed["url"] = url
        observed.update(kwargs)
        return b'{"status":"1","message":"OK","result":{"SafeGasPrice":"23","ProposeGasPrice":"25","FastGasPrice":"30","suggestedBaseFee":"10"}}'

    result = etherscan_secret.verify_connection(fetcher=fetcher).as_dict()

    assert result["configured"] is True
    assert result["last_verified_at"]
    assert observed["timeout"] == 5
    assert observed["max_bytes"] == 64 * 1024
    assert observed["max_redirects"] == 0
    # V2 唯一選項：key 走 query param apikey=（非 header）。
    assert "apikey=test-etherscan-key-1234567890" in observed["url"]
    # status 一律遮罩，不含明文 key。
    assert "test-etherscan-key-1234567890" not in json.dumps(result)


def test_verify_url_is_v2_gasoracle_endpoint(monkeypatch):
    """verify 打的是 V2 gasoracle 端點（chainid=1 + module=gastracker + action=gasoracle）。"""
    fake = FakeSSM()
    fake.value = "test-etherscan-key-1234567890"
    monkeypatch.setenv(
        "TRUSTFORGE_ETHERSCAN_SSM_PARAMETER",
        "/trustforge/test/etherscan-api-key",
    )
    monkeypatch.setattr(etherscan_secret, "_ssm_client", lambda: fake)
    etherscan_secret.invalidate_cache()
    observed = {}

    def fetcher(url, **kwargs):
        observed["url"] = url
        return b'{"status":"1","message":"OK","result":{"SafeGasPrice":"23"}}'

    etherscan_secret.verify_connection(fetcher=fetcher)
    url = observed["url"]
    assert "api.etherscan.io/v2/api" in url
    assert "chainid=1" in url
    assert "module=gastracker" in url
    assert "action=gasoracle" in url


def test_verify_rejects_when_result_lacks_safegasprice(monkeypatch):
    """result 缺 SafeGasPrice（如憑證被拒、result 是錯誤字串）→ RuntimeError。"""
    fake = FakeSSM()
    fake.value = "test-etherscan-key-1234567890"
    monkeypatch.setenv(
        "TRUSTFORGE_ETHERSCAN_SSM_PARAMETER",
        "/trustforge/test/etherscan-api-key",
    )
    monkeypatch.setattr(etherscan_secret, "_ssm_client", lambda: fake)
    etherscan_secret.invalidate_cache()

    def fetcher(url, **kwargs):
        # Etherscan 失敗時 result 是錯誤訊息字串（非 dict），status=0。
        return b'{"status":"0","message":"Invalid API Key","result":"Invalid API Key"}'

    with pytest.raises(RuntimeError, match="rejected"):
        etherscan_secret.verify_connection(fetcher=fetcher)


def test_environment_fallback_only_when_ssm_parameter_is_not_enabled(monkeypatch):
    monkeypatch.delenv("TRUSTFORGE_ETHERSCAN_SSM_PARAMETER", raising=False)
    monkeypatch.delenv("TRUSTFORGE_ETHERSCAN_API_KEY_FILE", raising=False)
    monkeypatch.setenv("ETHERSCAN_API_KEY", "local-etherscan-key-123456")
    etherscan_secret.invalidate_cache()

    assert etherscan_secret.resolve_api_key() == (
        "local-etherscan-key-123456",
        "environment",
    )


def test_secure_local_key_file_is_supported_without_exposing_value(
    monkeypatch, tmp_path
):
    key_file = tmp_path / "etherscan.apikey"
    key_file.write_text("local-file-etherscan-key-123456", encoding="utf-8")
    key_file.chmod(0o600)
    monkeypatch.delenv("TRUSTFORGE_ETHERSCAN_SSM_PARAMETER", raising=False)
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
    monkeypatch.setenv("TRUSTFORGE_ETHERSCAN_API_KEY_FILE", str(key_file))

    value, source = etherscan_secret.resolve_api_key()

    assert value == "local-file-etherscan-key-123456"
    assert source == "file"


def test_local_key_file_fails_closed_when_permissions_are_too_broad(
    monkeypatch, tmp_path
):
    key_file = tmp_path / "etherscan.apikey"
    key_file.write_text("local-file-etherscan-key-123456", encoding="utf-8")
    key_file.chmod(0o644)
    monkeypatch.delenv("TRUSTFORGE_ETHERSCAN_SSM_PARAMETER", raising=False)
    monkeypatch.setenv("TRUSTFORGE_ETHERSCAN_API_KEY_FILE", str(key_file))

    assert etherscan_secret.resolve_api_key() == (None, "unavailable")


def test_etherscan_connector_uses_controlled_secret_resolver(monkeypatch):
    """connector 從 etherscan_secret.resolve_api_key 取 key（不打真 SSM/env）。"""
    observed = {}
    monkeypatch.setattr(
        etherscan,
        "resolve_api_key",
        lambda: ("controlled-etherscan-key-1234567890", "ssm"),
    )

    def fake_fetch(url):
        observed["url"] = url
        return b'{"status":"1","message":"OK","result":[]}'

    monkeypatch.setattr(etherscan, "_fetch_url", fake_fetch)

    assert etherscan.EtherscanWhaleSource().fetch("ETH", coin="ETH") == []
    # V2 唯一選項：key 在 query param apikey=。
    assert "apikey=controlled-etherscan-key-1234567890" in observed["url"]


def test_ssm_parameter_not_found_falls_back_to_env(monkeypatch):
    """SSM 參數已設（非空）但尚未建立（ParameterNotFound）= 遷移中，回落 env。"""
    fake = FakeSSM()  # value=None → get_parameter 拋帶 ParameterNotFound 的例外
    monkeypatch.setattr(etherscan_secret, "_ssm_client", lambda: fake)
    monkeypatch.setenv(
        "TRUSTFORGE_ETHERSCAN_SSM_PARAMETER", "/trustforge/test/etherscan-api-key"
    )
    monkeypatch.delenv("TRUSTFORGE_ETHERSCAN_API_KEY_FILE", raising=False)
    monkeypatch.setenv("ETHERSCAN_API_KEY", "local-etherscan-key-123456")
    etherscan_secret.invalidate_cache()

    assert etherscan_secret.resolve_api_key() == (
        "local-etherscan-key-123456",
        "environment",
    )


def test_ssm_non_parameter_not_found_error_fails_closed(monkeypatch):
    """SSM 拋非 ParameterNotFound 錯誤（AccessDenied/網路/解密失敗）= 真故障，
    fail-closed 回 unavailable，不回落 env。"""

    class AccessDeniedSSM:
        def get_parameter(self, **kwargs):
            error = RuntimeError("denied")
            error.response = {"Error": {"Code": "AccessDenied"}}  # type: ignore[attr-defined]
            raise error

    monkeypatch.setattr(etherscan_secret, "_ssm_client", lambda: AccessDeniedSSM())
    monkeypatch.setenv(
        "TRUSTFORGE_ETHERSCAN_SSM_PARAMETER", "/trustforge/test/etherscan-api-key"
    )
    monkeypatch.delenv("TRUSTFORGE_ETHERSCAN_API_KEY_FILE", raising=False)
    monkeypatch.setenv("ETHERSCAN_API_KEY", "local-etherscan-key-123456")
    etherscan_secret.invalidate_cache()

    assert etherscan_secret.resolve_api_key() == (None, "unavailable")


def test_put_api_key_raises_when_round_trip_drifts(monkeypatch):
    """put_api_key 寫入後 resolve 回的值 != 寫入值（SSM 端 drift）→ RuntimeError。"""

    class DriftSSM(FakeSSM):
        def get_parameter(self, **kwargs):
            return {"Parameter": {"Value": "different-etherscan-key-1234567890"}}

    drift = DriftSSM()
    monkeypatch.setattr(etherscan_secret, "_ssm_client", lambda: drift)
    monkeypatch.setenv(
        "TRUSTFORGE_ETHERSCAN_SSM_PARAMETER", "/trustforge/test/etherscan-api-key"
    )
    etherscan_secret.invalidate_cache()

    with pytest.raises(RuntimeError):
        etherscan_secret.put_api_key("new-etherscan-key-1234567890")


def test_resolve_disables_env_fallback_when_requested(monkeypatch):
    """allow_env_fallback_on_missing_ssm=False：SSM ParameterNotFound 時不再回落 env。"""
    fake = FakeSSM()
    monkeypatch.setattr(etherscan_secret, "_ssm_client", lambda: fake)
    monkeypatch.setenv(
        "TRUSTFORGE_ETHERSCAN_SSM_PARAMETER", "/trustforge/test/etherscan-api-key"
    )
    monkeypatch.delenv("TRUSTFORGE_ETHERSCAN_API_KEY_FILE", raising=False)
    monkeypatch.setenv("ETHERSCAN_API_KEY", "local-etherscan-key-123456")
    etherscan_secret.invalidate_cache()

    assert etherscan_secret.resolve_api_key(
        allow_env_fallback_on_missing_ssm=False
    ) == (None, "unavailable")


def test_put_api_key_rejects_env_fallback_as_activation_verification(monkeypatch):
    """put 後驗證不可把 env fallback（值恰好相同）當啟用成功。"""

    class PutOkGetMissingSSM(FakeSSM):
        def get_parameter(self, **kwargs):
            error = RuntimeError("missing after put")
            error.response = {"Error": {"Code": "ParameterNotFound"}}  # type: ignore[attr-defined]
            raise error

    fake = PutOkGetMissingSSM()
    monkeypatch.setattr(etherscan_secret, "_ssm_client", lambda: fake)
    monkeypatch.setenv(
        "TRUSTFORGE_ETHERSCAN_SSM_PARAMETER", "/trustforge/test/etherscan-api-key"
    )
    monkeypatch.delenv("TRUSTFORGE_ETHERSCAN_API_KEY_FILE", raising=False)
    key = "activation-equals-env-key-0123456789"
    monkeypatch.setenv("ETHERSCAN_API_KEY", key)  # 與寫入值相同
    etherscan_secret.invalidate_cache()

    with pytest.raises(RuntimeError):
        etherscan_secret.put_api_key(key)


def test_clear_api_key_reports_active_env_after_ssm_delete(monkeypatch):
    """clear 只刪 SSM 參數；env 並存時 status 如實揭露仍在現役 (True, "environment")。"""
    fake = FakeSSM()
    fake.value = "ssm-stored-etherscan-key-1234567890"
    monkeypatch.setattr(etherscan_secret, "_ssm_client", lambda: fake)
    monkeypatch.setenv(
        "TRUSTFORGE_ETHERSCAN_SSM_PARAMETER", "/trustforge/test/etherscan-api-key"
    )
    monkeypatch.delenv("TRUSTFORGE_ETHERSCAN_API_KEY_FILE", raising=False)
    monkeypatch.setenv("ETHERSCAN_API_KEY", "legacy-env-etherscan-key-1234567890")
    etherscan_secret.invalidate_cache()

    result = etherscan_secret.clear_api_key()

    assert result == etherscan_secret.SecretStatus(True, "environment", None)


def test_clear_api_key_disables_when_ssm_was_only_source(monkeypatch):
    """only-SSM：clear 後無 env 無檔 → 真正撤銷 (False, "unconfigured")。"""
    fake = FakeSSM()
    fake.value = "ssm-stored-etherscan-key-1234567890"
    monkeypatch.setattr(etherscan_secret, "_ssm_client", lambda: fake)
    monkeypatch.setenv(
        "TRUSTFORGE_ETHERSCAN_SSM_PARAMETER", "/trustforge/test/etherscan-api-key"
    )
    monkeypatch.delenv("TRUSTFORGE_ETHERSCAN_API_KEY_FILE", raising=False)
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
    etherscan_secret.invalidate_cache()

    result = etherscan_secret.clear_api_key()

    assert result == etherscan_secret.SecretStatus(False, "unconfigured", None)
