# 實作任務：Task Skill Registry Schema、Revision 與 Dependency Repository

> Issue: #917 | Epic: #914

## Task 1: 建立 skill_registry.py 模組骨架

- [x] 建立 `src/trustforge/skill_registry.py`
- [x] 實作 `TaskSkill` dataclass
- [x] 實作 `SkillRevision` dataclass
- [x] 實作 `SkillDependency` dataclass
- [x] 定義 constants：`VALID_FAMILIES`, `VALID_RISK_CLASSES`, `VALID_LIFECYCLES`, `VALID_RELATIONS`
- [x] 實作 `default_db_path() -> Path`
- [x] 實作 `revision_hash_for(content: dict) -> str`

## Task 2: 實作 Migration

- [x] 實作 `upgrade(conn: sqlite3.Connection) -> None`
  - 建立 `_meta` table
  - 建立 `skills` table（含 CHECK constraints）
  - 建立 `skill_revisions` table
  - 建立 `skill_dependencies` table（含 PK、CHECK from≠to）
  - 建立 partial unique index on `(skill_id) WHERE is_active=1`
  - 更新 version
- [x] 實作 `rollback(conn: sqlite3.Connection) -> None`
  - DROP tables
  - 更新 version

## Task 3: 實作 SkillRegistryRepository CRUD

- [x] 實作 `__init__`（db_path, lazy connection）
- [x] 實作 `ensure_schema()`
- [x] 實作 `save_skill(skill: TaskSkill) -> None`
  - Validate family, risk_class, lifecycle
- [x] 實作 `get_skill(skill_id) -> TaskSkill | None`
- [x] 實作 `list_skills(*, family=None, lifecycle=None) -> list[TaskSkill]`
- [x] 實作 `save_revision(revision: SkillRevision) -> None`
  - Verify hash == revision_hash_for(content)
  - Existing same hash + same content → no-op
  - Existing same hash + different content → raise ValueError
- [x] 實作 `get_revision(revision_hash) -> SkillRevision | None`
- [x] 實作 `get_active_revision(skill_id) -> SkillRevision | None`
- [x] 實作 `set_active(skill_id, revision_hash) -> None`
  - Transaction: unset old active → set new active
  - Validate revision exists and belongs to skill_id
- [x] 實作 `close()`

## Task 4: 實作 Dependency Management 與 Cycle Detection

- [x] 實作 `add_dependency(dep: SkillDependency) -> None`
  - Validate from ≠ to
  - Validate relation in VALID_RELATIONS
  - Check for cycle（DFS, depth ≤ 10, only `requires` edges）
  - Insert
- [x] 實作 `get_dependencies(skill_id) -> list[SkillDependency]`
- [x] 實作 `get_dependents(skill_id) -> list[SkillDependency]`
- [x] 實作 `_has_cycle(skill_id, target, visited, depth) -> bool`

## Task 5: 實作 Lifecycle Validation

- [x] 高風險 skill（external_write / deploy_or_release）lifecycle 不可跳過 staged
- [x] `update_lifecycle(skill_id, new_status) -> None`
  - Validate transition is legal
  - High-risk check

## Task 6: 單元測試

- [x] 建立 `tests/test_skill_registry.py`
- [x] 測試 migration upgrade/rollback
- [x] 測試 save_skill + get_skill round-trip
- [x] 測試 save_revision immutability（same hash/content → OK）
- [x] 測試 save_revision hash mismatch → error
- [x] 測試 save_revision hash collision（different content） → error
- [x] 測試 get_active_revision
- [x] 測試 set_active switch
- [x] 測試 add_dependency normal case
- [x] 測試 self-cycle → fail
- [x] 測試 transitive cycle (A→B→C→A) → fail
- [x] 測試 high-risk lifecycle cannot skip staged
- [x] 測試 list_skills filtering
- [x] 確認不 import skills.py / outer_skill_policy
- [x] 確認 pre-push 通過

### HEAD evidence

Implemented by `src/trustforge/skill_registry.py`; schema authorization,
revision immutability, dependency cycles, lifecycle rules, and repository
behavior are covered by `tests/test_skill_registry.py`.
