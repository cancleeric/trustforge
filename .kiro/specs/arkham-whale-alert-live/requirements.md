# Spec：Arkham + Whale Alert 真實 API 整合（Live Integration）

## 概述

修正 `whale_trades.py` 中的 `ArkhamIntelSource` 連接器以對齊 Arkham Intel API v1.1.0 真實規格，
並驗證 `WhaleAlertSource` 搭配實際取得的 API key 可正常運作。

**背景**：現有連接器是基於早期推測實作的骨架，Arkham 端的 base URL、認證方式、回應解析全部錯誤。
現已取得兩個 API 的 key，需修正程式碼以接上真實資料。

**分析報告**：`docs/reports/ANALYSIS-arkham-whale-alert-integration.md`

---

## 需求（Requirements）

### R1：Arkham API 端點與認證修正

- Base URL 修正為 `https://api.arkm.com`
- 認證方式改為 HTTP header `API-Key: <key>`（非 query param `apiKey`）
- 端點保持 `GET /transfers`
- 幣種過濾使用 `chains` 參數（`bitcoin`, `ethereum`, `solana`, `bsc`, `xrp`）
- 時間過濾使用 `timeLast=1h`
- 金額過濾使用 `usdGte=1000000`
- 分頁使用 `limit=20`
- Rate limit 遵守：heavy endpoint 1 req/s（排程器 5 分鐘間隔已足夠）

### R2：Arkham 回應解析修正

- 幣種符號從 `transfer.tokenSymbol`（字串）取得
- USD 金額從 `transfer.historicalUSD`（浮點數）取得
- 時間戳從 `transfer.blockTimestamp`（ISO 8601 字串）解析
- 來源實體從 `transfer.fromAddress.arkhamEntity.name` 或 `.arkhamLabel.name` 取得
- 目的實體從 `transfer.toAddress.arkhamEntity.name` 或 `.arkhamLabel.name` 取得
- 驗證狀態：有 `arkhamEntity` 或 `arkhamLabel` 物件 = 已驗證

### R3：Whale Alert 驗證

- 確認現有端點 `https://api.whale-alert.io/v1/transactions` + `api_key` query param 正確
- 使用已取得的 API key 進行端到端測試
- 確認免費層回應結構符合現有 `_parse_transaction` 邏輯
- Rate limit 注意：免費層 10 req/min

### R4：環境變數配置

- `ARKHAM_API_KEY`：Arkham Intelligence API key
- `WHALE_ALERT_API_KEY`：Whale Alert API key（已有：`nmONwfLZ3rPYaMiKeC0zLBbudgseYEsi`）
- 兩者皆為選用；無 key 時靜默降級（現有行為不變）

### R5：離線樣本更新

- 更新 `demo/sample_data/whale_trades.json` 中 Arkham 相關樣本的結構
- 使樣本符合真實 API 回應 schema（`tokenSymbol`、`historicalUSD`、ISO 時間戳等）
- 確保離線模式（`_WhaleOfflineSampleSource`）仍可正常運作

### R6：安全約束（不變）

- API key 僅從環境變數讀取，絕不 hardcode
- key 不進入 Document.url / Document.meta / log
- URL 白名單由程式碼內建常數組成
- SSRF-safe fetch（`safe_fetch.py`）
- timeout 5 秒 / 回應上限 512 KB

---

## 非範圍（Out of Scope）

| 排除項 | 原因 |
|--------|------|
| Arkham WebSocket 即時串流 | Phase 2；當前排程器 5 分鐘 polling 已足夠 |
| Arkham Risk Scoring 端點 | Phase 2；需付費 add-on |
| Arkham `/token/market/{id}` | Phase 2；目前有 CoinGecko |
| Whale Alert Enterprise 功能 | 需付費升級 |
| 信任評分引擎修改 | kind/信譽映射不變 |

---

## 風險與限制

| 風險 | 影響 | 緩解 |
|------|------|------|
| Arkham key 權限不足（401/403） | 連接器無法取得資料 | 降級跳過（既有行為）；確認 key 有效 |
| Whale Alert 免費層資料量少 | 信號稀疏 | 已知限制；有離線樣本兜底 |
| Arkham per-row billing（2 credits/row） | 長期成本 | limit=20 控制；試用額度 1M 足夠比賽期間 |
| API schema 微調 | 解析失敗 | 防禦式解析 + 缺欄位容錯（raise → 排程器保留舊快取） |

---

## 成功指標

- [ ] `ArkhamIntelSource` 使用正確 base URL + header 認證成功取得資料
- [ ] Arkham 回應正確解析為 Document（kind=celebrity_trade）
- [ ] `WhaleAlertSource` 用實際 key 成功取得大額轉帳資料
- [ ] 離線模式仍可正確產出兩種 kind 的 Document
- [ ] 所有現有測試通過（不破壞既有行為）
- [ ] 無 API key 洩漏（env var only）
