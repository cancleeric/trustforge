# Agent OS Security Disposition

> Epic: [#914](https://github.com/cancleeric/trustforge/issues/914)
> Issue: [#925](https://github.com/cancleeric/trustforge/issues/925)
> Date: 2026-07-29
> Status: BLOCKED — desktop/mobile Eye and commit-bound CISO/CPO review pending
> Branch: `agos/915-architecture-contracts`

## Reviewer Information

- Implementation: Kiro (automated)
- Security review required: harper (CISO)
- Product review required: gray (CPO)
- Implementation baseline: current branch HEAD; the immutable reviewed SHA is
  recorded in the PR disposition comment
- Closeout review binding: the commit containing this document must be reviewed
  again after integration if production code has advanced beyond that baseline
- CPO disposition: task/backlog reconciliation completed; desktop/mobile Eye
  and exact-HEAD re-review remain pending
- CISO disposition: pending; this document does not self-approve security

## Security Controls Implemented

### 1. DB Authorization — File-Based Token (No Env-Var Bypass)

**Mechanism**: `/tmp/eric-auth-YYYYMMDD-trustforge-{purpose}.token`
**Content**: `authorized {purpose} YYYY-MM-DD`
Authorization is centralized at the schema mutation boundary; direct upgrade
and rollback paths are covered by the same guard. No unresolved DB authorization
claim is recorded here. Production use still requires Eric's same-day token.

### 2. Tool Execution Gate — Fail-Closed on Init Failure

**Mechanism**: `tool_audited_fetch()` raises `PermissionError` if:
- `_tool_registry is None` (init failed → fail-closed, not fail-open)
- `assert_executable()` → unknown tool or high-risk tool

**No silent pass**: Even if the entire Agent OS fails to initialize, tools cannot execute when AGOS is enabled.

### 3. Transitive High-Risk Approval + Sandbox Gate

**Mechanism**: `freeze_manifest()` checks `risk_class` on ALL resolved revisions (not just top-level requested skills).
**Sandbox**: `approve_activation()` requires `sandbox_passed=True` parameter. Without it, approval is rejected.
**DB column**: `activation_proposals.sandbox_passed INTEGER NOT NULL DEFAULT 0`
**Verification**: `_sandbox_verified()` checks `sandbox_passed=1` in the approved proposal record.

### 4. Context Builder — DB-Verified Evidence Eligibility

**Mechanism**: Context Builder does NOT trust caller-supplied `evidence_eligible` flag in MemoryRef.
- Looks up the actual `MemoryEntry` from DB via `memory_repo.get(mref.memory_id)`
- If entry not found in DB → excluded as "stale"
- Uses `entry.evidence_eligible` (the DB-verified value)
- If no repo available → defaults to `False` (fail-closed)

### 5. Real Analysis Flow Integration

**Hook points**:
- `_stage_source_ingestion` → `_agos_build_context()`: memory retrieval, skill selection, tool inventory, manifest creation
- `_stage_claim_extraction` → `_agos_record_tool()`: Bedrock invocation audit
- `_stage_report_delivery` → `_agos_finalize()`: run completion

**Memory retrieval**: Existing `retrieval_context` (historical question matches) mapped through `MemoryRetrievalAdapter` as formal memory references.
**Skill selection**: `SkillLoader.discover(family="analysis")` finds active skills.
**Tool audit**: Bedrock calls recorded in tool invocation audit trail.

### 6. Admin API Auth — X-Admin-Token (No Dual Bearer)

**Mechanism**: `dispatch_admin_agos()` does NOT do its own auth check.
Auth is handled by the outer `web.py` `_admin_auth_check()` which:
- Uses `X-Admin-Token` header (existing convention)
- Implements rate limiting + lockout
- Uses `hmac.compare_digest` constant-time comparison

No `Authorization: Bearer` dual-gate. Single auth path.

### 7. Admin API Governance Fields

Memory endpoint returns: `lineage_rank`, `selection_reason`, `evidence_eligible_verified`, `inclusion_status`
Skills endpoint returns: `family`, `risk_class`, `lifecycle`, `side_effect_class`, `dependencies[]`, `frozen_at`
Tools endpoint returns: `side_effect_class`, `evidence_class`, `approval_requirement`
Context endpoint returns: `included_refs`, `excluded_refs` (with reasons), `exclusion_reasons` summary

## Known Limitations (Honest Disclosure)

1. **Sandbox gate is declaration-based**: `sandbox_passed=True` is a parameter the caller asserts; there is no automated sandbox execution environment in this MVP. The gate ensures the approval record documents that sandbox testing occurred, but enforcement is on the human reviewer.

2. **Memory retrieval in analysis_flow is limited to question_context**: The existing `question_context()` results are mapped. There is no embedding-based semantic search or vector RAG — the adapter wraps what exists.

3. **Tool registry pre-population**: The registry starts empty. Tools must be explicitly registered before AGOS enforcement activates. When AGOS is enabled but tools haven't been registered, all tool calls are blocked (fail-closed by design).

4. **Admin UI is functional but not eye-scanned**: Components exist, tests pass, TypeScript compiles. Desktop/mobile eye scan has not been performed.

## Test Evidence

Evidence is commit-bound by the immutable exact-HEAD SHA recorded in the PR
review disposition; this document does not embed a self-referential commit.

| Test scope | Recorded result |
|------------|-----------------|
| Current targeted AGOS replay/guard/regression/lineage run | 78 PASS |
| Current targeted Admin AGOS frontend run | 18 PASS |
| Current frontend production build | PASS |
| Authenticated real-handler HTTP E2E | PASS (included above) |
| Full pre-push backend parallel lane | 6043 PASS, 12 skipped |
| Full pre-push backend serial lane | 3 PASS, 1 skipped |
| Full pre-push frontend | 596 PASS |

The full pre-push results are the recorded HEAD gate results. Replay,
non-regression, lineage-consistency, security-guard, and authenticated HTTP
tests now have concrete passing evidence. No expected-failure exception remains
claimed. The manual Eye scan and exact-HEAD human reviews remain open.

## Disposition

- [ ] harper (CISO): APPROVED / BLOCKED
- [ ] gray (CPO): exact-HEAD re-review pending after docs reconciliation; Eye
  remains a separate manual gate
- [ ] /codex-review adversarial gate: PASS / FAIL
- [x] Authenticated real-handler HTTP E2E: PASS

## Activation Conditions

1. Production deployment NOT authorized by this disposition
2. DB tokens must be issued by Eric before first production `upgrade()` runs
3. Feature flag `TRUSTFORGE_AGOS_ENABLED` must remain `0` until explicit activation approval
4. High-risk tools/skills cannot auto-execute without human approval + sandbox evidence
