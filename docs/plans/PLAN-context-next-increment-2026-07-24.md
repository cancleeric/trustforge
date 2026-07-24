# 新手脈絡功能 — 下一增量計劃（2026-07-24）

owner: gray（CPO）
base: `develop`
狀態：規劃中（不執行實作，PR 皆打回 `develop`）

## 現狀盤點（含佐證）

| 模組 | 狀態 | 佐證 |
|------|------|------|
| ① 資產脈絡查詢 `/asset-context` | PR #648（`feat/asset-context-lookup`）審查中，尚未 merge develop | `frontend/src/pages/AssetContextLookupPage.tsx` + `SectorLayerCard.tsx`（分支內），`git show origin/feat/asset-context-lookup --stat` 共 13 檔 |
| ② Glossary 標註 + risk_note popover | 已 merge develop（PR #644 `feat/glossary-risknote-ui`、PR #627 `feat/588-annotated-text-popover`），但**只在文字含詞時才觸發**，目前 demo 報告不含相關詞 → 評審看不到 | `frontend/src/components/AnnotatedText.tsx`、`GlossaryTerm.tsx`、`frontend/src/lib/glossaryCatalog.ts`（29 個詞，7 個有 `riskNote`，含 `gas_fee`：「A token usually cannot be used to pay its own transfer fees…on Arbitrum…typically paid in ETH, not in ARB」） |
| ③ Peer/EcoLink | 後端 #589（peer metrics）/#590（ecolink/impact path）**完全沒接線** | `git grep` `src/trustforge/web.py`/`schema.py` 對 `peer_metric|impact_path|ecolink` 零命中；`src/trustforge/peer_metrics.py`（135 行）、`src/trustforge/ecolink.py`（245 行）、`src/trustforge/ecolink_connector.py`（87 行）只是**純 dataclass/enum 資料契約**（`MetricValue`、`DependencyKind`、`ImpactDirection`…），沒有 API route（`@app.route`/`add_url_rule` 零筆）、沒有資料抓取邏輯接上真實來源、前端 `frontend/src/**` 對 `peer|ecolink` 零命中（無任何 mock UI） |

關鍵發現：**SectorLayerCard.tsx 的「Gas 代幣」欄位邏輯與 glossary `gas_fee` 詞條的 riskNote 是同一件事**（ARB 轉帳需付 ETH gas），現有卡片已用手刻的 `gasMismatch` badge（⚠️ 轉帳手續費需 {gasToken}）表達同樣意思，但沒有呼叫 `AnnotatedText`／`GlossaryTerm`，導致模組②的標準化 popover 元件完全沒有出現在模組①頁面上——這正是「評審看不到模組②」的根因，也是最小成本的修補點。

---

## 優先 1｜讓模組②在 /asset-context 頁面可見（快、高價值）

### PR-A：SectorLayerCard 改用 AnnotatedText 承載 glossary 標註
- **改哪些檔**：
  - `frontend/src/components/SectorLayerCard.tsx`：
    - 「上下游關聯」`relationSummary()` 產出的白話句子（含「手續費與 {Chain} 的 gas 機制相關」）改用 `<AnnotatedText text={summary} />` 包住，而非純 `<p>`，讓句子裡的「gas」「依附」等詞若命中 catalog 自動出現 GlossaryTerm 底線+popover。
    - 新增一個顯式「名詞解釋」小節（放在卡片底部，dependencies 之後）：固定羅列本卡片相關的 2-3 個 glossary 詞（`gas_fee`、視 `token_role==governance` 是否加 `tokenomics`、`unlock_sell_pressure`），用 `<GlossaryTerm term={GLOSSARY_BY_ID.gas_fee} label="Gas Fee" />` 直接渲染 chip，不依賴文字比對——這是**保底**，即使 `relationSummary()` 因缺值回傳 `null`（見既有註解：缺值不猜、跳過整句），glossary 仍會顯示。
    - Gas 代幣 `gasMismatch` badge 旁加一個 `GlossaryTerm` chip（複用既有 `gas_fee` riskNote），避免自製文案跟 catalog 說法不一致（目前 badge 文案「轉帳手續費需 {gasToken}」與 catalog riskNote 文字重複但沒連動，未來修一邊會漏改另一邊）。
  - `frontend/src/components/SectorLayerCard.test.tsx`：補測試——ARB 查詢結果需能在 DOM 找到 glossary term（`role`/`aria` 或既有 GlossaryTerm 的 test id）、popover 展開後含 riskNote 文字「typically paid in ETH, not in ARB」。
  - 不改 `AssetContextLookupPage.tsx` 版面（沿用既有欄位順序），除非 QA 覺得「名詞解釋」小節需要獨立 section 標題（可在 code review 討論）。
