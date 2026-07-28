# Design：Arkham + Whale Alert Live Integration

## 架構概覽（不變）

```
                   ┌──────────────────┐
                   │  whale_trades.py │
                   └────────┬─────────┘
                            │
             ┌──────────────┼──────────────┐
             ▼              ▼              ▼
    WhaleAlertSource  ArkhamIntelSource  OfflineSample
    (whale_onchain)   (celebrity_trade)   (兩種 kind)
             │              │
             ▼              ▼
        safe_fetch      safe_fetch
             │              │
             ▼              ▼
  whale-alert.io/v1   api.arkm.com
  (api_key query)     (API-Key header)
```

整體架構不變：兩個 Source class → CachedSource 包裝 → 排程器 5 分鐘 fetch。
本次修正集中在 `ArkhamIntelSource` 的 fetch 邏輯與 response parsing。

---

## Arkham Intel API v1.1.0 正確規格

### 端點

```
GET https://api.arkm.com/transfers
```

### 認證

```http
API-Key: <ARKHAM_API_KEY>
```

**不是** query parameter。通過 `safe_fetch.fetch_url()` 的 `extra_headers` 傳入。

### 請求參數

| 參數 | 類型 | 用途 | 我們的用法 |
|------|------|------|-----------|
| `timeLast` | string | 相對時間窗 | `"1h"`（最近 1 小時的轉帳） |
| `usdGte` | number | 最低 USD 金額 | `1000000`（100 萬） |
| `chains` | string | 鏈過濾（逗號分隔） | 按幣種映射 |
| `limit` | number | 分頁大小 | `20` |
| `sortKey` | string | 排序欄位 | `"time"`（預設） |
| `sortDir` | string | 排序方向 | `"desc"`（預設，最新在前） |

### 幣種 → 鏈映射

| 幣種 | Arkham chain 名稱 |
|------|-------------------|
| BTC | `bitcoin` |
| ETH | `ethereum` |
| SOL | `solana` |
| BNB | `bsc` |
| XRP | `xrp` |

### 回應結構

```json
{
  "transfers": [
    {
      "fromAddress": {
        "address": "0x28C6c06298d514Db089934071355E5743bf21d60",
        "arkhamEntity": {
          "name": "Binance",
          "type": "exchange",
          "id": "binance"
        },
        "arkhamLabel": {
          "name": "Binance: Hot Wallet 1",
          "address": "0x28C6c06298d514Db089934071355E5743bf21d60"
        }
      },
      "toAddress": {
        "address": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
        "arkhamEntity": {
          "name": "Vitalik Buterin",
          "type": "individual",
          "id": "vitalik-buterin"
        },
        "arkhamLabel": null
      },
      "tokenSymbol": "ETH",
      "historicalUSD": 5200000.0,
      "unitValue": 1500.5,
      "chain": "ethereum",
      "transactionHash": "0xabc123...",
      "blockTimestamp": "2026-07-28T11:01:35Z"
    }
  ]
}
```

### Rate Limits

- `/transfers` 是 **heavy endpoint**：1 request/second
- 排程器 5 分鐘間隔 → 完全不觸發
- 計費：2 credits/row → 20 rows = 40 credits/call

---

## Whale Alert API 正確規格（驗證）

### 端點

```
GET https://api.whale-alert.io/v1/transactions
```

### 認證

```
?api_key=<WHALE_ALERT_API_KEY>
```

Query parameter（現有程式碼正確）。

### 請求參數

| 參數 | 類型 | 用途 | 我們的用法 |
|------|------|------|-----------|
| `api_key` | string | 認證 | env var |
| `min_value` | number | 最低 USD | `1000000` |
| `start` | number | 起始時間 epoch | `now - 3600` |
| `currency` | string | 幣種過濾 | `btc`/`eth`/`sol`/`bnb`/`xrp` |

### 回應結構（現有程式碼已正確對應）

```json
{
  "result": "success",
  "cursor": "...",
  "count": 5,
  "transactions": [
    {
      "blockchain": "bitcoin",
      "symbol": "btc",
      "hash": "abc123...",
      "timestamp": 1722160895,
      "amount": 1200.5,
      "amount_usd": 72000000.0,
      "from": {
        "address": "bc1q...",
        "owner": "binance",
        "owner_type": "exchange"
      },
      "to": {
        "address": "bc1q...",
        "owner": "unknown",
        "owner_type": "unknown"
      }
    }
  ]
}
```

