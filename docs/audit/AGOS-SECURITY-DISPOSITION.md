# Agent OS Security Disposition

> Epic: [#914](https://github.com/cancleeric/trustforge/issues/914)
> Issue: [#925](https://github.com/cancleeric/trustforge/issues/925)
> Date: 2026-07-29
> Status: PENDING REVIEWER SIGN-OFF

## Reviewer Information

- Self-review by: Implementation author (automated pre-review)
- Date: 2026-07-29
- Branch: `agos/915-architecture-contracts`
- Requires: harper (CISO) + gray (CPO) sign-off before merge

## Security Checklist (Verified)

### Trust Kernel Immutability ✓

- [x] No Agent OS module imports `trustforge.trust.kernel` or `trustforge.trust.scoring`
  - Verified: `grep -r "from.*trust" src/trustforge/memory_os.py src/trustforge/skill_registry.py src/trustforge/tool_registry.py src/trustforge/context_builder.py src/trustforge/agos_runtime.py src/trustforge/memory_retrieval.py src/trustforge/skill_loader.py src/trustforge/agos_admin_api.py` → no trust imports
- [x] Trust weights (`DEFAULT_WEIGHTS`) unchanged — no modification in any AGOS file
- [x] Scoring formula unchanged — AGOS modules do not reference scoring functions
- [x] PIT time boundary unchanged — no `time.time()` in AGOS modules for scoring
- [x] Evidence binding (claim_id → source → Document) unchanged
- [x] Dawid-Skene EM unchanged

### Evidence Integrity ✓

- [x] `evidence_eligible` defaults to `false` (fail-closed)
  - Enforced: `MemoryEntry` dataclass default = `False`; `_get_or_create_entry` starts with `False` and only promotes after `validate_evidence_eligible()` passes
- [x] Historical conclusions (hermes-* provider + semantic kind) blocked from Evidence
  - Enforced: `validate_evidence_eligible()` explicitly rejects; `_is_historical_conclusion()` double-checks at retrieval
- [x] Dialogue memory cannot become Evidence
  - Enforced: `validate_evidence_eligible()` rejects `kind=="dialogue"`
- [x] `context_only` tool output cannot enter scoring pipeline
  - Enforced: `can_produce_evidence()` returns False for `none` and `context_only`
- [x] Evidence eligibility validation requires: provider + published_at + retrieved_at + content_hash (64-hex)
  - Enforced: `validate_evidence_eligible()` checks all four + kind + historical guard

### Approval Governance ✓

- [x] High-risk skills require activation proposal + approval before freeze_manifest
  - Enforced: `freeze_manifest()` calls `is_activation_approved()` → raises ValueError if not approved
- [x] High-risk skills cannot skip `staged` lifecycle
  - Enforced: `update_lifecycle()` checks risk_class before allowing draft→active
- [x] External-write tools require `approval_requirement=always` (invariant at registration)
  - Enforced: `register_tool()` raises ValueError if side_effect_class is high-risk but approval≠always
- [x] Unknown tools fail closed — `assert_executable()` raises PermissionError
  - Enforced: `tool_audited_fetch()` calls `assert_executable()` when AGOS enabled
- [x] Deploy_or_release tools blocked at runtime
  - Enforced: `assert_executable()` raises PermissionError for high-risk side_effect_class

### DB Authorization ✓

- [x] All three DB migrations require same-day authorization token
  - Enforced: `verify_db_authorization()` called at top of each `upgrade()` function
  - Token format: `agos-{purpose}-{YYYY-MM-DD}` (must match today UTC)
  - Bypass only in test (TRUSTFORGE_TESTING=1) or when AGOS disabled
  - 9 dedicated tests verify block/pass behavior

### Memory OS Security ✓

- [x] Content hash is SHA-256 and content-addressed (immutable)
- [x] Duplicate (provider, content_hash) rejected via UNIQUE index
- [x] Self-links rejected at application level
- [x] Append-only design (no DELETE operations exposed)

### Skill Registry Security ✓

- [x] Revision content immutable after write (content-addressed hash verified on save)
- [x] Hash collision detected and rejected
- [x] Self-cycle dependency rejected
- [x] Transitive cycle dependency detected (DFS, depth ≤ 10)
- [x] Existing outer-policy families unchanged (no modification to `skills.py`)

### Tool Registry Security ✓

- [x] Invocation records append-only (no DELETE method)
- [x] Input/output hashes recorded
- [x] `assert_executable()` blocks unknown tools with PermissionError
- [x] Approval invariant enforced at registration time

### Context Manifest Security ✓

- [x] One manifest per run (UNIQUE on run_id)
- [x] Content hash deterministic (canonical JSON + SHA-256, verified in tests)
- [x] Manifest immutable after creation (INSERT only, duplicate silently ignored)
- [x] Excluded references tracked with reasons
- [x] Evidence-ineligible memory marked in manifest for runtime enforcement

### Admin API Security ✓

- [x] All endpoints authorization-gated (Bearer token from TRUSTFORGE_ADMIN_TOKEN)
- [x] No token configured → no access (fail-closed; `check_admin_auth` returns False)
- [x] Sensitive content redacted by default (content_ref → "[REDACTED]")
- [x] Read-only: no mutation endpoints (all handlers are GET-only)
- [x] No secret values in responses

### Runtime Integration ✓

- [x] Feature flag (TRUSTFORGE_AGOS_ENABLED) defaults to OFF
- [x] Graceful degradation: try/except in all hook points, logs warning on failure
- [x] `analysis_flow.py` hooks are fail-soft (do not affect pipeline outcome)
- [x] Frozen references used throughout run (frozen manifest persisted in DB)
- [x] `tool_audited_fetch` does not alter return value of wrapped function

### Route Integration ✓

- [x] Admin API registered in `web.py` under existing `/api/admin/` auth gate
- [x] Frontend route registered in `App.tsx` at `/admin/agos`
- [x] Neither route added to public navigation

### No New Dependencies ✓

- [x] No third-party packages added
- [x] Only stdlib sqlite3 used (boto3 already present, not newly added)
- [x] No outbound network calls in Agent OS layer

## Test Evidence

| Test File | Tests | Status |
|-----------|-------|--------|
| `tests/test_agos_db_auth.py` | 9 | PASS |
| `tests/test_memory_os.py` | 27 | PASS |
| `tests/test_skill_registry.py` | 35 | PASS |
| `tests/test_tool_registry.py` | 33 | PASS |
| `tests/test_memory_retrieval.py` | 18 | PASS |
| `tests/test_skill_loader.py` | 26 | PASS |
| `tests/test_context_builder.py` | 19 | PASS |
| `tests/test_agos_runtime.py` | 21 | PASS |
| `tests/test_agos_admin_api.py` | 22 | PASS |
| `tests/test_agos_e2e.py` | 17 | PASS |
| `frontend/.../AgosBadge.test.tsx` + `AdminAgosPage.test.tsx` | 15 | PASS |
| **Total** | **242** | **ALL PASS** |

## Disposition

- [ ] APPROVED for merge (requires harper + gray sign-off)
- [ ] APPROVED with conditions: ___________
- [ ] REJECTED: ___________

## Conditions

1. Production deployment and activation are NOT authorized by this disposition
2. DB schema changes require Eric's same-day `TRUSTFORGE_AGOS_DB_AUTH_TOKEN` before first production `upgrade()` runs
3. High-risk tools (external_write/deploy_or_release) cannot auto-execute; human approval required
4. Feature flag (`TRUSTFORGE_AGOS_ENABLED`) must remain `0` in production until explicit activation approval

## Remaining Actions (Not in Scope)

- Security reviewer (harper) sign-off
- Product reviewer (gray) sign-off
- Production deployment approval (separate release gate)
- DB authorization token issuance by Eric (same-day, per-purpose)
