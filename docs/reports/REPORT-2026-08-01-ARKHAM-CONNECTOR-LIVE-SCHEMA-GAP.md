# Arkham Connector 真實 Schema 落差分析

- 日期：2026-08-01
- 範圍：`src/trustforge/ingestion/whale_trades.py` 的 `ArkhamIntelSource`
- 性質：唯讀診斷；本報告未修改 connector、secret、DB 或外部設定
- 結論：Arkham API 與 API key 可用，但 TrustForge connector 目前無法可靠產出 Arkham `Document`

## 1. 執行摘要

Arkham 官方 API 已以有效 key、`API-Key` header 與可識別 User-Agent 實測成功。`GET /chains` 與 `GET /transfers` 均回 HTTP 200；BTC transfers 查詢有真實候選資料。然而，`ArkhamIntelSource.fetch(..., coin="BTC")` 完整執行後產出 0 個 `Document`。

根因不是連線、認證或額度，而是 connector 把「查詢鏈」與「目標資產」視為同一概念，並假設所有鏈都回傳 EVM 式 transfer schema。真實 BTC UTXO 回應沒有 `tokenSymbol`、使用 `fromAddresses` 複數；parser 在第一個 symbol guard 即丟棄資料。Ethereum 查詢則會回傳該鏈上的各種 token，例如 WETH，而 parser 又要求 symbol 必須嚴格等於 `ETH`，同樣造成大量有效資料被丟棄。

## 2. 實測證據

### 2.1 API 與認證

- 官方 Base URL：`https://api.arkm.com`
- 認證：`API-Key: <key>` header
- 官方機器可讀文件：`https://intel.arkm.com/llms.txt`
- 使用 Python 預設 User-Agent 時，Cloudflare 回 403；改用 `TrustForge/...` User-Agent 後回 HTTP 200。
- API key 僅從 `/Users/yinghaowang/kiro/apikey/arkm.apikey` 注入執行環境，未寫入 repo 或輸出。

### 2.2 真實 BTC transfer

以 `chains=bitcoin&usdGte=1000000&timeLast=1h&limit=1` 實測：

- HTTP 200
- `transfers` 為 list，回傳 1 筆
- response `count` 顯示有候選資料
- 欄位包含：`blockHeight`、`fromAddresses`、`fromValue`、`toAddress`、`historicalUSD`、`transactionHash`
- 欄位不包含：`tokenSymbol`、`tokenId`、`tokenName`、`fromAddress`

### 2.3 真實 Ethereum transfer

以 `chains=ethereum&usdGte=1000000&timeLast=1h&limit=1` 實測：

- HTTP 200
- 使用 EVM 式 `fromAddress` / `toAddress`
- 具有 `tokenSymbol`，首筆實測為 `WETH`，不是 `ETH`
- 證明 `chains=ethereum` 是鏈篩選，不是 ETH 原生資產篩選

### 2.4 TrustForge connector

- `tests/test_whale_trades_arkham.py`：10 passed
- 真 key 執行 `ArkhamIntelSource().fetch("", coin="BTC")`：完成但回傳 0 個 `Document`
- 單元測試綠燈與真實行為矛盾，屬 fixture fidelity 缺口

## 3. 根因分析

### R1：BTC UTXO schema 未支援（直接根因）

`_parse_transfer()` 先讀 `transfer["tokenSymbol"]`。BTC transfer 沒有該欄位，因此 symbol 變成空字串，隨即被 `_SUPPORTED_COINS` guard 拒絕。後續的 `fromAddress` 解析根本不會執行；即使執行，也讀不到 BTC 的 `fromAddresses`。

### R2：鏈與資產語義混淆（系統性根因）

`coin="ETH"` 被轉成 `chains=ethereum`，但 parser 又要求每筆 `tokenSymbol == "ETH"`。Arkham 的 chain filter 會回傳 WETH、USDC 等鏈上資產，故合法 transfer 被當成錯誤資料。BNB/BSC、ARB/Arbitrum 也有同類風險。

### R3：測試 fixture 不代表官方真實 schema

現有 BTC fixture 同時提供 `tokenSymbol="BTC"` 與單數 `fromAddress`，實際上更接近 EVM token schema。測試只驗證程式對自製 payload 的行為，沒有保護官方 schema 契約。

### R4：靜默資料損失

每筆 parser rejection 都只回 `None`，fetch 最終回空 list；沒有 machine-readable rejection reason、schema drift counter 或告警。因此上層無法區分「真的沒有資料」與「全部解析失敗」。

### R5：探測工具與正式 connector 的 User-Agent 不一致

正式 connector 已設定 `TrustForge/1.0 (research)`，可通過 Cloudflare。以 Python 預設 User-Agent 寫的裸探測會得到 403，容易誤診為 key 或方案問題。這不是正式 connector 的故障，但應納入 live probe 規範。

## 4. 影響

- BTC：現行真實 transfer 幾乎必然在 symbol guard 被丟棄。
- ETH／BNB／ARB：非原生 symbol（如 WETH、USDC、WBNB）會被丟棄，導致樣本偏差。
- SOL／XRP：需以官方真實 schema fixture 驗證，不能從 EVM 假設外推。
- 信任分析：Arkham 來源表面啟用、實際可能持續輸出 0，造成來源多樣性與鏈上歸因被高估。
- 營運：`/transfers` 每筆 2 credits；無效解析仍會消耗成功請求的 credits。

## 5. 安全與成本判定

- 不需 DB schema 或 migration。
- 不需 secret rotation；key 繼續只由環境／既有 secret resolver 注入。
- 真實測試必須使用 `limit=1` 或固定小額 budget，避免現行 connector 的固定 `limit=20` 每次最多消耗 40 credits。
- 不得把真實地址、完整交易資料或 API key提交為 fixture；fixture 必須去識別化並保留 schema 形狀。

## 6. 最終判定

狀態：**API healthy / connector functionally broken**。

在完成多 schema normalization、鏈／資產語義決策、真實契約 fixture 與 rejection telemetry 前，不應將 Arkham 標記為正常資料來源。

