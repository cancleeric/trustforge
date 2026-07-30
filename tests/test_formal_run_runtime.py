from __future__ import annotations

import pytest

from trustforge.formal_run_idempotency import IdempotencyUnavailable
from trustforge.formal_run_idempotency_dynamodb import (
    DynamoDbFormalRunIdempotencyStore,
)
from trustforge.formal_run_idempotency_sqlite import SqliteFormalRunIdempotencyStore
from trustforge.formal_run_runtime import formal_run_store


def test_development_factory_requires_explicit_absolute_sqlite_path(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("TRUSTFORGE_ENV", "development")
    monkeypatch.delenv("TRUSTFORGE_FORMAL_RUN_SQLITE_PATH", raising=False)
    with pytest.raises(IdempotencyUnavailable, match="path is not configured"):
        formal_run_store()
    with pytest.raises(IdempotencyUnavailable, match="absolute"):
        formal_run_store(sqlite_path="relative.db")
    store = formal_run_store(sqlite_path=tmp_path / "formal.db")
    assert isinstance(store, SqliteFormalRunIdempotencyStore)


def test_unknown_environment_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTFORGE_ENV", "staging")
    with pytest.raises(IdempotencyUnavailable, match="explicitly"):
        formal_run_store(sqlite_path=tmp_path / "formal.db")


def test_production_never_falls_back_to_sqlite(monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTFORGE_ENV", "production")
    monkeypatch.setenv("TRUSTFORGE_FORMAL_RUN_SQLITE_PATH", str(tmp_path / "formal.db"))
    monkeypatch.delenv("TRUSTFORGE_FORMAL_RUN_DYNAMODB_TABLE", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    with pytest.raises(IdempotencyUnavailable, match="DynamoDB"):
        formal_run_store()
    assert not (tmp_path / "formal.db").exists()


def test_production_factory_builds_dynamodb(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_ENV", "production")
    monkeypatch.setenv("TRUSTFORGE_FORMAL_RUN_DYNAMODB_TABLE", "formal-authority")
    monkeypatch.setenv("AWS_REGION", "ap-northeast-1")

    class Client:
        get_item = put_item = update_item = delete_item = transact_write_items = (
            lambda self, **_kwargs: {}
        )

    monkeypatch.setattr("boto3.client", lambda *_args, **_kwargs: Client())
    assert isinstance(formal_run_store(), DynamoDbFormalRunIdempotencyStore)
