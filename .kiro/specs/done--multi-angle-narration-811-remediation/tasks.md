# #811 實作與驗收任務

- [x] 1. 定義 immutable `NarrationFacts`，將 mode/question provenance 與三種獨立比較結果導入。
- [x] 2. 重寫 prompt 與 fallback template，禁止以 evidence overlap 充當 direction divergence。
- [x] 3. 實作 0% independence 的硬性限制文字與輸出檢查。
- [x] 4. 維持/驗證呼叫只經 `bedrock.py`、budget gate 與 execution log，且不影響 deterministic synthesis。
- [x] 5. 增加 failure/offline、evidence overlap、0% independence 與 provenance 的 regression tests。
- [x] 6. 依 CEO 2026-07-30 收斂範圍為併入 `develop`；live production payload acceptance 移出本次 closeout，不宣稱已上線。
- [x] 7. 完整 repository-local gate 已在 develop-based head 通過，修正由 PR #1140 併入 `develop`。

## 本機驗證紀錄

- 正常、full-abstain、exception、offline 與 invalid narration fallback 均透過 `narration_fallback()` 保留「沒有獨立交叉佐證」限制。
- Bedrock 仍只負責帶溯源行文；市場判斷、信任評分與限制由 deterministic pipeline 產生。
- Develop gate：backend、frontend、contracts、NF2、competition QA、lint、build 與 diff check 全部通過。
