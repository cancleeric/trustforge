# Skill Change Control

Mutable Hermes skills improve the outer workflow, never the deterministic Trust
Layer. Every revision follows `stage -> approve -> active -> rollback if needed`.
The JSONL record is append-only and stores revision hash, prior hash, reason and
validation evidence. Approval requires named QA/replay evidence; rollback can
only target a previously approved hash.

```bash
python3 scripts/manage_skill_change.py stage source-policy skills/source-policy.md --summary "candidate"
python3 scripts/manage_skill_change.py approve source-policy HASH --evidence '{"question_bank":"240/240"}'
python3 scripts/manage_skill_change.py rollback source-policy OLD_HASH --reason "evidence coverage regressed"
```

Rollback is a new immutable event, never deletion of the failed experiment.