**結論：WhaleAlertSource 的 `fetch()` 和 `_parse_transaction()` 無需修改**，
僅需驗證 API key 有效性。

---

## 修改設計：ArkhamIntelSource

### fetch() 重寫

```python
def fetch(self, query: str, coin: str = "") -> list[Document]:
    api_key = os.environ.get(_ARKHAM_KEY_ENV, "").strip()
    if not api_key:
        return []

    if coin and coin.upper() not in _SUPPORTED_COINS:
        return []

    params: dict[str, str | int] = {
        "usdGte": _MIN_VALUE_USD,
        "timeLast": "1h",
        "limit": 20,
    }
    # 按幣種映射到 Arkham chain 名稱
    if coin:
        chains = _ARKHAM_COIN_CHAINS.get(coin.upper())
        if chains:
            params["chains"] = ",".join(chains)

    url = f"https://api.arkm.com/transfers?{urlencode(params)}"
    extra_headers = {"API-Key": api_key}
    raw = _fetch_url(url, extra_headers=extra_headers)
    data = json.loads(raw)

    if not isinstance(data, dict):
        return []

    transfers = data.get("transfers", [])
    if not isinstance(transfers, list):
        return []

    docs: list[Document] = []
    for transfer in transfers:
        if not isinstance(transfer, dict):
            continue
        doc = self._parse_transfer(transfer, coin)
        if doc is not None:
            docs.append(doc)
    return docs
```

### _parse_transfer() 重寫

```python
def _parse_transfer(self, transfer: dict, target_coin: str) -> Document | None:
    """解析單筆 Arkham v1.1.0 轉帳為 Document。"""
    # 幣種（v1.1.0：tokenSymbol 是頂層字串）
    symbol = str(transfer.get("tokenSymbol", "")).upper()
    if symbol not in _SUPPORTED_COINS:
        return None
    if target_coin and symbol != target_coin.upper():
        return None

    # 金額（v1.1.0：historicalUSD 是頂層浮點數）
    amount_usd = _finite_num(transfer.get("historicalUSD"), lo=_MIN_VALUE_USD)
    if amount_usd is None:
        return None

    # 原始數量
    unit_value = _finite_num(transfer.get("unitValue"), lo=0)

    # 時間戳（v1.1.0：blockTimestamp 是 ISO 8601 字串）
    block_ts_str = transfer.get("blockTimestamp", "")
    ts = _parse_iso_timestamp(block_ts_str)
    if ts is None:
        ts = time.time()

    # 實體標記（v1.1.0：arkhamEntity 和 arkhamLabel 是巢狀物件）
    from_addr = transfer.get("fromAddress", {})
    to_addr = transfer.get("toAddress", {})
    from_label = _extract_entity_name(from_addr)
    to_label = _extract_entity_name(to_addr)

    # 驗證狀態：有 arkhamEntity 或 arkhamLabel 物件 = 已驗證
    verified = _has_arkham_attribution(from_addr) or _has_arkham_attribution(to_addr)

    # 判斷買/賣方向
    # 有標記的實體是 toAddress → 該實體「收到」資產 → 買入
    # 有標記的實體是 fromAddress → 該實體「送出」資產 → 賣出
    if _has_arkham_attribution(to_addr):
        entity_name = to_label
        action, action_desc = "buy", "買入"
    else:
        entity_name = from_label
        action, action_desc = "sell", "賣出"

    # 方向詞（供 _infer_direction 推斷）
    direction_word = f"（看漲訊號：名人{action_desc}）" if action == "buy" \
        else f"（看跌訊號：名人{action_desc}）"

    usd_str = f"{amount_usd:,.0f}"
    verified_str = "鏈上已驗證" if verified else "未經鏈上驗證"

    text = (
        f"已標記錢包（{entity_name}）{action_desc} {symbol}"
        f"（約 {usd_str} USD），{verified_str}"
        f"{direction_word}"
    )

    tx_hash = transfer.get("transactionHash", "")
    doc_id = "arkham-" + hashlib.md5(
        f"{tx_hash}-{symbol}-{ts}".encode()
    ).hexdigest()[:12]

    return Document(
        id=doc_id,
        kind=self.kind,
        source=self.name,
        text=text,
        url=f"https://platform.arkhamintelligence.com/explorer/tx/{tx_hash}",
        ts=ts,
        meta={
            "coin": symbol,
            "amount_usd": amount_usd,
            "verified_onchain": verified,
            "entity": entity_name,
            "action": action,
            "content_reference": text[:120],
        },
    )
```

