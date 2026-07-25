# 資產脈絡查詢（Asset Context Lookup）— 獨立 PR 計劃

- 撰寫：gray（CPO）
- 日期：2026-07-24
- 分支基礎：`develop`（所有 PR 打回 `develop`，不碰 `main`；`main` 合併另有專人）
- 狀態：規劃案，尚未派工執行
- CEO 已定方向：**Option A** — 把「新手脈絡模組①」拆成獨立的「資產脈絡查詢/lookup」小工具，
  查 `$ARB` 出 `[Layer 2]` 卡 + 上下游/`gas_token` 說明，**吃現有 `asset_context` 資料，
  解耦於完整信任分析流程之外**。本計畫僅涵蓋此獨立查詢，不擴大範圍。

---

## 0. 動工前抽查佐證（本次新查，非沿用假設）

### 0.1 `COIN_POOL` 與資料覆蓋

- `COIN_POOL = ("BTC","ETH","SOL","BNB","XRP")`，`ARB` 確實不在內（#584 全棧啟用未做）。本方案
  刻意繞開 `COIN_POOL`，不透過既有五幣分析流程觸發，改走獨立查詢路徑。
- `data/asset_context_records.json` 目前**只有 ARB 兩筆歷史紀錄**（`valid_from`
  2025-01-01 / 2026-01-01），其他五個 L1 幣完全沒有 `asset_context` 資料 —— 沒有資料可查就沒有
  查詢結果，這點在需求文案上要說清楚（非 bug，是資料覆蓋範圍）。

### 0.2 關鍵落差：CEO 描述「已含 settlement_chain/gas_token/dependencies」需修正一半

- **Schema 層確實已有這三個欄位**：`src/trustforge/asset_context.py:78-80`
  （`AssetContext.settlement_chain: str = "unknown"`、`gas_token: str = "unknown"`、
  `dependencies: tuple[str, ...] = ()`），且 `asset_context_from_dict()`（同檔 143-214 行）
  把這三個列為**選填欄位**，容許舊 payload 省略、回退到預設值。
- **但 `data/asset_context_records.json` 的兩筆 ARB fixture 資料完全沒有這三個鍵**
  （已讀取確認，見附錄 A 原始 JSON）。代入 `asset_context_from_dict()` 後，這三個欄位會
  **一律退回預設值 `"unknown"` / `()`**，不是 CEO 認知中「Ethereum / ETH / [下游清單]」的
  真實內容。
- 換句話說：**後端「能不能渲染這三個欄位」的程式碼路徑已通，但「渲染出來的內容」目前是
  unknown/空，不是有意義的資料**。這個資料缺口必須在 Phase 1 的第一張 PR 補上（更新
  fixture JSON，非改 schema/程式碼），否則查詢頁面上線第一天就會看到「gas_token: unknown」，
  對「新手脈絡」的產品訴求是負面示範。
- `frontend/src/lib/types.ts:188-200` 的 `AssetContext` TS interface **同樣缺這三個欄位**
  （只有 `schema_version/asset_id/symbol/name/sector/layer/token_role/market_cap_tier/
  ecosystem/parent_asset_id/tags`），前端要渲染就必須先補型別。

### 0.3 API 接線現況

- `src/trustforge/web.py:5057-5066` 已有 `_asset_context_repository()`（lazy-load 
  `AssetContextRepository` 單例，讀 `data/asset_context_records.json`）。
- `src/trustforge/web.py:5110-5123`：現有邏輯是**掛在 `/api/analyze` 的 response 組裝裡**——
  分析完一份 `report` 後才用 `report.symbol` 去 `repository.by_symbol()` 查，把結果塞進
  `data["asset_context"]`/`data["risk_notices"]`。**這條路徑必須先跑完整分析流程**（含
  evidence 蒐集、信任分數計算等），才會帶出 `asset_context`——這正是 CEO 要求解耦掉的部分。
- `AssetContextRepository.by_symbol(symbol, as_of)`（`asset_context_repository.py:51-62`）
  是純資料查詢，**不依賴 report 物件**，本身就可以被獨立呼叫，只是目前沒有獨立路由包住它。
