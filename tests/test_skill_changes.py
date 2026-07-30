import pytest
from trustforge import skill_changes
from trustforge.skill_changes import active_revision, approve, content_hash, rollback, stage


def test_locked_log_retries_when_canonical_target_changes_after_lock(tmp_path, monkeypatch):
    legacy = tmp_path / "app" / "out" / "skill_changes.jsonl"
    persistent = tmp_path / "state" / "skill_changes.jsonl"
    resolutions = iter((legacy, persistent, persistent, persistent))
    operations = []

    class RecordingFcntl:
        LOCK_EX = 1
        LOCK_UN = 2

        @staticmethod
        def flock(_fd, operation):
            operations.append(operation)

    monkeypatch.setattr(skill_changes, "_canonical_log_path", lambda _path: next(resolutions))
    monkeypatch.setattr(skill_changes, "fcntl", RecordingFcntl())

    with skill_changes._locked_log(legacy) as target:
        assert target == persistent

    assert operations == [1, 2, 1, 1, 2, 2]
    assert (legacy.parent / "skill_changes.jsonl.lock").is_file()
    assert (persistent.parent / "skill_changes.jsonl.lock").is_file()


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
