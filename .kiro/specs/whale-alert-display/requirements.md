# Spec：大額轉帳資料顯示（Whale Alert Display）

## 概述

讓 Whale Alert 取回的鯨魚大額轉帳資料在前端三個層級正確顯示：
1. Dashboard 右軌即時信號卡（新增）
2. 分析報告證據清單的品牌名稱與權威性徽章（修正）
3. 大額轉帳歷程面板（新增，支援 1天/7天/30天）

**分析報告**：`docs/plans/PLAN-whale-alert-display.md`

---

## 需求

### R1：來源品牌名稱與權威性徽章（P0）

- `sourceBrand.ts` 新增 `whale-alert` → `'Whale Alert · 鯨魚大額轉帳'`
- `sourceBrand.ts` 新增 `arkham-intel` → `'Arkham · 標記錢包交易'`
- `THIRD_PARTY_KINDS` 新增 `whale_onchain` 和 `celebrity_trade`
- EvidenceTable 中顯示正確品牌名稱和「高·第三方」權威性徽章

### R2：後端鯨魚摘要 API（P1）

- 新增 `GET /api/whale-summary?coin=BTC`
- 回傳最近 1 小時的聚合統計：
  - `total_count`：大額轉帳總筆數
  - `total_usd`：總金額（USD）
  - `net_exchange_flow_usd`：交易所淨流入（正=流入/賣壓，負=流出/囤積）
  - `max_single_usd`：最大單筆金額
  - `recent_transfers`：最近 5 筆轉帳摘要（金額/來源/目的/方向）
  - `updated_at`：快取最後更新時間
- 資料來源：從 cache 讀 `whale-alert:<coin>` 最新資料做即時聚合
- 無資料時回傳空結構（`total_count: 0`），不報錯

### R3：Dashboard 右軌鯨魚即時卡（P1）

- 位置：`HermesRightRail`，信任分數圓弧下方
- 顯示內容：
  - 標題：「🐋 {COIN} 鯨魚動態（最近1h）」
  - 交易所淨流出/入金額 + 方向指標（↗囤積 / ↘賣壓）
  - 大額轉帳筆數
  - 最大單筆金額
  - 最近 3 筆摘要（金額 + from → to + 方向標記）
- 每 30 秒自動 poll 更新
- 無資料時顯示「暫無大額轉帳紀錄」灰字
- 響應式：<900px 時收折或移入 mobile 佈局

### R4：後端鯨魚歷程 API（P2）

- 新增 `GET /api/whale-history?coin=BTC&days=7`
- 支援 `days` 參數：1 / 7 / 30
- 回傳：
  - `summary`：期間內統計（總筆數/總金額/淨交易所流/最大單）
  - `timeline`：按小時（days≤1）或按天（days>1）聚合的筆數+金額
  - `transfers`：明細列表（最多 100 筆，降序）
- 資料來源：`SourceEventArchive`（Bronze truth JSONL）
- 超出累積天數時回傳目前可用的資料 + `available_since` 欄位

### R5：大額轉帳歷程面板（P2）

- 路由：Dashboard workspace module 或獨立頁面 `/whale-history`
- UI 組成：
  - 時間範圍切換：[1天] [7天] [30天]
  - 統計摘要卡
  - 趨勢柱狀圖（每小時或每天的筆數/金額）
  - 明細表（時間/金額/來源/目的/方向）
- 支援切換幣種（預設跟隨 Dashboard 當前選中幣）

---

## 非範圍

| 排除項 | 原因 |
|--------|------|
| Arkham 資料顯示 | 目前無 API key，等取得後再做 |
| WebSocket 即時推送 | 免費層不支援 |
| SOL/BNB 鯨魚資料 | 免費層不覆蓋 |
| 穩定幣 mint/burn 獨立面板 | Phase 3 |

---

## 成功指標

- [ ] EvidenceTable 中 whale-alert 證據顯示「Whale Alert · 鯨魚大額轉帳」+ 藍色「高·第三方」徽章
- [ ] `/api/whale-summary?coin=BTC` 回傳正確聚合資料
- [ ] Dashboard 右軌顯示即時鯨魚信號卡（有資料時）
- [ ] `/api/whale-history?coin=BTC&days=7` 回傳歷程資料
- [ ] 歷程面板正確顯示統計 + 明細
- [ ] 所有現有測試不破壞
