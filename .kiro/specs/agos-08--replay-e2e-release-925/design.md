# 設計：Agent OS Replay、E2E 與 Release-Hardening Gate

> Issue: #925 | Epic: #914

## 架構決策

### AD-1: Test Module Structure

```
tests/
├── test_agos_replay.py         # Replay verification tests
├── test_agos_e2e_guards.py     # Security invariant E2E tests
├── test_agos_e2e_regression.py # Non-regression E2E tests
├── test_agos_lineage_consistency.py  # Runtime ↔ Admin consistency
└── test_agos_release_gate.py   # Release readiness checks
```

### AD-2: Replay Verification

```python
@dataclass
class ReplayResult:
    passed: bool
    manifest_hash_match: bool
    skill_hash_matches: list[tuple[str, bool]]  # (skill_id, match)
    memory_hash_matches: list[tuple[str, bool]]  # (memory_id, match)
    mismatches: list[str]  # human-readable mismatch descriptions

def verify_replay(manifest: ContextManifest, memory_repo, skill_registry) -> ReplayResult:
    """Re-derive all hashes from stored content and compare."""
    mismatches = []

    # Verify manifest content_hash
    recomputed = _compute_hash(manifest.run_id, manifest.included_refs, ...)
    if recomputed != manifest.content_hash:
        mismatches.append(f"manifest hash: expected {manifest.content_hash}, got {recomputed}")

    # Verify each skill revision hash
    for sref in manifest.included_refs.skill_refs:
        rev = skill_registry.get_revision(sref["revision_hash"])
        if rev:
            computed = revision_hash_for(rev.content)
            if computed != rev.revision_hash:
                mismatches.append(f"skill {sref['skill_id']}: hash mismatch")

    # Verify each memory content hash
    for mref in manifest.included_refs.memory_refs:
        entry = memory_repo.get(mref["memory_id"])
        if entry:
            # Re-hash from stored content_ref
            ...

    return ReplayResult(passed=len(mismatches) == 0, ...)
```

### AD-3: Guard Test Pattern

Each guard test follows:
1. Setup: create the prohibited state
2. Action: attempt the prohibited operation
3. Assert: operation is blocked/rejected

```python
class TestHistoricalMemoryGuard:
    def test_cannot_set_evidence_eligible(self):
        entry = MemoryEntry(kind="semantic", provider="hermes-analysis", ...)
        with pytest.raises(ValueError, match="dialogue memory cannot be evidence|historical"):
            validate_evidence_eligible(entry)

    def test_cannot_enter_scoring_pipeline(self):
        # Even if somehow evidence_eligible=True, scoring rejects it
        ...
```

### AD-4: Lineage Consistency Test

```python
def test_runtime_matches_admin_api():
    # 1. Run analysis with AGOS_ENABLED=1
    run_id = run_analysis_fixture()

    # 2. Query lineage from runtime (direct DB)
    runtime_manifest = agos_runtime.get_run_context(run_id)

    # 3. Query same data via Admin API
    response = client.get(f"/api/admin/agos/context?run_id={run_id}", headers=admin_headers)
    api_manifest = response.json()["data"]

    # 4. Assert consistency
    assert runtime_manifest.content_hash == api_manifest["content_hash"]
    assert len(runtime_manifest.included_refs.memory_refs) == api_manifest["included_count"] - ...
```

### AD-5: Release Gate Script

```python
# tests/test_agos_release_gate.py

def test_release_readiness():
    """Meta-test that verifies all release criteria are met."""
    checks = {
        "backend_tests": _run_pytest(),
        "frontend_tests": _run_vitest(),
        "frontend_build": _run_frontend_build(),
        "lint_clean": _run_lint(),
        "replay_pass": _run_replay_tests(),
        "guard_pass": _run_guard_tests(),
        "regression_pass": _run_regression_tests(),
        "lineage_consistency": _run_consistency_tests(),
    }
    for name, passed in checks.items():
        assert passed, f"Release gate failed: {name}"
```

### AD-6: Security Disposition Template

```markdown
## Security Reviewer Disposition

- Reviewer: ___________
- Date: ___________
- Commit: ___________

### Checklist
- [ ] Trust Kernel boundary: no prohibited imports added
- [ ] Evidence binding: claim_id → source → Document chain intact
- [ ] Approval governance: high-risk actions require human approval
- [ ] Admin API: no secret leakage, authorization enforced
- [ ] Memory guard: historical conclusions blocked from scoring
- [ ] Tool guard: unknown tools fail closed
- [ ] No new third-party dependencies introduced
- [ ] DB migrations reversible

### Disposition
- [ ] APPROVED for merge
- [ ] APPROVED with conditions: ___________
- [ ] REJECTED: ___________

### Notes
___________
```

## 測試策略

All tests in this issue ARE the deliverable. They verify the correctness
of #916–#924 as an integrated system.

Total test files: 5 (as listed in AD-1)
Estimated test count: 30-40 test functions