- 路由風格確認：本專案的 `/api/*` 端點是手寫的 raw HTTP dispatch（`web.py` 7745 行後一長串
  `if u.path == "/api/xxx":`），**不是 FastAPI/Flask 裝飾器**，新增端點的最小改法是照同一種
  `if u.path == "/api/asset-context":` 分支加進去，風格與現有代碼一致，改動量小。
- 認證：既有 `/api/analyze`、`/api/overview` 等公開端點只套 rate-limit、不需認證（見
  `_public_evidence_dict` 註解 5044-5051 行明載「不需認證，任何人都能打」）。新端點
  `/api/asset-context` 比照辦理即可（唯讀查詢、無寫入、無敏感欄位），**不需要 harper**；
  若後續要限流或加認證另議，不在本計劃阻塞範圍內。

**最小接法結論**：新增一支輕量唯讀端點 `GET /api/asset-context?symbol=ARB`（或
`?asset_id=asset:arb`），內部直接呼叫既有 `_asset_context_repository().by_symbol()`，
**不觸碰 `/api/analyze` 既有邏輯、不用跑分析流程**。找不到資料時回傳
`{"asset_context": null}`（HTTP 200，非 404，語意是「查無此資產的脈絡資料」，前端顯示
「目前無此資產的脈絡資料」而非報錯）。

### 0.4 設計稿比對

- `docs/design/hermes-r2-darkbridge/`（15 頁 `.dc.html`）全文檢索 `sector`/`layer`/
  `token-role`/`settlement`/`gas-token` 均無命中 —— **沒有既有 mockup**，此結論與
  `PLAN-context-ui-2026-07-24.md` §0（模組①③無設計稿）一致，非本次重新推翻。
- `HERMES Design System.dc.html` 有既有 card/badge/table token 可延伸組裝。
- CEO 已授權：無 mockup 時可依 Design System token 直接組裝，不需先出新 mockup 卡工程進度。
  本計劃沿用此授權，SectorLayerCard 造型比照 Design System 既有 badge + info-card 元件
  （例如 `.hrm-badge`/`.hrm-card` 類 token，實際 class 名以工程盤點 Design System 頁為準）。

### 0.5 前端落點建議

盤點 `frontend/src/App.tsx` 現有路由（`/`, `/analyze`, `/compare`, `/status`, `/costs`,
`/history`, `/admin`, `/settings`, `/help`, `/notifications`），評估兩個選項：

| 選項 | 說明 | 建議 |
|---|---|---|
| (a) 獨立路由 `/asset-context` 或 `/lookup` | 新增一頁，含輸入框（symbol）+ 送出 + 卡片渲染區 | **建議採用** —— 語意上與「完整分析」（`/analyze`）明確區隔，避免用戶誤以為查一下 sector 卡就等於做了信任分析；未來也方便獨立導流/行銷（例如「30 秒看懂一個代幣的定位」） |
| (b) 塞進 `HermesDashboard`（首頁）側欄小工具 | 首頁曝光高，但 `HermesDashboard` 是「五幣信任儀表板」語意，硬塞一個跟 `COIN_POOL` 五幣無關、只認 ARB 的查詢工具，語意混雜 | 不建議作為主路徑；可在 Phase 2（PR C）評估要不要在首頁加一個「快速查詢」入口按鈕連到 (a) 的獨立頁，不直接嵌內容 |

**建議落點：新增獨立路由 `/asset-context`（頁面元件 `AssetContextLookupPage.tsx`），
Header 主導覽視覺重量比照 `/help`/`/notifications`（次要功能，不搶 `/analyze`/`/compare`
的主位置，但要進導覽讓用戶找得到，不像 `/admin`/`/settings` 刻意隱藏）。**

### 0.6 與既有 #585 的關係——取代，非並存

