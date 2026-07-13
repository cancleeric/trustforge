import pytest
from trustforge.skill_changes import active_revision, approve, content_hash, rollback, stage

def test_skill_change_history_requires_stage_and_evidence_then_can_roll_back(tmp_path):
    log = tmp_path / "changes.jsonl"
    old = stage("source-policy", "version one", "initial", log_path=log)
    approve("source-policy", old["skill_hash"], {"question_bank": "24/24"}, log_path=log)
    new = stage("source-policy", "version two", "faster", log_path=log)
    approve("source-policy", new["skill_hash"], {"question_bank": "24/24", "replay": "unchanged"}, log_path=log)
    assert active_revision("source-policy", log_path=log) == content_hash("version two")
    rollback("source-policy", old["skill_hash"], "latency improvement reduced evidence", log_path=log)
    assert active_revision("source-policy", log_path=log) == content_hash("version one")

def test_skill_change_cannot_approve_unknown_or_evidenceless_revision(tmp_path):
    with pytest.raises(ValueError): approve("report-style", "not-staged", {"qa": "pass"}, log_path=tmp_path / "x")
