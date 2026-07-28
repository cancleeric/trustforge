# Design：大額轉帳資料顯示

## 架構

```
┌─────────────────────────────────────────────────────────┐
│                    前端（React）                          │
├──────────────┬──────────────────┬───────────────────────┤
│ sourceBrand  │ WhaleActivity    │ WhaleHistoryPanel     │
│ (R1:品牌名)   │ Panel (R3:即時卡) │ (R5:歷程面板)          │
└──────┬───────┴────────┬─────────┴──────────┬────────────┘
       │                │                    │
       │     GET /api/whale-summary   GET /api/whale-history
       │                │                    │
┌──────┴────────────────┴────────────────────┴────────────┐
│                    後端（Python）                          │
├──────────────────────────────────────────────────────────┤
│ server.py    路由新增兩個端點                               │
│              ↓                                           │
│ whale_api.py  聚合邏輯：讀 cache/archive → 統計 → JSON    │
│              ↓                     ↓                     │
│       cache（最新一批）      SourceEventArchive（歷史）    │
└──────────────────────────────────────────────────────────┘
```

---

## 後端設計

### `/api/whale-summary` 回應結構

```json
{
  "ok": true,
  "data": {
    "coin": "BTC",
    "period_hours": 1,
    "total_count": 47,
    "total_usd": 89200000,
    "net_exchange_flow_usd": -12300000,
    "exchange_inflow_usd": 5600000,
    "exchange_outflow_usd": 17900000,
    "max_single_usd": 5744745,
    "whale_transfer_count": 38,
    "exchange_inflow_count": 3,
    "exchange_outflow_count": 6,
    "recent_transfers": [
      {
        "amount_usd": 5744745,
        "coin": "BTC",
        "from": "unknown",
        "to": "unknown",
        "direction": "whale_transfer",
        "ts": 1722160895
      }
    ],
    "updated_at": "2026-07-28T15:30:00Z",
    "signal": "exchange_net_outflow",
    "signal_label": "淨流出交易所（囤積訊號）"
  }
}
```

### `/api/whale-history` 回應結構

```json
{
  "ok": true,
  "data": {
    "coin": "BTC",
    "days": 7,
    "available_since": "2026-07-28T00:00:00Z",
    "summary": {
      "total_count": 328,
      "total_usd": 2100000000,
      "net_exchange_flow_usd": -180000000,
      "max_single_usd": 26219880
    },
    "timeline": [
      {"bucket": "2026-07-28", "count": 47, "total_usd": 89200000, "net_flow_usd": -12300000},
      {"bucket": "2026-07-27", "count": 52, "total_usd": 102000000, "net_flow_usd": 5200000}
    ],
    "transfers": [
      {
        "amount_usd": 5744745,
        "amount": 89.5,
        "coin": "BTC",
        "from": "unknown",
        "to": "Binance",
        "direction": "exchange_inflow",
        "ts": 1722160895,
        "tx_url": "https://whale-alert.io/transaction/bitcoin/abc123"
      }
    ]
  }
}
```

---

## 後端實作路徑

### `src/trustforge/whale_api.py`（新檔案）

```python
def whale_summary(coin: str, backend: CacheBackend) -> dict:
    """從 cache 讀最新 whale-alert 資料，聚合為摘要統計。"""
    ...

def whale_history(coin: str, days: int, archive_dir: Path) -> dict:
    """從 SourceEventArchive 讀歷史 whale Document，聚合為時序+明細。"""
    ...
```

### `src/trustforge/server.py` 路由新增

```python
elif path == "/api/whale-summary":
    coin = params.get("coin", ["BTC"])[0].upper()
    data = whale_summary(coin, cache_backend)
    return json_response({"ok": True, "data": data})

elif path == "/api/whale-history":
    coin = params.get("coin", ["BTC"])[0].upper()
    days = int(params.get("days", ["7"])[0])
    data = whale_history(coin, days, archive_dir)
    return json_response({"ok": True, "data": data})
```

---

## 前端設計

### R1：sourceBrand.ts 修改

```typescript
// 新增顯示名稱
'whale-alert': 'Whale Alert · 鯨魚大額轉帳',
'arkham-intel': 'Arkham · 標記錢包交易',

// kind tier 新增
const THIRD_PARTY_KINDS = new Set(['price_live', 'onchain', 'whale_onchain', 'celebrity_trade'])
```

### R3：WhaleActivityPanel.tsx（新元件）

- 純展示元件，接收 `WhaleSummary` props
- 父層（HermesRightRail）負責 fetch + polling
- 無資料時顯示 placeholder
- 顏色語義：淨流出=cyan（看多）、淨流入=amber（警示）

### R5：WhaleHistoryPanel.tsx（新元件）

- 作為 workspace module 或獨立路由
- 內含時間切換 tabs + 統計卡 + 柱狀圖 + 明細表
- 柱狀圖用純 CSS/SVG（不引入圖表庫，遵循零第三方原則）
- 明細表用既有的表格元件風格

---

## 不變的部分

- 信任評分引擎邏輯（whale_onchain kind 已正確計入）
- 排程器 fetch 間隔（5 分鐘）
- cache 層 + SourceEventArchive 寫入機制
- safe_fetch / SSRF 防護
- 其他來源的顯示邏輯
