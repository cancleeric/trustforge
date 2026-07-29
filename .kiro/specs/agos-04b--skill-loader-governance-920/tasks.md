# 實作任務：Task Skill Loader、Frozen Manifest 與 Approval Governance

> Issue: #920 | Epic: #914

## Task 1: 建立 skill_loader.py 模組

- [ ] 建立 `src/trustforge/skill_loader.py`
- [ ] 實作 `FrozenSkillEntry` dataclass
- [ ] 實作 `FrozenSkillManifest` dataclass
- [ ] 實作 `ActivationProposal` dataclass

## Task 2: 實作 Skill Discovery

- [ ] 實作 `SkillLoader.__init__(self, registry: SkillRegistryRepository)`
- [ ] 實作 `discover(*, trigger, family, risk_class, lifecycle="active") -> list[TaskSkill]`
  - Filter by family, lifecycle, risk_class
  - Only return active lifecycle by default

## Task 3: 實作 Dependency Resolution

- [ ] 實作 `resolve_dependencies(skill_id) -> list[SkillRevision]`
  - DFS topological sort over `requires` edges
  - Stale dep (no active revision) → raise ValueError
  - Return leaf-first order
- [ ] 實作 `is_stale(skill_id) -> bool`
  - Check lifecycle, active revision, recursive deps

## Task 4: 實作 Frozen Manifest

- [ ] 新增 `frozen_skill_manifests` table migration
- [ ] 實作 `freeze_manifest(run_id, skill_ids, reasons) -> FrozenSkillManifest`
  - Resolve all transitive deps
  - Check no stale/unapproved high-risk
  - Persist to DB
- [ ] 實作 `get_frozen_manifest(run_id) -> FrozenSkillManifest | None`
- [ ] 確保凍結後 active pointer 變更不影響已凍結 manifest

## Task 5: 實作 Activation Governance

- [ ] 新增 `activation_proposals` table migration
- [ ] 實作 `propose_activation(skill_id, revision_hash, reason) -> ActivationProposal`
  - Only for high-risk skills
- [ ] 實作 `approve_activation(proposal_id, approved_by) -> None`
- [ ] 實作 `reject_activation(proposal_id, rejected_by, reason) -> None`
- [ ] 實作 `is_activation_approved(skill_id, revision_hash) -> bool`
- [ ] freeze_manifest 中檢查高風險 skill 必須有 approval

## Task 6: 單元測試

- [ ] 建立 `tests/test_skill_loader.py`
- [ ] 測試 discover filtering (family, lifecycle, risk_class)
- [ ] 測試 resolve_dependencies normal case (leaf-first order)
- [ ] 測試 resolve_dependencies stale dep → error
- [ ] 測試 freeze_manifest captures exact hashes
- [ ] 測試 frozen manifest 不受後續 active pointer 變更影響
- [ ] 測試 is_stale: frozen lifecycle → True
- [ ] 測試 is_stale: no active revision → True
- [ ] 測試 is_stale: stale transitive dep → True
- [ ] 測試 propose_activation for high-risk
- [ ] 測試 propose_activation for non-high-risk → error
- [ ] 測試 approve/reject flow
- [ ] 測試 unapproved high-risk → freeze_manifest rejects
- [ ] 確認 outer policy hashes 不受影響（import skills.py 的 tests 通過）
- [ ] 確認 pre-push 通過
