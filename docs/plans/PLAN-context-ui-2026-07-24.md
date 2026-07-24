# 新手脈絡功能 — 三模組 UI 開發計劃

- 撰寫：gray（CPO）
- 日期：2026-07-24
- 分支基礎：`develop`（所有 PR 打回 `develop`，不碰 `main`）
- 狀態：規劃案，尚未派工執行

## 0. 背景與抽查佐證

三個「新手脈絡」後端模組在 `develop` 已大多完成，但**前端幾乎沒有對應 UI**。抽查結果如下（皆為 `develop` 現況，非推測）：

| 模組 | 後端狀態（已抽查） | 前端狀態（已抽查） |
|---|---|---|
| ① Sector/Layer/Token Role | `src/trustforge/asset_context.py`、`asset_context_repository.py` 齊全；`web.py:5111-5122` 已把 `asset_context`/`risk_notices` 塞進 API response；`schema.py:158-159` Report 已有欄位 | `frontend/src/lib/types.ts:184` 只有 `asset_context?: AssetContext \| null` 這個 TS type，**完全沒有渲染元件**。`grep` 全 repo 找不到 SectorLayerCard 之類元件 |
| ② Glossary/標註 | `src/trustforge/glossary.py` 已有 `risk_note: str` 欄位（`tests/test_glossary_catalog.py` 覆蓋）；`term_annotations.py` 標註引擎存在，但 `web.py` **沒有**任何 `term_annotations` 輸出邏輯（grep 全 repo 為 0 命中）→ #583（Report API 契約）**仍是真的沒做**，不是 issue 過期 | `frontend/src/lib/glossaryCatalog.ts` 有 9 個詞條但**無 `risk_note` 欄位**，與 Python 端不同步；`GlossaryTerm.tsx` popover 原件存在；`AnalysisReportView.tsx` 目前**沒有任何自動標註**（純文字直出） |
| ③ Peer/EcoLink | `peer_metrics.py`、`tvl_connector.py`、`ecolink.py` 存在，但 #589（Comparison API 輸出 peer metrics）、#590（EcoLink evaluator 契約）皆 OPEN，`web.py` 無對應輸出 | `frontend/src/hermes/HermesRightRail.tsx`、`frontend/src/pages/ComparePage.tsx` grep 不到任何 `peer`/`ecolink`/`asset_context` 字樣，**完全空白** |

### 設計稿比對（`docs/design/hermes-r2-darkbridge/`，15 頁 .dc.html）

- 唯一與本次三模組直接相關的既有設計元素是 `.hrm-term` / `.hrm-tip`（虛線底線 + hover tooltip），出現在 **Analysis Result / Compare / Help Center / Trust Timeline** 等多頁，是**模組②標註樣式的既有視覺基準**（與現有 `GlossaryTerm.tsx` 的 popover 方向一致，可延伸）。
- **設計稿中沒有** Sector/Layer 卡片、Peer 比較表、EcoLink 路徑面板的既有 mockup（全文檢索 sector/token-role/settlement/gas-token/peer/ecolink 在 15 頁中無命中）。這代表模組①③的視覺是**新增規格**，需依 Design System 頁（`HERMES Design System.dc.html`）的既有 card/table token 延伸，而非照既有稿描摹 → **這點需要 CEO 或設計端確認是否要先出 mockup，或授權工程依現有 Design System 元件自行組裝**（見 §5 待裁示）。

### Issue 佐證（非憑空）

| Issue | 標題 | 狀態 | 相依 |
|---|---|---|---|
| #579 | Analyze API 加入 asset_context/risk_notices | OPEN（但**程式碼已實作**，`web.py:5111-5122`，issue 應可視為待補驗收證據後關閉，非阻塞前端） | depends #576（AssetContext repository，OPEN） |
| #583 | Report term_annotations 契約 | OPEN，**真的沒做**（web.py 無輸出） | depends #578（標註引擎，**CLOSED**已完成） |
| #585 | Sector/Layer/Token Role 卡片 UI | OPEN | depends #579 |
| #588 | AnnotatedText + glossary popover | OPEN | depends #583 |
| #591 | Peer comparison desktop/mobile UI | OPEN | depends #589（OPEN） |
| #592 | EcoLink impact path panel | OPEN | depends #590（OPEN） |

### PR #627（AnnotatedText，已 approve 未 merge）——關鍵發現

`gh pr diff 627` 抽查確認：

