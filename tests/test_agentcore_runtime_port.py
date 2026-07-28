from __future__ import annotations

from dataclasses import dataclass
from unittest import mock

import pytest

from trustforge.agent.agentcore_memory import build_memory_session_manager
from trustforge.agent.agentcore_runtime import (
    analyze_market,
    invoke_payload,
    list_supported_coins,
)


@dataclass
class _Value:
    value: str


def test_supported_coins_uses_canonical_pool():
    assert list_supported_coins() == ["ARB", "BNB", "BTC", "ETH", "SOL", "XRP"]


def test_invoke_payload_validates_required_fields():
    with pytest.raises(ValueError, match="coin must be a string"):
        invoke_payload({"query": "市場如何"})
    with pytest.raises(ValueError, match="query must be a string"):
        invoke_payload({"coin": "BTC"})


def test_invoke_payload_rejects_non_string_and_oversized_values():
    with pytest.raises(ValueError, match="coin must be a string"):
        invoke_payload({"coin": ["BTC"], "query": "市場如何"})
    with pytest.raises(ValueError, match="query must be a string"):
        invoke_payload({"coin": "BTC", "query": {"text": "市場如何"}})
    with pytest.raises(ValueError, match="query is too long"):
        invoke_payload({"coin": "BTC", "query": "x" * 4001})


def test_analyze_market_delegates_to_governed_pipeline():
    with mock.patch(
        "trustforge.agent.agentcore_runtime.run",
        return_value=(_Value("report"), [_Value("evidence")], _Value("log")),
    ) as pipeline:
        result = analyze_market(
            "btc",
            "市場如何",
            data_mode="sample",
            llm_mode="off",
        )

    assert result == {
        "report": {"value": "report"},
        "evidence": [{"value": "evidence"}],
        "execution_log": {"value": "log"},
    }
    assert pipeline.call_args.args[:3][0:2] == ("BTC", "市場如何")
    assert pipeline.call_args.kwargs == {"data_mode": "sample", "llm_mode": "off"}


def test_memory_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TRUSTFORGE_AGENTCORE_MEMORY_ENABLED", raising=False)
    assert (
        build_memory_session_manager(actor_id="actor", session_id="session") is None
    )


def test_memory_requires_id_when_enabled(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_AGENTCORE_MEMORY_ENABLED", "true")
    monkeypatch.delenv("TRUSTFORGE_AGENTCORE_MEMORY_ID", raising=False)
    with pytest.raises(RuntimeError, match="MEMORY_ID"):
        build_memory_session_manager(actor_id="actor", session_id="session")


def test_memory_factory_receives_scoped_identity(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_AGENTCORE_MEMORY_ENABLED", "true")
    monkeypatch.setenv("TRUSTFORGE_AGENTCORE_MEMORY_ID", "memory-1")
    factory = mock.Mock(return_value=object())

    result = build_memory_session_manager(
        actor_id="actor-1",
        session_id="session-1",
        factory=factory,
    )

    assert result is factory.return_value
    factory.assert_called_once_with(
        agentcore_memory_id="memory-1",
        actor_id="actor-1",
        session_id="session-1",
    )