- **驗收**：
  - Desktop + mobile（375px）：查 `ARB` → 看到 [Layer 2] 卡 → 「Gas 代幣」欄有 ⚠️ badge + 可點的 GlossaryTerm chip → 點開 popover 顯示 riskNote 全文，不截斷（mobile 需捲動但不溢出，沿用 PR #644 的 `cap popover height with scroll` 修法）。
  - 查 `BTC`（L1/資料有限）：`settlement_chain`/`gas_token` 為 unknown → 不觸發 gasMismatch badge、不假造 glossary 提示，但固定的「名詞解釋」小節仍顯示（因為是靜態羅列，不是文字比對），誠實標示「此資產無 L2 特有欄位」。
  - 缺資料時不得出現「依附於 unknown」等假訊息（沿用既有 `relationSummary()` guard）。
- **工時**：6h（含測試）
- **reviewer**：harper（前端/UI 一致性，且他熟悉 GlossaryTerm/AnnotatedText 原始設計 #588/#627）
- **可平行**：可與優先 2、優先 3 平行；但**應排在 PR #648 review 通過/merge develop 之後**（PR-A 是在 develop 版 `SectorLayerCard.tsx` 上動刀，若 #648 先合併，PR-A 直接 base develop 即可；若 #648 還在改，PR-A 可先在 `feat/asset-context-lookup` 分支上疊加，待兩者一起合併，需與負責 #648 的工程師協調 base）。
- **是否需要 harper**：需要，UI 元件改動＋mobile popover 驗證。

### PR-B（可選加強，若優先 1 時間有餘）：demo 報告內容補一句含 glossary 詞的文字
- 若評審是走「完整分析報告」流程而非 `/asset-context` 獨立頁，另需在 `data/asset_context_records.json` 或報告模板補一句含 `gas`/`tokenomics`/`解鎖` 等詞的敘述文字，讓 `/analyze` 報告本身也能觸發 `AnnotatedText`。
- **改哪些檔**：視實際 demo 用的報告資料源而定（需先確認評審會看 `/asset-context` 頁還是 `/analyze` 完整報告——建議先問 CEO/評審流程再排此 PR，避免白工）。
- **工時**：2-4h（待確認範圍）
- **reviewer**：harper
- 標記為**待確認優先序**，暫不列入本輪必做項目。

---

## 優先 2｜模組③ Peer/EcoLink 路徑評估（決策點，不建議本輪動工）

### 後端要先做什麼（#589/#590 真實狀態）
現況：`peer_metrics.py`／`ecolink.py`／`ecolink_connector.py` 只是**資料契約層**（dataclass + enum + `to_dict()` + 驗證），近似「定義了 JSON schema」，距離「可用 API」還缺：
1. **資料來源接線**：目前無任何 connector 真的去抓 peer 指標（如同業 TVL/交易量比較）或 ecolink 升級事件（如 Arbitrum 治理公告）；`ecolink.py` 裡的 `OFFICIAL_ECOLINK_HOSTS` 白名單顯示有設計「只信任官方來源」但沒有實際抓取/解析邏輯。
2. **API route**：`web.py` 完全沒有 `/api/peer-metrics`、`/api/impact-path`（或類似）端點，需新增 handler + OpenAPI + 認證/限流比照既有公開端點（PR #648 模組①的 `GET /api/asset-context` 可作範本）。
3. **資料落地**：peer metrics 需要「跟誰比」的清單（同 sector 幣種）與比較基準時間點；ecolink 需要「升級事件時間序列」儲存（目前無 fixture，`data/asset_context_records.json` 沒有這類欄位）。
4. **契約測試 vs 端對端**：現有 `tests/test_peer_metrics_contract.py`、`tests/test_ecolink_contract.py`、`tests/test_ecolink_connector.py` 只驗證 dataclass 契約本身（型別/驗證邏輯），不是任何實際串接的回歸測試。