- 新增 `AnnotatedText.tsx` + `lib/annotatedText.ts` 的 `findGlossaryAnnotations()`：**前端本地字串比對**（最長匹配、非重疊、ASCII word-boundary），**不吃後端 `term_annotations` 欄位**，完全繞過 #583 的 API 契約。
- 改動點：`AnalysisReportView.tsx`（市場結論/限制/could_flip/contrarian 四處）、`FactsInferenceLadder.tsx`（事實/推論/結論三處）、`KeyBasisList.tsx`（claim/explanation）全部包上 `<AnnotatedText>`。
- `GlossaryTerm.tsx` 同時補了 popover 定位邏輯（viewport clamp，含窄螢幕測試）。
- 未使用 `dangerouslySetInnerHTML`，改用 React node 陣列拼接 —— **符合 #588 驗收條件「不得使用未清理 dangerouslySetInnerHTML」**，可行性文件中提示的 XSS 風險在此 PR 中**已規避**（純文字 slice + React 元素，非 HTML 字串注入）。
- 已有 Vitest 覆蓋（含窄 viewport popover clamp 測試）、lint、build、eye scan 都跑過，PR 描述附驗證指令。
- **待補**：`glossaryCatalog.ts` 詞條缺 `risk_note`，且只有 9 個詞（gas_fee/tokenomics/unlock_sell_pressure 等），需與 `glossary.py` 詞表核對是否有遺漏 term_id。

### 與 #627 的協調結論

**建議：先收（merge）#627，不 supersede。** 理由：
1. approve 狀態、測試/lint/build/eye scan 已跑過，架構決策（本地比對取代後端 `term_annotations` API）合理且已規避 XSS——重做只是浪費已驗證的工作。
2. 唯一缺口是 `risk_note` 未同步到前端 catalog，這是**小補丁**而非重做，排入 Phase A 第一張 PR 即可。
3. 這代表 **#583（Report term_annotations 契約）在前端策略上可以降階**：既然 #627 走純前端比對路線，#583 的後端 API 契約**不是 #588 UI 的硬阻塞**，可以解除 #588→#583 的相依關係，改標記 #583 為「未來若要做跨語言/後端可控標註再做」的獨立技術債，不卡本次 UI 交付。**此為架構偏離既定 issue 相依圖的決策，列入 CEO 待裁示點（見 §5）**。

## 1. 分階段規劃

### Phase A — 模組②　Glossary 標註 UI（優先，後端最完整、風險最低、可視化最快）

| PR | 標題 | 改動檔案 | 工時 | Reviewer | 可否平行 |
|---|---|---|---|---|---|
| A1 | `chore(review): merge #627 AnnotatedText into develop` | 無新改動，僅解衝突、對齊 develop 最新 lint/build | 1h | codex | 否，A2/A3 需先合併 |
| A2 | `feat(glossary): sync risk_note 與詞表到 glossaryCatalog.ts` | `frontend/src/lib/glossaryCatalog.ts`（補 `risk_note` 欄位、核對 `glossary.py` 全詞表無遺漏 term_id）、`GlossaryTerm.tsx`（popover 內顯示 ⚠️ risk_note，risk_note 為空字串時不顯示區塊） | 6h | codex | 可與 A3 平行（各自檔案不重疊，risk_note 顯示邏輯在 A2 完成後 A3 UI 才有東西吃，故建議序列） |
| A3 | `feat(ui): glossary popover risk_note 顯示 + mobile/keyboard 驗收` | `GlossaryTerm.tsx`、`GlossaryTerm.test.tsx`（新增 risk_note 顯示測試、375/390 viewport clamp 測試、keyboard Tab/Escape 測試） | 4h | qa-lead 指派 E2E reviewer | 依賴 A2 完成 |

**Phase A 驗收（每張 PR 共同標準）：**
- desktop + 375×667 / 390×844 mobile 無橫向溢位（沿用 #627 既有 clamp 邏輯驗證）
- popover 可用鍵盤（Tab 聚焦、Enter/Space 開啟、Escape 關閉）與觸控開啟
- `risk_note` 為空字串時誠實不顯示（不得顯示空 `<b></b>` 之類殘影），缺值/stale 需誠實呈現而非留白造成誤解
- `npm --prefix frontend test`、`npm --prefix frontend run lint`、`npm --prefix frontend run build`、`git diff --check` 全過
- 安全標記：**不需要** harper/CISO 雙審（本階段全部走 React 節點拼接，未使用 `dangerouslySetInnerHTML`，A1 merge 時需再次確認 develop 最新版仍無此 API 使用，若有變動則升級為需雙審）

Phase A 合計工時：**11h**（A1 串行 1h → A2/A3 串行 10h），實際 wall-clock 若 A2/A3 同一人接續約 **11h**，若拆兩人 A2→A3 交接仍需序列（因果相依），**wall-clock ≈ 11h**。

---

### Phase B — 模組①　Sector/Layer/Token Role 卡片 UI

依 #585 acceptance，前置為 #579（API 已有程式碼但 issue 未關閉——**執行前需先確認 #579 是否要補驗收證據關閉，或視為已可用直接開工**，此為執行面小事項，非本計劃阻塞）。

