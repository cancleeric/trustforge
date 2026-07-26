# Spec：名人/鯨魚交易信心參考（Celebrity & Whale Trade Signals）

## 概述

為 TrustForge 新增「名人交易」信號來源，追蹤鏈上大額轉帳（鯨魚）與公開知名交易者動向，
作為信心參考的佐證型信號整合進現有信任評分引擎。

---

## 一、需求（Requirements）

### R1：鏈上鯨魚追蹤（Whale Alert）
- 接入 Whale Alert API（https://api.whale-alert.io/v1/transactions）追蹤大額鏈上轉帳
- 信號類型：交易所流入（賣壓）、流出（囤積）、鯨魚間轉帳
- 最低金額門檻：100 萬 USD 等值以上
- 支援 5 幣白名單：BTC、ETH、SOL、BNB、XRP

### R2：名人/KOL 公開交易宣告
- 追蹤已驗證的知名交易者公開宣告的交易行為
- 資料來源：Arkham Intelligence 標記錢包、LookOnChain 推文
- 必須與鏈上數據交叉驗證（未驗證的自動降級）

### R3：信譽分層
- 鏈上可驗證（kind=`whale_onchain`）：信譽 0.88（客觀事實，但非一手交易所數據）
- 名人公開宣告（kind=`celebrity_trade`）：信譽 0.50（意見型，需佐證）
- 未經鏈上驗證的名人宣告：自動降級至 social 等級 0.35

### R4：防偽機制
- 利益衝突偵測：交叉比對名人宣告時間 vs 鏈上建倉時間
- 聯合喊單偵測：複用既有 `_coordination_template_flags` 機制
- 時效衰減加速：名人交易信號半衰期設為 2 小時（一般新聞 24 小時）

### R5：離線/線上雙模式
- 離線模式使用 `demo/sample_data/whale_trades.json` 樣本
- 線上模式透過 CachedSource 讀快取（排程器定期更新）

### R6：安全措施
- SSRF-safe fetch（safe_fetch.py）
- API key 從環境變數讀取，不 hardcode
- URL 白名單寫死，不接受外部傳入
- timeout 5 秒 / 回應上限 512 KB

---

## 四、風險與限制

| 風險 | 影響 | 緩解 |
|------|------|------|
| Whale Alert 免費層 rate limit | 可能無法即時追蹤 | 快取層 + 排程（5 分鐘間隔） |
| 名人標記錢包誤判 | 錯誤歸因 | 預設信譽只有 0.50，需佐證才升 |
| Pump & dump 利用名人效應 | 被操縱信號污染 | ManipulationPenalty + 時序交叉驗證 |
| API 變更/下線 | 來源失效 | collect() try/except 容錯，降級不崩 |

---

## 五、成功指標

- [x] 離線模式可正確產出 whale_onchain 和 celebrity_trade Document
- [x] 新 kind 在 scoring 中獲得正確的基礎信譽分
- [x] 已驗證的鯨魚信號能作為獨立佐證來源提升 corroboration 分項
- [x] 未驗證的名人宣告被正確降級至 social 等級
- [x] 所有安全措施（SSRF、key 隱藏、URL 白名單）就位
