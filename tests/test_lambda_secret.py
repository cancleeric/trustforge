from __future__ import annotations

import os

import pytest

from trustforge import lambda_secret


class _SecretsClient:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response or {}
        self.error = error
        self.calls: list[str] = []

    def get_secret_value(self, *, SecretId: str):
        self.calls.append(SecretId)
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture(autouse=True)
def _reset_secret_loader(monkeypatch):
    monkeypatch.delenv("TRUSTFORGE_LIVE_TOKEN_SECRET_ARN", raising=False)
    monkeypatch.delenv("TRUSTFORGE_LIVE_TOKEN", raising=False)
    monkeypatch.setattr(lambda_secret, "_hydrated", False)
    yield
    os.environ.pop("TRUSTFORGE_LIVE_TOKEN", None)


def test_missing_secret_arn_preserves_offline_contract():
    client = _SecretsClient()

    assert lambda_secret.hydrate_live_token(client=client) is False
    assert client.calls == []
    assert "TRUSTFORGE_LIVE_TOKEN" not in os.environ


def test_secret_string_is_loaded_once_per_cold_start(monkeypatch):
    arn = "arn:aws:secretsmanager:us-east-1:850849012389:secret:competition-token"
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN_SECRET_ARN", arn)
    client = _SecretsClient({"SecretString": "private-value"})

    assert lambda_secret.hydrate_live_token(client=client) is True
    assert lambda_secret.hydrate_live_token(client=client) is True
    assert client.calls == [arn]
    assert os.environ["TRUSTFORGE_LIVE_TOKEN"] == "private-value"


@pytest.mark.parametrize("response", [{}, {"SecretBinary": b"x"}, {"SecretString": ""}, {"SecretString": "   "}])
def test_missing_nonempty_secret_string_fails_closed(monkeypatch, response):
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN_SECRET_ARN", "arn:test")

    with pytest.raises(RuntimeError, match="non-empty SecretString"):
        lambda_secret.hydrate_live_token(client=_SecretsClient(response))
    assert "TRUSTFORGE_LIVE_TOKEN" not in os.environ
    assert lambda_secret._hydrated is False


def test_secrets_manager_error_fails_closed_without_leaking(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN_SECRET_ARN", "arn:test")
    error = PermissionError("access denied")

    with pytest.raises(PermissionError, match="access denied"):
        lambda_secret.hydrate_live_token(client=_SecretsClient(error=error))
    assert "TRUSTFORGE_LIVE_TOKEN" not in os.environ
    assert lambda_secret._hydrated is False


def test_plaintext_and_secret_arn_cannot_coexist(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN_SECRET_ARN", "arn:test")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", "must-not-be-used")

    with pytest.raises(RuntimeError, match="must not be configured"):
        lambda_secret.hydrate_live_token(client=_SecretsClient())
