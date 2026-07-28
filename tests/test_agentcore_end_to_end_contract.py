from __future__ import annotations

import io
import json
from unittest import mock

from trustforge.agent.agentcore_adapter import _agentcore_invoke
from trustforge.agent.agentcore_runtime import invoke_payload


def test_event_payload_round_trips_through_runtime_contract(monkeypatch):
    monkeypatch.setenv("TRUSTFORGE_AGENTCORE_RUNTIME_ARN", "arn:test:runtime")
    runtime_payload = {
        "coin": "BTC",
        "query": "分析 BTC",
        "question_type": "multi_source",
        "data_mode": "sample",
        "llm_mode": "off",
    }
    client = mock.Mock()
    client.invoke_agent_runtime.return_value = {
        "runtimeSessionId": "12345678-1234-1234-1234-123456789012",
        "statusCode": 200,
        "response": io.BytesIO(json.dumps({"accepted": True}).encode()),
    }

    with mock.patch(
        "trustforge.agent.agentcore_runtime.analyze_market",
        return_value={"report": {}, "evidence": [], "execution_log": {}},
    ) as analyze:
        result = _agentcore_invoke(
            agent_name="hermes",
            input_text="分析 BTC",
            runtime_payload=runtime_payload,
            client=client,
        )
        sent = json.loads(client.invoke_agent_runtime.call_args.kwargs["payload"])
        runtime_result = invoke_payload(sent)

    assert result["status"] == "succeeded"
    assert runtime_result["report"] == {}
    analyze.assert_called_once_with(
        "BTC",
        "分析 BTC",
        question_type="multi_source",
        data_mode="sample",
        llm_mode="off",
    )
    generated_session = client.invoke_agent_runtime.call_args.kwargs[
        "runtimeSessionId"
    ]
    assert len(generated_session) >= 33
