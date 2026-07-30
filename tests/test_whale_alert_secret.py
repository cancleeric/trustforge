from __future__ import annotations

import json

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
