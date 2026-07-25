"""Pure transition rules for approval-gated upgrade proposals."""

from __future__ import annotations

from dataclasses import dataclass


TERMINAL_PROPOSAL_STATES = frozenset({"approved", "rejected", "activated", "rolled_back"})
AUTOMATED_REVIEW_VERDICTS = frozenset({"insufficient", "sandbox_ready", "rejected"})


@dataclass(frozen=True)
class UpgradeTransition:
    state: str
    payload: dict[str, str]


def review_transition(verdict: str) -> UpgradeTransition:
    if verdict not in AUTOMATED_REVIEW_VERDICTS:
        raise ValueError("unknown automated review verdict")
    state = "llm_reviewed" if verdict == "sandbox_ready" else verdict
    return UpgradeTransition(state=state, payload={"verdict": verdict})


def sandbox_transition(current_state: str, passed: bool) -> UpgradeTransition:
    if current_state not in {"llm_reviewed", "sandbox_failed"}:
        raise ValueError(
            "sandbox requires llm_reviewed or sandbox_failed proposal"
        )
    return UpgradeTransition(
        state="sandbox_passed" if passed else "sandbox_failed",
        payload={"previous_state": current_state},
    )


def decision_transition(
    current_state: str,
    decision: str,
    _legacy_untrusted_actor_display: str | None = None,
) -> UpgradeTransition:
    """Apply pure state rules.

    The optional third argument exists only for legacy callers.  It is
    intentionally ignored and must never be treated as authorization; Queue
    mutations authorize an ``AuthenticatedPrincipal`` through AuthorityAdapter
    before invoking this transition.
    """
    if decision not in {"approve", "reject"}:
        raise ValueError("decision must be approve or reject")
    if current_state in TERMINAL_PROPOSAL_STATES:
        raise ValueError("proposal already has terminal decision")
    if decision == "approve":
        if current_state != "sandbox_passed":
            raise ValueError("approval requires passed sandbox")
    return UpgradeTransition(
        state="approved" if decision == "approve" else "rejected",
        payload={"previous_state": current_state},
    )


def activation_transition(current_state: str, sandbox_passed: bool) -> UpgradeTransition:
    if current_state != "approved":
        raise ValueError("activation requires approved proposal")
    if not sandbox_passed:
        raise ValueError("activation requires passed sandbox")
    return UpgradeTransition(state="activated", payload={"previous_state": current_state})


def rollback_transition(current_state: str) -> UpgradeTransition:
    if current_state != "activated":
        raise ValueError("rollback requires an activated proposal")
    return UpgradeTransition(state="rolled_back", payload={"previous_state": current_state})
