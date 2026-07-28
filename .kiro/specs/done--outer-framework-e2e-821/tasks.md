# Outer Framework E2E 驗證任務

## Tasks

- [x] 1. 本機跑完 diagnose() 產出 proposals
- [x] 2. record_reviews() 推進到 llm_reviewed
- [x] 3. SandboxAttestationAuthority.issue() + record_sandbox() 推進到 sandbox_passed
- [x] 4. decide() 推進到 approved（含 AuthenticatedPrincipal 認證）
- [x] 5. activate() 推進到 activated（含 HermesActivationHandler + artifact resolve）
- [x] 6. 確認安全護欄正常（認證 / attestation / hash 校驗）
- [x] 7. 記錄驗證結果到 spec + issue
