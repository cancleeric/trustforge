from __future__ import annotations

import json
import os
from pathlib import Path

from trustforge.config_snapshot import (
    ConfigSnapshot,
    _capture_config_json,
    current_config_identity,
)


def test_snapshot_identity_deterministic(monkeypatch) -> None:
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-model")
    monkeypatch.setenv("TRUSTFORGE_CSP_MODE", "legacy")
    for k in (
        "CACHE_BACKEND", "TRUSTFORGE_CACHE_TABLE", "TRUSTFORGE_COST_LEDGER_TABLE",
        "COST_LEDGER_BACKEND", "AWS_REGION", "TRUSTFORGE_BUDGET_GUARD_BACKEND",
        "TRUSTFORGE_BUDGET_COUNTER_TABLE", "TRUSTFORGE_CW_METRICS",
        "TRUSTFORGE_IDEMPOTENCY_LEASE_BACKEND", "TRUSTFORGE_LEASE_TABLE",
    ):
        monkeypatch.delenv(k, raising=False)

    s1 = ConfigSnapshot.capture(host="test")
    s2 = ConfigSnapshot.capture(host="test")

    assert s1.identity == s2.identity
    assert s1.identity.startswith("sha256:")

    payload = json.loads(s1.payload)
    assert payload["BEDROCK_MODEL_ID"] == "test-model"
    assert payload["TRUSTFORGE_CSP_MODE"] == "legacy"


def test_snapshot_with_host() -> None:
    snapshot = ConfigSnapshot.capture(host="buildhost")
    assert snapshot.captured_host == "buildhost"
    assert snapshot.captured_at is not None


def test_snapshot_captures_env(monkeypatch) -> None:
    monkeypatch.setenv("BEDROCK_MODEL_ID", "claude-v3")
    monkeypatch.setenv("CACHE_BACKEND", "dynamodb")
    for k in os.environ:
        if k not in ("BEDROCK_MODEL_ID", "CACHE_BACKEND", "PATH", "HOME", "USER"):
            if k in {
                "TRUSTFORGE_CACHE_TABLE", "TRUSTFORGE_COST_LEDGER_TABLE",
                "COST_LEDGER_BACKEND", "AWS_REGION", "TRUSTFORGE_CSP_MODE",
                "TRUSTFORGE_BUDGET_GUARD_BACKEND", "TRUSTFORGE_BUDGET_COUNTER_TABLE",
                "TRUSTFORGE_CW_METRICS", "TRUSTFORGE_IDEMPOTENCY_LEASE_BACKEND",
                "TRUSTFORGE_LEASE_TABLE",
            }:
                monkeypatch.delenv(k, raising=False)

    snapshot = ConfigSnapshot.capture(host="test")
    payload = json.loads(snapshot.payload)
    assert payload.get("BEDROCK_MODEL_ID") == "claude-v3"
    assert payload.get("CACHE_BACKEND") == "dynamodb"


def test_snapshot_roundtrip() -> None:
    s1 = ConfigSnapshot.capture(host="test")
    raw = s1.to_bytes()
    s2 = ConfigSnapshot.from_bytes(raw)
    assert s1.identity == s2.identity
    assert s1.captured_at == s2.captured_at
    assert s1.captured_host == s2.captured_host
    assert s1.payload == s2.payload


def test_sensitive_tokens_redacted(monkeypatch) -> None:
    for k in (
        "BEDROCK_MODEL_ID", "CACHE_BACKEND", "TRUSTFORGE_CACHE_TABLE",
        "TRUSTFORGE_COST_LEDGER_TABLE", "COST_LEDGER_BACKEND", "AWS_REGION",
        "TRUSTFORGE_CSP_MODE", "TRUSTFORGE_BUDGET_GUARD_BACKEND",
        "TRUSTFORGE_BUDGET_COUNTER_TABLE", "TRUSTFORGE_CW_METRICS",
        "TRUSTFORGE_IDEMPOTENCY_LEASE_BACKEND", "TRUSTFORGE_LEASE_TABLE",
    ):
        monkeypatch.delenv(k, raising=False)

    monkeypatch.setenv("TRUSTFORGE_ADMIN_TOKEN", "secret-admin-token")
    monkeypatch.setenv("TRUSTFORGE_LIVE_TOKEN", "secret-live-token")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "claude")
    monkeypatch.setenv("TRUSTFORGE_CSP_MODE", "legacy")

    snapshot = ConfigSnapshot.capture(host="test")
    payload_str = snapshot.payload
    assert "secret-admin-token" not in payload_str
    assert "secret-live-token" not in payload_str


def test_current_config_identity(monkeypatch) -> None:
    for k in (
        "BEDROCK_MODEL_ID", "CACHE_BACKEND", "TRUSTFORGE_CACHE_TABLE",
        "TRUSTFORGE_COST_LEDGER_TABLE", "COST_LEDGER_BACKEND", "AWS_REGION",
        "TRUSTFORGE_CSP_MODE", "TRUSTFORGE_BUDGET_GUARD_BACKEND",
        "TRUSTFORGE_BUDGET_COUNTER_TABLE", "TRUSTFORGE_CW_METRICS",
        "TRUSTFORGE_IDEMPOTENCY_LEASE_BACKEND", "TRUSTFORGE_LEASE_TABLE",
        "TRUSTFORGE_ADMIN_TOKEN", "TRUSTFORGE_LIVE_TOKEN",
    ):
        monkeypatch.delenv(k, raising=False)

    monkeypatch.setenv("BEDROCK_MODEL_ID", "model-x")
    monkeypatch.setenv("TRUSTFORGE_CSP_MODE", "react")
    identity = current_config_identity()
    assert identity.startswith("sha256:")


def test_snapshot_env_key_subset(monkeypatch) -> None:
    for k in (
        "BEDROCK_MODEL_ID", "CACHE_BACKEND", "TRUSTFORGE_CACHE_TABLE",
        "TRUSTFORGE_COST_LEDGER_TABLE", "COST_LEDGER_BACKEND", "AWS_REGION",
        "TRUSTFORGE_CSP_MODE", "TRUSTFORGE_BUDGET_GUARD_BACKEND",
        "TRUSTFORGE_BUDGET_COUNTER_TABLE", "TRUSTFORGE_CW_METRICS",
        "TRUSTFORGE_IDEMPOTENCY_LEASE_BACKEND", "TRUSTFORGE_LEASE_TABLE",
    ):
        monkeypatch.delenv(k, raising=False)

    monkeypatch.setenv("BEDROCK_MODEL_ID", "alpha")
    payload = json.loads(_capture_config_json())
    assert "BEDROCK_MODEL_ID" in payload
    assert "IRRELEVANT_VAR" not in payload
