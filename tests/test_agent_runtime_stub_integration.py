"""Non-production AgentRuntime integration regressions (#409)."""

from __future__ import annotations

import json

from trustforge.execution_event_log import ExecutionEventLog
from trustforge.ports import (
    AgentRuntimeProvider,
    FakeAgentRuntimeProvider,
    RuntimeCapability,
    RuntimeToolCall,
    RuntimeTraceEvent,
)


def test_fake_agent_runtime_records_full_stubbed_integration_lifecycle():
    """Session, run, tool, trace, and cancel stay provider-neutral."""

    runtime = FakeAgentRuntimeProvider(
        [
            RuntimeCapability(
                name="tool-run",
                version="stub-v1",
                limits={"max_tools": 2, "timeout_sec": 5},
            )
        ]
    )
    log = ExecutionEventLog(
        run_id="agent-runtime-smoke",
        started_at="2026-07-23T00:00:00Z",
        budget_sec=30,
    )

    assert isinstance(runtime, AgentRuntimeProvider)
    assert runtime.capabilities()[0].limits == {"max_tools": 2, "timeout_sec": 5}

    session = runtime.start_session({"purpose": "stubbed-integration"})
    tool = RuntimeToolCall(
        name="summarize_ticket",
        arguments={"ticket_id": "T-100", "token": "must-not-leak"},
        timeout_sec=5,
    )
    run = runtime.start_run(
        session,
        input={"task": "summarize ticket T-100"},
        tools=[tool],
    )
    runtime.trace(
        run.run_id,
        RuntimeTraceEvent(
            event="tool.completed",
            payload={"tool": tool.name, "status": "ok", "secret": "must-not-leak"},
        ),
    )
    log.append(
        ts="2026-07-23T00:00:01Z",
        elapsed_sec=1.0,
        tool="agent_runtime.trace",
        params={"run_id": run.run_id, "secret": "must-not-leak"},
        summary="stubbed runtime trace recorded",
    )

    assert run.status == "running"
    assert run.output == {
        "session_id": session.session_id,
        "input": {"task": "summarize ticket T-100"},
        "tools": ["summarize_ticket"],
    }
    assert runtime.traces[-1][0] == run.run_id
    assert runtime.traces[-1][1].payload["status"] == "ok"
    assert runtime.cancel_run(run.run_id) is True
    assert runtime.runs[run.run_id].status == "cancelled"

    jsonl = log.to_jsonl()
    assert "must-not-leak" not in jsonl
    assert "[REDACTED]" in jsonl


def test_agent_runtime_public_contracts_remain_domain_neutral():
    """Runtime integration fields should not expose TrustForge market terms."""

    from dataclasses import fields

    from trustforge.ports import RuntimeCapability, RuntimeRun, RuntimeSession

    forbidden = {"bedrock", "claim", "coin", "evidence", "hermes", "trust_score"}
    contracts = (RuntimeCapability, RuntimeRun, RuntimeSession, RuntimeToolCall, RuntimeTraceEvent)
    leaked = {
        contract.__name__: sorted(field.name for field in fields(contract) if field.name in forbidden)
        for contract in contracts
    }

    assert {name: hits for name, hits in leaked.items() if hits} == {}


def test_agent_runtime_stub_output_is_json_serializable():
    """External integration harnesses can persist the fake runtime result."""

    runtime = FakeAgentRuntimeProvider()
    session = runtime.start_session({"tenant": "external-test"})
    run = runtime.start_run(
        session,
        input={"operation": "health-check"},
        tools=[RuntimeToolCall(name="noop")],
    )

    payload = json.dumps(
        {
            "run_id": run.run_id,
            "status": run.status,
            "output": run.output,
            "sessions": [session.session_id for session in runtime.sessions],
        },
        sort_keys=True,
    )

    assert json.loads(payload)["status"] == "running"
