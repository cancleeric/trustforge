# 實作任務：Agent OS Replay、E2E 與 Release-Hardening Gate

> Issue: #925 | Epic: #914

## Task 1: 實作 Replay Verification

- [ ] 建立 `tests/test_agos_replay.py`
- [ ] 實作 `verify_replay(manifest, memory_repo, skill_registry) -> ReplayResult`
  - 可放在 `src/trustforge/agos_replay.py` 或直接在 test 中
- [x] 測試：正常 manifest replay → hashes match
- [ ] 測試：tampered content → hash mismatch detected
- [x] 測試：skill revision replay → hash reproducible
- [ ] 測試：memory content hash replay → reproducible

## Task 2: 實作 Security Guard E2E Tests

- [ ] 建立 `tests/test_agos_e2e_guards.py`
- [x] 測試：historical memory (hermes-* + semantic) cannot set evidence_eligible
- [ ] 測試：historical memory cannot enter scoring pipeline
- [x] 測試：unknown skill cannot be selected into manifest
- [x] 測試：stale skill (no active revision) → excluded
- [x] 測試：retired skill → excluded
- [x] 測試：unknown tool → is_known=False → cannot execute
- [x] 測試：external_write tool without approval → rejected
- [x] 測試：deploy_or_release capability → requires approval

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

- [x] 建立 `docs/audit/AGOS-SECURITY-DISPOSITION.md`
- [x] Security review checklist template
- [x] Disposition fields（reviewer, date, commit, status）
- [x] Notes section

## Task 7: Final Verification

- [ ] 執行所有新增 E2E tests
- [ ] 執行完整 pytest suite 無回歸
- [ ] 執行前端 vitest 無回歸
- [ ] 執行前端 build 成功
- [ ] 執行 lint / type-check 通過
- [ ] 執行完整 pre-push gate 通過
- [x] 確認 production deployment/activation 未被觸發

### Closeout 補充（2026-07-29）

- [x] 真 `web.Handler`／TCP／外層 `X-Admin-Token` 未授權契約：401
- [ ] 真 `web.Handler` authenticated success 契約：目前 strict XFAIL；AGOS
  route 傳 `bytes` 給預期 `str` 的 `_send()`，production backend 修正不在本
  docs/test-only closeout 權限內
- [ ] 人工 desktop/mobile Eye scan
- [ ] harper CISO disposition
- [ ] gray CPO disposition
- [ ] DB authorization receipt
