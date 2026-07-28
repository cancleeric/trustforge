# Design

## 架構決策

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

## 資料模型

新增兩個 Document kind：

| Kind | Source Name | 信號語義 | 信譽 |
|------|-------------|----------|------|
| `whale_onchain` | `whale-alert` | 鏈上大額轉帳事實 | 0.88 |
| `celebrity_trade` | `arkham-intel` | 標記錢包/名人交易 | 0.50 |

## Document 產出格式

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

## 信任評分整合

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

## API 整合規格

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

## 安全考量

- 所有 URL 硬編碼，不接受外部輸入
- API key 僅從環境變數讀取，不進入 Document.url/meta/log
- 透過 safe_fetch.py 的 SSRF-safe 機制發送請求
- 金額/時間戳欄位使用 `_require_number` 模式嚴格驗證
