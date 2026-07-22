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


def test_sandbox_transition_rejects_terminal_proposals():
    assert sandbox_transition("llm_reviewed", True).state == "sandbox_passed"
    assert sandbox_transition("llm_reviewed", False).state == "sandbox_failed"

    with pytest.raises(ValueError, match="terminal proposal"):
        sandbox_transition("approved", True)


def test_decision_transition_requires_human_actor_for_approval():
    approved = decision_transition("sandbox_passed", "approve", "release-operator")
    assert approved.state == "approved"
    assert approved.payload == {"previous_state": "sandbox_passed"}

    rejected = decision_transition("sandbox_failed", "reject", "codex-bot")
    assert rejected.state == "rejected"

    with pytest.raises(ValueError, match="human actor"):
        decision_transition("sandbox_passed", "approve", "codex-bot")

    with pytest.raises(ValueError, match="passed sandbox"):
        decision_transition("llm_reviewed", "approve", "release-operator")


def test_activation_and_rollback_transitions_are_explicit_terminal_steps():
    assert activation_transition("approved", True).state == "activated"
    assert rollback_transition("activated").state == "rolled_back"

    with pytest.raises(ValueError, match="approved proposal"):
        activation_transition("sandbox_passed", True)

    with pytest.raises(ValueError, match="passed sandbox"):
        activation_transition("approved", False)

    with pytest.raises(ValueError, match="activated proposal"):
        rollback_transition("approved")