- `#585`（Sector/Layer/Token Role 卡片 UI，OPEN，見 `PLAN-context-ui-2026-07-24.md`）原設計是
  **卡片嵌在 `/analyze` 完整分析結果頁裡**（依賴 #579 分析流程先跑完才有 `asset_context`）。
- 本計劃（Option A）是**獨立查詢頁**，不依賴分析流程。兩者的核心渲染元件
  （`SectorLayerCard`）可以共用，**但入口與資料流不同**。
- **結論：本計劃在「入口/資料流」層面取代 #585 原本規劃的「嵌入分析結果頁」路徑
  （不再需要因為 #585 而堵在 #579/#576 之後）；但 `SectorLayerCard` 元件本身可被兩處共用**。
  建議把 #585 的 issue 說明改註記為「已由 #（本計劃新開 issue）以獨立查詢頁形式達成核心
  訴求，原『嵌入分析結果頁』的子任務降為可選 nice-to-have，不阻塞」。此為 issue 治理決策，
  列入 §5 待 CEO 裁示（是否要工程順手把 `SectorLayerCard` 也嵌回 `/analyze` 頁，或明確只做
  獨立頁、#585 直接關閉）。
- 與模組②（glossary）的關係：glossary 的 `risk_note`/popover 已在 develop（PR #627），
  但目前只掛在 `/analyze` 分析文字上，**沒有掛在 asset_context 查詢結果的白話說明文字上**。
  本計劃 Phase 1 的 PR D（可選）評估是否讓查詢頁的說明文字也用 `AnnotatedText` 包一層，
  讓「Layer 2」「Rollup」等詞彙也能點開 glossary popover——這樣模組①②在查詢頁上同時可見，
  一次交付兩個模組的「常駐可見入口」缺口。

---

## 1. 目標 / 非目標

### 目標

1. 用戶可在獨立頁面輸入或選擇資產 symbol（優先支援 `$ARB`，其餘資產若無資料則誠實顯示
   「目前無此資產的脈絡資料」）。
2. 渲染 `SectorLayerCard`：`[Layer 2]` badge、`settlement_chain`（Ethereum）、
   `gas_token`（ETH）、`token_role`（governance）、`ecosystem`、`dependencies`
   （上下游關聯，白話說明例如「$ARB 結算在 Ethereum 上，Gas 費以 ETH 計價」）。
3. 未知欄位（`unknown`）誠實顯示為「尚無資料」，**不得猜測或補假資料**。
4. （可選）查詢結果文字內的詞彙（Layer 2、Rollup、Gas Fee 等）掛 `AnnotatedText`，
   點開 glossary popover，讓模組②同時可見。

### 非目標（明確排除，本計劃不做）

- **不**把 ARB 加進 `COIN_POOL`、不做 #584 全棧啟用。
- **不**跑完整信任分析流程（無 evidence 蒐集、無信任分數計算、無 W1-W4 權重）。
- **不**做 Peer 比較（模組③ `#589`/`#591`）或 EcoLink 影響路徑面板（`#590`/`#592`）。
- **不**在本計劃內新增 ARB 以外資產的 `asset_context` fixture 資料（五個 L1 幣的資料補齊
  是獨立的資料治理工作，超出本次「解耦查詢功能」範圍；若要示範多資產，僅在 Phase 1 PR A
  補齊 ARB 既有欄位的正確值，不新增其他資產）。
- **不**做認證/限流的新設計（沿用既有公開端點慣例）。

---

## 2. PR 拆解

