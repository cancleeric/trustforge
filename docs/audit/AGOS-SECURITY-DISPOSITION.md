# Agent OS Security Disposition

> Epic: [#914](https://github.com/cancleeric/trustforge/issues/914)
> Issue: [#925](https://github.com/cancleeric/trustforge/issues/925)
> Date: 2026-07-29
> Status: PENDING REVIEW

## Reviewer Information

- Reviewer: ___________
- Date: ___________
- Commit: ___________

## Security Checklist

### Trust Kernel Immutability

- [ ] No Agent OS module imports `trustforge.trust.kernel` or `trustforge.trust.scoring`
- [ ] Trust weights (`DEFAULT_WEIGHTS`) unchanged
- [ ] Scoring formula unchanged
- [ ] PIT time boundary unchanged
- [ ] Evidence binding (claim_id → source → Document) unchanged
- [ ] Dawid-Skene EM unchanged

### Evidence Integrity

- [ ] `evidence_eligible` defaults to `false` (fail-closed)
- [ ] Historical conclusions (hermes-* provider + semantic kind) blocked from Evidence
- [ ] Dialogue memory cannot become Evidence
- [ ] `context_only` tool output cannot enter scoring pipeline
- [ ] Evidence eligibility validation requires: provider + published_at + retrieved_at + content_hash

### Approval Governance

- [ ] High-risk skills (external_write / deploy_or_release) require activation proposal + approval
- [ ] High-risk skills cannot skip `staged` lifecycle
- [ ] External-write tools require `approval_requirement=always` (invariant enforced at registration)
- [ ] Unknown tools fail closed (`is_known=False` → cannot execute)
- [ ] Deploy_or_release tools always require human approval

### Memory OS Security

- [ ] Content hash is SHA-256 and content-addressed (immutable)
- [ ] Duplicate (provider, content_hash) rejected
- [ ] Self-links rejected
- [ ] Append-only design (no DELETE operations exposed)

### Skill Registry Security

- [ ] Revision content immutable after write
- [ ] Hash collision (same hash, different content) detected and rejected
- [ ] Self-cycle dependency rejected
- [ ] Transitive cycle dependency detected (DFS, depth ≤ 10)
- [ ] Existing outer-policy families unchanged

### Tool Registry Security

- [ ] Invocation records append-only (no DELETE)
- [ ] Input/output hashes recorded for audit trail
- [ ] Unknown tool registration fails without explicit registration
- [ ] Approval invariant enforced: external_write → approval=always

### Context Manifest Security

- [ ] One manifest per run (UNIQUE on run_id)
- [ ] Content hash deterministic and reproducible
- [ ] Manifest immutable after creation (cannot UPDATE)
- [ ] Excluded references tracked with reasons
- [ ] Evidence-ineligible memory excluded from scoring inputs

### Admin API Security

- [ ] All endpoints authorization-gated (Bearer token)
- [ ] No token configured → no access (fail-closed)
- [ ] Sensitive content redacted by default
- [ ] Read-only: no mutation endpoints
- [ ] No secret leakage in responses

### Runtime Integration

- [ ] Feature flag (TRUSTFORGE_AGOS_ENABLED) defaults to OFF
- [ ] Graceful degradation: Agent OS failure does not crash analysis run
- [ ] Trust scoring inputs unchanged with AGOS enabled vs disabled
- [ ] Frozen references used throughout run (no mid-run mutation)

### No New Dependencies

- [ ] No third-party packages added to requirements
- [ ] Only stdlib sqlite3 + existing boto3 used
- [ ] No network calls in Agent OS layer (pure local persistence)

## E2E Test Evidence

| Test File | Tests | Status |
|-----------|-------|--------|
| `tests/test_agos_e2e.py` (replay + guards + regression + consistency) | ___ | PENDING |
| `tests/test_memory_os.py` | 27 | PASS |
| `tests/test_skill_registry.py` | 35 | PASS |
| `tests/test_tool_registry.py` | 33 | PASS |
| `tests/test_memory_retrieval.py` | 18 | PASS |
| `tests/test_skill_loader.py` | 26 | PASS |
| `tests/test_context_builder.py` | 19 | PASS |
| `tests/test_agos_runtime.py` | 19 | PASS |
| `tests/test_agos_admin_api.py` | 22 | PASS |
| `frontend/.../AgosBadge.test.tsx` + `AdminAgosPage.test.tsx` | 15 | PASS |

## Disposition

- [ ] APPROVED for merge
- [ ] APPROVED with conditions: ___________
- [ ] REJECTED: ___________

## Conditions / Notes

- Production deployment and activation are NOT authorized by this disposition
- Any production/activation action requires a separate approval gate
- DB schema changes require Eric's same-day authorization token (per Epic #914 safety boundary)

___________
