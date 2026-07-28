# 規劃：大額轉帳資料顯示位置

> 日期：2026-07-28
> 狀態：規劃中

---

## 現狀

大額轉帳（Whale Alert）資料目前：
- ✅ 已成功接入 API（BTC 有資料）
- ✅ 已加入排程器（5 分鐘自動更新）
- ✅ 作為 `whale_onchain` kind 進入信任評分引擎
- ⚠️ 前端**沒有獨立的鯨魚面板**——只混在證據清單（EvidenceTable）裡

**用戶看到的現況：** 分析報告 → 展開證據清單 → 其中幾條 source=`whale-alert` 的證據行

---

## 規劃：三層顯示

### 第一層：Dashboard 右軌 — 即時鯨魚信號卡（新增）

**位置：** `HermesRightRail`，在信任分數圓弧下方、跨來源分歧面板上方

**內容：**
```
┌─────────────────────────────┐
│ 🐋 BTC 鯨魚動態（最近 1h）   │
│                             │
│  交易所淨流出  +$12.3M ↗ 囤積 │
│  大額轉帳      47 筆          │
│  最大單筆      $5.7M          │
│                             │
│  最近 3 筆：                  │
│  • $5.7M unknown→unknown    │
│  • $3.7M unknown→unknown    │
│  • $3.4M Binance→unknown ↗  │
└─────────────────────────────┘
```

**資料來源：** 從 cache 讀 `whale-alert:BTC` 的最新資料，前端聚合統計

**價值：** 一眼看到鯨魚在做什麼，不用點進分析報告

---

### 第二層：分析報告 — 證據清單中的鯨魚證據（已有）

**位置：** `AnalysisReportView` → `EvidenceTable`

**現況：** 已經會顯示，但需要改進：
- 加入 `whale-alert` 和 `arkham-intel` 的顯示名稱到 `sourceBrand.ts`
- 加入 `whale_onchain` 和 `celebrity_trade` 到 `THIRD_PARTY_KINDS`（權威性徽章）

---

### 第三層：歷史頁面 — 大額轉帳歷程（新增）

**位置：** `HistoryPage` 或新增獨立的 `/whale-history` 頁面

**內容：**

```
┌───────────────────────────────────────────┐
│ BTC 鯨魚大額轉帳歷程                        │
│                                           │
│ 時間範圍：[1天] [7天] [30天]                 │
│                                           │
│ ┌─ 統計摘要 ─────────────────────────────┐ │
│ │ 總筆數：328 │ 總流量：$2.1B            │ │
│ │ 交易所淨流入：-$180M（淨流出=囤積訊號） │ │
│ │ 最大單筆：$26.2M USDT                  │ │
│ └─────────────────────────────────────────┘ │
│                                           │
│ ┌─ 趨勢圖（柱狀圖）──────────────────────┐ │
│ │  每小時/每天大額轉帳筆數 + 金額          │ │
│ │  ████ ██████ ████████ ███ █████████     │ │
│ └─────────────────────────────────────────┘ │
│                                           │
│ ┌─ 明細表 ───────────────────────────────┐ │
│ │ 時間       金額      從     到    方向   │ │
│ │ 14:32  $5.7M BTC  unk.  unk.  轉帳   │ │
│ │ 14:28  $3.7M BTC  unk.  unk.  轉帳   │ │
│ │ 14:15  $3.4M BTC  Bin.  unk.  流出↗  │ │
│ │ ...                                    │ │
│ └─────────────────────────────────────────┘ │
└───────────────────────────────────────────┘
```

**關鍵問題：歷史資料從哪來？**

| 方案 | 做法 | 優缺點 |
|------|------|--------|
| A. 排程器快取累積 | 每 5 分鐘寫入 cache，自然累積歷史 | ✅ 免費；❌ 只有排程器開始跑之後的資料 |
| B. 排程器 + Archive | 已有 `SourceEventArchive`（Bronze truth），每筆 fetch 都 append | ✅ 已有機制；✅ 時間序列完整 |
| C. Whale Alert 付費層 | 30 天歷史 API | ❌ $699/月；免費層只有即時 |

**建議用方案 B（Archive）：** 排程器已有 `SourceEventArchive` 機制，每次 fetch 的所有 Document 都會 append 到 JSONL archive。從今天開始累積，1 天後就有 1 天歷史，7 天就有 7 天。

---

## 前端需新增/修改的檔案

| 檔案 | 變更 |
|------|------|
| `sourceBrand.ts` | 加 `whale-alert` / `arkham-intel` 顯示名 + kind tier |
| `HermesRightRail.tsx` | 新增鯨魚信號摘要卡（讀 `/api/whale-summary`） |
| 新增 `WhaleActivityPanel.tsx` | 右軌的鯨魚即時卡元件 |
| 新增 `WhaleHistoryPage.tsx`（或擴充 HistoryPage） | 大額轉帳歷程頁 |
| `endpoints.ts` | 新增 `getWhaleSummary()` / `getWhaleHistory()` API 呼叫 |

## 後端需新增的 API

| 端點 | 回傳 | 來源 |
|------|------|------|
| `GET /api/whale-summary?coin=BTC` | 最近 1h 統計（筆數/淨流入出/最大單/最近 N 筆） | 從 cache 讀 + 即時聚合 |
| `GET /api/whale-history?coin=BTC&days=7` | 時序明細（從 archive JSONL 讀） | SourceEventArchive |

---

## 優先順序

| Phase | 內容 | 工時 |
|-------|------|------|
| **P0（現在）** | `sourceBrand.ts` 加顯示名 + kind tier | 10 min |
| **P1（本週）** | 後端 `/api/whale-summary` + 右軌即時卡 | 2-3 hr |
| **P2（本週）** | 後端 `/api/whale-history` + 歷程頁 | 3-4 hr |
| **P3（選用）** | 趨勢圖（每小時柱狀圖） | 2 hr |

---

## 即時可做的最小改動（P0）

讓現有 EvidenceTable 正確顯示 whale-alert 的品牌名稱和權威性徽章：

```typescript
// sourceBrand.ts 新增
'whale-alert': 'Whale Alert · 鯨魚大額轉帳',
'arkham-intel': 'Arkham · 標記錢包交易',
```

```typescript
// kind tier 新增
const THIRD_PARTY_KINDS = new Set(['price_live', 'onchain', 'whale_onchain', 'celebrity_trade'])
```
