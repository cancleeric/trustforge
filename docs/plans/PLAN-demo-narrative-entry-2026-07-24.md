# Demo 敘事整合入口 — 執行計劃（2026-07-24）

> 作者：gray（CPO）。承接 `docs/plans/PLAN-next-competition-readiness-2026-07-24.md` 項目 2，CEO 已核准動工。
> 基準：`develop`。範圍：本文件只做規劃，不含程式碼改動；PR 一律打回 `develop`。
> 目標：讓「資產脈絡查詢 `/asset-context`、EcoLink `/eco-link`、Peer 比較 `/peer-metrics`」三大新手模組，在評審 **30 秒 demo 動線**內被看見、有敘事串接，而非孤立於 Header 右側小字連結。

## 0. 動工前查證摘要

- **`frontend/src/components/Header.tsx`**：主 nav 是 5 個核心頁籤（HERMES/Analyze/Compare/History/Sources/Costs，含底線 active 態，`navItems` 陣列驅動）。三模組（`/asset-context`、`/eco-link`、`/peer-metrics`）是額外掛在 header 右側的 `<Link>`，樣式為 `text-xs text-tf-muted`，視覺權重明顯低於主 nav，彼此無串接、無敘事引導。
- **`frontend/src/pages/`**：三模組各自獨立頁面（`AssetContextLookupPage.tsx`、`EcoLinkPage.tsx`、`PeerMetricsPage.tsx`），均已有對應測試檔，功能完整但入口薄弱。
- **主畫面現況**：`/` 路由是 `HermesDashboard.tsx`（597 行，星系視覺化主戰情室），已內建成熟的「首次使用引導」基礎設施——`HermesFirstRun`（`firstRunOpen` 狀態，`shouldShowHermesOnboarding()` 判斷）、`HermesOnboarding`（`onboardingOpen`，點 Help 開啟）、`beginnerMode`（cookie 驅動的新手/完整模式切換）。這代表「新手敘事卡片」不是要從零蓋，而是**擴充既有的新手體驗骨架**。
- **R2 設計稿比對**：`docs/design/hermes-r2-darkbridge/HERMES Onboarding.dc.html` 已有現成的「3 步驟卡片」視覺樣式（`steps` 陣列：01 選定目標 → 02 跨源驗證 → 03 讀懂信任，卡片用 `var(--card)` 背景 + `var(--border)` + 菱形編號徽章 + icon + title + desc，`grid-template-columns: repeat(3, 1fr)`），與 tf design tokens（`--color-tf-*`）可直接對稿。**R2 稿沒有現成的三模組專屬入口設計**，但既有 3 步驟卡片的視覺語言（編號徽章、卡片網格、CTA 箭頭按鈕）可直接套用改編到「新手 3 步：查代幣定位 → 名詞解釋 → 同層/生態」——授權工程依此既有 pattern 組裝，非憑空新設計。
- **`HERMES Help Center.dc.html`** 有稿，`/help` 已存在，可作為敘事卡片「查看完整說明」CTA 的既有落點，不需新增頁面。

## 1. 方案比較

| 方案 | 做法 | 優點 | 風險/缺點 |
|---|---|---|---|
| A. 升級進主導覽 | 三模組併入 `Header.tsx` 的 `navItems`，變成第 6-8 個頁籤或加一個「新手脈絡」下拉分組 | 全站任一頁都能看到，權重最高 | 5 個核心頁籤是評分主線（分析/比較/歷史/來源/成本），硬塞 3 個「加分展示面」模組進同一視覺層級，會**稀釋主線頁籤的識別度**，且已查證（見上一份計劃）這三模組**不接評分主線的 report 生成**，用主 nav 頁籤等級曝光有「過度包裝 fixture-based 展示功能」之虞，違反誠實揭露精神的隱性風險 |
| **B. 主 dashboard 敘事卡片區（首選）** | 在 `HermesDashboard.tsx`（`/`，評審打開網址第一眼就看到的畫面）新增一個「新手 3 步」卡片區塊：查代幣定位（→ `/asset-context`）→ 名詞解釋（→ `/help` 或既有 tooltip）→ 同層/生態比較（→ `/peer-metrics` + `/eco-link`），套用 R2 稿既有的 3 步驟卡片視覺語言 | 落在評審**第一屏**，不需要先做一次分析才看到；重用既有新手體驗骨架（`beginnerMode`/`HermesFirstRun` 已驗證此頁面接受這類卡片區塊）；不動主 nav 不稀釋評分主線頁籤；工時可控 | 需要在已經很複雜的 597 行 `HermesDashboard.tsx` 裡找乾淨插入點，避免和星系視覺化搶版面；mobile 版面需另外收斂（該頁已有 `HermesMobileDivergenceEntry`，需確認共存） |
| C. 整合頁串成一條 ARB 動線 | 新增一個頁面，把三模組串成「選一個代幣 → 查資產脈絡 → 看生態關聯 → 比同層」的單一動線頁 | 敘事最完整、最適合「demo 講一個故事」 | 新增頁面 = 新路由 + 新導覽入口，等於同時要解 A 的「入口去哪」問題，工時墊高（≥12h 有風險）；且評審 30 秒未必會主動點進第 4 個非主線頁面，除非有人帶著講 |

