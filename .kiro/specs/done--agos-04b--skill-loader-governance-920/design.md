# 設計：Task Skill Loader、Frozen Manifest 與 Approval Governance

> Issue: #920 | Epic: #914

## 架構決策

### AD-1: 新模組 `skill_loader.py`

新增 `src/trustforge/skill_loader.py`，依賴 `skill_registry.py`（#917）。

### AD-2: Frozen Manifest 持久化

Frozen manifest 存入 skill_registry.db 的新表 `frozen_skill_manifests`：

```sql
CREATE TABLE frozen_skill_manifests (
    run_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    revision_hash TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, skill_id)
);
```

### AD-3: Discovery 流程

```python
class SkillLoader:
    def __init__(self, registry: SkillRegistryRepository):
        self._registry = registry

    def discover(self, *, trigger=None, family=None, risk_class=None, lifecycle="active"):
        skills = self._registry.list_skills(family=family, lifecycle=lifecycle)
        if risk_class:
            skills = [s for s in skills if s.risk_class == risk_class]
        # Additional trigger-based filtering (future extensibility)
        return skills
```

### AD-4: Dependency Resolution — Topological Sort

```python
def resolve_dependencies(self, skill_id: str) -> list[SkillRevision]:
    """BFS/DFS topological sort of `requires` edges. Returns leaf-first order."""
    visited = set()
    order = []
    self._dfs_resolve(skill_id, visited, order)
    return order

def _dfs_resolve(self, skill_id, visited, order):
    if skill_id in visited:
        return
    visited.add(skill_id)
    deps = self._registry.get_dependencies(skill_id)
    for dep in deps:
        if dep.relation == "requires":
            rev = self._registry.get_active_revision(dep.to_skill_id)
            if rev is None:
                raise ValueError(f"stale dependency: {dep.to_skill_id} has no active revision")
            self._dfs_resolve(dep.to_skill_id, visited, order)
    rev = self._registry.get_active_revision(skill_id)
    if rev is None:
        raise ValueError(f"skill {skill_id} has no active revision")
    order.append(rev)
```

### AD-5: Freeze Flow

```python
def freeze_manifest(self, run_id: str, skill_ids: list[str], reasons: dict[str, str]) -> FrozenSkillManifest:
    """Freeze selected skills + all transitive deps for a run."""
    entries = []
    for sid in skill_ids:
        revs = self.resolve_dependencies(sid)
        for rev in revs:
            if rev.skill_id not in [e.skill_id for e in entries]:
                entries.append(FrozenSkillEntry(
                    skill_id=rev.skill_id,
                    revision_hash=rev.revision_hash,
                    reason=reasons.get(rev.skill_id, "transitive_dependency"),
                ))
    manifest = FrozenSkillManifest(run_id=run_id, created_at=now_iso(), entries=entries)
    self._persist_manifest(manifest)
    return manifest
```

### AD-6: Activation Governance

```python
@dataclass
class ActivationProposal:
    proposal_id: str
    skill_id: str
    revision_hash: str
    reason: str
    proposed_at: str
    status: str  # "pending" | "approved" | "rejected"
    approved_by: str | None
    approved_at: str | None

def propose_activation(self, skill_id: str, revision_hash: str, reason: str) -> ActivationProposal:
    """Create activation proposal for high-risk skill."""
    skill = self._registry.get_skill(skill_id)
    if skill.risk_class not in ("external_write", "deploy_or_release"):
        raise ValueError("only high-risk skills require activation proposal")
    # persist proposal
    ...

def approve_activation(self, proposal_id: str, approved_by: str) -> None:
    """Human approves activation. Sets lifecycle to active."""
    ...

def is_activation_approved(self, skill_id: str, revision_hash: str) -> bool:
    """Check if a specific revision has been approved for activation."""
    ...
```

## Stale Detection

```python
def is_stale(self, skill_id: str) -> bool:
    skill = self._registry.get_skill(skill_id)
    if skill is None:
        return True
    if skill.lifecycle in ("frozen", "retired"):
        return True
    if self._registry.get_active_revision(skill_id) is None:
        return True
    # Check deps
    for dep in self._registry.get_dependencies(skill_id):
        if dep.relation == "requires" and self.is_stale(dep.to_skill_id):
            return True
    return False
```

## 測試策略

`tests/test_skill_loader.py`：
- discover with various filters
- resolve_dependencies normal case
- resolve_dependencies with stale dep → error
- freeze_manifest captures exact hashes
- Frozen manifest survives active pointer change
- Stale detection: frozen lifecycle → stale
- Stale detection: no active revision → stale
- Stale detection: stale dependency → stale
- Activation proposal for high-risk skill
- Activation rejection for non-high-risk → error
- Unapproved high-risk → cannot freeze
- Output boundary check (via risk_class)
