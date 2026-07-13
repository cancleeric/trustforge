import json

import pytest

from trustforge.skill_changes import approve, rollback, stage
from trustforge.skills import artifact_hash, load_artifact, resolve_active_skills, skill_id_for, write_artifact


def test_baseline_outer_skills_are_hash_loaded():
    skills = resolve_active_skills()
    assert {item["family"] for item in skills} == {"source", "analysis", "report", "evaluation", "improvement"}
    assert all(len(item["revision"]) == 64 for item in skills)


def test_approved_skill_replaces_baseline_and_rollback_restores_it(tmp_path):
    root = tmp_path / "skills"; log = tmp_path / "changes.jsonl"
    baseline = {"family": "report", "name": "baseline", "rules": ["include limits"]}
    revision, _ = write_artifact(baseline, root=root)
    # Create the other families required by the complete registry.
    for family in ("source", "analysis", "evaluation", "improvement"):
        write_artifact({"family": family, "name": family, "rules": ["baseline"]}, root=root)
    candidate = {"family": "report", "name": "candidate", "rules": ["include limits", "show sources"]}
    candidate_hash, _ = write_artifact(candidate, root=root)
    stage(skill_id_for("report"), json.dumps(candidate, sort_keys=True, separators=(",", ":")), "candidate", log_path=log)
    approve(skill_id_for("report"), candidate_hash, {"sandbox": "passed"}, log_path=log)
    assert next(x for x in resolve_active_skills(root=root, log_path=log) if x["family"] == "report")["revision"] == candidate_hash
    # Seed an approved baseline pointer, then prove rollback uses it.
    stage(skill_id_for("report"), json.dumps(baseline, sort_keys=True, separators=(",", ":")), "baseline", log_path=log)
    approve(skill_id_for("report"), revision, {"sandbox": "passed"}, log_path=log)
    rollback(skill_id_for("report"), candidate_hash, "regression", log_path=log)
    assert next(x for x in resolve_active_skills(root=root, log_path=log) if x["family"] == "report")["revision"] == candidate_hash


def test_outer_skill_cannot_override_core(tmp_path):
    with pytest.raises(ValueError):
        write_artifact({"family": "source", "rules": ["bad"], "trust_weights": {"x": 1}}, root=tmp_path)