## 2. 建議：首選方案 B（主 dashboard 新手 3 步敘事卡片），不做 A/C

理由：
1. **命中「30 秒看得到」的硬需求** — B 是唯一落在首屏（`/` 首次載入）的方案，不需要評審先做完一次分析或主動點進次要頁面才看得到；A 和 C 都要評審多一步操作或多一次視覺掃描才會注意到。
2. **不稀釋評分主線** — 三模組已查證非評分主線必要輸入（fixture-based、非接 report 生成），維持在「新手引導卡片」層級曝光，而非拉到與 Analyze/Compare 同等的主 nav 頁籤，符合誠實揭露精神，也避免評審誤以為這三個是核心分析能力而去深度質疑資料真實性。
3. **重用既有骨架、工時最省** — `HermesDashboard.tsx` 已有 `beginnerMode`/`HermesFirstRun`/`HermesOnboarding` 三層新手體驗機制，且 R2 稿的 3 步驟卡片視覺語言可直接套用改編，不必新設計、不必新路由，PR 拆解可控在 ≤12h 內完成。
4. Header 右側小字連結**維持保留**（不刪除），作為進階使用者/評審事後複查的次要入口，不與新卡片區衝突。

不建議 A：與主線頁籤混淆評分焦點。不建議 C：新頁面+新入口的複合工時風險超過 12h 上限，且不如 B 落地快、見效直接。若 CEO 認為 demo 現場需要更完整的「一條龍動線」，可將 C 列為賽後加做項，不在本輪。

## 3. PR 拆解

| PR | 內容 | 改動檔案 | 工時 | Reviewer | 需 harper? | 可平行? |
|---|---|---|---|---|---|---|
| **PR1：新手 3 步敘事卡片區塊（元件）** | 新增 `HermesBeginnerNarrative.tsx`（暫名）元件：3 張卡片（查代幣定位/名詞解釋/同層生態），套用 R2 稿 3 步驟卡片視覺（編號徽章、`--color-tf-*` token 對稿 R2 `--card`/`--border`/`--cyan`），每卡帶 CTA 導向 `/asset-context`、`/help`（或 `/asset-context` 內建 tooltip）、`/peer-metrics`+`/eco-link`；i18n key 走 `useHermesI18n` | 新增 `frontend/src/hermes/HermesBeginnerNarrative.tsx`；新增/擴充 `frontend/src/hermes/hermesI18n` 對應 key；新增 `HermesBeginnerNarrative.test.tsx`（渲染 + CTA href 正確性） | 4h | 前端：CTO 線工程師實作 → product-manager 驗收文案/動線是否清楚（非技術審） | 否 | 是（獨立元件，可先行） |
| **PR2：插入 HermesDashboard 版面** | 把 PR1 元件插入 `HermesDashboard.tsx` 乾淨插入點（建議：`beginnerMode===true` 且未展開任何 module 時，星系視覺化下方或 `HermesTopBar` 與星系之間新增一個可收合區塊；`beginnerMode===false`/展開 module 時不顯示，避免干擾進階操作與 demo 深水區畫面）；確認與 `HermesMobileDivergenceEntry`（既有 mobile 入口）共存不衝突 | `frontend/src/pages/HermesDashboard.tsx`；可能微調 `frontend/src/pages/HermesDashboard.test.tsx` | 4h | CTO 線工程師 → product-manager 驗收動線清楚度 + qa-lead 驗收無回歸 | 否 | 依賴 PR1 合併（不可平行於 PR1，可平行於 PR3） |
| **PR3：跨裝置驗收 + E2E 回歸** | Playwright E2E：桌面 1440px + 手機 375px 兩種 viewport 快照/互動測試，驗證（a）卡片區在兩種尺寸下無文字/邊框溢位，（b）三個 CTA 點擊後正確導向對應路由，（c）`beginnerMode` 關閉時卡片區不顯示不影響既有進階畫面，（d）不破壞既有 `HermesDashboard.test.tsx`/`Header.test.tsx` 全綠 | 新增 `frontend/tests/e2e/hermes-beginner-narrative.spec.ts`（或既有 e2e 目錄慣例路徑）；跑 `npx playwright test` 全量回歸 | 3h | qa-lead 主責 | 否 | 依賴 PR1+PR2 合併後執行，不可平行 |

