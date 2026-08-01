# Arkham Connector 真實 Schema 強化開發計劃

- 日期：2026-08-01
- 狀態：提案，尚未核准實作
- 依據：`docs/reports/REPORT-2026-08-01-ARKHAM-CONNECTOR-LIVE-SCHEMA-GAP.md`
- 前置門檻：先建立 GitHub issue，明列 acceptance criteria、依賴、成本上限與 reviewer

## 1. 目標

讓 `ArkhamIntelSource` 能在不洩漏 secret、不靜默吞掉 schema drift、且成本可控的前提下，正確處理 BTC UTXO 與 account-based chain transfer，並清楚區分 chain scope 與 asset scope。

## 2. 非目標

- 不修改 DB schema／migration。
- 不 rotation、搬移或提交 API key。
- 不新增 Arkham 付費方案或接受外部法律條款。
- 不在本輪接 WebSocket、alerts、risk scoring 或 user entity 寫入端點。
- 不把所有鏈一次擴成同一種資料模型；未驗證的鏈 fail closed。

## 3. 必須先拍板的產品語義

由 CPO 在 issue 中選定其中一種，不能由 parser 暗自決定：

1. **Chain activity（建議）**：`coin="ETH"` 代表 Ethereum 鏈的大額活動，保留 WETH、USDC 等 token，`meta.asset_symbol` 記錄實際資產。
2. **Native asset only**：只保留 BTC／ETH／BNB 等原生資產；必須使用官方可證明的 token/native 判定，不能只比對 display symbol。

若產品仍把 `meta.coin` 當分析主體，建議新增正規化後的 `chain` 與 `asset_symbol`，並保留明確的 compatibility mapping；不得把 WETH 無聲改寫成 ETH。

## 4. 實作切片

### Phase A：issue 與契約證據

- 建立 scoped issue，附本報告與官方文件連結。
- 指定 CPO reviewer；此變更會影響資料真實性與付費 credits，另請 CISO 審查 secret/logging 與成本防線。
- 以 `limit=1` 各取 BTC、ETH，以及 CPO 決定納入的其他鏈 schema。
- 將 payload 去識別化：替換地址、hash、entity 名稱與金額，但保留欄位、型別、list/cardinality。
- 保存官方文件版本或擷取日期，避免 fixture 被誤認為永久契約。

### Phase B：Normalization layer

在 `ArkhamIntelSource` 內新增小型純函式 normalization，輸入 provider payload、輸出內部 canonical transfer；parser 不再直接依賴 provider 欄位。

Canonical 欄位至少包含：

- `chain`
- `asset_symbol: str | None`
- `amount_usd`
- `timestamp`
- `transaction_hash`
- `from_parties: list[dict]`
- `to_parties: list[dict]`
- `attributed_party`
- `schema_family`（如 `utxo` / `account`）

規則：

- BTC：從 `chain=bitcoin` 推導 chain identity；接受 `fromAddresses`；沒有可靠 symbol 時不得用空字串拒絕。
- Account-based chain：接受 `fromAddress` / `toAddress` 與 token metadata。
- 不認得的 schema：fail closed，記錄安全的 rejection reason，不輸出原始 payload。

### Phase C：查詢與成本控制

- 將硬編碼 `limit=20` 改為有上限的 connector 設定，預設值由產品決定。
- live probe 固定 `limit=1`，並使用正式 safe-fetch User-Agent。
- 保留 1 request/second 限制；429 依 `Retry-After` backoff，不做無界重試。
- 每次 run 記錄 request count、returned rows、accepted rows、rejected rows 與估算 credits，不含地址或 key。

### Phase D：Parser 與訊號語義

- 由 canonical transfer 建立 `Document`。
- 多輸入地址時定義 deterministic attribution priority，不任意取第一筆做買賣推論。
- 若沒有 Arkham entity／label，不得宣稱「名人買入／賣出」；應降級為未歸因的大額 transfer，或依產品決策不產出 celebrity signal。
- `verified_onchain` 與 `attributed` 分開建模；鏈上存在不等於實體歸因可靠。
- `Document.id` 使用穩定 canonical identity；同一交易多 transfer 不得碰撞。

### Phase E：測試

新增／修正測試至少涵蓋：

- 真實形狀的 BTC UTXO fixture 可產出 canonical transfer。
- BTC 缺 `tokenSymbol` 不會被錯誤丟棄。
- BTC 多個 `fromAddresses` 的 deterministic 行為。
- ETH 原生資產與 ERC-20/WETH 的產品語義。
- BSC／Arbitrum 不把 chain name 當 token symbol。
- malformed、NaN/Infinity、過低金額、缺 timestamp、缺 hash。
- 無 attribution 不生成虛假的 celebrity buy/sell 文案。
- 未知 schema 有 rejection reason，且 log 不含地址、payload、key。
- User-Agent、timeout、response-size cap、redirect/SSRF 防線維持有效。
- cost estimator 與 limit 上限。

### Phase F：驗證與交付

- 跑 focused tests，再跑 repository-local `.githooks/pre-push`。
- 真 key canary：BTC 與一個 account-based chain 各 `limit=1`；保存去識別化結果、HTTP status、accepted/rejected count、credits。
- 開 PR 並連結 issue，附 commit-bound pre-push evidence。
- 執行 adversarial `/codex-review`，修完所有 finding。
- 由 CISO 審 secret/logging/cost abuse，由 CPO 審 chain/asset 與訊號文案。
- 本案無 UI 變更，eye scan 標記 N/A 並說明理由。
- merge 後在 merged branch 重跑 local gate；是否部署另走明確 release workflow。

## 5. Acceptance Criteria

- [ ] BTC 真實形狀 fixture 至少產出 1 個正確 canonical transfer／`Document`。
- [ ] `coin="BTC"` 的 live canary `limit=1` 在有資料時 accepted count 大於 0。
- [ ] Ethereum 鏈上 WETH／USDC 的保留或排除行為符合 issue 已拍板語義。
- [ ] 不再以缺少 `tokenSymbol` 作為 BTC 的必然 rejection。
- [ ] accepted、rejected、rejection reason 與估算 credits 可觀測。
- [ ] 任何 log、exception、fixture、PR evidence 都不含 API key 或真實完整地址／hash。
- [ ] live probe 單次最多 2 credits；測試套件預設不連外、0 credits。
- [ ] 既有 Whale Alert 行為無 regression。
- [ ] `.githooks/pre-push` 全綠，PR 無 unresolved findings。

## 6. 風險與回復

| 風險 | 防線 |
|---|---|
| 錯把鏈上 token 當原生幣 | 分離 `chain` / `asset_symbol`，由 CPO 拍板語義 |
| API schema 再漂移 | normalization + rejection reason + contract fixtures |
| credits 放大 | configurable bounded limit、limit=1 canary、usage telemetry |
| 敏感資料進 log/fixture | 去識別化 fixture、只記 aggregate counters |
| 錯誤買賣／名人訊號 | attribution 與 on-chain verification 分離，無歸因就降級 |

回復方式：connector 變更維持單一 scoped commit；若 live canary 或 quality gate 失敗，回退該 commit 並停用 Arkham source，不動 DB、不動 secret。

## 7. 建議派工順序

1. CPO：拍板 chain activity vs native asset only，寫入 issue acceptance criteria。
2. CISO：審 API key、log redaction、credits 與 live-test budget。
3. 開發：normalization、parser、telemetry、fixtures 與 tests。
4. Reviewer：adversarial review，特別檢查靜默資料損失與虛假 attribution。
5. CEO：親驗 `limit=1` 真實 canary 後才可回報 Arkham connector 完成。

