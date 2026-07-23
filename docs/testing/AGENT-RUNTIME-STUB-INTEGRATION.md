# Agent Runtime Stub Integration

Issue: #409

## Scope

This is a non-production test harness for the shared `AgentRuntimeProvider` contract. It does not enable AgentCore, production deployment, GitHub Actions workflows, or any live provider.

## Covered Flow

- Capability discovery
- Session creation
- Run start
- Tool call declaration
- Runtime trace recording
- Cancellation
- JSON serialization of run output
- Secret redaction through `ExecutionEventLog`

## Boundary Notes

The test uses `FakeAgentRuntimeProvider` only. It verifies that runtime DTO fields stay generic and do not expose TrustForge market-analysis terms such as `coin`, `claim`, `hermes`, or `trust_score`.

## Verification

Run:

```bash
/home/node/.openclaw/pytest-venv/bin/pytest tests/test_agent_runtime_stub_integration.py tests/test_provider_ports.py -q --no-cov
```
