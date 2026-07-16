from trustforge.upgrade_queue import UpgradeQueue


def test_upgrade_queue_persists_proposal_and_llm_review_across_instances(tmp_path):
    path = tmp_path / "upgrade.sqlite3"
    queue = UpgradeQueue(path)
    assert queue.sync_diagnostic({"proposals": [{
        "id": "source-reliability", "area": "data-acquisition", "severity": "high",
        "evidence": {"failed": 3}, "proposed_experiment": "bounded retry",
    }]}) == 1
    assert queue.record_reviews({"reviews": [{
        "proposal_id": "source-reliability", "verdict": "sandbox_ready",
        "reasons": ["measured"], "required_checks": ["replay"],
    }]}) == 1
    status = UpgradeQueue(path).status()
    assert status["durable"] is True
    assert status["proposals"][0]["state"] == "llm_reviewed"
    assert status["reviews"][0]["verdict"] == "sandbox_ready"


def test_upgrade_queue_diagnostic_refresh_does_not_erase_review_state(tmp_path):
    queue = UpgradeQueue(tmp_path / "upgrade.sqlite3")
    report = {"proposals": [{"id": "p", "area": "x", "severity": "medium"}]}
    queue.sync_diagnostic(report)
    queue.record_reviews({"reviews": [{"proposal_id": "p", "verdict": "insufficient"}]})
    queue.sync_diagnostic(report)
    assert queue.status()["proposals"][0]["state"] == "insufficient"


def test_upgrade_queue_sandbox_and_human_gate_are_durable(tmp_path):
    path = tmp_path / "upgrade.sqlite3"
    queue = UpgradeQueue(path)
    queue.sync_diagnostic({"proposals": [{"id": "p", "area": "x", "severity": "high"}]})
    run = queue.record_sandbox("p", True, "sha256:abc", {"tests": 24})
    assert run["state"] == "sandbox_passed"
    decision = queue.decide("p", "approve", "operator@example", "regression green")
    assert decision["state"] == "approved"
    assert decision["activated"] is False
    status = UpgradeQueue(path).status()
    assert status["sandbox_runs"][0]["artifact_hash"] == "sha256:abc"
    assert status["decisions"][0]["actor"] == "operator@example"


def test_upgrade_queue_rejects_approval_without_passed_sandbox(tmp_path):
    queue = UpgradeQueue(tmp_path / "upgrade.sqlite3")
    queue.sync_diagnostic({"proposals": [{"id": "p", "area": "x", "severity": "high"}]})
    import pytest
    with pytest.raises(ValueError, match="passed sandbox"):
        queue.decide("p", "approve", "operator", "too early")
    rejected = queue.decide("p", "reject", "operator", "unsafe candidate")
    assert rejected["state"] == "rejected"
