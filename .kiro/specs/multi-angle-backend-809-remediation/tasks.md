# #809 實作與驗收任務

- [x] 1. 定義每一角度的 mode-specific question templates 與不可變 job context。
- [x] 2. 將 mode/question 從 enqueue 傳入 Claim Extraction input、stage event、後續 pipeline context 和 result payload。
- [x] 3. 在 Claim Extraction 建立 provenance gate；context 遺失或不符時 fail safely。
- [x] 4. 強化 synthesis trigger，驗證五個真實 job 的 snapshot、provenance 與完整 stage sequence。
- [x] 5. 更新 API contract/status 與 lineage，暴露 per-angle context 和 pending reason。
- [x] 6. 增加缺路、混 snapshot、mode/question mismatch 的 regression tests。
- [x] 7. 實作可參數化 production acceptance verifier：要求部署後新 snapshot ID、唯讀連線、實際 prompt-context receipt、execution receipt 與 separated synthesis dimensions；補成功與 fail-closed schema/payload 測試。
- [x] 8. 依 CEO 2026-07-30 收斂範圍為併入 `develop`；production run／snapshot acceptance 明確移出本次 closeout，不宣稱已上線。
- [x] 9. 完整 repository-local gate 已在 develop-based head 通過，修正由 PR #1140 併入 `develop`。

## 本機驗證紀錄

- `model_invoked` 僅在 Bedrock provider 成功回傳後為 true；regex fallback 與 offline 不得偽稱 prompt 已被模型接受。
- verifier 使用 SQLite URI `mode=ro` 並在 finally 關閉連線；驗證 model invocation、hash、execution receipt 與 0% independence limit 均 fail closed。
- Develop gate：backend parallel 7,260 passed、serial 14 passed；frontend 667 passed；contracts、NF2、competition QA、lint、build、diff check 全部通過。
