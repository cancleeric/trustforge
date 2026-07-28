# Multi-angle 前端五角度總覽與 Drilldown

> Issue: #810
> 依賴: #809 (後端 API endpoint，需先完成)

## 需求

新增前端元件呈現五角度綜合分析結果：總覽表格、衝突標示、角度 drilldown、觸發按鈕。

## 功能需求

### FR-1: MultiAngleOverview.tsx
- 五角度摘要表格：角度 / 結論(direction) / 信心(calibrated_confidence) / 狀態(decision_state) / 分歧標記
- desktop 用 table layout，mobile 用 card layout
- snapshot_id 顯示在標題區
- consensus 區塊：共識結論 + 加權信心 + evidence_independence 百分比

### FR-2: ConflictBadge.tsx
- 橙色 pill 標示角度間衝突
- hover 顯示衝突 summary
- 無衝突時不顯示

### FR-3: Angle Drilldown
- 點擊角度行展開為既有 AnalysisReportView
- 複用現有報告渲染元件

### FR-4: multiAngleEndpoints.ts
- `fetchMultiAngleReport(coin, snapshot_id?)`: GET /api/multi-angle
- `submitMultiAngle(coin, question?, locale?)`: POST /api/multi-angle
- TypeScript interface: MultiAngleReport / AngleResult / AngleConflict

### FR-5: 觸發按鈕
- 「執行五角度綜合分析」按鈕
- 明確標示消耗 5× 預算
- 送出後 poll job_ids 狀態（複用現有 job polling）
- 全部完成後自動載入 MultiAngleOverview

### FR-6: i18n
- 中/英文本支援（hermesI18n.tsx）

## 非功能需求

- Responsive：desktop table / mobile card
- Accessible：proper aria labels, keyboard nav
- 不引入新第三方依賴

## 約束

- 複用現有 AnalysisReportView 做 drilldown
- 複用現有 Hermes 設計語言（顏色、字體、間距）
- abstain 角度用灰色標示
