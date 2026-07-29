# 設計：Context Builder 與 Immutable Manifest

> Issue: #921 | Epic: #914

## 架構決策

### AD-1: 新模組 `context_builder.py`

新增 `src/trustforge/context_builder.py`，依賴：
- `memory_os.py` (#916) — MemoryEntry / MemoryRepository
- `skill_loader.py` (#920) — FrozenSkillManifest
- `tool_registry.py` (#918) — ToolCapability

### AD-2: Manifest 持久化

使用獨立 SQLite DB 或共用 memory_os.db 的新表：

```sql
CREATE TABLE context_manifests (
    manifest_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL,
    token_budget INTEGER NOT NULL,
    token_used INTEGER NOT NULL,
    included_refs TEXT NOT NULL,   -- JSON
    excluded_refs TEXT NOT NULL,   -- JSON
    created_at TEXT NOT NULL
);
```

`run_id` UNIQUE 確保一個 run 只有一份 manifest。

### AD-3: Build Pipeline

```python
class ContextBuilder:
    def __init__(self, memory_repo, skill_loader, tool_registry):
        ...

    def build(self, *, run_id, snapshot_ref, question_ref,
              memory_refs, skill_manifest, tool_refs, policy_refs,
              token_budget=4096) -> ContextManifest:
        included = IncludedRefs(...)
        excluded = []
        token_used = 0

        # 1. Process memory refs
        for mref in (memory_refs or []):
            entry = self._memory_repo.get(mref.memory_id)
            if entry and entry.expires_at and entry.expires_at < now_iso():
                excluded.append(ExcludedRef(mref.memory_id, "memory", "stale"))
                continue
            token_cost = self._estimate_tokens(mref.content_preview)
            if token_used + token_cost > token_budget:
                excluded.append(ExcludedRef(mref.memory_id, "memory", "over_budget"))
                continue
            token_used += token_cost
            included.memory_refs.append({...})

        # 2. Process skill manifest
        if skill_manifest:
            for entry in skill_manifest.entries:
                skill = self._skill_loader._registry.get_skill(entry.skill_id)
                if skill and skill.lifecycle in ("frozen", "retired"):
                    excluded.append(ExcludedRef(entry.skill_id, "skill", "stale"))
                    continue
                if skill and skill.risk_class in ("external_write", "deploy_or_release"):
                    if not self._skill_loader.is_activation_approved(entry.skill_id, entry.revision_hash):
                        excluded.append(ExcludedRef(entry.skill_id, "skill", "approval_required"))
                        continue
                included.skill_refs.append({...})

        # 3. Process tool refs
        for tool_id in (tool_refs or []):
            if not self._tool_registry.is_known(tool_id):
                excluded.append(ExcludedRef(tool_id, "tool", "stale"))
                continue
            if self._tool_registry.requires_approval(tool_id):
                excluded.append(ExcludedRef(tool_id, "tool", "approval_required"))
                continue
            included.tool_refs.append({...})

        # 4. Build manifest
        content_hash = self._compute_hash(run_id, included, excluded, token_budget, token_used)
        manifest = ContextManifest(
            manifest_id=str(uuid4()),
            run_id=run_id,
            created_at=now_iso(),
            content_hash=content_hash,
            token_budget=token_budget,
            token_used=token_used,
            included_refs=included,
            excluded_refs=excluded,
        )
        self._persist(manifest)
        return manifest
```

### AD-4: Token Estimation

Simple character-based estimation（1 token ≈ 4 chars for English, 2 chars for CJK）:

```python
def _estimate_tokens(self, text: str) -> int:
    cjk_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    ascii_count = len(text) - cjk_count
    return (ascii_count // 4) + (cjk_count // 2) + 1
```

### AD-5: Deterministic Hash

```python
def _compute_hash(self, run_id, included, excluded, token_budget, token_used) -> str:
    payload = {
        "run_id": run_id,
        "included_refs": included.to_dict(),
        "excluded_refs": [e.to_dict() for e in excluded],
        "token_budget": token_budget,
        "token_used": token_used,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

## 資料流

```
MemoryRetrievalAdapter (#919) → memory_refs
SkillLoader (#920) → FrozenSkillManifest
ToolRegistry (#918) → tool capabilities
                │
                ↓
        ContextBuilder.build()
                │
    ┌───────────┼───────────┐
    ↓           ↓           ↓
included    excluded    content_hash
    │           │
    ↓           ↓
context_manifests table (immutable)
```

## 測試策略

`tests/test_context_builder.py`：
- build produces immutable manifest with correct hash
- Same input → same content_hash (deterministic)
- Stale memory (expired) → excluded
- Over-budget memory → excluded
- Unapproved high-risk skill → excluded
- Unknown tool → excluded
- evidence_ineligible memory → marked but may appear in context display
- Manifest persists and cannot be overwritten
- manifest_summary returns correct counts
