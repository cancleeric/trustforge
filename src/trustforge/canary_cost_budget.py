"""Signed, release-bound monetary budgets for HTTP canary requests."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from trustforge.agent.shadow_contracts import canonical_json

BUDGET_VERSION = "trustforge.canary-cost-budget/v1"
BUDGET_DOMAIN = b"trustforge.canary-cost-budget.v1\x00"


class CanaryCostBudgetError(ValueError):
    """A cost authorization is malformed, stale, or has an invalid binding."""


def _utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise CanaryCostBudgetError("budget timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CanaryCostBudgetError("budget timestamp must be timezone aware")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class CanaryCostBudget:
    """CEO/operator-issued maximum spend for one exact canary request class."""

    deployment_ledger_id: str
    canary_epoch: str
    active_artifact_digest: str
    candidate_artifact_digest: str
    ramp_id: str
    routing_policy_digest: str
    ramp_budget_id: str
    request_binding_digest: str
    model_call_cap: int
    monetary_cap_microusd: int
    per_request_model_calls: int
    per_request_cost_microusd: int
    issued_at: str
    expires_at: str
    nonce: str
    key_id: str
    signature: str
    receipt_version: str = BUDGET_VERSION

    def unsigned(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("signature")
        return value

    @property
    def budget_id(self) -> str:
        return "sha256:" + hashlib.sha256(
            BUDGET_DOMAIN + canonical_json(self.unsigned())
        ).hexdigest()


def verify_budget(
    budget: CanaryCostBudget,
    *,
    keyring: Mapping[str, bytes],
    now: datetime,
    deployment_ledger_id: str,
    canary_epoch: str,
    active_artifact_digest: str,
    candidate_artifact_digest: str,
    ramp_id: str,
    routing_policy_digest: str,
    request_binding_digest: str,
) -> None:
    """Verify signature, freshness, limits, and every release/request binding."""
    if budget.receipt_version != BUDGET_VERSION:
        raise CanaryCostBudgetError("budget receipt version is invalid")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in (
            budget.model_call_cap,
            budget.monetary_cap_microusd,
            budget.per_request_model_calls,
            budget.per_request_cost_microusd,
        )
    ):
        raise CanaryCostBudgetError("budget limits must be positive integers")
    if (
        budget.per_request_model_calls > budget.model_call_cap
        or budget.per_request_cost_microusd > budget.monetary_cap_microusd
    ):
        raise CanaryCostBudgetError("per-request budget exceeds ramp cap")
    if not (
        budget.ramp_budget_id.startswith("sha256:")
        and len(budget.ramp_budget_id) == 71
        and all(
            character in "0123456789abcdef"
            for character in budget.ramp_budget_id[7:]
        )
    ):
        raise CanaryCostBudgetError("ramp budget id is invalid")
    expected = {
        "deployment_ledger_id": deployment_ledger_id,
        "canary_epoch": canary_epoch,
        "active_artifact_digest": active_artifact_digest,
        "candidate_artifact_digest": candidate_artifact_digest,
        "ramp_id": ramp_id,
        "routing_policy_digest": routing_policy_digest,
        "request_binding_digest": request_binding_digest,
    }
    if any(
        not hmac.compare_digest(str(getattr(budget, name)), str(value))
        for name, value in expected.items()
    ):
        raise CanaryCostBudgetError("budget release or request binding is invalid")
    issued, expires = _utc(budget.issued_at), _utc(budget.expires_at)
    if now.tzinfo is None or now.utcoffset() is None:
        raise CanaryCostBudgetError("budget verification clock must be aware")
    current = now.astimezone(timezone.utc)
    if (
        issued > current
        or expires <= current
        or expires <= issued
        or expires - issued > timedelta(hours=24)
        or not budget.nonce
    ):
        raise CanaryCostBudgetError("budget is stale or future-dated")
    key = keyring.get(budget.key_id)
    try:
        if key is None or len(key) != 32:
            raise InvalidSignature
        Ed25519PublicKey.from_public_bytes(key).verify(
            bytes.fromhex(budget.signature),
            BUDGET_DOMAIN + canonical_json(budget.unsigned()),
        )
    except (InvalidSignature, ValueError) as exc:
        raise CanaryCostBudgetError("budget signature is invalid") from exc


__all__ = [
    "BUDGET_DOMAIN",
    "BUDGET_VERSION",
    "CanaryCostBudget",
    "CanaryCostBudgetError",
    "verify_budget",
]
