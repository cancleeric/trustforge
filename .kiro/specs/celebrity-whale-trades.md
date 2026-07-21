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

## 二、設計（Design）

### 架構決策

```
                   ┌──────────────────┐
                   │  whale_trades.py │
                   └────────┬─────────┘
                            │
             ┌──────────────┼──────────────┐
             ▼              ▼              ▼
    WhaleAlertSource  ArkhamSource   OfflineSample
    (whale_onchain)   (celebrity_trade)  (兩種 kind)
             │              │
             ▼              ▼
        safe_fetch      safe_fetch
             │              │
             ▼              ▼
    whale-alert.io    arkham API
```

### 資料模型

新增兩個 Document kind：

| Kind | Source Name | 信號語義 | 信譽 |
|------|-------------|----------|------|
| `whale_onchain` | `whale-alert` | 鏈上大額轉帳事實 | 0.88 |
| `celebrity_trade` | `arkham-intel` | 標記錢包/名人交易 | 0.50 |

### Document 產出格式

```python
# whale_onchain 範例
Document(
    id="whale-alert-<hash>",
    kind="whale_onchain",
    source="whale-alert",
    text="BTC 鯨魚轉出交易所：1,200 BTC（約 7,200 萬 USD）從 Binance 轉至未知錢包",
    url="https://whale-alert.io/transaction/...",
    ts=1721345678.0,
    meta={"coin": "BTC", "amount_usd": 72000000, "direction": "exchange_outflow",
          "from": "Binance", "to": "unknown_wallet", "content_reference": "..."}
)

# celebrity_trade 範例
Document(
    id="arkham-<hash>",
    kind="celebrity_trade",
    source="arkham-intel",
    text="已標記錢包（疑似 Michael Saylor）買入 500 BTC（約 3,000 萬 USD），鏈上已驗證",
    url="https://platform.arkhamintelligence.com/...",
    ts=1721345678.0,
    meta={"coin": "BTC", "amount_usd": 30000000, "verified_onchain": True,
          "entity": "Michael Saylor", "action": "buy", "content_reference": "..."}
)
```

### 信任評分整合

1. **KIND_REPUTATION 新增：**
   - `whale_onchain`: 0.88
   - `celebrity_trade`: 0.50

2. **動態降級邏輯：**
   - `meta["verified_onchain"] == False` 的 celebrity_trade → 信譽降至 0.35（等同 social）
   - 在 `_source_reputation()` 中加入此判斷

3. **RecencyDecay 加速：**
   - 為 whale_onchain / celebrity_trade 設定更短的半衰期（2 小時 vs 預設 24 小時）
   - 透過新增 `KIND_HALFLIFE_HOURS` 映射實現

4. **佐證加分：**
   - whale_onchain 作為獨立來源計入 CrossSourceCorroboration
   - celebrity_trade（已驗證）同理；未驗證的不計入獨立佐證

### API 整合規格

**Whale Alert API：**
- 端點：`GET https://api.whale-alert.io/v1/transactions`
- 參數：`api_key`, `min_value=1000000`, `start=<epoch>`, `cursor`
- 環境變數：`WHALE_ALERT_API_KEY`
- Rate limit：免費層 10 req/min

**Arkham Intelligence：**
- 端點：`GET https://api.arkhamintelligence.com/transfers`
- 參數：`apiKey`, `base=<coin>`, `usdGte=1000000`
- 環境變數：`ARKHAM_API_KEY`
- Rate limit：待確認

### 安全考量

- 所有 URL 硬編碼，不接受外部輸入
- API key 僅從環境變數讀取，不進入 Document.url/meta/log
- 透過 safe_fetch.py 的 SSRF-safe 機制發送請求
- 金額/時間戳欄位使用 `_require_number` 模式嚴格驗證

---

## 三、實作任務（Tasks）

### Task 1：建立 connector 骨架
- 檔案：`src/trustforge/ingestion/whale_trades.py`
- 實作 `WhaleAlertSource(Source)` — kind=`whale_onchain`
- 實作 `ArkhamIntelSource(Source)` — kind=`celebrity_trade`
- 實作 `build_whale_sources() -> list[Source]` 工廠函式
- 安全措施：safe_fetch、timeout、size limit、URL 白名單

### Task 2：離線樣本資料
- 檔案：`demo/sample_data/whale_trades.json`
- 提供 8-10 筆涵蓋不同情境的樣本：
  - 交易所流出（看漲訊號）
  - 交易所流入（賣壓訊號）
  - 已驗證名人買入/賣出
  - 未驗證名人宣告（測試降級邏輯）

### Task 3：整合到 collect() 流程
- 在 `base.py` 的 `collect()` 中加入延遲匯入 + 呼叫
- 在 `SOURCE_KINDS` 中考慮是否新增（或保持獨立於文件型來源）

### Task 4：信譽與評分整合
- `scoring.py` 的 `KIND_REPUTATION` 新增兩個 kind
- 實作動態降級（未驗證 → 降為 social 信譽）
- 新增 `KIND_HALFLIFE_HOURS` 或在 `_recency_decay` 中特化處理

### Task 5：快取層支援
- 確認 `cache.py` 的 `CachedSource` 能正確包裝新 connector
- 在 `fetch_scheduler.py` 中加入排程設定

### Task 6：驗證與測試
- 確認 offline collect() 能正確載入 whale_trades.json
- 確認 scoring 能正確處理新 kind 的信譽/衰減
- 確認 import chain 正常運作

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
