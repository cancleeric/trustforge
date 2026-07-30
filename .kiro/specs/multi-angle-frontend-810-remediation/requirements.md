# #810 五角度總覽與 Drilldown 修正

> Issue: #810 (OPEN，退回修正)
> 依賴：#808、#809 remediation contracts
> 實證：production snapshot `snap-btc-eca5b069d33ea8ac`
> 完成門檻：使用真實 API payload 的 UI 驗收通過前不得關閉。

## 目標

前端總覽必須忠實顯示五個真實 pipeline 結果及其 provenance，並把方向分歧、完整度差距、證據重疊分開呈現。不得將 evidence overlap 中的十組來源或十個 shared source 顯示為「10 個分歧」。

## 功能需求

### FR-1：真實五角度總覽

`MultiAngleOverview` 顯示每個 angle 的 mode、實際 question、direction、confidence、decision state、completeness、snapshot ID 與可 drilldown 的 provenance。桌面為可存取表格、行動版為 cards；缺資料、pending 和 abstain 以不同狀態呈現，不偽裝成成功共識。

### FR-2：三類獨立訊號

使用三個明確區塊/標籤：

- 方向分歧：只顯示相反方向的角度配對數和詳細配對。
- 完整度差距：顯示角度資料完整度、缺欄/缺 evidence 與比較差距。
- 證據重疊：顯示每個 angle pair 的共享/聯集來源與 overlap ratio。

前端不可從 evidence overlap 數量推導或覆寫 direction divergence 數。舊 `conflicts` 相容資料若存在，必須按 `kind` 顯示，不得採用未分類計數。

### FR-3：Drilldown

點擊任一 angle 時，以既有報告檢視元件開啟該真實 report，並展示 job 的 mode/question、Claim Extraction provenance、source/evidence。不得用合成 placeholder 取代 angle report。

### FR-4：真實 payload UI 驗收

前端 contract 與 component/integration tests 必須載入後端擷取的 `snap-btc-eca5b069d33ea8ac` API payload（或其有 digest 的不可變 export），而非人工撰寫的 `MultiAngleReport`。須驗證五列、三個訊號分區、0% independence 限制文字與 drilldown target。

### FR-5：0% independence 誠實文案

當 `evidence_independence` 為 0%，總覽必須清楚寫出無獨立交叉佐證；不可顯示「獨立驗證」、「cross-validated」或等價正面宣稱。

## 驗收條件

1. production payload 視覺/元件驗收顯示五個真實 mode/question 與同一 `snap-btc-eca5b069d33ea8ac`。
2. 使用含十組 source overlap 的 payload，方向分歧 UI 只顯示後端 direction-divergences 結果，絕不顯示為 10 分歧。
3. completeness gap 與 evidence overlap 在獨立區塊中呈現，且有可讀的數值/限制說明。
4. 0% independence 的真實或受控 regression payload 不含任何獨立交叉佐證宣稱。
5. 實機 eye scan 完成 desktop/mobile、overflow、pending/error、drilldown 和真實資料可讀性；未完成前 #810 維持 OPEN。
