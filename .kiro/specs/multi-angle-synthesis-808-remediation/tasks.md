# #808 實作與驗收任務

- [ ] 1. 定義分離的 direction divergence、completeness gap、evidence overlap、independence 資料模型與相容序列化。
- [ ] 2. 將 mode/question/snapshot provenance 納入 `AngleResult` 還原與完整度計算。
- [ ] 3. 重構 `synthesize_angles()`，以獨立階段計算三種比較結果與零獨立性限制。
- [ ] 4. 調整 deterministic summary/narrative facts，禁止 0% independence 的獨立佐證說法。
- [ ] 5. 增加單元與 regression 測試，覆蓋十組 overlap 不等於十個 direction divergence、abstain 與缺欄完整度。
- [ ] 6. 建立 production snapshot manifest/讀取機制，鎖定 `snap-btc-eca5b069d33ea8ac` 的五個真實 payload。
- [ ] 7. 執行真實 payload synthesis 驗收並保存輸出證據；不得使用手寫 AngleResult 或 synthetic payload 取代。
- [ ] 8. 執行相關測試與 repository-local gate；僅在 task 7 的真實驗收通過後，才能提報 #808 可關閉。
