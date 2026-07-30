# #811 實作與驗收任務

- [ ] 1. 定義 immutable `NarrationFacts`，將 mode/question provenance 與三種獨立比較結果導入。
- [ ] 2. 重寫 prompt 與 fallback template，禁止以 evidence overlap 充當 direction divergence。
- [ ] 3. 實作 0% independence 的硬性限制文字與輸出檢查。
- [ ] 4. 維持/驗證呼叫只經 `bedrock.py`、budget gate 與 execution log，且不影響 deterministic synthesis。
- [ ] 5. 增加 failure/offline、十組 overlap、0% independence 與 provenance 的 regression tests。
- [ ] 6. 載入 `snap-btc-eca5b069d33ea8ac` 真實 synthesis payload，驗證 facts 與 fallback；可用時再驗證 Bedrock output，保存 evidence。
- [ ] 7. 執行相關測試與 repository-local gate；僅在 task 6 production acceptance 通過後，才能提報 #811 可關閉。
