# Second Consumer Platform API Report

Issue: #423

## Consumer

The regression models a non-financial warehouse inventory consumer. It imports only shared platform contracts:

- `trustforge.ports`
- `trustforge.execution_event_log`
- `trustforge.upgrade_state_machine`

It does not import the TrustForge app pipeline, agent orchestrator, web layer, legacy scoring internals, or `trustforge_core`.

## Covered APIs

- Provider: `ModelRequest`, `FakeModelProvider`
- Event log: `ExecutionEventLog`
- Observability: `FakeObservabilityProvider`
- Budget: `FakeBudgetProvider`
- Approval/security: `PolicyRequest`, `evaluate_security_decision`, `decision_transition`

## Friction

- The shared API can run with `PYTHONPATH=src` and `python -P` in a clean subprocess.
- No production runtime, provider resolver, AWS client, web server, or formal run setup is required.
- The consumer still imports from the `trustforge` package namespace; package-mode ADR #424 should decide whether that remains acceptable or moves to a dedicated package.

## Domain Leakage

The test locks that second-consumer dataclass field names do not expose domain terms:

- `bedrock`
- `claim`
- `coin`
- `hermes`
- `trust_score`

`PolicyDecision.evidence` remains allowed because it is a generic audit field, not a TrustForge market-analysis concept.

The subprocess also asserts these modules stay unloaded:

- `trustforge_core`
- `trustforge.agent.orchestrator`
- `trustforge.pipeline`
- `trustforge.trust.scoring`
- `trustforge.web`

## Verification

Run:

```bash
/home/node/.openclaw/pytest-venv/bin/pytest tests/test_second_consumer_platform_api.py -q --no-cov
```
