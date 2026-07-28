# Multi-angle Synthesis LLM 敘事

> Issue: #811
> 依賴: #808 + #809
> 優先級: P2（選填加值，非必要）

## 需求

為 MultiAngleReport 加上 LLM 敘事：把確定性演算法算出的衝突清單與共識改寫成一段人類可讀的摘要文字。

## 功能需求

### FR-1: narrate_synthesis(report, client, log) -> str
- 接收已產出的 MultiAngleReport
- 組裝 prompt：只含結構化資料（consensus, conflicts, agreement_matrix）
- prompt 硬約束：「只能用下列結構化資料敘事，不可自行發明交叉訊號」
- 呼叫 Bedrock 1 次（短 prompt）
- 回傳敘事文字

### FR-2: 失敗降級
- Bedrock 呼叫失敗 → 回傳 `report.synthesis_summary`（程式組裝的文字）
- 離線模式 → 跳過 LLM，直接回傳 synthesis_summary
- 不影響 MultiAngleReport 結構化欄位

### FR-3: 成本控管
- 走 `_bedrock_live_attempt` 或同等機制
- 受 budget_guard 控管
- 記帳到 execution log

### FR-4: 整合點
- `_maybe_trigger_synthesis` 完成後可選擇呼叫
- 環境變數 `TRUSTFORGE_MULTI_ANGLE_NARRATION=1` 開啟（預設關）
- 結果寫入 MultiAngleReport payload 的 `narration` 欄位（選填）

## 非功能需求

- LLM 不決策：移除 LLM 後 MultiAngleReport 結構化欄位不受影響
- 反作弊合規：prompt 明確約束不可自行發明訊號

## 約束

- 不修改 multi_angle.py 的 synthesize_angles() 邏輯
- LLM 呼叫受既有 budget_guard 控管
- 只用 AWS Bedrock（不走其他 API）
