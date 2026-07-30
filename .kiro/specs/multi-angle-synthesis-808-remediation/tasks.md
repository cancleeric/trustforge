# #808 實作與驗收任務

- [x] 1. 定義分離的 direction divergence、completeness gap、evidence overlap、independence 資料模型與相容序列化。
- [x] 2. 將 mode/question/snapshot provenance 納入 `AngleResult` 還原與完整度計算。
- [x] 3. 重構 `synthesize_angles()`，以獨立階段計算三種比較結果與零獨立性限制。
- [x] 4. 調整 deterministic summary/narrative facts，禁止 0% independence 的獨立佐證說法。
- [x] 5. 增加單元與 regression 測試，覆蓋 evidence overlap 不等於 direction divergence、abstain 與缺欄完整度。
- [x] 6. 建立可參數化 production snapshot verifier；呼叫端必須提供部署後新產生的 snapshot ID，並以唯讀 SQLite 投影驗證五個真實 payload。
- [x] 7. 依 CEO 2026-07-30 收斂範圍為併入 `develop`；新 production snapshot acceptance 移出本次 closeout，不宣稱已部署。
- [x] 8. 完整 repository-local gate 已在 develop-based head 通過，修正由 PR #1140 併入 `develop`。

## 本機驗證紀錄

- 相關 multi-angle、flow、cost-ledger 與 verifier 聚焦測試曾通過；frontend 專屬測試、lint、build、資料契約與 question bank 亦已執行。
- 完整 `.githooks/pre-push` 已通過：backend parallel 7,260、serial 14、frontend 667，另含 contracts、NF2、competition QA、lint、build 與 diff check。
