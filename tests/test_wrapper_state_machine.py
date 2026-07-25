"""Exhaustive transition-table tests for the wrapper state machine (#510).

These tests pin the structural rules: no skipping, no reversing, rollback only
from live states.  They do *not* exercise authorization, checksum binding, or
the ModelHub probe — those live in ``test_wrapper_artifact_control.py``.
"""
from __future__ import annotations

import pytest

from trustforge.wrapper_state_machine import (
    ALLOWED_TRANSITIONS,
    INITIAL_WRAPPER_STATE,
    ROLLBACK_TARGET_STATES,
    TERMINAL_WRAPPER_STATE,
    WRAPPER_STATES,
    can_rollback,
    is_terminal,
    transition,
)


# The full legal edge set, expected by the table-driven negative test below.
EXPECTED_EDGES = {
    ("diagnostics", "proposal"),
    ("proposal", "candidate_build"),
    ("candidate_build", "sandbox_replay"),
    ("sandbox_replay", "review"),
    ("review", "human_activation"),
    ("human_activation", "monitoring"),
    ("human_activation", "rollback"),
    ("monitoring", "rollback"),
}


def test_state_machine_has_eight_canonical_states_in_order():
    assert WRAPPER_STATES == (
        "diagnostics",
        "proposal",
        "candidate_build",
        "sandbox_replay",
        "review",
        "human_activation",
        "monitoring",
        "rollback",
    )
    assert INITIAL_WRAPPER_STATE == "diagnostics"
    assert TERMINAL_WRAPPER_STATE == "rollback"


def test_allowed_transitions_match_expected_forward_graph():
    assert ALLOWED_TRANSITIONS == EXPECTED_EDGES


@pytest.mark.parametrize(
    "current,target",
    sorted(EXPECTED_EDGES),
)
def test_every_allowed_transition_succeeds(current, target):
    result = transition(current, target)
    assert result.state == target
    assert result.payload == {}


def test_no_skip_proposal_directly_to_activation():
    """The headline #510 invariant: cannot skip the sandbox/approval gates."""
    with pytest.raises(ValueError, match="forbidden wrapper transition"):
        transition("proposal", "human_activation")
    with pytest.raises(ValueError, match="forbidden wrapper transition"):
        transition("candidate_build", "human_activation")
    with pytest.raises(ValueError, match="forbidden wrapper transition"):
        transition("sandbox_replay", "human_activation")


def test_no_skip_diagnostics_directly_to_review_or_later():
    for forbidden in ("candidate_build", "sandbox_replay", "review", "human_activation", "monitoring"):
        with pytest.raises(ValueError, match="forbidden wrapper transition"):
            transition("diagnostics", forbidden)


def test_no_reverse_from_activation_back_to_sandbox_or_review():
    with pytest.raises(ValueError, match="forbidden wrapper transition"):
        transition("human_activation", "review")
    with pytest.raises(ValueError, match="forbidden wrapper transition"):
        transition("human_activation", "sandbox_replay")
    with pytest.raises(ValueError, match="forbidden wrapper transition"):
        transition("monitoring", "review")


def test_rollback_only_reachable_from_live_states():
    assert ROLLBACK_TARGET_STATES == frozenset({"human_activation", "monitoring"})
    assert can_rollback("human_activation") is True
    assert can_rollback("monitoring") is True
    # Every pre-activation state refuses rollback.
    for state in ("diagnostics", "proposal", "candidate_build", "sandbox_replay", "review"):
        assert can_rollback(state) is False
        with pytest.raises(ValueError, match="forbidden wrapper transition"):
            transition(state, "rollback")


def test_rollback_is_terminal_and_cannot_be_reopened():
    assert is_terminal("rollback") is True
    for target in WRAPPER_STATES:
        if target == "rollback":
            continue
        with pytest.raises(ValueError, match="forbidden wrapper transition"):
            transition("rollback", target)


def test_unknown_state_raises_regardless_of_target():
    with pytest.raises(ValueError, match="unknown wrapper state"):
        transition("bogus", "proposal")
    with pytest.raises(ValueError, match="unknown wrapper state"):
        transition("proposal", "bogus")


def test_self_transition_is_forbidden():
    """A state may not transition to itself; that would be a silent no-op
    that could mask a missed gate."""
    for state in WRAPPER_STATES:
        with pytest.raises(ValueError, match="forbidden wrapper transition"):
            transition(state, state)


def test_negative_table_every_disallowed_pair_raises():
    """Table-driven negative: every (current, target) pair not in the allowed
    edge set must raise.  This catches regressions where someone adds a state
    but forgets the edges, or accidentally broadens the edge set."""
    disallowed = [
        (current, target)
        for current in WRAPPER_STATES
        for target in WRAPPER_STATES
        if (current, target) not in EXPECTED_EDGES
    ]
    assert len(disallowed) > 0  # sanity: the table is non-trivial
    for current, target in disallowed:
        with pytest.raises(ValueError, match="forbidden wrapper transition"):
            transition(current, target)
