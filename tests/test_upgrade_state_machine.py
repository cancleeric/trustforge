import pytest

from trustforge.upgrade_state_machine import (
    activation_transition,
    decision_transition,
    review_transition,
    rollback_transition,
    sandbox_transition,
)


def test_review_transition_maps_sandbox_ready_to_llm_reviewed():
    assert review_transition("sandbox_ready").state == "llm_reviewed"
    assert review_transition("insufficient").state == "insufficient"
    assert review_transition("rejected").state == "rejected"


def test_sandbox_transition_has_exact_eligible_matrix():
    assert sandbox_transition("llm_reviewed", True).state == "sandbox_passed"
    assert sandbox_transition("llm_reviewed", False).state == "sandbox_failed"
    assert sandbox_transition("sandbox_failed", True).state == "sandbox_passed"
    assert sandbox_transition("sandbox_failed", False).state == "sandbox_failed"

    for state in (
        "proposed", "insufficient", "approved", "rejected",
        "activated", "rolled_back", "unknown",
    ):
        with pytest.raises(ValueError, match="requires llm_reviewed"):
            sandbox_transition(state, True)


def test_decision_transition_requires_human_actor_for_approval():
    approved = decision_transition("sandbox_passed", "approve")
    assert approved.state == "approved"
    assert approved.payload == {"previous_state": "sandbox_passed"}

    rejected = decision_transition("sandbox_failed", "reject")
    assert rejected.state == "rejected"

    with pytest.raises(ValueError, match="passed sandbox"):
        decision_transition("llm_reviewed", "approve")


def test_legacy_actor_display_is_ignored_and_never_authorizes_transition():
    assert decision_transition(
        "sandbox_passed", "approve", "codex-bot"
    ).state == "approved"
    with pytest.raises(ValueError, match="passed sandbox"):
        decision_transition("proposed", "approve", "warehouse-manager")
    with pytest.raises(ValueError, match="approve or reject"):
        decision_transition("sandbox_passed", "activate", "warehouse-manager")


@pytest.mark.parametrize("verdict", ["approved", "approve", "reject", "unknown", "", "sandbox_passed"])
def test_automated_review_verdict_is_exact_allowlist(verdict):
    with pytest.raises(ValueError, match="unknown automated"):
        review_transition(verdict)


def test_activation_and_rollback_transitions_are_explicit_terminal_steps():
    assert activation_transition("approved", True).state == "activated"
    assert rollback_transition("activated").state == "rolled_back"

    with pytest.raises(ValueError, match="approved proposal"):
        activation_transition("sandbox_passed", True)

    with pytest.raises(ValueError, match="passed sandbox"):
        activation_transition("approved", False)

    with pytest.raises(ValueError, match="activated proposal"):
        rollback_transition("approved")
