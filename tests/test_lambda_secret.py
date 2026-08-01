from __future__ import annotations

import os

import pytest

from trustforge import lambda_secret


_UNSET = object()


class _SecretsClient:
    def __init__(self, response=_UNSET, error: Exception | None = None):
        self.response = {} if response is _UNSET else response
        self.error = error
        self.calls: list[str] = []

    def get_secret_value(self, **request):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture(autouse=True)
def _reset_secret_loader(monkeypatch):
    monkeypatch.delenv("TRUSTFORGE_LIVE_TOKEN_SECRET_ARN", raising=False)
    monkeypatch.delenv("TRUSTFORGE_LIVE_TOKEN", raising=False)
    monkeypatch.delenv("TRUSTFORGE_LIVE_TOKEN_SECRET_VERSION_ID", raising=False)
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
    assert client.calls == [{"SecretId": arn}]
    assert os.environ["TRUSTFORGE_LIVE_TOKEN"] == "private-value"


@pytest.mark.parametrize(
    "response",
    [None, [], {}, {"SecretBinary": b"x"}, {"SecretString": 7}, {"SecretString": []}, {"SecretString": ""}, {"SecretString": "   "}],
)
def test_missing_nonempty_secret_string_fails_closed(monkeypatch, response):
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN_SECRET_ARN", "arn:test")

    with pytest.raises(RuntimeError, match="invalid response|non-empty SecretString"):
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


def test_version_id_is_pinned_for_rotation(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN_SECRET_ARN", "arn:test")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN_SECRET_VERSION_ID", "version-2")
    client = _SecretsClient({"SecretString": "rotated-value"})

    assert lambda_secret.hydrate_live_token(client=client) is True
    assert client.calls == [{"SecretId": "arn:test", "VersionId": "version-2"}]


def test_token_value_is_not_emitted(monkeypatch, capsys, caplog):
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN_SECRET_ARN", "arn:test")
    token = "never-print-this-value"

    assert lambda_secret.hydrate_live_token(client=_SecretsClient({"SecretString": token}))
    captured = capsys.readouterr()
    assert token not in captured.out
    assert token not in captured.err
    assert all(token not in record.getMessage() for record in caplog.records)
