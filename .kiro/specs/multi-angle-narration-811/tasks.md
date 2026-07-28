# Multi-angle Narration 實作任務

## Tasks

- [ ] 1. multi_angle.py: 新增 `narrate_synthesis(report, client, log)` 函式
- [ ] 2. 設計 prompt template（硬約束不可自行發明訊號）
- [ ] 3. 整合 _bedrock_live_attempt 或同等成本控管機制
- [ ] 4. 失敗降級邏輯：Bedrock fail → fallback to synthesis_summary
- [ ] 5. 整合到 _maybe_trigger_synthesis：env flag 控制（預設關）
- [ ] 6. 測試：離線降級正確、LLM mock 成功路徑、結構化欄位不受影響
- [ ] 7. 確認測試通過、commit
