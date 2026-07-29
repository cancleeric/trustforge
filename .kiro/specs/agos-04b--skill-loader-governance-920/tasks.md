# 實作任務：Task Skill Loader、Frozen Manifest 與 Approval Governance

> Issue: #920 | Epic: #914

## Task 1: 建立 skill_loader.py 模組

- [x] 建立 `src/trustforge/skill_loader.py`
- [x] 實作 `FrozenSkillEntry` dataclass
- [x] 實作 `FrozenSkillManifest` dataclass
- [x] 實作 `ActivationProposal` dataclass

## Task 2: 實作 Skill Discovery

- [x] 實作 `SkillLoader.__init__(self, registry: SkillRegistryRepository)`
- [x] 實作 `discover(*, trigger, family, risk_class, lifecycle="active") -> list[TaskSkill]`
  - Filter by family, lifecycle, risk_class
  - Only return active lifecycle by default

## Task 3: 實作 Dependency Resolution

- [x] 實作 `resolve_dependencies(skill_id) -> list[SkillRevision]`
  - DFS topological sort over `requires` edges
  - Stale dep (no active revision) → raise ValueError
  - Return leaf-first order
- [x] 實作 `is_stale(skill_id) -> bool`
  - Check lifecycle, active revision, recursive deps

## Task 4: 實作 Frozen Manifest

- [x] 新增 `frozen_skill_manifests` table migration
- [x] 實作 `freeze_manifest(run_id, skill_ids, reasons) -> FrozenSkillManifest`
  - Resolve all transitive deps
  - Check no stale/unapproved high-risk
  - Persist to DB
- [x] 實作 `get_frozen_manifest(run_id) -> FrozenSkillManifest | None`
- [x] 確保凍結後 active pointer 變更不影響已凍結 manifest

## Task 5: 實作 Activation Governance

- [x] 新增 `activation_proposals` table migration
- [x] 實作 `propose_activation(skill_id, revision_hash, reason) -> ActivationProposal`
  - Only for high-risk skills
- [x] 實作 `approve_activation(proposal_id, approved_by) -> None`
- [x] 實作 `reject_activation(proposal_id, rejected_by, reason) -> None`
- [x] 實作 `is_activation_approved(skill_id, revision_hash) -> bool`
- [x] freeze_manifest 中檢查高風險 skill 必須有 approval

## Task 6: 單元測試

- [x] 建立 `tests/test_skill_loader.py`
- [x] 測試 discover filtering (family, lifecycle, risk_class)
- [x] 測試 resolve_dependencies normal case (leaf-first order)
- [x] 測試 resolve_dependencies stale dep → error
- [x] 測試 freeze_manifest captures exact hashes
- [x] 測試 frozen manifest 不受後續 active pointer 變更影響
- [x] 測試 is_stale: frozen lifecycle → True
- [x] 測試 is_stale: no active revision → True
- [x] 測試 is_stale: stale transitive dep → True
- [x] 測試 propose_activation for high-risk
- [x] 測試 propose_activation for non-high-risk → error
- [x] 測試 approve/reject flow
- [x] 測試 unapproved high-risk → freeze_manifest rejects
- [x] 確認 outer policy hashes 不受影響（import skills.py 的 tests 通過）
- [x] 確認 pre-push 通過

### HEAD evidence

Implemented by `src/trustforge/skill_loader.py`; discovery, transitive
resolution, frozen manifests, staleness, activation, sandbox receipt, and
approval behavior are covered by `tests/test_skill_loader.py`.
