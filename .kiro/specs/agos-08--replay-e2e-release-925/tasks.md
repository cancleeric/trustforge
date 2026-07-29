# 實作任務：Agent OS Replay、E2E 與 Release-Hardening Gate

> Issue: #925 | Epic: #914

## Task 1: 實作 Replay Verification

- [ ] 建立 `tests/test_agos_replay.py`
- [ ] 實作 `verify_replay(manifest, memory_repo, skill_registry) -> ReplayResult`
  - 可放在 `src/trustforge/agos_replay.py` 或直接在 test 中
- [ ] 測試：正常 manifest replay → hashes match
- [ ] 測試：tampered content → hash mismatch detected
- [ ] 測試：skill revision replay → hash reproducible
- [ ] 測試：memory content hash replay → reproducible

## Task 2: 實作 Security Guard E2E Tests

- [ ] 建立 `tests/test_agos_e2e_guards.py`
- [ ] 測試：historical memory (hermes-* + semantic) cannot set evidence_eligible
- [ ] 測試：historical memory cannot enter scoring pipeline
- [ ] 測試：unknown skill cannot be selected into manifest
- [ ] 測試：stale skill (no active revision) → excluded
- [ ] 測試：retired skill → excluded
- [ ] 測試：unknown tool → is_known=False → cannot execute
- [ ] 測試：external_write tool without approval → rejected
- [ ] 測試：deploy_or_release capability → requires approval

## Task 3: 實作 Non-Regression E2E Tests

- [ ] 建立 `tests/test_agos_e2e_regression.py`
- [ ] 測試：analysis pipeline same Trust scores with AGOS=0 vs AGOS=1
- [ ] 測試：Question RAG same results
- [ ] 測試：Dialogue workflow unchanged
- [ ] 測試：Report generation unchanged（structure + fields）
- [ ] 測試：Evidence.json output unchanged

## Task 4: 實作 Lineage Consistency Tests

- [ ] 建立 `tests/test_agos_lineage_consistency.py`
- [ ] 測試：runtime context manifest == Admin API context
- [ ] 測試：runtime memory refs == Admin API memories
- [ ] 測試：runtime tool invocations == Admin API tools
- [ ] 測試：runtime skill manifest == Admin API skills

## Task 5: 實作 Release Gate

- [ ] 建立 `tests/test_agos_release_gate.py`
- [ ] Meta-test：backend tests pass
- [ ] Meta-test：frontend tests pass
- [ ] Meta-test：frontend build success
- [ ] Meta-test：lint clean
- [ ] Meta-test：replay pass
- [ ] Meta-test：guard pass
- [ ] Meta-test：regression pass
- [ ] Meta-test：lineage consistency pass

## Task 6: Security Disposition Document

- [ ] 建立 `docs/audit/AGOS-SECURITY-DISPOSITION.md`
- [ ] Security review checklist template
- [ ] Disposition fields（reviewer, date, commit, status）
- [ ] Notes section

## Task 7: Final Verification

- [ ] 執行所有新增 E2E tests
- [ ] 執行完整 pytest suite 無回歸
- [ ] 執行前端 vitest 無回歸
- [ ] 執行前端 build 成功
- [ ] 執行 lint / type-check 通過
- [ ] 執行完整 pre-push gate 通過
- [ ] 確認 production deployment/activation 未被觸發
