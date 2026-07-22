"""Pure transition rules for approval-gated upgrade proposals."""

from __future__ import annotations

from dataclasses import dataclass


TERMINAL_PROPOSAL_STATES = frozenset({"approved", "rejected", "activated", "rolled_back"})

_AUTOMATED_APPROVAL_ACTOR_TOKENS = (
    "agent",
    "auto",
    "automation",
    "bedrock",
    "bot",
    "claude",
    "codex",
    "gemini",
    "gpt",
    "llm",
    "model",
    "openai",
    "service",
)


@dataclass(frozen=True)
class UpgradeTransition:
    state: str
    payload: dict[str, str]


def is_human_approval_actor(actor: str) -> bool:
    normalized = actor.strip().lower()
    if not normalized:
        return False
    return not any(token in normalized for token in _AUTOMATED_APPROVAL_ACTOR_TOKENS)


def review_transition(verdict: str) -> UpgradeTransition:
    state = "llm_reviewed" if verdict == "sandbox_ready" else verdict
    return UpgradeTransition(state=state, payload={"verdict": verdict})


def sandbox_transition(current_state: str, passed: bool) -> UpgradeTransition:
    if current_state in TERMINAL_PROPOSAL_STATES:
        raise ValueError("terminal proposal cannot be sandboxed")
    return UpgradeTransition(
        state="sandbox_passed" if passed else "sandbox_failed",
        payload={"previous_state": current_state},
    )


def decision_transition(current_state: str, decision: str, actor: str) -> UpgradeTransition:
    if decision not in {"approve", "reject"}:
        raise ValueError("decision must be approve or reject")
    if current_state in TERMINAL_PROPOSAL_STATES:
        raise ValueError("proposal already has terminal decision")
    if decision == "approve":
        if not is_human_approval_actor(actor):
            raise ValueError("approval requires human actor")
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
