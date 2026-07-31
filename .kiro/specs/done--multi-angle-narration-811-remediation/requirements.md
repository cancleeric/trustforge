# #811 整合分析 LLM 敘事修正

> Issue: #811 (OPEN，退回修正)
> 依賴：#808、#809 remediation contracts
> 實證：production snapshot `snap-btc-eca5b069d33ea8ac`
> 完成門檻：真實 synthesis payload 與 Bedrock/fallback 驗收通過前不得關閉。

## 目標

敘事只能以 deterministic synthesis 的結構化事實行文，特別是 mode/question provenance、方向分歧、完整度差距、證據重疊及 evidence independence。敘事不得自行把十組來源重疊描述成十個方向分歧，也不得在獨立性為 0% 時宣稱獨立交叉佐證。

## 功能需求

### FR-1：受限事實輸入

`narrate_synthesis()` 僅接收由 #808 建構的 typed narration facts，不直接解析任意原始 evidence 或請求模型做分析。facts 必含各角度的 mode/question/provenance、三個分離結果、independence ratio 與 required limits。

### FR-2：真實性規則

Prompt 和 deterministic fallback 都必須：將方向分歧、完整度差距、證據重疊分段敘述；只報各自 facts 的數值；在 `independent_cross_validation=false` 或 ratio=0 時明確說明無獨立交叉佐證，並禁止相反敘述。

### FR-3：Bedrock-only 與 fail-soft

所有模型呼叫維持經 `src/trustforge/bedrock.py` 與既有 budget/logging guard。環境 flag 預設關閉；離線、live gate 拒絕、budget 拒絕或 Bedrock 失敗時，輸出同樣符合真實性規則的 deterministic fallback。LLM 不影響 structure、market judgment 或 synthesis decision。

### FR-4：真實 payload 驗收

以 `snap-btc-eca5b069d33ea8ac` 的 real `MultiAngleReport` 驗證 prompt facts 與 fallback；若 live narration 可執行，另驗證 Bedrock output。不得以手寫 report 取代真實 pipeline acceptance。所有輸出必須可回鏈到同一 snapshot。

## 驗收條件

1. 真實 report 的敘事 input 明確保留五個 mode/question，並分別提及/可引用方向、完整度與重疊 facts。
2. 十組 evidence overlap 的 regression/real payload 中，敘事不宣稱「10 個方向分歧」。
3. ratio=0 時，fallback 與任一 Bedrock output 都不含獨立交叉佐證的正面宣稱，且含限制聲明。
4. 驗證所有 LLM 呼叫只經 bedrock wrapper 並受預算/執行紀錄控制。
5. 實際 production payload 驗收與輸出證據保存；未完成前 #811 維持 OPEN。