**合計工時：11h**（≤12h 上限內）。

## 4. 驗收標準（每 PR 共通門檻）

1. **Desktop（≥1280px）與 Mobile（375px/390px）皆無文字溢位、無卡片擠壓、無橫向 scrollbar 意外出現**。
2. **動線清楚**：三張卡片標題可一眼辨識「查代幣定位 / 名詞解釋 / 同層生態」對應到哪個模組，CTA 文案明確（非純圖示），點擊後導向正確路由。
3. **沿用 R2 Design System token**：顏色/圓角/邊框沿用 `frontend/src/index.css` 或既有 tailwind config 中對稿 `docs/design/hermes-r2-darkbridge/` 的 `--color-tf-*` 變數，不得新增未定義的硬編碼色碼。
4. **不破壞誠實態揭露**：卡片文案不得暗示三模組資料為即時/官方權威來源（沿用既有頁面內 `illustrative: true` 揭露語氣），避免過度包裝。
5. **既有測試全綠**：`Header.test.tsx`、`HermesDashboard.test.tsx`、三模組各自 `*.test.tsx` 不因本次改動回歸失敗。
6. **`beginnerMode` 語意一致**：新卡片區只在新手情境出現，不干擾已切換「完整模式」的進階使用者/評審深水區操作流程。

## 5. Reviewer 與流程

- 前端實作：CTO 線工程師（沿用既有 `HermesDashboard.tsx`/`hermes/` 目錄 owner）。
- 動線/敘事把關：**product-manager**（驗收文案、CTA 清楚度、是否真的解決「30 秒看得到」問題）。
- 品質門檻：**qa-lead**（PR3 E2E 主責，並在 PR2 合併後跑一次既有回歸套件）。
- **不需要 harper**：本計劃不涉及安全/權限/資料存取變更，純前端 UX 展示層。
- 三個 PR 皆打回 `develop`，不合併 `main`。

## 6. 需 CEO 裁示點

1. **是否核准首選方案 B**（主 dashboard 新手 3 步敘事卡片），並否決本輪動工 A（升級進主導覽）與 C（獨立整合頁）？
2. **PR2 插入點的取捨**：是否同意卡片區只在 `beginnerMode===true` 顯示（新手模式），完整模式使用者/評審深水區操作時不顯示？或 CEO 希望不分模式恆常顯示（需額外評估與星系視覺化版面衝突的風險與工時）？
3. **CTA「名詞解釋」導向 `/help` 頁 或 `/asset-context` 頁內建 tooltip**：兩者皆可，工時差異在 PR1 的 0.5-1h 內，需 CEO/product-manager 決定哪個更貼近「評審會主動點」的直覺（若 CEO 無特別偏好，預設走 `/help`，因為該頁已有 R2 對稿設計，風險最低）。
4. 是否同意**授權工程依既有 R2 3 步驟卡片視覺語言組裝**（因 R2 稿無此三模組專屬入口對應稿），最終由 CEO Chrome 親測把關（沿用上一份計劃已核准的授權路徑）？

---

## 一頁摘要（供 CEO 快速裁示）

- **建議方案**：B（主 dashboard `/` 首屏新增「新手 3 步」敘事卡片區：查代幣定位→名詞解釋→同層生態，分別導向 `/asset-context`、`/help`、`/peer-metrics`+`/eco-link`），不做 A（升級主 nav，會稀釋評分主線）、不做 C（獨立整合頁，工時風險超標且見效不如首屏直接）。
- **PR 拆解**：PR1 新手卡片元件（4h，可先行）→ PR2 插入 HermesDashboard 版面（4h，依賴 PR1）→ PR3 跨裝置 E2E 回歸（3h，依賴 PR1+PR2）。**合計 11h**，PR1 可與其他工作平行，PR2/PR3 需序列。
- **Reviewer**：前端 CTO 線工程師實作；product-manager 驗收動線/文案；qa-lead 驗收 E2E 與回歸。**不需 harper**（純前端 UX，非安全變更）。
- **裁示點**：(1) 核准方案 B、否決 A/C；(2) 卡片區只在 `beginnerMode` 顯示 vs 恆常顯示；(3) 「名詞解釋」CTA 導向 `/help`（預設）或 `/asset-context` 內建 tooltip；(4) 授權工程依 R2 既有 3 步驟卡片視覺語言組裝、CEO Chrome 親測收尾。
