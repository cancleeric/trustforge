# Multi-angle Narration 實作任務

## Tasks

- [x] 1. multi_angle.py: 新增 `narrate_synthesis(report, client, log)` 函式
- [x] 2. 設計 prompt template（硬約束不可自行發明訊號）
- [x] 3. 整合離線判斷 + env flag 控制（TRUSTFORGE_MULTI_ANGLE_NARRATION=1）
- [x] 4. 失敗降級邏輯：Bedrock fail → fallback to synthesis_summary
- [x] 5. 整合到 _maybe_trigger_synthesis：narration 呼叫 + payload 寫入
- [x] 6. 測試：離線降級、LLM mock 成功、失敗降級、結構化欄位不受影響（5 tests）
- [x] 7. 確認測試通過、commit
