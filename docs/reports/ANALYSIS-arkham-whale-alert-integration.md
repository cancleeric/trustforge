# 分析報告：Arkham + Whale Alert 外部資料整合

> 分析日期：2026-07-28
> 分析者：Kiro（AI-assisted）
> 狀態：待實作

---

## 1. 背景

TrustForge 現有 `whale_trades.py` 已實作兩個連接器骨架：
- `WhaleAlertSource`（whale_onchain）
- `ArkhamIntelSource`（celebrity_trade）

但兩者的 API 規格**基於早期推測**，與真實 API 有顯著差異。現已取得：
- Whale Alert API key：`WHALE_ALERT_API_KEY` 環境變數（已有 key）
- Arkham Intel API key：`ARKHAM_API_KEY` 環境變數（已有 key）

本報告對比現有程式碼與真實 API 文件的差異，明確需修正項目。

---

## 2. Arkham Intelligence API — 差異分析

### 2.1 現有程式碼（錯誤）

```python
# whale_trades.py 第 86-87 行（舊 spec）
url = "https://api.arkhamintelligence.com/transfers?" + urlencode(params)
params = {"apiKey": api_key, "usdGte": ..., "base": coin.lower()}
```

| 項目 | 現有程式碼 | 真實 API（v1.1.0） |
|------|-----------|-------------------|
| Base URL | `https://api.arkhamintelligence.com` | `https://api.arkm.com` |
| 認證方式 | query param `apiKey` | HTTP header `API-Key: <key>` |
| 端點路徑 | `/transfers` | `/transfers`（正確） |
| 幣種過濾 | `base=<coin>` | `chains=<chain_name>` 或 `tokens=<symbol>` |
| 時間過濾 | 無 | `timeLast=1h` 或 `timeGte`/`timeLte`（Unix ms） |
| 金額過濾 | `usdGte` | `usdGte`（正確） |
| 分頁 | 無 | `limit`（預設 20）、`offset` |
| Rate limit | 未知 | 20 req/s（標準）；`/transfers` 是 heavy endpoint 1 req/s |
| 計費 | 未知 | 2 credits/row（per-row billing） |

### 2.2 回應結構差異

**現有程式碼期望的結構：**
```json
{
  "transfers": [{
    "token": {"symbol": "BTC"},
    "unitValueUsd": 5000000,
    "blockTimestamp": 1721345678,
    "fromAddress": {"arkhamLabel": "Binance", "address": "0x..."},
    "toAddress": {"arkhamLabel": "Unknown", "address": "0x..."},
    "transactionHash": "0x..."
  }]
}
```

**真實 API 回應結構（v1.1.0 /transfers）：**
```json
{
  "transfers": [{
    "fromAddress": {
      "address": "0x...",
      "arkhamEntity": {"name": "Binance", "type": "exchange"},
      "arkhamLabel": {"name": "Binance: Hot Wallet", "address": "0x..."}
    },
    "toAddress": {
      "address": "0x...",
      "arkhamEntity": null,
      "arkhamLabel": null
    },
    "tokenSymbol": "USDT",
    "historicalUSD": 5000000.0,
    "unitValue": 5000000.0,
    "chain": "ethereum",
    "transactionHash": "0x...",
    "blockTimestamp": "2026-07-28T11:01:35Z"
  }]
}
```

### 2.3 關鍵差異摘要

| 欄位 | 現有假設 | 真實結構 |
|------|---------|---------|
| 幣種符號 | `transfer.token.symbol` | `transfer.tokenSymbol`（直接字串） |
| USD 金額 | `transfer.unitValueUsd` | `transfer.historicalUSD`（歷史 USD 價值） |
| 時間戳格式 | epoch 數值 | ISO 8601 字串（`"2026-07-28T11:01:35Z"`） |
| 來源實體 | `fromAddress.arkhamLabel`（字串） | `fromAddress.arkhamEntity.name`（巢狀物件） |
| 目的實體 | `toAddress.arkhamLabel`（字串） | `toAddress.arkhamEntity.name`（巢狀物件） |
| arkhamLabel | 字串 | 物件 `{"name": "...", "address": "..."}` |

### 2.4 可用的額外端點

除 `/transfers` 外，以下端點對 TrustForge 有價值：

| 端點 | 用途 | Credits | 備註 |
|------|------|---------|------|
| `GET /intelligence/entity/{entity}` | 查實體資訊 | 1/call | 輕量，可輔助標記 |
| `GET /token/market/{id}` | 即時代幣市場數據 | 1/call | 可替代 CoinGecko |
| `GET /token/holders/{id}` | 大戶持倉 | 30/call | 高 cost，慎用 |
| `GET /risk/address/{address}` | 地址風險評分 | 5/call | 操縱偵測輔助 |
| `GET /marketdata/altcoin_index` | Altcoin 指數 | 1/call | 市場溫度 |

---

## 3. Whale Alert API — 差異分析

