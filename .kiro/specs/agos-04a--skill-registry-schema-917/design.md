# 設計：Task Skill Registry Schema、Revision 與 Dependency Repository

> Issue: #917 | Epic: #914

## 架構決策

### AD-1: 獨立模組 `skill_registry.py`

新增 `src/trustforge/skill_registry.py`，不修改既有 `skills.py`。

兩者的關係：
- `skills.py` — 管理 outer-policy family artifacts（source/analysis/report/evaluation/improvement）
- `skill_registry.py` — 管理 task skill metadata + revision + dependencies

Task Skill 的 `family` 欄位使用相同的 5-family enum，但 skill 本身是更細粒度的
可組合單位（e.g. "analysis-fundamental", "analysis-sentiment", "report-markdown-gen"）。

### AD-2: 共用 DB 或獨立 DB

MVP 使用**獨立 SQLite DB**（`data/skill_registry.db`），與 memory_os.db 分離。
理由：
- 各子系統可獨立 upgrade/rollback
- 避免 cross-table 鎖競爭
- 後續合併到共用 DB 只需搬表

```python
def default_db_path() -> Path:
    return Path(os.getenv("TRUSTFORGE_SKILL_REGISTRY_DB", "data/skill_registry.db"))
```

### AD-3: Immutable Revision — Content-Addressed

```python
def revision_hash_for(content: dict) -> str:
    """Calculate deterministic hash for skill revision content."""
    payload = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

一旦 revision 寫入，不可 UPDATE content 欄位。要修改只能建新 revision。

### AD-4: Active Revision 管理

每個 skill 最多有一個 `is_active=1` 的 revision。切換 active：
1. 將舊 active revision `is_active=0`
2. 將新 revision `is_active=1`
3. 在同一 transaction 內完成

SQLite partial unique index 確保不會有多個 active：
```sql
CREATE UNIQUE INDEX idx_skill_active ON skill_revisions(skill_id) WHERE is_active = 1;
```

### AD-5: Cycle Detection

DFS 演算法，深度上限 10：

```python
def _has_cycle(repo, skill_id: str, visited: set, depth: int = 0) -> bool:
    if depth > 10:
        return True  # treat as cycle (too deep)
    if skill_id in visited:
        return True
    visited.add(skill_id)
    for dep in repo.get_dependencies(skill_id):
        if dep.relation == "requires" and _has_cycle(repo, dep.to_skill_id, visited.copy(), depth + 1):
            return True
    return False
```

`optional` 和 `conflicts` 不參與 cycle detection（只有 `requires` 構成硬依賴圖）。

### AD-6: Lifecycle State Machine

```
draft → staged → active → frozen → retired
         ↑                    │
         └────────────────────┘ (unfreeze: only with approval)
```

- `draft`: 開發中，可隨意修改 metadata
- `staged`: 準備上線，需要 review
- `active`: 正式使用中
- `frozen`: 暫時凍結（不可被選擇，但 revision 保留）
- `retired`: 永久下架

高風險 skill（`external_write` / `deploy_or_release`）不可直接 `draft → active`，
必須經過 `staged` 且有 approval。

## 資料流

```
TaskSkill(lifecycle=draft) → SkillRegistryRepository.save_skill()
                                      │
                                      ↓
SkillRevision(content) → save_revision() → verify hash matches
                                      │
                                      ↓
                              skill_revisions table (immutable)
                                      │
set_active(skill_id, hash) ───────────┘
                                      │
SkillDependency → add_dependency() → cycle check → skill_dependencies table
```

## 測試策略

`tests/test_skill_registry.py`：
- Migration upgrade/rollback
- save_skill + get_skill round-trip
- save_revision immutability（same hash same content → OK; different content → error）
- get_active_revision
- set_active switch
- add_dependency + get_dependencies
- Self-cycle → fail
- Transitive cycle → fail
- High-risk lifecycle validation（cannot skip staged）
- list_skills filtering by family/lifecycle