| PR | 標題 | 改動檔案（預估） | 工時 | Reviewer | 可否平行 |
|---|---|---|---|---|---|
| B1 | `feat(ui): 新增 SectorLayerCard 元件（[Layer 2] badge + 上下游/gas_token/token_role 卡）` | 新增 `frontend/src/components/SectorLayerCard.tsx` + test；`frontend/src/lib/types.ts` 補齊 `AssetContext` 相關型別若有缺；`AnalysisReportView.tsx` 掛載點 | 10h（issue 上限） | codex + product-manager 對 acceptance | 可與 Phase A 完全平行（不同檔案、不同模組） |

**Phase B 驗收**：unknown/stale/legacy payload fail-soft 顯示（例如缺 `gas_token` 時顯示「資料不足」而非 crash 或留白）、上下游與風險提示可追來源（需附連到 evidence/citation 的 UI 掛勾）、desktop/mobile/a11y component tests。

**安全標記**：純資料渲染卡片，無 HTML 注入風險，**不需要**雙審。

---

### Phase C — 模組③　Peer 比較 + EcoLink UI（依賴後端 #589/#590 尚未完成，工時最大）

| PR | 標題 | 改動檔案（預估） | 工時 | Reviewer | 可否平行 |
|---|---|---|---|---|---|
| C1 | `feat(ui): 同層 peer comparison desktop table + mobile card`（對應 #591） | 新增 `frontend/src/components/PeerComparisonPanel.tsx`（或掛在 `ComparePage.tsx`）+ test | 12h（issue 上限，若拆分需再拆） | codex | 需 #589 後端先出 API，**本計劃只排前端**，前端可先用 mock fixture 開發，正式串接需後端完成 |
| C2 | `feat(ui): Eco-Link impact path panel`（對應 #592） | 新增 `frontend/src/components/EcoLinkPanel.tsx`（掛 `HermesRightRail.tsx`）+ test | 10h（issue 上限） | codex | 同 C1，依賴 #590，可與 C1 平行開發（各自獨立面板） |

**Phase C 驗收**：清楚顯示方法/時間戳/stale/missing 狀態、375/390 無橫向溢位、EcoLink 每段可查看 supporting/contrarian evidence、鍵盤可展開路徑。

**安全標記**：純資料渲染，無 `dangerouslySetInnerHTML` 需求，**不需要**雙審；但因涉及跨資產影響推論（可能被誤讀為財務建議），**建議** product-manager 對外顯字句過一輪文案審查（非安全審查，屬 CPO 內部把關，非 CEO 裁示點）。

---

## 2. 平行 Wall-clock 估算

- Phase A（模組②）：11h，序列，**優先立即開工**（後端已備妥，無外部依賴）。
- Phase B（模組①）：10h，可與 Phase A **完全平行**（不同工程師/不同檔案）。
- Phase C（模組③）：前端本體 12h + 10h = 22h，但**受後端 #589/#590 進度阻塞**（尚未完成），若後端與前端同步啟動，前端可先用 mock fixture 開發不等後端，最終串接需等後端交付。

**若三軌同時起步（A/B 立即可做，C 先行 mock 開發）：**
- 關鍵路徑 = max(Phase A 11h, Phase B 10h, Phase C 前端 22h) = **22h**（Phase C 為瓶頸，且其正式驗收仍需後端 #589/#590 完成後才能真正跑通串接測試，實際上線時間 = 後端完成時間 + C 收尾）。
- 若人力只有 1-2 人輪替（非三線並行），總工時序列相加 = 11 + 10 + 22 = **43h**。

---

## 3. 需 CEO 裁示點

1. **#627 走純前端字串比對，是否正式取代 #583 後端 `term_annotations` API 契約？**（本計劃建議：是，#583 降階為未來技術債，不卡本次交付。這是偏離既定 issue 相依圖的架構決策，需 CEO 拍板是否同意此偏離，或要求仍補上 #583 後端契約再讓前端切換。）
2. **模組①③（Sector/Layer 卡片、Peer 比較表、EcoLink 面板）在 `docs/design/hermes-r2-darkbridge/` 中無既有 mockup**，是否需要先補設計稿，還是授權工程依 Design System 既有 token 自行組裝後再由設計驗收？（影響 Phase B/C 是否可以直接開工，或需先插入設計稿產出的前置步驟。）
3. **Phase C 依賴的 #589（Comparison API peer metrics）/#590（EcoLink evaluator 契約）皆為 OPEN 的後端工作**，不在本 CPO UI 計劃範圍內——需 CEO/CTO 側確認後端排期，否則 Phase C 前端只能停在 mock fixture 階段，無法正式驗收。
4. 是否同意 **A1（merge #627）作為 Phase A 開工前置**，而非重做 AnnotatedText？（已附程式碼/測試/驗證指令佐證，本計劃判斷為可收。）

---

## 4. 明確排除

- 不涉及技術實作（本文件為計劃，不 Edit/Write 任何 code）。
- 不涉及 `main` 分支合併排程（有專人負責）。
- 不涉及後端 #576/#578/#589/#590 的實作排期（屬 CTO/工程排程，本計劃只排前端 UI）。
- 不涉及定價/行銷文案。
