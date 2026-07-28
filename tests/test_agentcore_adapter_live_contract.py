from __future__ import annotations

import io
import json
from unittest import mock

from trustforge.agent.agentcore_adapter import _agentcore_invoke, agentcore_status


def test_agentcore_invoke_fails_closed_without_runtime(monkeypatch):
    monkeypatch.delenv("TRUSTFORGE_AGENTCORE_RUNTIME_ARN", raising=False)
    result = _agentcore_invoke(agent_name="hermes", input_text="hello")
    assert result["status"] == "failed"
    assert "not configured" in result["output"]["error"]


def test_agentcore_invoke_uses_documented_runtime_contract(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_AGENTCORE_RUNTIME_ARN", "arn:test:runtime")
    client = mock.Mock()
    client.invoke_agent_runtime.return_value = {
        "runtimeSessionId": "12345678-1234-1234-1234-123456789012",
        "statusCode": 200,
        "response": io.BytesIO(json.dumps({"completion": "ok"}).encode()),
    }

    result = _agentcore_invoke(
        agent_name="hermes",
        input_text="hello",
        session_id="12345678-1234-1234-1234-123456789012",
        client=client,
    )

    assert result == {
        "run_id": "12345678-1234-1234-1234-123456789012",
        "status": "succeeded",
        "output": {"completion": "ok"},
    }
    request = client.invoke_agent_runtime.call_args.kwargs
    assert request["agentRuntimeArn"] == "arn:test:runtime"
    assert (
        request["runtimeSessionId"] == "12345678-1234-1234-1234-123456789012"
    )
    assert json.loads(request["payload"]) == {
        "prompt": "hello",
        "agent_name": "hermes",
    }


def test_agentcore_invoke_rejects_empty_or_statusless_response(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_AGENTCORE_RUNTIME_ARN", "arn:test:runtime")
    client = mock.Mock()
    client.invoke_agent_runtime.return_value = {
        "runtimeSessionId": "session-1",
        "response": io.BytesIO(b""),
    }

    result = _agentcore_invoke(
        agent_name="hermes",
        input_text="hello",
        client=client,
    )

    assert result["status"] == "failed"


def test_agentcore_invoke_rejects_short_session_id(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_AGENTCORE_RUNTIME_ARN", "arn:test:runtime")
    client = mock.Mock()

    result = _agentcore_invoke(
        agent_name="hermes",
        input_text="hello",
        session_id="too-short",
        client=client,
    )

    assert result["status"] == "failed"
    client.invoke_agent_runtime.assert_not_called()


def test_agentcore_invoke_rejects_oversized_session_id(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_AGENTCORE_RUNTIME_ARN", "arn:test:runtime")
    client = mock.Mock()

    result = _agentcore_invoke(
        agent_name="hermes",
        input_text="hello",
        session_id="x" * 257,
        client=client,
    )

    assert result["status"] == "failed"
    client.invoke_agent_runtime.assert_not_called()


def test_status_does_not_expose_runtime_arn(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "TRUSTFORGE_BACKEND_REGISTRY_PATH", str(tmp_path / "providers.json")
    )
    monkeypatch.setenv("TRUSTFORGE_AGENTCORE_RUNTIME_ARN", "arn:sensitive:value")
    status = agentcore_status()
    assert status["runtime_configured"] is True
    assert "arn:sensitive:value" not in json.dumps(status)