| PR | 標題 | 改動檔案 | 驗收 | 工時 | Reviewer | 可平行 |
|---|---|---|---|---|---|---|
| A | `fix(data): 補齊 ARB asset_context fixture 的 settlement_chain/gas_token/dependencies` | `data/asset_context_records.json`（僅補值：`settlement_chain: "Ethereum"`, `gas_token: "ETH"`, `dependencies: [...]`，依 `asset_context.py:143-214` 的 schema 規則填寫合法值，不新增資產、不改 schema 程式碼） | 1) `python -c` 或既有 unit test 跑 `load_asset_context_records()` 成功解析、無 ValueError；2) `AssetContextRepository.by_symbol("ARB", as_of=now)` 回傳的 `record.context.settlement_chain == "Ethereum"` 且 `gas_token == "ETH"`；3) 既有 `test_asset_context*.py`（若有）全綠 | 2h | codex | 是，與 B/C 檔案不重疊，可平行；但 D 依賴 A 的資料才有東西可渲染真實值（unknown 也能開發，只是不真實） |
| B | `feat(api): 新增輕量唯讀端點 GET /api/asset-context` | `src/trustforge/web.py`（在既有 `if u.path == "/api/xxx":` dispatch 區塊新增 `/api/asset-context` 分支，query param `symbol`，呼叫既有 `_asset_context_repository().by_symbol()`，查無資料回 `{"asset_context": null}` HTTP 200，非 404）、對應 route 測試檔（`tests/test_web_*.py` 依既有命名慣例新增，非新建測試框架） | 1) `curl /api/asset-context?symbol=ARB` 回傳含 `settlement_chain`/`gas_token`/`dependencies` 的完整 JSON；2) `curl /api/asset-context?symbol=BTC`（無資料）回 200 + `asset_context: null`，**不是** 500/404；3) 無認證即可打通（比照 `/api/analyze` 慣例，測試需明確斷言不需 header）；4) 不修改 `/api/analyze` 既有行為（既有測試全綠，回歸測試跑一次） | 6h | codex，qa-lead 審 API 契約 | 可與 C 平行（前後端分離開發，約定好 response shape 即可） |
| C | `feat(ui): SectorLayerCard 元件 + AssetContext TS 型別補欄位` | `frontend/src/lib/types.ts`（`AssetContext` interface 補 `settlement_chain: string`、`gas_token: string`、`dependencies: string[]`）、新增 `frontend/src/components/SectorLayerCard.tsx`（+ `.test.tsx`）：渲染 `[Layer 2]` badge、`settlement_chain`/`gas_token`/`token_role`/`ecosystem`/`dependencies` 列表；`unknown` 值顯示「尚無資料」灰色樣式而非原樣印出字串 `"unknown"` | 1) desktop（1280px）+ mobile（375/390px）viewport 無橫向溢位（Playwright 截圖或既有 viewport 測試慣例）；2) `settlement_chain === "unknown"` 時顯示「尚無資料」，不顯示原始字串 `unknown`；3) `dependencies` 為空陣列時顯示「無已知上下游關聯」而非空白區塊；4) Design System token 沿用（class 命名比照 `HERMES Design System.dc.html` 既有 badge/card） | 8h | codex，qa-lead 指派 E2E reviewer | 可與 B 平行 |
| D | `feat(ui): AssetContextLookupPage 獨立路由 + 查詢入口` | `frontend/src/App.tsx`（新增 `/asset-context` route）、新增 `frontend/src/pages/AssetContextLookupPage.tsx`（+ `.test.tsx`）：輸入框（symbol）+ 查詢按鈕 + 呼叫 PR B 端點 + 渲染 PR C 的 `SectorLayerCard`；查無資料時顯示空狀態文案；Header 導覽加入次要入口連結（比照 `/help` 視覺權重） | 1) 輸入 `ARB` → 顯示完整卡片；輸入 `BTC`/任意無資料 symbol → 顯示「目前無此資產的脈絡資料」，不報錯不空白；2) desktop+mobile 無溢位；3) loading/error 狀態明確（API 打不通時顯示「查詢失敗，請稍後再試」而非白畫面）；4) Header 導覽可點進本頁（非隱藏路由） | 10h | codex，qa-lead 指派 E2E reviewer | 依賴 B（API）+ C（元件）完成，序列在最後 |
| E（可選） | `feat(ui): 查詢頁說明文字掛 AnnotatedText，串接模組②` | `AssetContextLookupPage.tsx`（既有元件包一層 `AnnotatedText`，沿用 PR #627 的 `findGlossaryAnnotations()`）、`frontend/src/lib/glossaryCatalog.ts`（若「Layer 2」「Rollup」等詞尚未收錄，需新增 term） | 1) 頁面上的「Layer 2」「Gas Fee」等詞彙可點開 glossary popover，顯示 `risk_note`（若有）；2) 沿用 PR #627 已驗證的 viewport clamp/keyboard 測試模式，不需重新設計互動邏輯 | 5h | codex | 依賴 D 完成後才有頁面可包；與 A-D 皆非阻塞關係，可延後到下一個 sprint |