### 新增 helper 函式

```python
# 幣種 → Arkham chain 映射
_ARKHAM_COIN_CHAINS: dict[str, list[str]] = {
    "BTC": ["bitcoin"],
    "ETH": ["ethereum"],
    "SOL": ["solana"],
    "BNB": ["bsc"],
    "XRP": ["xrp"],
}


def _parse_iso_timestamp(ts_str: str) -> float | None:
    """解析 ISO 8601 時間戳為 epoch 秒。容錯：格式不合回 None。"""
    if not isinstance(ts_str, str) or not ts_str:
        return None
    try:
        # 支援 "2026-07-28T11:01:35Z" 格式
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _extract_entity_name(addr_obj: dict) -> str:
    """從 Arkham v1.1.0 address 物件中萃取最佳顯示名稱。

    優先順序：arkhamEntity.name > arkhamLabel.name > address[:10]
    """
    if not isinstance(addr_obj, dict):
        return "unknown"
    entity = addr_obj.get("arkhamEntity")
    if isinstance(entity, dict) and entity.get("name"):
        return str(entity["name"])
    label = addr_obj.get("arkhamLabel")
    if isinstance(label, dict) and label.get("name"):
        return str(label["name"])
    address = addr_obj.get("address", "")
    return str(address)[:10] if address else "unknown"


def _has_arkham_attribution(addr_obj: dict) -> bool:
    """判斷 address 物件是否有 Arkham 歸因（entity 或 label）。"""
    if not isinstance(addr_obj, dict):
        return False
    entity = addr_obj.get("arkhamEntity")
    if isinstance(entity, dict) and entity.get("name"):
        return True
    label = addr_obj.get("arkhamLabel")
    if isinstance(label, dict) and label.get("name"):
        return True
    return False
```

---

## 離線樣本更新

`demo/sample_data/whale_trades.json` 中 `kind=celebrity_trade` 的樣本需對齊新 schema：

**Before（舊格式）：**
```json
{
  "token": {"symbol": "BTC"},
  "unitValueUsd": 5000000,
  "blockTimestamp": 1721345678,
  "fromAddress": {"arkhamLabel": "MicroStrategy"},
  "toAddress": {"arkhamLabel": ""},
  "transactionHash": "0x..."
}
```

**After（新格式，內嵌在 Document meta 中無須改變，只影響 raw response mock）：**

離線樣本本身是已經被 parse 過的 `Document` 格式（id/kind/text/url/ts/meta），
不是 raw API 回應。**因此離線樣本的 Document 結構無須改變**——
只有 `_parse_transfer()` 的輸入（真實 API 回應）格式改變了。

測試 mock 需要更新為新的 raw response 格式。

---

## 測試策略

| 測試層 | 驗證內容 |
|--------|---------|
| Unit（mock） | `_parse_transfer()` 正確解析 v1.1.0 回應結構 |
| Unit（mock） | 缺欄位 / 型別錯誤時返回 None（不崩） |
| Unit（mock） | `_parse_iso_timestamp()` 各種格式容錯 |
| Unit（mock） | `_extract_entity_name()` 優先順序正確 |
| Integration（真實 key） | `fetch()` 能成功取得資料（CI skip if no key） |
| Integration（真實 key） | Whale Alert key 認證成功 |
| Existing | 所有既有 `test_whale_trades*.py` 通過 |

---

## 不變的部分

- `WhaleAlertSource`：fetch + parse 不變
- `Document` dataclass：結構不變
- `scoring.py` 信譽映射：`whale_onchain=0.88`, `celebrity_trade=0.50` 不變
- `CachedSource` 包裝：介面不變
- `safe_fetch.py`：完全相容，不需修改
- `base.py collect()`：呼叫邏輯不變
- 排程器 `fetch_scheduler.py`：間隔/容錯不變
