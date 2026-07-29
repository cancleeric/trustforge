# Agent OS Replay、E2E 與 Release-Hardening Gate

> Issue: #925 | Epic: #914
> Depends on: #922, #924
> Labels: agent-os, testing, release, P0

## 背景

Agent OS 各模組（Memory, Skill, Tool, Context, Runtime, Admin）已分別實作。
本 issue 建立 replay verification 和 end-to-end release gates，確保整體系統
在各種邊界條件下行為正確。

## 範圍

- Replay verification（frozen manifest → reproducible hashes）
- Security invariant E2E tests
- Non-regression E2E tests
- Release-readiness checklist

**不包含**：production deployment 或 activation（需另走人工 approval）。

## 功能需求

### FR-1: Replay Verification

重播同一份 frozen manifest，驗證產出相同 reference hashes：
- Context manifest content_hash reproducible
- Skill revision hashes unchanged
- Memory content hashes unchanged

```python
def verify_replay(manifest: ContextManifest) -> ReplayResult:
    """Re-derive all hashes from stored content. Returns match/mismatch report."""
```

### FR-2: Historical Memory Guard（E2E）

End-to-end 驗證：historical memory 無論如何設定都不能進入 Evidence/scoring input。
- Create historical memory (hermes-* provider, semantic kind)
- Attempt to set evidence_eligible=True → fail
- Attempt to inject into scoring pipeline → blocked

### FR-3: Unknown/Stale Skill Guard（E2E）

- Unknown skill_id → cannot be selected
- Stale skill (no active revision) → cannot be frozen
- Retired skill → excluded from manifest

### FR-4: Unknown Tool Guard（E2E）

- Unknown tool_id → `is_known()` = False → cannot execute
- External-write tool without approval → rejected

### FR-5: Runtime Lineage ↔ Admin Disclosure Consistency

驗證：runtime 產出的 lineage data 與 Admin API 回傳的內容一致。
- Context manifest from runtime = context from Admin API
- Memory refs from runtime = memories from Admin API
- Tool invocations from runtime = tools from Admin API

### FR-6: Existing Flow Non-Regression

- Analysis pipeline produces same Trust scores with AGOS_ENABLED=0 vs 1
- Question RAG returns same results
- Dialogue workflow unchanged
- Report generation unchanged

### FR-7: Security Reviewer Disposition

提供 security review checklist 與 disposition template：
- [ ] Trust Kernel immutability preserved
- [ ] Evidence binding integrity
- [ ] Approval governance for high-risk actions
- [ ] No secret leakage in Admin API
- [ ] Authorization gate enforced

### FR-8: Release-Readiness Checks

- Backend: all tests pass, lint clean, type-check clean
- Frontend: all tests pass, build success, lint clean
- Integration: E2E replay + guard tests pass
- Pre-push gate: full pass
- Security disposition: recorded

## 非功能需求

- **NFR-1: 可重複** — replay tests 為 deterministic
- **NFR-2: CI-compatible** — 所有 E2E tests 可在 CI 環境執行
- **NFR-3: 不部署** — 本 issue 不 deploy to production

## 驗收條件

1. Replaying frozen manifest reproduces reference hashes
2. Historical memory cannot become Evidence/scoring input
3. Unknown/stale skill and unknown tool fail closed
4. External-write/deploy capability cannot execute without human approval
5. Runtime lineage matches Admin API/UI disclosure
6. Existing analysis, Question RAG, dialogue flows 不回歸
7. Security reviewer disposition recorded
8. Full pre-push, backend/frontend E2E, release-readiness checks pass
9. 任何 production/activation action remains separately approval-gated