**粗估工時（後端）**：
- Peer metrics API（含 fixture 資料 + route + OpenAPI + 契約→API 整合測試）：約 10-14h
- EcoLink 事件 API（含 connector 真接白名單來源或先用 fixture 假資料替代）：約 10-14h
- 兩者合計約 20-28h，且**不含**「真的去抓外部治理論壇/官網公告」這類需要爬蟲/RSS 整合的工作（若要接真實來源，工時會顯著增加，建議先用 fixture 資料上線，同模組①「先 fixture、不接完整連接器」的做法）。

### 前端能先做什麼（不等後端）
- 可先用**假資料 fixture**（比照模組① `data/asset_context_records.json` 的模式）在前端做 Peer 比較卡片與 EcoLink 時間軸的**視覺原型**（無真實 API，`docs/design` 或 Storybook 層級），供 UI/UX 先驗證但不對外 demo（避免評審誤以為是真資料）。
- **不建議**在沒有後端 API 前就把 mock 資料接進正式 `/asset-context` 或 `/analyze` 頁面對外展示，違反「誠實顯示 unknown/未接資料」原則。

### CEO 裁示點
> **是否投入 20-28h 後端工時把模組③從「資料契約」推進到「fixture-based 可用 API」？**
> - 若本輪 demo 重點是模組①②（查詢頁 + glossary），模組③可延後到下一 sprint，前端僅做視覺原型不上線。
> - 若模組③是評審這次的必看項目，需要立即排入下一 sprint 並指派後端工程師接手 #589/#590 剩餘工作（目前這兩個 issue 的「後端」部分實際上尚未真正開始，只完成了資料契約）。

---

## 優先 3｜UI/UX 收斂 polish 項目

1. **Design token 一致性**：`SectorLayerCard.tsx` 已用 `--color-tf-link`/`--color-tf-warn`/`--color-tf-border` 等 R2 token，`GlossaryTerm.tsx`/`AnnotatedText.tsx` 需比對是否吃同一組 token（未逐一核對，需在 PR-A code review 時一併檢查，避免模組①②視覺不一致，例如 popover 邊框色 vs 卡片邊框色）。
2. **導覽連貫性**：`/asset-context` 目前透過 `Header.tsx` 新增入口（PR #648），需確認從查詢頁→（若使用者想看完整報告）能否順暢跳轉到 `/analyze`（反之亦然），目前兩頁是否有交叉連結待確認（若無，屬於後續小 PR，非本輪必做）。
3. **Mobile 檢查清單**（納入 PR-A 驗收，不另開 PR）：
   - GlossaryTerm popover 在 375px 寬度不溢出（沿用 #644 的 scroll 修法）。
   - `AssetContextLookupPage.tsx` 的 `SUGGESTIONS` 快速查詢 chip 列表在窄螢幕是否換行正常（`flex-wrap` 已有，需實測非模擬）。
   - `SectorLayerCard.tsx` 的 `grid grid-cols-2` 在極窄螢幕（<360px）是否還可讀（可能需要 `sm:` 斷點微調，若實測有問題另開小 PR，工時 2h）。
4. 以上 1、3 併入 PR-A 的 code review checklist；2 若需要動代碼另開 PR-C（工時 3h，reviewer harper，可平行）。

---

## 摘要（給 CEO 一頁看板）

| 項目 | PR | 工時 | reviewer | 可平行 | 需 harper |
|------|----|------|----------|--------|-----------|
| 優先1-A：SectorLayerCard 接上 GlossaryTerm/AnnotatedText | PR-A | 6h | harper | 待 #648 merge 後 | 是 |
| 優先1-B：demo 報告文字補 glossary 詞（待確認範圍） | PR-B（可選） | 2-4h | harper | 可平行 | 是 |
| 優先2：模組③後端 fixture-based API（peer+ecolink） | 待裁示 | 20-28h | 待指派後端 | 需先裁示 | 否 |
| 優先3-C：導覽交叉連結小修 | PR-C（可選） | 3h | harper | 可平行 | 是 |

**PR 數**：本輪必做 1 個（PR-A），可選 2 個（PR-B、PR-C）；模組③不產出 PR，僅產出決策點。

**需 CEO 裁示**：
1. 模組③是否投入 20-28h 後端工時（本 sprint 或延後）。
2. 評審 demo 流程走 `/asset-context` 獨立頁還是 `/analyze` 完整報告，決定是否需要 PR-B。

**驗收共同原則**：desktop + mobile 375px 皆測；查無資料/未接資料一律誠實顯示 `unknown`／「無已知上下游關聯」等既有措辭，不得為了展示效果假造數值或關聯句。
