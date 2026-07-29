# Task Skill Registry Schema、Revision 與 Dependency Repository

> Issue: #917 | Epic: #914
> Depends on: #915
> Labels: agent-os, database, P0
> Safety: ⛔ DB schema/migration — 需 Eric 當日授權 token

## 背景

現有 `skills.py` 管理 5 個 outer-policy family 的 immutable artifact。Agent OS 新增
「Task Skill」概念：更細粒度的可組合技能，帶有 risk classification、dependency graph
與 lifecycle governance。

Task Skill 與 outer-policy family 共存但不覆寫。

## 範圍

實作 task skill、revision 與 dependency 的 persistence（SQLite）、repository CRUD、
validation logic。

**不包含**：Skill Loader/governance runtime（#920）、Context Builder（#921）、
production wiring。

## 功能需求

### FR-1: skills table

| Column | Type | Constraint |
|--------|------|-----------|
| skill_id | TEXT | PK |
| family | TEXT | NOT NULL, CHECK in (source, analysis, report, evaluation, improvement) |
| name | TEXT | NOT NULL |
| description | TEXT | NOT NULL DEFAULT '' |
| risk_class | TEXT | NOT NULL, CHECK in (read_only, local_write, external_write, deploy_or_release) |
| side_effect_class | TEXT | NOT NULL DEFAULT '' |
| verification_preconditions | TEXT (JSON array) | NOT NULL DEFAULT '[]' |
| verification_postconditions | TEXT (JSON array) | NOT NULL DEFAULT '[]' |
| lifecycle | TEXT | NOT NULL DEFAULT 'draft', CHECK in (draft, staged, active, frozen, retired) |
| created_at | TEXT (ISO 8601) | NOT NULL |

### FR-2: skill_revisions table

| Column | Type | Constraint |
|--------|------|-----------|
| revision_hash | TEXT (SHA-256) | PK |
| skill_id | TEXT | FK → skills, NOT NULL |
| content | TEXT (JSON) | NOT NULL |
| is_active | INTEGER | NOT NULL DEFAULT 0 |
| created_at | TEXT (ISO 8601) | NOT NULL |

- UNIQUE partial index: `(skill_id) WHERE is_active = 1`（每 skill 最多一個 active revision）
- Content 不可修改（immutable, content-addressed）

### FR-3: skill_dependencies table

| Column | Type | Constraint |
|--------|------|-----------|
| from_skill_id | TEXT | FK → skills |
| to_skill_id | TEXT | FK → skills |
| relation | TEXT | NOT NULL, CHECK in (requires, optional, conflicts) |
| created_at | TEXT (ISO 8601) | NOT NULL |

- PK: `(from_skill_id, to_skill_id, relation)`
- Self-cycle 禁止（from ≠ to）
- N-level cycle detection（深度 ≤ 10）

### FR-4: TaskSkill / SkillRevision / SkillDependency dataclasses

```python
@dataclass
class TaskSkill:
    skill_id: str
    family: str
    name: str
    description: str
    risk_class: str
    side_effect_class: str
    verification_preconditions: list[str]
    verification_postconditions: list[str]
    lifecycle: str
    created_at: str

@dataclass
class SkillRevision:
    revision_hash: str
    skill_id: str
    content: dict
    is_active: bool
    created_at: str

@dataclass
class SkillDependency:
    from_skill_id: str
    to_skill_id: str
    relation: str
    created_at: str
```

### FR-5: SkillRegistryRepository

- `save_skill(skill: TaskSkill) -> None`
- `get_skill(skill_id: str) -> TaskSkill | None`
- `list_skills(*, family: str | None = None, lifecycle: str | None = None) -> list[TaskSkill]`
- `save_revision(revision: SkillRevision) -> None` — immutable write
- `get_revision(revision_hash: str) -> SkillRevision | None`
- `get_active_revision(skill_id: str) -> SkillRevision | None`
- `set_active(skill_id: str, revision_hash: str) -> None`
- `add_dependency(dep: SkillDependency) -> None` — 含 cycle detection
- `get_dependencies(skill_id: str) -> list[SkillDependency]`
- `get_dependents(skill_id: str) -> list[SkillDependency]`

### FR-6: Validation Rules

- Revision content hash 必須符合 `SHA-256(canonical_json(content))`
- 已存在的 revision_hash 若 content 不同 → hash collision error
- Risk_class 為 `external_write` 或 `deploy_or_release` 時，lifecycle 不可直接跳到 `active`
- Self-cycle dependency → fail closed
- Transitive cycle dependency（A→B→C→A）→ fail closed

## 非功能需求

- **NFR-1: 零第三方依賴** — 純 stdlib sqlite3
- **NFR-2: 與既有 outer-policy 不衝突** — 不修改 `skills.py` / `SKILL_FAMILIES` / `FORBIDDEN_FAMILIES`
- **NFR-3: fail-closed** — invalid hash, cycle, missing required → 拒絕

## 驗收條件

1. Skill, revision 和 dependency contracts 持久化
2. Revision content 是 immutable 且 content-addressed
3. Risk, side-effect class, verification contract 和 lifecycle status 為必填
4. Invalid dependency / self-cycle fail closed
5. 既有 outer policy family behavior 不變
6. Migration upgrade/rollback 與 repository tests 通過
7. 完整 pre-push 通過
