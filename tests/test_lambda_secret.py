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
    for _, arn_env, version_env, target_env in lambda_secret._SECRET_SPECS:
        monkeypatch.delenv(arn_env, raising=False)
        monkeypatch.delenv(version_env, raising=False)
        monkeypatch.delenv(target_env, raising=False)
    monkeypatch.setattr(lambda_secret, "_hydrated", False)
    yield
    for _, _, _, target_env in lambda_secret._SECRET_SPECS:
        os.environ.pop(target_env, None)


def test_missing_secret_arn_preserves_offline_contract():
    client = _SecretsClient()

    assert lambda_secret.hydrate_live_token(client=client) is False
    assert client.calls == []
    assert "TRUSTFORGE_LIVE_TOKEN" not in os.environ


def test_secret_string_is_loaded_once_per_cold_start(monkeypatch):
    arn = "arn:aws:secretsmanager:us-east-1:850849012389:secret:competition-token"
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN_SECRET_ARN", arn)
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN_SECRET_VERSION_ID", "version-1")
    client = _SecretsClient({"SecretString": "private-value"})

    assert lambda_secret.hydrate_live_token(client=client) is True
    assert lambda_secret.hydrate_live_token(client=client) is True
    assert client.calls == [{"SecretId": arn, "VersionId": "version-1"}]
    assert os.environ["TRUSTFORGE_LIVE_TOKEN"] == "private-value"


@pytest.mark.parametrize(
    "response",
    [None, [], {}, {"SecretBinary": b"x"}, {"SecretString": 7}, {"SecretString": []}, {"SecretString": ""}, {"SecretString": "   "}],
)
def test_missing_nonempty_secret_string_fails_closed(monkeypatch, response):
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN_SECRET_ARN", "arn:test")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN_SECRET_VERSION_ID", "version-1")

    with pytest.raises(RuntimeError, match="invalid response|non-empty SecretString"):
        lambda_secret.hydrate_live_token(client=_SecretsClient(response))
    assert "TRUSTFORGE_LIVE_TOKEN" not in os.environ
    assert lambda_secret._hydrated is False


def test_secrets_manager_error_fails_closed_without_leaking(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN_SECRET_ARN", "arn:test")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN_SECRET_VERSION_ID", "version-1")
    error = PermissionError("access denied")

    with pytest.raises(PermissionError, match="access denied"):
        lambda_secret.hydrate_live_token(client=_SecretsClient(error=error))
    assert "TRUSTFORGE_LIVE_TOKEN" not in os.environ
    assert lambda_secret._hydrated is False


def test_plaintext_and_secret_arn_cannot_coexist(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN_SECRET_ARN", "arn:test")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN_SECRET_VERSION_ID", "version-1")
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
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN_SECRET_VERSION_ID", "version-1")
    token = "never-print-this-value"

    assert lambda_secret.hydrate_live_token(client=_SecretsClient({"SecretString": token}))
    captured = capsys.readouterr()
    assert token not in captured.out
    assert token not in captured.err
    assert all(token not in record.getMessage() for record in caplog.records)


def test_all_provider_secrets_are_loaded_atomically(monkeypatch):
    providers = lambda_secret._SECRET_SPECS[1:]
    responses = []
    for index, (_, arn_env, version_env, _) in enumerate(providers):
        monkeypatch.setenv(arn_env, f"arn:provider:{index}")
        monkeypatch.setenv(version_env, f"version-{index}")
        responses.append({"SecretString": f"provider-value-{index}"})

    class Client:
        def __init__(self):
            self.calls = []

        def get_secret_value(self, **request):
            self.calls.append(request)
            return responses[len(self.calls) - 1]

    client = Client()
    assert lambda_secret.hydrate_lambda_secrets(client=client) is True
    assert len(client.calls) == 4
    for index, (_, _, _, target_env) in enumerate(providers):
        assert os.environ[target_env] == f"provider-value-{index}"


def test_partial_provider_failure_leaves_no_plaintext(monkeypatch):
    providers = lambda_secret._SECRET_SPECS[1:3]
    for index, (_, arn_env, version_env, _) in enumerate(providers):
        monkeypatch.setenv(arn_env, f"arn:provider:{index}")
        monkeypatch.setenv(version_env, f"version-{index}")

    class Client:
        calls = 0

        def get_secret_value(self, **request):
            self.calls += 1
            if self.calls == 2:
                raise PermissionError("denied")
            return {"SecretString": "first-provider-value"}

    with pytest.raises(PermissionError, match="denied"):
        lambda_secret.hydrate_lambda_secrets(client=Client())
    assert all(target_env not in os.environ for _, _, _, target_env in providers)
    assert lambda_secret._hydrated is False


def test_configured_secret_requires_pinned_version(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_ARKHAM_SECRET_ARN", "arn:arkham")

    with pytest.raises(RuntimeError, match="requires ARN and VersionId"):
        lambda_secret.hydrate_lambda_secrets(client=_SecretsClient())
