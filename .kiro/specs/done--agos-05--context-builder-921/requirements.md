# Context Builder 與 Immutable Manifest

> Issue: #921 | Epic: #914
> Depends on: #916, #917, #918, #920
> Labels: agent-os, context, P0

## 背景

每次分析 run 需要一份 deterministic、immutable 的 context manifest，記錄
snapshot、question、memory、skill、tool 和 policy 的完整參照，以及被排除
的 references（含排除原因）。

## 範圍

實作 ContextBuilder，產生 per-run immutable manifest。

**不包含**：Runtime integration（#922）、Admin API（#923）。

## 功能需求

### FR-1: ContextBuilder 主入口

```python
class ContextBuilder:
    def build(
        self,
        *,
        run_id: str,
        snapshot_ref: str | None = None,
        question_ref: str | None = None,
        memory_refs: list[MemoryRef] | None = None,
        skill_manifest: FrozenSkillManifest | None = None,
        tool_refs: list[str] | None = None,
        policy_refs: list[dict] | None = None,
        token_budget: int = 4096,
    ) -> ContextManifest:
```

### FR-2: ContextManifest dataclass

```python
@dataclass
class ContextManifest:
    manifest_id: str          # UUID
    run_id: str
    created_at: str
    content_hash: str         # deterministic SHA-256
    token_budget: int
    token_used: int
    included_refs: IncludedRefs
    excluded_refs: list[ExcludedRef]

@dataclass
class IncludedRefs:
    snapshot_ref: str | None
    question_ref: str | None
    memory_refs: list[dict]   # {memory_id, rank, reason, evidence_eligible}
    skill_refs: list[dict]    # {skill_id, revision_hash, reason}
    tool_refs: list[dict]     # {tool_id, version}
    policy_refs: list[dict]   # {policy_id, revision_hash}

@dataclass
class ExcludedRef:
    ref_id: str
    ref_type: str             # memory | skill | tool | policy
    reason: str               # stale | over_budget | approval_required | evidence_ineligible
```

### FR-3: Immutability Guarantee

- 一旦 build() 回傳 manifest，該 manifest 的 content_hash 是 final
- 後續 memory/skill/tool/policy 更新不改變已建立的 manifest
- Manifest 寫入 DB 後不可 UPDATE content

### FR-4: Deterministic Hash

```python
content_hash = SHA-256(canonical_json({
    "run_id": ...,
    "included_refs": ...,
    "excluded_refs": ...,
    "token_budget": ...,
    "token_used": ...,
}))
```

相同輸入 → 相同 hash（reproducible）。

### FR-5: Exclusion Logic

Build 過程中，以下 refs 被排除（不進入 included）：
1. **stale**: memory 過期（expires_at < now）、skill lifecycle=frozen/retired
2. **over_budget**: token 用量超過 budget
3. **approval_required**: 高風險 skill/tool 未獲 approval
4. **evidence_ineligible**: memory evidence_eligible=false → 排除出 Evidence input（但仍可作為 context display）

每個 ExcludedRef 記錄排除原因。

### FR-6: Evidence-Ineligible Guard

`evidence_eligible=false` 的 memory 可以出現在 context display（report 呈現、Admin summary），
但**不可進入 Evidence inputs**（Trust scoring pipeline）。

Context Builder 在 manifest 中明確標記 `evidence_eligible` 欄位，
供 Runtime (#922) 在組裝 scoring input 時遵守。

### FR-7: Report / Admin Disclosure

提供 helper：
```python
def manifest_summary(manifest: ContextManifest) -> dict:
    """Summary for report/admin display (not Evidence)."""
    return {
        "included_count": ...,
        "excluded_count": ...,
        "token_used_pct": ...,
        "exclusion_reasons": Counter(e.reason for e in manifest.excluded_refs),
    }
```

## 非功能需求

- **NFR-1: 零第三方依賴**
- **NFR-2: Deterministic** — same input → same content_hash
- **NFR-3: Immutable** — once created, never modified

## 驗收條件

1. ContextBuilder 每次 run 產生一份 immutable manifest
2. 既有 manifests 不受後續 memory/skill/tool/policy 更新影響
3. Content hash deterministic 且 reproducible
4. excluded_refs 記錄 stale, over-budget, approval-required, evidence-ineligible
5. Evidence-ineligible memory 排除出 Evidence inputs
6. Report/Admin summary 揭露 context（不 claim 為 Evidence）
7. 完整 pre-push 通過
