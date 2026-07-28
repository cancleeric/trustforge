# Comparison Analysis Development Plan

> 版本: 1.0.0 | 日期: 2026-07-28 | 決策: Eric Wang
> 目標: 將 TrustForge 比較分析從「兩份單幣報告並排」升級為「結構化比較報告」

## 背景

目前 `pipeline.run_comparison()` 只做兩次獨立 pipeline (`run()`) 後回傳五元組
`(report_a, evidence_a, report_b, evidence_b, log)`。`comparison_to_markdown()`
也只是將兩份 `Report.to_markdown()` 並排（加上簡單的相對強弱表格），缺少：

1. **共同結論（Common Conclusion）** — 無 A vs B 的綜合判斷
2. **四個比較面向（Comparison Dimensions）** — 沒有結構化的價格、鏈上、情緒、生態比較
3. **雙邊證據對照（Bilateral Evidence Refs）** — 證據無法追溯到特定維度的 A/B 貢獻
4. **信心上限（Confidence Ceiling）** — 比較信心未受規則層約束
5. **推翻條件（Overturn Conditions）** — 無法解釋什麼情況下結論會反轉

## 架構設計

```
┌─────────────────────────────────────────────────────┐
│              ComparisonRunResult                      │
│  ┌─────────────┐  ┌──────────────────────────────┐   │
│  │ report_a     │  │ ComparisonReport             │   │
│  │ report_b     │  │  ├─ conclusion: str          │   │
│  │ evidence_a   │  │  ├─ dimensions[4]:           │   │
│  │ evidence_b   │  │  │   DimensionResult         │   │
│  └─────────────┘  │  │   ├─ label                 │   │
│                   │  │   ├─ a_evidence_refs[]      │   │
│  ┌─────────────┐  │  │   ├─ b_evidence_refs[]      │   │
│  │ log          │  │  │   ├─ finding               │   │
│  └─────────────┘  │  │   └─ confidence             │   │
│                   │  ├─ confidence: float          │   │
│                   │  ├─ limits: list[str]          │   │
│                   │  └─ could_flip: list[str]      │   │
│                   └──────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

## 實作階段

### CA-01 官方比較契約與 golden failing tests
- 固定官方比較範例 fixture（BTC vs ETH）
- 定義 four-dimension contract
- A/B swap metamorphic tests
- 所有測試不可依賴 live network

### CA-02 ComparisonReport schema 與具名執行結果
- `ComparisonDimension` / `DimensionResult` dataclass
- `ComparisonReport` dataclass（conclusion, dimensions, confidence, limits, could_flip）
- `ComparisonRunResult` dataclass（wrapping A/B reports + comparison report）
- 序列化 / 反序列化合約

### CA-03 比較資料正規化、證據對照與 deterministic fallback
- A/B 可比性檢查（時間對齊、freshness、unit、market、metric definition）
- 四維度證據歸類（價格→動能、鏈上→活動、新聞/社交→情緒、生態→發展）
- 無 LLM fallback: 純規則比較（price trend / onchain count / sentiment direction）
- 不可比或資料不足時 abstain

### CA-04 Bedrock comparative synthesis 與輸出驗證
- 固定 JSON schema 的 Bedrock prompt
- 未知 evidence ref / 未引用數字 / overclaim 拒絕
- confidence 不超過規則層 ceiling
- timeout / invalid JSON → retry → deterministic fallback
- 記錄 latency、cost、execution events

### CA-05 run_comparison 單一比較任務整合與相容層
- 整合 CA-02/03/04 為 single `run_comparison` 呼叫
- 回傳 `ComparisonRunResult`
- A/B supporting analysis 保留
- Bedrock failure 降級至 deterministic fallback
- 現有單幣 pipeline tests 全綠

### CA-06 比較 API、CLI、Lambda 與 OpenAPI contract 遷移
- `/analyze` / `/api/analyze` / CLI / Lambda 統一回傳 comparison contract
- OpenAPI 與實際 payload 一致
- 舊欄位向後相容

### CA-07 Frontend 單一比較報告主視圖
- `ComparePage` 改為顯示 comparison_report（非兩份並排）
- 四面向可展開、evidence 可追到 A/B 來源
- loading / partial / abstain / error states

### CA-08 比較 Markdown、HTML 匯出與 Evidence List 一致性
- Markdown / HTML 匯出主體為 comparison report
- 四個 dimensions + 共同結論完整
- evidence 使用穩定 ID

### CA-09 持續分析 DB-free comparison snapshot synthesis
- 讀取層產生 comparison report（無 DB schema 異動）
- A/B snapshot 缺失時回 pending
- revision / freshness / window 不相容時拒絕

### CA-10 官方比較題 E2E、十分鐘 deadline 與 release gate
- 全鏈路驗收（BTC vs ETH golden + 另一組 pair）
- Bedrock success / timeout / offline fallback 全驗
- backend / frontend / lint / build / contract / pre-push 全綠

### CA-11 durable comparison workflow 技術設計
- 僅產出 ADR / schema proposal / migration proposal
- 標記 blocked-external（Eric DB auth required）
- 不建立 migration、不執行 SQL

## 四個比較面向定義

| Dimension | Label | A Evidence | B Evidence | 規則層判斷依據 |
|-----------|-------|-----------|-----------|---------------|
| 價格動能 | 價格動能比較 | price docs | price docs | 方向、漲幅%、波動率 |
| 鏈上活動 | 鏈上活動比較 | onchain docs | onchain docs | 大額流向、TVL 變化 |
| 市場情緒 | 市場情緒比較 | news/social docs | news/social docs | 情緒方向、來源數 |
| 生態發展 | 生態發展比較 | regulatory docs | regulatory docs | 法規/採用/生態指標 |

## 核心設計原則

1. **誠實優於好看**: 資料不足時 abstain，不強行比較
2. **證據必須可追溯**: 每個維度的 A/B 貢獻必須對應到具體 evidence ref
3. **規則層信心上限**: 即使 Bedrock 給高信心，規則層可因維度覆蓋不足而下修
4. **離線 fallback 不可略過**: 無 Bedrock / Bedrock timeout 時，必須產出規則層比較
5. **DB-free**: Phase 1 所有比較均在讀取層完成，不建立新 table / column / index
