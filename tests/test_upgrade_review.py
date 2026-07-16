import json

from trustforge.upgrade_review import review


def test_llm_upgrade_reviewer_can_challenge_but_never_activate():
    diagnostic = {"proposals": [{
        "id": "p1", "area": "data-acquisition", "severity": "high",
        "evidence": {"failed_runs": 4}, "proposed_experiment": "bounded retry",
        "success_metric": "seven clean cycles",
    }]}
    def complete(system, prompt):
        assert "cannot approve deployment" in system
        assert "failed_runs" in prompt
        return json.dumps({"reviews": [{"proposal_id": "p1", "verdict": "sandbox_ready",
                                        "reasons": ["measured failures"],
                                        "required_checks": ["replay", "rollback"]}]})

    result = review(diagnostic, complete)

    assert result["status"] == "reviewed"
    assert result["can_activate"] is False
    assert result["reviews"][0]["verdict"] == "sandbox_ready"


def test_llm_upgrade_reviewer_does_not_call_model_without_candidates():
    result = review({"proposals": []}, lambda *_: (_ for _ in ()).throw(AssertionError("must not call")))
    assert result == {"status": "no_candidates", "reviews": [], "can_activate": False}
