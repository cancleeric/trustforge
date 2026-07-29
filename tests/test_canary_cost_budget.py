from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from trustforge.agent.shadow_contracts import canonical_json
from trustforge.canary_cost_budget import (
    BUDGET_DOMAIN,
    BUDGET_VERSION,
    CanaryCostBudget,
    CanaryCostBudgetError,
    verify_budget,
)

NOW = datetime(2026, 7, 30, tzinfo=timezone.utc)
PRIVATE = Ed25519PrivateKey.generate()
PUBLIC = PRIVATE.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _budget(**changes) -> CanaryCostBudget:
    unsigned = {
        "deployment_ledger_id": "ledger-1",
        "canary_epoch": "sha256:" + "e" * 64,
        "active_artifact_digest": "sha256:" + "a" * 64,
        "candidate_artifact_digest": "sha256:" + "b" * 64,
        "ramp_id": "ramp-1",
        "routing_policy_digest": "sha256:" + "p" * 64,
        "ramp_budget_id": "sha256:" + "f" * 64,
        "request_binding_digest": "sha256:" + "q" * 64,
        "model_call_cap": 10,
        "monetary_cap_microusd": 1000,
        "per_request_model_calls": 2,
        "per_request_cost_microusd": 100,
        "issued_at": (NOW - timedelta(minutes=1)).isoformat(),
        "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
        "nonce": "budget-1",
        "key_id": "budget-1",
        "receipt_version": BUDGET_VERSION,
    }
    unsigned.update(changes)
    return CanaryCostBudget(
        **unsigned,
        signature=PRIVATE.sign(BUDGET_DOMAIN + canonical_json(unsigned)).hex(),
    )


def _verify(budget: CanaryCostBudget) -> None:
    verify_budget(
        budget,
        keyring={"budget-1": PUBLIC},
        now=NOW,
        deployment_ledger_id="ledger-1",
        canary_epoch="sha256:" + "e" * 64,
        active_artifact_digest="sha256:" + "a" * 64,
        candidate_artifact_digest="sha256:" + "b" * 64,
        ramp_id="ramp-1",
        routing_policy_digest="sha256:" + "p" * 64,
        request_binding_digest="sha256:" + "q" * 64,
    )


def test_exact_signed_budget_verifies():
    _verify(_budget())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("signature", "00" * 64),
        ("expires_at", (NOW - timedelta(seconds=1)).isoformat()),
        ("ramp_id", "ramp-other"),
        ("candidate_artifact_digest", "sha256:" + "c" * 64),
        ("request_binding_digest", "sha256:" + "z" * 64),
    ],
)
def test_tampered_stale_or_cross_scope_budget_fails_closed(field, value):
    original = _budget()
    with pytest.raises(CanaryCostBudgetError):
        _verify(replace(original, **{field: value}))
