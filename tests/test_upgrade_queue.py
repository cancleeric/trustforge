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


def test_real_sandbox_runner_persists_result_to_upgrade_queue(tmp_path, monkeypatch):
    import importlib.util
    import json
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "run_skill_sandbox.py"
    spec = importlib.util.spec_from_file_location("run_skill_sandbox", script)
    assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    queue_path = tmp_path / "upgrade.sqlite3"
    queue = UpgradeQueue(queue_path)
    queue.sync_diagnostic({"proposals": [{"id": "candidate-1", "area": "analysis", "severity": "medium"}]})
    artifact = tmp_path / "candidate.json"
    artifact.write_text(json.dumps({"family": "analysis", "rules": ["bounded"]}), encoding="utf-8")
    output = tmp_path / "sandbox.json"
    monkeypatch.setattr(runner, "_run", lambda argv: {"argv": argv, "returncode": 0, "stdout_tail": "ok", "stderr_tail": ""})
    monkeypatch.setattr(runner, "write_artifact", lambda candidate: (runner.artifact_hash(candidate), artifact))

    assert runner.main([str(artifact), "--proposal-id", "candidate-1", "--queue-db", str(queue_path), "--out", str(output)]) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["queue_run"]["state"] == "sandbox_passed"
    status = UpgradeQueue(queue_path).status()
    assert status["proposals"][0]["state"] == "sandbox_passed"
    assert status["sandbox_runs"][0]["artifact_hash"].startswith("sha256:")


def test_approved_sandbox_candidate_requires_explicit_activation(tmp_path, monkeypatch):
    import importlib.util
    import json
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "run_skill_sandbox.py"
    spec = importlib.util.spec_from_file_location("run_skill_sandbox_activation", script)
    assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    queue_path = tmp_path / "upgrade.sqlite3"
    skill_root = tmp_path / "skills"
    log_path = tmp_path / "skill_changes.jsonl"
    monkeypatch.setenv("TRUSTFORGE_SKILL_ROOT", str(skill_root))
    monkeypatch.setattr(runner, "_run", lambda argv: {"argv": argv, "returncode": 0, "stdout_tail": "ok", "stderr_tail": ""})
    artifact = tmp_path / "candidate.json"
    artifact.write_text(json.dumps({"family": "analysis", "rules": ["bounded"]}), encoding="utf-8")
    queue = UpgradeQueue(queue_path)
    queue.sync_diagnostic({"proposals": [{"id": "candidate-2", "area": "analysis", "severity": "medium"}]})

    assert runner.main([str(artifact), "--proposal-id", "candidate-2", "--queue-db", str(queue_path), "--out", str(tmp_path / "result.json")]) == 0
    decision = queue.decide("candidate-2", "approve", "reviewer", "sandbox green")
    assert decision["activated"] is False
    activation = queue.activate("candidate-2", "release-operator", "approved release", log_path=log_path)
    assert activation["state"] == "activated"
    assert UpgradeQueue(queue_path).status()["activations"][0]["actor"] == "release-operator"
