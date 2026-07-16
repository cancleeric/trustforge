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