**合計（PR A-D 必做）：26h；含可選 PR E：31h。** 全部 ≤12h/PR，符合工時上限。

---

## 3. 執行順序建議

```
PR A（資料修補，2h）──┐
                      ├──> PR D（獨立頁面，10h，依賴 B+C）──> PR E（可選，5h）
PR B（API，6h）───────┤
PR C（前端元件，8h）──┘
```

A/B/C 三張可平行開工（A 純資料、B 純後端、C 純前端元件，互不衝突），D 收斂三者、E 可選延後。

---

## 4. 與 #585 / #583 / #588 的關係彙整

| Issue | 原定位（`PLAN-context-ui-2026-07-24.md`） | 本計劃的處理 |
|---|---|---|
| #585（Sector/Layer 卡片 UI，嵌分析結果頁） | 依賴 #579（已由程式碼達成，issue 未關） | **取代其入口路徑**：本計劃改走獨立查詢頁，`SectorLayerCard` 元件可日後回頭嵌入 `/analyze`（若 CEO/工程認為值得），但不是本計劃阻塞項。建議 issue 註記降階，見 §5 |
| #583（Report term_annotations 契約） | `PLAN-context-ui` 已建議降階（前端本地比對取代後端 API） | 本計劃 PR E 沿用同一結論，不重新開後端契約 |
| #588（AnnotatedText + glossary popover） | 即 PR #627，已 approve 未 merge | 本計劃 PR E 依賴其已合併到 `develop`（若 PR #627 到 PR E 開工時仍未合併，需先處理，非本計劃新增阻塞） |

---

## 5. 需 CEO 裁示點

1. **#585 issue 治理**：是否同意本計劃「獨立查詢頁」取代 #585 原定「嵌入 `/analyze` 頁」的
   入口路徑？若同意，#585 是否直接關閉並開新 issue 追蹤本計劃，或保留 #585 註記「核心訴求
   已由新查詢頁達成，嵌入分析頁降為可選」？
2. **PR E 是否納入本次交付**：可選項，串接模組②讓查詢頁詞彙可點開 glossary popover。若不
   納入，模組②仍缺一個「常駐可見入口」（`PLAN-context-ui` 已指出的缺口），需另案處理。
3. **前端落點**：確認採用 §0.5 建議的獨立路由 `/asset-context`，而非塞進 `HermesDashboard`
   首頁側欄。
4. **資料範圍**：確認 Phase 1 僅補齊 ARB 既有欄位真實值（PR A），不新增其他資產的
   `asset_context` fixture——若要示範查詢多個資產（例如放第二個 L2 幣做對照），需另開資料
   治理任務，不在本計劃工時內。

---

## 附錄 A：`data/asset_context_records.json` 現況（已讀取確認）

```json
[
  {
    "context": {
      "schema_version": "1.0.0",
      "asset_id": "asset:arb",
      "symbol": "ARB",
      "name": "Arbitrum",
      "sector": "l2",
      "layer": "layer_2",
      "token_role": "governance",
      "market_cap_tier": "large",
      "ecosystem": "ethereum",
      "parent_asset_id": "asset:eth",
      "tags": ["rollup", "optimistic"]
      // 注意：無 settlement_chain / gas_token / dependencies 鍵
    },
    "valid_from": "2026-01-01T00:00:00Z",
    "fetched_at": "2026-01-02T00:00:00Z",
    "source": "fixture://asset-context/arb/2026-01-01"
  }
  // ...另一筆 2025-01-01 舊紀錄，同樣缺這三個欄位
]
```
