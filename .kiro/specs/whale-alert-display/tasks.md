# Tasks：大額轉帳資料顯示

## Task 1：來源品牌名稱與 kind tier（P0, 10min）

- [x] `frontend/src/lib/sourceBrand.ts`：新增 `'whale-alert'` 和 `'arkham-intel'` 顯示名稱
- [x] `frontend/src/lib/sourceBrand.ts`：`THIRD_PARTY_KINDS` 新增 `'whale_onchain'` 和 `'celebrity_trade'`
- [x] 確認 EvidenceTable 中 whale 證據顯示正確名稱和徽章

**檔案**：`frontend/src/lib/sourceBrand.ts`
**需求**：R1

---

## Task 2：後端 whale_api.py — 摘要聚合邏輯（P1, 45min）

- [x] 新建 `src/trustforge/whale_api.py`
- [x] 實作 `whale_summary(coin, backend)` 函式：
  - 從 cache 讀 `whale-alert:<coin>` 的 Document 列表
  - 聚合：total_count, total_usd, net_exchange_flow, max_single, direction 統計
  - 取最近 5 筆作為 recent_transfers
  - 推導 signal（net_outflow/net_inflow/neutral）
- [x] 實作 `whale_history(coin, days, archive_dir)` 函式：
  - 從 SourceEventArchive 讀歷史 whale-alert Document
  - 按時間桶（天或小時）聚合 timeline
  - 回傳 summary + timeline + transfers（限 100 筆）
- [x] 無資料/無 cache 時返回空結構不報錯

**檔案**：`src/trustforge/whale_api.py`（新建）
**需求**：R2, R4

---

## Task 3：後端路由註冊（P1, 15min）

- [x] `server.py` 新增 `GET /api/whale-summary` 路由
- [x] `server.py` 新增 `GET /api/whale-history` 路由
- [x] 參數解析：coin（預設 BTC）、days（預設 7，限制 1/7/30）
- [x] 回傳格式 `{"ok": true, "data": {...}}`

**檔案**：`src/trustforge/server.py`
**需求**：R2, R4

---

## Task 4：前端 WhaleActivityPanel 元件（P1, 1hr）

- [x] 新建 `frontend/src/components/WhaleActivityPanel.tsx`
- [x] Props：`{ summary: WhaleSummary | null }`
- [x] 顯示：標題、淨流入出、筆數、最大單、最近 3 筆
- [x] 無資料 placeholder
- [x] 顏色語義：淨流出=cyan、淨流入=amber
- [x] 響應式樣式

**檔案**：`frontend/src/components/WhaleActivityPanel.tsx`（新建）
**需求**：R3

---

## Task 5：右軌整合 WhaleActivityPanel（P1, 30min）

- [x] `HermesRightRail.tsx` 引入 WhaleActivityPanel
- [x] 新增 props `whaleSummary` 傳入
- [x] `HermesDashboard.tsx` 新增 whale-summary polling（30 秒）
- [x] 將 poll 結果透過 props 傳給 HermesRightRail
- [x] `frontend/src/lib/endpoints.ts` 新增 `getWhaleSummary()` 函式

**檔案**：`HermesRightRail.tsx`, `HermesDashboard.tsx`, `endpoints.ts`
**需求**：R3

---

## Task 6：前端 WhaleHistoryPanel 元件（P2, 2hr）

- [x] 新建 `frontend/src/components/WhaleHistoryPanel.tsx`
- [x] 時間範圍切換 tabs（1天/7天/30天）
- [x] 統計摘要卡（4 個數字）
- [x] 趨勢柱狀圖（純 CSS/SVG，不引入圖表庫）
- [x] 明細表（時間/金額/來源/目的/方向）
- [x] `frontend/src/lib/endpoints.ts` 新增 `getWhaleHistory()` 函式

**檔案**：`frontend/src/components/WhaleHistoryPanel.tsx`（新建）, `endpoints.ts`
**需求**：R5

---

## Task 7：歷程頁面路由整合（P2, 30min）

- [x] 決定路由方式：workspace module `whale` 或獨立路由
- [x] 整合到 navigation（TopBar 或 StageBar 入口）
- [x] 支援 coin 切換（跟隨 Dashboard 選中幣）

**檔案**：`HermesDashboard.tsx` 或 `App.tsx`
**需求**：R5

---

## Task 8：測試與驗證（P1+P2, 30min）

- [x] 後端：whale_api.py 單元測試（mock cache data）
- [x] 前端：WhaleActivityPanel 渲染測試
- [x] 整合：啟動 dev server 驗證 API → UI 資料流通
- [x] 回歸：既有測試全通

**需求**：全部

---

## 依賴關係

```
Task 1 → 無依賴，可立即做
Task 2 → 無依賴
Task 3 → Task 2（聚合邏輯先完成）
Task 4 → 無依賴（純 UI 元件）
Task 5 → Task 3 + Task 4（API + 元件都好了才能接）
Task 6 → Task 3（需要 history API）
Task 7 → Task 6
Task 8 → 全部完成後
```

---

## 預估工時

| Phase | Tasks | 估計 |
|-------|-------|------|
| P0 | T1 | 10 min |
| P1 | T2 + T3 + T4 + T5 | ~2.5 hr |
| P2 | T6 + T7 | ~2.5 hr |
| 驗證 | T8 | 30 min |
| **合計** | | **~5.5 hr** |
