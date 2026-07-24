"""Pure transition rules for the wrapper-artifact controlled upgrade lifecycle.

This is the wrapper-specific companion to :mod:`trustforge.upgrade_state_machine`.
Where the upgrade state machine governs approval-gated *outer* proposals, this
module governs the lifecycle of a *wrapper artifact* under the third-track
"Wrapper controlled upgrade" plan.

Eight states, in strict order::

    diagnostics -> proposal -> candidate_build -> sandbox_replay
                -> review -> human_activation -> monitoring
                -> rollback (terminal)

Hard rules enforced by the transition table (and verified by the controller in
:mod:`trustforge.wrapper_artifact_control`):

* **No skipping.** A wrapper cannot jump from ``proposal`` straight to
  ``human_activation``; every intermediate gate must be entered in order.
* **No reverse.** A wrapper cannot move from ``human_activation`` back to
  ``sandbox_replay``; forward-only.
* **Rollback only after activation.** Rollback is reachable from
  ``human_activation`` and ``monitoring`` only — never from ``review`` or
  earlier (rolling back something that was never activated is meaningless).
* **Single terminal state.** ``rollback`` is terminal; once there, the
  lifecycle cannot be re-opened through this state machine.

The functions here are *pure*: they neither authorize a human actor, verify an
approval record, evaluate a ModelHub probe, nor move a pointer.  Authorization
and cryptographic binding happen in the controller, which is the only place an
``ApprovalRecord`` is minted or consumed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Canonical lifecycle states, in forward order. Order matters: any pair not in
# ALLOWED_TRANSITIONS is forbidden, so adding a state here without adding its
# inbound/outbound edges is fail-closed.
WRAPPER_STATES: tuple[str, ...] = (
    "diagnostics",
    "proposal",
    "candidate_build",
    "sandbox_replay",
    "review",
    "human_activation",
    "monitoring",
    "rollback",
)

INITIAL_WRAPPER_STATE = "diagnostics"
TERMINAL_WRAPPER_STATE = "rollback"
ROLLBACK_TARGET_STATES: frozenset[str] = frozenset({"human_activation", "monitoring"})

# Every legal forward or rollback edge. Pairs not listed here raise ValueError,
# which makes "skip a gate" and "reverse the order" impossible by construction.
ALLOWED_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("diagnostics", "proposal"),
        ("proposal", "candidate_build"),
        ("candidate_build", "sandbox_replay"),
        ("sandbox_replay", "review"),
        ("review", "human_activation"),
        ("human_activation", "monitoring"),
        # Rollback is reachable from either live state. Activation itself can
        # fail in production before monitoring begins, so rollback from
        # ``human_activation`` is explicitly permitted.
        ("human_activation", "rollback"),
        ("monitoring", "rollback"),
    }
)


@dataclass(frozen=True)
class WrapperTransition:
    """Result of a successful wrapper state transition.

    ``payload`` records the typed evidence the controller attached when it
    requested the transition (sandbox result, approval id, activation event,
    rollback target).  The state machine itself never inspects this payload;
    it exists so callers can thread evidence through audits and tests.
    """

    state: str
    payload: dict[str, Any]


def _check_known(state: str) -> None:
    if state not in WRAPPER_STATES:
        raise ValueError(f"unknown wrapper state: {state!r}")


def transition(current: str, target: str) -> WrapperTransition:
    """Apply the pure wrapper state-machine rule.

    Raises ``ValueError`` for any unknown state or any pair that is not in
    :data:`ALLOWED_TRANSITIONS`.  The controller fills in ``payload`` after
    authorization; this function returns an empty payload because the rule
    itself is purely structural.
    """
    _check_known(current)
    _check_known(target)
    if (current, target) not in ALLOWED_TRANSITIONS:
        raise ValueError(
            f"forbidden wrapper transition: {current} -> {target}"
        )
    return WrapperTransition(state=target, payload={})


def can_rollback(current: str) -> bool:
    """Whether ``rollback`` is a legal next state from ``current``."""
    _check_known(current)
    return (current, "rollback") in ALLOWED_TRANSITIONS


def is_terminal(state: str) -> bool:
    _check_known(state)
    return state == TERMINAL_WRAPPER_STATE


__all__ = [
    "ALLOWED_TRANSITIONS",
    "INITIAL_WRAPPER_STATE",
    "ROLLBACK_TARGET_STATES",
    "TERMINAL_WRAPPER_STATE",
    "WRAPPER_STATES",
    "WrapperTransition",
    "can_rollback",
    "is_terminal",
    "transition",
]
