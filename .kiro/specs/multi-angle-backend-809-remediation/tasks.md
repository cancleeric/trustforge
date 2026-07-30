# #809 實作與驗收任務

- [ ] 1. 定義每一角度的 mode-specific question templates 與不可變 job context。
- [ ] 2. 將 mode/question 從 enqueue 傳入 Claim Extraction input、stage event、後續 pipeline context 和 result payload。
- [ ] 3. 在 Claim Extraction 建立 provenance gate；context 遺失或不符時 fail safely。
- [ ] 4. 強化 synthesis trigger，驗證五個真實 job 的 snapshot、provenance 與完整 stage sequence。
- [ ] 5. 更新 API contract/status 與 lineage，暴露 per-angle context 和 pending reason。
- [ ] 6. 增加缺路、混 snapshot、mode/question mismatch 的 regression tests。
- [ ] 7. 實作並執行 `snap-btc-eca5b069d33ea8ac` 真實五路 acceptance harness，保存五個 Claim Extraction 的稽核輸出。
- [ ] 8. 執行相關測試與 repository-local gate；僅在 task 7 通過後，才能提報 #809 可關閉。
