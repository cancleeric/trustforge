# 實作任務：Task Skill Registry Schema、Revision 與 Dependency Repository

> Issue: #917 | Epic: #914

## Task 1: 建立 skill_registry.py 模組骨架

- [ ] 建立 `src/trustforge/skill_registry.py`
- [ ] 實作 `TaskSkill` dataclass
- [ ] 實作 `SkillRevision` dataclass
- [ ] 實作 `SkillDependency` dataclass
- [ ] 定義 constants：`VALID_FAMILIES`, `VALID_RISK_CLASSES`, `VALID_LIFECYCLES`, `VALID_RELATIONS`
- [ ] 實作 `default_db_path() -> Path`
- [ ] 實作 `revision_hash_for(content: dict) -> str`

## Task 2: 實作 Migration

- [ ] 實作 `upgrade(conn: sqlite3.Connection) -> None`
  - 建立 `_meta` table
  - 建立 `skills` table（含 CHECK constraints）
  - 建立 `skill_revisions` table
  - 建立 `skill_dependencies` table（含 PK、CHECK from≠to）
  - 建立 partial unique index on `(skill_id) WHERE is_active=1`
  - 更新 version
- [ ] 實作 `rollback(conn: sqlite3.Connection) -> None`
  - DROP tables
  - 更新 version

## Task 3: 實作 SkillRegistryRepository CRUD

- [ ] 實作 `__init__`（db_path, lazy connection）
- [ ] 實作 `ensure_schema()`
- [ ] 實作 `save_skill(skill: TaskSkill) -> None`
  - Validate family, risk_class, lifecycle
- [ ] 實作 `get_skill(skill_id) -> TaskSkill | None`
- [ ] 實作 `list_skills(*, family=None, lifecycle=None) -> list[TaskSkill]`
- [ ] 實作 `save_revision(revision: SkillRevision) -> None`
  - Verify hash == revision_hash_for(content)
  - Existing same hash + same content → no-op
  - Existing same hash + different content → raise ValueError
- [ ] 實作 `get_revision(revision_hash) -> SkillRevision | None`
- [ ] 實作 `get_active_revision(skill_id) -> SkillRevision | None`
- [ ] 實作 `set_active(skill_id, revision_hash) -> None`
  - Transaction: unset old active → set new active
  - Validate revision exists and belongs to skill_id
- [ ] 實作 `close()`

## Task 4: 實作 Dependency Management 與 Cycle Detection

- [ ] 實作 `add_dependency(dep: SkillDependency) -> None`
  - Validate from ≠ to
  - Validate relation in VALID_RELATIONS
  - Check for cycle（DFS, depth ≤ 10, only `requires` edges）
  - Insert
- [ ] 實作 `get_dependencies(skill_id) -> list[SkillDependency]`
- [ ] 實作 `get_dependents(skill_id) -> list[SkillDependency]`
- [ ] 實作 `_has_cycle(skill_id, target, visited, depth) -> bool`

## Task 5: 實作 Lifecycle Validation

- [ ] 高風險 skill（external_write / deploy_or_release）lifecycle 不可跳過 staged
- [ ] `update_lifecycle(skill_id, new_status) -> None`
  - Validate transition is legal
  - High-risk check

## Task 6: 單元測試

- [ ] 建立 `tests/test_skill_registry.py`
- [ ] 測試 migration upgrade/rollback
- [ ] 測試 save_skill + get_skill round-trip
- [ ] 測試 save_revision immutability（same hash/content → OK）
- [ ] 測試 save_revision hash mismatch → error
- [ ] 測試 save_revision hash collision（different content） → error
- [ ] 測試 get_active_revision
- [ ] 測試 set_active switch
- [ ] 測試 add_dependency normal case
- [ ] 測試 self-cycle → fail
- [ ] 測試 transitive cycle (A→B→C→A) → fail
- [ ] 測試 high-risk lifecycle cannot skip staged
- [ ] 測試 list_skills filtering
- [ ] 確認不 import skills.py / outer_skill_policy
- [ ] 確認 pre-push 通過
