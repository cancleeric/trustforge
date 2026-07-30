# #808 Multi-angle Synthesis 資料契約與演算法修正

> Issue: #808 (OPEN，退回修正)
> 取代範圍：`.kiro/specs/done--multi-angle-synthesis-808/`
> 依賴實證：production snapshot `snap-btc-eca5b069d33ea8ac`
> 完成門檻：真實 production payload 驗收通過前，不得標示完成或關閉工單。

## 目標

修正五角度綜合資料契約與確定性演算法，讓 `risk`、`sentiment`、`fundamentals`、`news`、`catalyst` 的真實 pipeline 結果可以被忠實呈現。方向分歧、完整度差距與證據重疊是三個不同的量測，不得共用、混淆或以來源對數量冒充分歧數。

## 功能需求

### FR-1：可追溯的 AngleResult

每一個 `AngleResult` 必須保留 `angle`、`mode`、`question`、`snapshot_id`、`direction`、`calibrated_confidence`、`decision_state`、`key_basis_count`、`evidence_refs`，以及足以 drilldown 的原始 report/evidence 引用。`mode` 和 `question` 來自 Claim Extraction 前已固定的 job context，不能在 synthesis 階段推測或以預設值覆寫。

### FR-2：分離的比較結果

`MultiAngleReport` 必須分開提供下列欄位，而非把它們都編碼為 `conflicts`：

- `direction_divergences`：僅列出 active 角度間方向相反的角度配對；每一配對至多一筆。
- `completeness_gaps`：列出每個角度的資料完整度及與其他角度可比較的差距；完整度以實際 report/evidence 可用性計算，不得等同 calibrated confidence。
- `evidence_overlaps`：列出 evidence/source 集合的交集、聯集與重疊率；每一角度配對至多一筆。
- `evidence_independence`：以所有 active 角度 evidence/source 的集合關係計算，並可追溯其分子、分母與使用的 source IDs。

可保留相容性的 `conflicts` 欄位，但其內容必須是上述資料的明確相容投影，不能把十組來源重疊報為「10 個方向分歧」。

### FR-3：確定性共識與 abstain 保護

共識、agreement matrix、synthesis summary 與 limits 必須完全由程式以輸入 payload 決定。方向分歧只依方向判定；完整度只依資料可用性判定；證據重疊只依規範化 evidence/source IDs 判定。`abstain` 不可被誤判為中性或同意。任何角度 abstain 時應降低結論狀態；全數 abstain 時為 `full_abstain`。

### FR-4：零獨立性誠實宣告

當 `evidence_independence` 為 `0.0`，結構化 summary、limits 與供敘事使用的事實資料必須明確標記「沒有獨立交叉佐證」。不得輸出或留下可使下游宣稱獨立 cross-validation 的欄位值、模板或標記。

### FR-5：真實 payload 相容

`angle_result_from_payload()` 必須可還原 production snapshot `snap-btc-eca5b069d33ea8ac` 中五個真實角度的 payload；對既有欄位缺漏採安全降級，並把缺失反映在 completeness，而非捏造值。

## 非功能需求與限制

- 僅 stdlib 與現有 TrustForge 模組，零新增 runtime dependency。
- `synthesize_angles()` 對五個結果為純記憶體、確定性、無 LLM 呼叫，目標小於 10ms。
- 不變更市場判斷來源；LLM 不得參與方向、完整度、重疊、獨立性或共識計算。
- 所有數值與配對可被從 payload 人工重算。

## 驗收條件

1. 單元與 property/regression 測試證明三種比較結果互相獨立：十筆或十組 evidence overlap 絕不導致 `direction_divergences == 10`；方向分歧只按相反方向的角度配對計數。
2. 使用 `snap-btc-eca5b069d33ea8ac` 的真實五路 production payload，產生含全部五個 angle 的 `MultiAngleReport`；不得以手寫 AngleResult、mock payload 或人工測試資料替代。
3. 該真實報告的每個 angle 都保留實際 mode/question/snapshot_id，並可追溯至 Claim Extraction job context。
4. 真實報告分開輸出方向分歧、完整度差距與證據重疊，且所有計數可由 payload 的角度配對與 source ID 集合核對。
5. 以獨立性為 0% 的真實或受控 regression payload 驗證：summary/limits/narration facts 均不宣稱「獨立交叉佐證」。
6. 將 production payload 驗收命令、不可變 snapshot 證據位置與實際輸出記錄於測試或驗收文件；在此之前 #808 維持 OPEN。
