import json

from trustforge import upgrade_control


def test_upgrade_control_exposes_full_versioned_topology_without_recursive_apply(monkeypatch, tmp_path):
    report = tmp_path / "improvement.json"
    report.write_text(json.dumps({
        "status": "attention_required", "generated_at": "2026-07-16T00:00:00Z",
        "proposals": [{"id": "analysis-flow-reliability", "area": "analysis-orchestration",
                       "severity": "high", "proposed_experiment": "sandbox replay",
                       "success_metric": "retry rate below threshold"}],
    }))
    monkeypatch.setenv("TRUSTFORGE_IMPROVEMENT_REPORT", str(report))
    monkeypatch.setattr(upgrade_control, "change_history", lambda: [])
    monkeypatch.setattr(upgrade_control, "run_skill_manifest", lambda: {
        "outer_skills": [{"family": family, "revision": family * 12, "origin": "baseline"}
                         for family in ("source", "analysis", "report", "evaluation", "improvement")]
    })

    data = upgrade_control.upgrade_status()

    assert len(data["modules"]) == 15
    assert data["planes"] == ["DATA PLANE", "INTELLIGENCE", "TRUST KERNEL", "DELIVERY", "OPERATIONS"]
    assert data["recursive_upgrade"] is False
    assert all(m["automatic_apply"] is False and m["recursive_upgrade"] is False for m in data["modules"])
    assert next(m for m in data["modules"] if m["id"] == "trust-scoring")["state"] == "locked"
    assert next(m for m in data["modules"] if m["id"] == "scheduler")["state"] == "candidate"
