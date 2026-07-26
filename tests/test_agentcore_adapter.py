"""Non-production integration tests for the AgentCoreRuntime adapter (#409).

All tests mock the AgentCore response — never touch the network.
"""

from __future__ import annotations

from unittest import mock

import pytest

from trustforge.agent.agentcore_adapter import (
    _agentcore_invoke,
    _builtin_invoke,
    invoke_agent,
    is_agentcore_active,
)


# ---------------------------------------------------------------------------
# invoke_agent routing
# ---------------------------------------------------------------------------

def test_invoke_agent_routes_to_builtin_when_provider_is_builtin(
    tmp_path, monkeypatch,
):
    """Default provider="builtin" → _builtin_invoke path."""
    monkeypatch.setenv(
        "TRUSTFORGE_BACKEND_REGISTRY_PATH",
        str(tmp_path / "providers.json"),
    )

    result = invoke_agent("test-agent", "hello")

    assert result["status"] == "succeeded"
    assert result["run_id"].startswith("builtin-")
    assert "hello" in result["output"]["completion"]


def test_invoke_agent_routes_to_agentcore_when_provider_is_agentcore(
    tmp_path, monkeypatch,
):
    """Provider="agentcore" routes to the AgentCore path."""
    monkeypatch.setenv(
        "TRUSTFORGE_BACKEND_REGISTRY_PATH",
        str(tmp_path / "providers.json"),
    )

    from trustforge.backend_registry import set_provider

    set_provider("llm", "agentcore")

    # Monkeypatch the internal agentcore function so we never touch boto3.
    fake_response = {
        "run_id": "run-mocked-001",
        "status": "succeeded",
        "output": {"completion": "AgentCore says: hello back"},
    }

    with mock.patch(
        "trustforge.agent.agentcore_adapter._agentcore_invoke",
        return_value=fake_response,
    ) as mocked:
        result = invoke_agent("test-agent", "ping")

    mocked.assert_called_once_with(
        agent_name="test-agent",
        input_text="ping",
        session_id=None,
    )
    assert result == fake_response


def test_invoke_agent_passes_session_id_through(
    tmp_path, monkeypatch,
):
    """session_id is forwarded to the backend path."""
    monkeypatch.setenv(
        "TRUSTFORGE_BACKEND_REGISTRY_PATH",
        str(tmp_path / "providers.json"),
    )

    from trustforge.backend_registry import set_provider

    set_provider("llm", "agentcore")

    fake_response = {
        "run_id": "run-session-002",
        "status": "succeeded",
        "output": {"completion": "ok"},
    }

    with mock.patch(
        "trustforge.agent.agentcore_adapter._agentcore_invoke",
        return_value=fake_response,
    ) as mocked:
        invoke_agent("agent-x", "task", session_id="sess-42")

    mocked.assert_called_once_with(
        agent_name="agent-x",
        input_text="task",
        session_id="sess-42",
    )


# ---------------------------------------------------------------------------
# is_agentcore_active
# ---------------------------------------------------------------------------

def test_is_agentcore_active_false_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "TRUSTFORGE_BACKEND_REGISTRY_PATH",
        str(tmp_path / "providers.json"),
    )
    assert is_agentcore_active() is False


def test_is_agentcore_active_true_when_switched(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "TRUSTFORGE_BACKEND_REGISTRY_PATH",
        str(tmp_path / "providers.json"),
    )
    from trustforge.backend_registry import set_provider

    set_provider("llm", "agentcore")
    assert is_agentcore_active() is True


# ---------------------------------------------------------------------------
# Response shape contract
# ---------------------------------------------------------------------------

REQUIRED_KEYS = {"run_id", "status", "output"}


def test_builtin_invoke_returns_contract_shape():
    result = _builtin_invoke(agent_name="shape-test", input_text="verify me")
    assert REQUIRED_KEYS.issubset(result.keys())
    assert isinstance(result["run_id"], str) and result["run_id"]
    assert result["status"] == "succeeded"
    assert isinstance(result["output"], dict)


def test_agentcore_invoke_mocked_returns_contract_shape(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "TRUSTFORGE_BACKEND_REGISTRY_PATH",
        str(tmp_path / "providers.json"),
    )
    from trustforge.backend_registry import set_provider
    set_provider("llm", "agentcore")

    fake = {
        "run_id": "run-shape-999",
        "status": "succeeded",
        "output": {"completion": "shape check"},
    }
    with mock.patch(
        "trustforge.agent.agentcore_adapter._agentcore_invoke",
        return_value=fake,
    ):
        result = invoke_agent("agent-shape", "check", session_id=None)

    assert REQUIRED_KEYS.issubset(result.keys())
    assert result["run_id"] == "run-shape-999"
    assert result["status"] == "succeeded"


# ---------------------------------------------------------------------------
# _agentcore_invoke module attribute (exists for monkeypatch targets)
# ---------------------------------------------------------------------------

def test_agentcore_invoke_symbol_is_callable():
    """Verify the symbol exists so monkeypatch targets don't silently miss."""
    assert callable(_agentcore_invoke)
