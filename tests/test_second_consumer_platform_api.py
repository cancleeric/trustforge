"""Second-consumer shared platform API regressions (#423)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_second_consumer_uses_shared_platform_apis_without_app_or_core_imports():
    """A non-TrustForge app can use platform contracts without app/core leakage."""

    repo_root = Path(__file__).resolve().parents[1]
    code = """
import json
import sys

from trustforge.execution_event_log import ExecutionEventLog
from trustforge.ports import (
    FakeBudgetProvider,
    FakeModelProvider,
    FakeObservabilityProvider,
    FakeSecurityDecisionProvider,
    ModelRequest,
    PolicyRequest,
    evaluate_security_decision,
)
from trustforge.upgrade_state_machine import decision_transition

budget = FakeBudgetProvider()
model = FakeModelProvider(default_text="inventory accepted")
observability = FakeObservabilityProvider()
security = FakeSecurityDecisionProvider()
log = ExecutionEventLog(
    run_id="warehouse-inventory-run",
    started_at="2026-07-23T00:00:00Z",
    budget_sec=60,
)

assert budget.check("inventory-model", 12, 4)
response = model.complete(
    ModelRequest(
        system="Classify an inventory routing request.",
        prompt="Route shelf audit ticket A-17.",
        response_format="text",
        model="inventory-model",
    )
)
budget.record(response.model, response.usage.input_tokens, response.usage.output_tokens, response.usage.cost_usd)
decision = evaluate_security_decision(
    security,
    PolicyRequest(
        subject="warehouse-operator",
        action="route",
        resource="inventory-ticket",
        context={"ticket": "A-17", "token": "must-not-leak"},
    ),
)
transition = decision_transition("sandbox_passed", "approve", "warehouse-manager")
observability.emit("inventory.route.completed", {"provider": response.provider, "allowed": decision.allowed})
log.append(
    ts="2026-07-23T00:00:01Z",
    elapsed_sec=1.2,
    tool="inventory.route",
    params={"provider": response.provider, "secret": "must-not-leak"},
    summary=response.text,
)

for forbidden in (
    "trustforge_core",
    "trustforge.agent.orchestrator",
    "trustforge.pipeline",
    "trustforge.trust.scoring",
    "trustforge.web",
):
    assert forbidden not in sys.modules, forbidden

print(json.dumps({
    "allowed": decision.allowed,
    "approval_state": transition.state,
    "budget_checks": len(budget.checks),
    "budget_records": len(budget.records),
    "events": len(observability.events),
    "jsonl": log.to_jsonl(),
    "model_calls": len(model.calls),
}, sort_keys=True))
"""
    env = {
        **os.environ,
        "PYTHONPATH": f"{repo_root / 'src'}{os.pathsep}{repo_root}",
    }
    result = subprocess.run(
        [sys.executable, "-P", "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    payload = json.loads(result.stdout)
    assert payload["allowed"] is True
    assert payload["approval_state"] == "approved"
    assert payload["budget_checks"] == 1
    assert payload["budget_records"] == 1
    assert payload["events"] == 1
    assert payload["model_calls"] == 1
    assert "[REDACTED]" in payload["jsonl"]
    assert "must-not-leak" not in payload["jsonl"]


def test_second_consumer_contract_surface_has_no_trustforge_domain_terms():
    """Public platform DTOs stay generic enough for a second consumer."""

    from dataclasses import fields, is_dataclass

    from trustforge.execution_event_log import ExecutionEventRecord, ExecutionStepRecord
    from trustforge.ports import ModelRequest, ModelResponse, ModelUsage, PolicyDecision, PolicyRequest

    forbidden = {"bedrock", "claim", "coin", "hermes", "trust_score"}
    contracts = (
        ExecutionEventRecord,
        ExecutionStepRecord,
        ModelRequest,
        ModelResponse,
        ModelUsage,
        PolicyDecision,
        PolicyRequest,
    )

    leaked: dict[str, list[str]] = {}
    for contract in contracts:
        assert is_dataclass(contract)
        hits = sorted(name for name in (field.name for field in fields(contract)) if name in forbidden)
        if hits:
            leaked[contract.__name__] = hits

    assert leaked == {}