### 3.1 現有程式碼（大致正確）

```python
url = "https://api.whale-alert.io/v1/transactions?" + urlencode(params)
params = {"api_key": api_key, "min_value": 1000000, "start": epoch}
```

| 項目 | 現有程式碼 | 真實 API |
|------|-----------|---------|
| Base URL | `https://api.whale-alert.io/v1/transactions` | 正確 ✅ |
| 認證方式 | query param `api_key` | 正確 ✅ |
| 金額過濾 | `min_value` | 正確 ✅ |
| 時間範圍 | `start` (epoch) | 正確 ✅ |
| 幣種過濾 | `currency` | 正確 ✅ |

### 3.2 API 層級與限制

根據 Whale Alert 文件：

| 方案 | 價格 | 主要限制 |
|------|------|---------|
| Alerts API | $29.95/月 | WebSocket；100 alerts/hr；個人用途 |
| Enterprise API | $699/月 | REST + WebSocket；1000 CPM；30 天歷史 |

**免費層（含你的 key）限制：**
- 10 API calls/min（1 call 每 6 秒）
- 僅 > 500K USD 交易
- 無歷史資料

### 3.3 需修正項目

Whale Alert 端的程式碼**相對正確**，但需處理：
1. **Rate limit 尊重**：目前 5 分鐘排程器間隔足夠（10 req/min 限制下）
2. **`min_value` 門檻調整**：免費層只回傳 > 500K，我們設 1M 門檻在安全區
3. **回應驗證**：需確認 `result` 欄位為 `"success"` 才處理
4. **環境變數已設定**：確認 key 能正常認證

---

## 4. safe_fetch.py 相容性分析

| 需求 | 支援狀態 |
|------|---------|
| API-Key header（Arkham） | ✅ `extra_headers` 已支援 |
| query param 認證（Whale Alert） | ✅ URL 拼接即可 |
| HTTPS-only | ✅ 兩個 API 都是 HTTPS |
| 同域轉址 | ✅ `api.arkm.com` 不變 |
| timeout 5s | ✅ 預設值 |
| 512KB 上限 | ✅ 預設值 |

**結論：`safe_fetch.py` 完全相容，無需修改。**

---

## 5. 影響範圍

### 5.1 需修改的檔案

| 檔案 | 變更內容 |
|------|---------|
| `src/trustforge/ingestion/whale_trades.py` | Arkham fetch 重寫 + parse 修正 |
| `demo/sample_data/whale_trades.json` | 更新樣本符合真實 schema |
| 測試檔案 | mock 回應需對齊真實 schema |

### 5.2 不需修改的檔案

| 檔案 | 原因 |
|------|------|
| `safe_fetch.py` | 完全相容 |
| `base.py` | Document 結構不變 |
| `trust/scoring.py` | kind/信譽映射不變 |
| `cache.py` | CachedSource 介面不變 |
| 排程器 | 間隔與容錯邏輯不變 |

---

## 6. 風險評估

| 風險 | 嚴重度 | 機率 | 緩解 |
|------|--------|------|------|
| Arkham API 需付費計畫才能用 | 高 | 中 | 先用 key 測試；若 401/403 則降級跳過 |
| Whale Alert 免費層資料量太少 | 中 | 高 | 已知限制；能取到即加分，取不到有離線樣本 |
| Rate limit 被觸發（Arkham 1 req/s） | 低 | 低 | 排程器 5 分鐘間隔，遠低於限制 |
| API 回應 schema 微調 | 低 | 低 | 防禦式解析 + 缺欄位容錯 |
| API key 洩漏 | 高 | 低 | env var only + 不進 Document/log |

---

## 7. 實作建議

### 優先順序

1. **Phase 1（本次）**：修正 `ArkhamIntelSource.fetch()` 和 `_parse_transfer()` 對齊真實 API
2. **Phase 1（本次）**：環境變數配置 + 端到端驗證
3. **Phase 2（選用）**：接入 Arkham `/token/market/{id}` 取代/補充 CoinGecko 即時價格
4. **Phase 2（選用）**：接入 Arkham `/risk/address/{address}` 輔助操縱偵測

### 成本控制

- Arkham `/transfers`：2 credits/row × 20 rows = 40 credits/call
- 5 幣 × 1 call/幣 × 12 次/小時 = 60 calls/hr = 2,400 credits/hr
- 試用額度 1M credits → 可撐約 416 小時（17 天）→ 足夠比賽期間

---

## 8. 結論

| 連接器 | 現狀 | 需修正量 |
|--------|------|---------|
| WhaleAlertSource | 端點/認證正確，僅需驗證 key 有效 | 小（驗證 + 測試） |
| ArkhamIntelSource | 端點/認證/解析全錯 | 大（完整重寫 fetch + parse） |

**建議**：建立新 spec `arkham-whale-alert-live`，重寫 Arkham 連接器、驗證 Whale Alert key、更新離線樣本與測試。
