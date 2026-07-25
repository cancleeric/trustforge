# TrustForge UI/UX 本輪開發計劃（2026-07-25）

- 角色：CPO（gray）
- 狀態：**規劃文件，未動 code**（依 CEO 指示：只產出計劃供審）
- Repo：`/Users/yinghaowang/HurricaneSoft/trustforge`，分支 `develop`
- 驗證基準：`develop` HEAD `85fee2b1`（2026-07-25 09:16:48 +0800）
- 前端基準：`npm run build` 綠、`NODE_ENV=development npx vitest run` → **48 test files / 346 tests 全過**（本機重跑，見附錄）
- 範圍與順序：CEO 已裁示 #541 → #542 → #361 → #564 → #362，本文件依此排列，但**每項先給「重新驗證後的現況」再給改法**——因為重新驗證發現兩項（#541、#564）在 develop 上已經被更早的 PR 修好，一項（#362）比 CEO 掌握的資訊更嚴重。

---

## 0. 重大發現（先讀這段，會改變工作分配）

| # | CEO 裁示前提 | 重新驗證後現況 | 影響 |
|---|---|---|---|
| **#541** | 「真缺口是下載 payload 未包含 `data_lineage`」 | **已修好**。PR #547（`Closes #541`，commit `07cfb9d`，2026-07-23 13:39 merge）已把 `data_lineage` 加進 `SnapshotModal` 的 payload、畫面渲染與下載 JSON，且有專屬測試 `SnapshotModal.test.tsx`。Issue #541 在 GitHub 上仍顯示 OPEN，是因為 `Closes #541` 只有合併進**預設分支**（`main`）才會自動關閉，PR 合併進的是 `develop`，尚未 promote 到 `main`——不是程式碼還沒修。 | **不需要新開發**。建議只需（a）CEO/QA 在 develop 上親測一次收尾，（b）手動關閉 #541 並附證據，不必再排 ≤8h 工時。#542 的「相依 #541」條件視為**已滿足**，可直接開工，不必等。 |
| **#564** | PR #561 head `5d3fe9e` 在乾淨安裝環境 `npm ci --include=dev` 失敗（lockfile 對不上 `@emnapi/*`） | **在 develop 上已不重現**。原因：PR #561 分支切出時早於 PR #547 的「fix: sync frontend lockfile for snapshot lineage」子提交，兩者各自獨立合併，但 #547（13:39）先於 #561（14:28）進 develop，#561 合併進 develop 時已經疊在修好的 lockfile 之上。本輪在乾淨 clone（`/private/tmp/.../trustforge-564-test`）跑 `npm ci --include=dev` → 176 packages、0 錯誤；`npm run build` → 綠；`NODE_ENV=development npm test -- --run src/components/TrainingStatusCard.test.tsx` → 4/4 過（見附錄逐字輸出）。 | **不需要新開發**。建議請原 reviewer（Samantha）或 CEO 在 develop HEAD 上重跑同一組驗證指令，通過即可關閉 #564，不必進實作。 |
| **#362** | CEO 引用「blocker #540 已關（幾何親驗完成）」，本輪列為 P2 墊底 | **目前是 FAIL，不是綠燈**。`frontend/scripts/verify-mobile-geometry.mjs`（#540 的產物）在 develop HEAD 重跑兩次，結果一致：375×667／390×844 共 **7 項斷言失敗**（topbar/stageBar 與 content 重疊、`analyze-module` 的 `moduleDeck` 不可見）。#540 close comment 聲稱「gate is green」與目前重現結果**不一致**；`git log 0bdf020..HEAD` 顯示 #540 之後有 4 個 commit 動過 `HermesDashboard.tsx`/mobile 相關區塊（含新增 `HermesMobileDivergenceEntry`、beginner narrative），研判是這之後才回歸。 | **不能只因 CEO 前提「已關」就當作可選降階**。CEO 決定 P2 墊底、桌面 demo 優先是業務判斷、本計劃尊重排序，但**必須讓 CEO 知道目前是真實 FAIL，不是「已驗證安全，有空再做」**。建議：進場前 CTO 依原訂「Playwright `--viewport-size 375,667` 重現」步驟執行——本文件已提前完成此步驟，可直接沿用附錄輸出作為 fixture，不必重跑一次「確認是否要修」，可以直接進"要怎麼修"。 |

---

## 1. #541 — Snapshot Modal 補齊 `data_lineage`

### 現況（file:line，已完成，非待辦）
- `frontend/src/components/SnapshotModal.tsx:16-27` `buildPayload()` 已含 `data_lineage: ev.data_lineage ?? null`
- `frontend/src/components/SnapshotModal.tsx:41-57` `buildPayloadLines()` 已把 `data_lineage` 加進畫面顯示的逐行 JSON（含 `null` 態的顏色區分）
- `frontend/src/components/SnapshotModal.tsx:165-203` LINEAGE 區塊已渲染 `file / dataset_role / coverage / analysis_window / trading_pair / rows / time_basis / interval / columns / sha256` 全部既有欄位；無 `data_lineage` 時顯示誠實文案（202 行：「此筆為即時 API 擷取，無檔案型可重現血緣鏈…」），不捏造
- `frontend/src/components/SnapshotModal.test.tsx:1-50+` 已有測試同時驗證畫面渲染與下載 JSON 內容一致
- `frontend/src/components/EvidenceTable.tsx:47-54` 證據列的「原始快照」入口正常開啟 `SnapshotModal`

### 改法概要
不需要程式改動。收尾動作：
1. CEO 在本機 dev（如 `localhost:517x`）親測任一含 `data_lineage` 的證據列 → 開 Snapshot → 下載 JSON，確認欄位齊全。
2. 附截圖/下載檔到 #541，手動關閉 issue（因為 `Closes #541` 未觸發自動關閉）。

### 相依/順序
- 無相依，領頭。**#542 依賴的是「#541 範疇完成」，不是「issue 狀態變成 closed」**——程式碼層面已完成，#542 可立即並行/接續開工，不必等 issue 手動關閉這個行政動作。

### 驗收條件
- [ ] `NODE_ENV=development npx vitest run --run src/components/SnapshotModal.test.tsx` 綠（本輪已驗證 2 個相關案例通過，屬於本文開頭 346 tests 的子集）
- [ ] CEO 親測：任一有 `data_lineage` 的證據列，Modal 顯示全部欄位、下載 JSON 內容與畫面一致
- [ ] 無 `data_lineage` fixture（如即時 API 來源）不 crash，顯示中性說明文字

### 風險
- 低。唯一風險是誤判「issue open = 沒做」導致重工——本文件已排除此風險。

---

## 2. #542 — 首頁 Breakdown Drawer 對齊證據／分歧／JSON 匯出

### 現況（file:line）
- **入口**：`frontend/src/pages/HermesDashboard.tsx:569-570` `onOpenComposite`/`onOpenDivergence` 設定 `selectedStage`；`frontend/src/pages/HermesDashboard.tsx:584-588` 掛載 `<StageDrilldown>`。`frontend/src/hermes/StageBar.tsx:73-94` 的 `hermes-energy-nodes` 5 個節點按鈕（含 `composite`／`crossverify`）在桌面與手機都渲染（`hermes-energy-deck` 不受 `>560px` 的 leftRail/rightRail 隱藏規則限制），所以composite drawer 在手機上本來就可觸達，不需要另建手機專屬入口（divergence 另有 `frontend/src/hermes/HermesMobileDivergenceEntry.tsx` 是額外的醒目 CTA，非必要條件）。
- **資料已就緒**：`frontend/src/hermes/StageDrilldown.tsx:39-41` 已經取得完整 `analysis = telemetry?.analysis`（型別 `AnalyzeData`，`frontend/src/lib/types.ts:367-377`），內含 `evidence: Evidence[]`、`trust_radar`、`execution_log: ExecutionEvent[]`、`report.cross_source_signal`。composite drawer **不缺資料**，缺的是這段資料在畫面上的呈現。
- **真缺口 1（evidence rows 缺失）**：`frontend/src/hermes/StageDrilldown.tsx:207-226` `selectedStage === 'composite'` 區塊只渲染 `derivation.components`（四軸權重列）與 `derivation.steps`（reasoning trace），**完全沒有 evidence rows、沒有 SnapshotModal 入口**。對照完整分析頁 `frontend/src/components/AnalysisReportView.tsx:142,146` 已經用同一份 `data.evidence` 掛 `<EvidenceTrailPanel>` + `<EvidenceTable>`（含 SnapshotModal 入口，見上面 #541 段落），composite drawer 沒有重用這兩個既有元件。
- **真缺口 2（分歧分解不誠實）**：`frontend/src/hermes/StageDrilldown.tsx:66` `crossItems: (report?.key_basis ?? []).slice(0, 4).map((item) => ({ stance: 'EVIDENCE', claim: item.claim, source: item.explanation, color: HERMES_CYAN }))` ——`stance` 欄位寫死字串 `'EVIDENCE'`，`source` 欄位塞的其實是 `explanation` 文字，**不是真的偏多/偏空立場分組**。而 `frontend/src/components/EvidenceTrailPanel.tsx:74-75` 搭配 `frontend/src/lib/stancePairs.ts` 的 `groupByStance(signal)` 已經是後端 `distinct_sources` 去重過、真正區分 `bullish`/`bearish` 陣營來源數的既有邏輯，且有明確的「防止同來源重複計入」設計註解。composite/divergence 區塊（`frontend/src/hermes/StageDrilldown.tsx:179-194`）目前完全沒用到這套函式。
- **真缺口 3（無 JSON 匯出）**：composite drawer 內沒有任何下載按鈕。對照 `frontend/src/components/HermesExecutionPanel.tsx:182-187` 完整分析頁已有「報告 / Evidence / Log」三顆下載鈕（`download()` helper 於同檔 50-57 行），可以照抄同一 pattern，資料源改成 `{ evidence: analysis.evidence, trust_radar: analysis.trust_radar, execution_log: analysis.execution_log }`。

### 改法概要（重用既有元件，不造第二套真相）
1. `StageDrilldown.tsx` 的 `composite` 分支（207-226 行）內插入：
   - `evidence.length > 0` 時渲染 `<EvidenceTable evidence={evidence} />`（沿用 #541 已修好的 SnapshotModal），要求至少顯示 5 筆（若 `evidence.length < 5` 全部顯示 + 誠實註記「本次僅 N 筆」，不得補假資料湊數）。
   - `evidence.length === 0` 時（尚未執行正式分析／`analysis` 為 `undefined`）顯示既有 `t('proxyTrace')` 語意的空狀態文案，不僅是空白。
2. `crossItems`（66 行）改為呼叫 `groupByStance(report.cross_source_signal)`，渲染 `bullish`/`bearish` 兩組真實來源清單，取代目前寫死的 `'EVIDENCE'` 字串；`report.cross_source_signal` 為 `null` 時維持現有「本次沒有形成可報告的跨來源訊號」文案（182 行既有邏輯保留）。
3. composite 區塊底部新增一顆下載鈕，仿 `HermesExecutionPanel.tsx:50-57` 的 `download()` helper，匯出 `{ evidence, trust_radar, execution_log }` 單一 JSON 檔，檔名建議 `${run_id}-breakdown.json`，與完整分析頁用同一個 `analysis` 物件、同一個 run，天然保證「JSON 下載內容與畫面同一 snapshot/run」。
4. `frontend/src/hermes/HermesRightRail.tsx:108-115`「查看完整拆解與推理」按鈕文案不變，仍是入口，不需改動。

### 相依/順序
- **依賴 #541 的程式碼結果（已滿足，見第 0 節），不依賴 #541 issue 關閉狀態**。可視為即刻可開工，與 #541 的收尾動作（issue 關閉、CEO 親測）**並行**執行。
- 與 #361（第 3 項）**互相獨立、可並行**（不同檔案：#542 動 `StageDrilldown.tsx`；#361 動 `HermesDashboard.tsx`/`HermesTopBar.tsx`/`hermesI18n.tsx`，若 #361 定案落在 TopBar 區域則無交集；若定案落在 RightRail 區域則需留意跟 `HermesRightRail.tsx` 的改動視窗錯開，避免同檔衝突——見第 3 項風險）。
- 應**先於 #362**排（CEO 已如此排），因為 #542 會在 composite drawer 內新增 evidence rows/下載按鈕，這些新元素之後必須被 #362 的 mobile overflow 修復一併涵蓋，若順序顛倒，#362 驗證會漏掉這批新內容。

### 驗收條件（含 Playwright 親驗）
- [ ] 桌面 1440×900：對某幣執行一次正式分析（非 proxy 態）後開 composite drawer，看到 ≥5 筆 evidence rows（或不足 5 筆時的誠實提示），任一列可開 SnapshotModal 並看到含 `data_lineage` 的完整內容
- [ ] 分歧分組顯示 `groupByStance` 算出的偏多/偏空來源數與清單，不再出現寫死的 `'EVIDENCE'` 字串
- [ ] 下載按鈕產出的 JSON 內 `evidence`/`trust_radar`/`execution_log` 三個 key 皆存在且與畫面同一 run
- [ ] 無 evidence（尚未跑正式分析）時開 composite drawer：不 crash、顯示中性空狀態，不得顯示假造 evidence
- [ ] Playwright `--viewport-size=375,667` 開 composite drawer：新增的 evidence rows/下載按鈕不造成 `document.documentElement.scrollWidth > clientWidth`（橫向溢出），且 drawer 內可縱向捲動看到全部新內容（可延伸 `frontend/scripts/verify-mobile-geometry.mjs` 的 `analyze-module` route 或另開一條 `composite-drawer` route 斷言）
- [ ] Esc / 點擊背景關閉、focus trap 行為維持既有（`frontend/src/hermes/StageDrilldown.tsx` 現有 `onClose` 邏輯不可破壞）
- [ ] 新增元件測試（目前無 `StageDrilldown.test.tsx`/`EvidenceTable` 在 drawer 情境下的測試，需新增，覆蓋「有 evidence」「無 evidence」「下載內容」三種情境）
- [ ] `npm run build`、`NODE_ENV=development npx vitest run` 全綠（不得低於本文基準 48 files/346 tests，只能增不能減）

### 風險
- **資料面**：`evidence`/`data_lineage` 皆為既有真實欄位直通渲染，不新增資料來源，風險低；但若 `report.cross_source_signal` 在極端情況下欄位不全（如 `stance_pairs` 缺 `distinct_sources`），`groupByStance` 的 fallback 邏輯（見 `stancePairs.ts` 註解）已處理過，需在測試中覆蓋這條 fallback 路徑，避免誤判為新 bug。
- **UI 密度風險**：composite drawer 目前是彈窗形式（`hermes-clip-lg hermes-stage-drilldown`），塞入 EvidenceTable（含 `<details>` 展開的長列表）後高度可能大增，需確認 drawer 本身有 `overflow-y: auto`（`frontend/src/hermes/StageDrilldown.tsx:104` 已有 `overflowY: 'auto'`，风险可控但要親測不要被外層 `hermes-root { overflow: hidden }`（`hermes.css:504` 附近的 root 設定）卡住捲動）。
- **與 #361 撞檔風險**：若 #361 CEO 選定的落點是 RightRail（而非 TopBar），兩張票會動到相鄰 UI，建議 #361 先確認落點再排開工順序，避免 merge conflict。

---

## 3. #361 — 首頁 hero 差異化副標

### 文案（CEO 已定案）
> Variant A：「不只 AI 觀點 — 跨 150+ 來源的信任裁決，每個分數都附可稽核血統。」

### 現況（file:line）
- `frontend/src/hermes/HermesTopBar.tsx:1-107` 目前是一條**固定 44px**（`hermes.css:321` `--hermes-top: 44px`）的窄列，內容是 logo、nav、`systemId`/`version`/連線狀態徽章（62-67 行），**沒有空間直接塞一句完整標語**，需新增一條獨立 hero strip 或改變現有列高。
- `frontend/src/hermes/HermesRightRail.tsx:55` 目前最上方只有一行 10px 小字「目前焦點：{coin}」，同樣沒有 hero 級的視覺留白；且 RightRail 在 `>900px` 才顯示（`frontend/scripts/verify-mobile-geometry.mjs:121` `rightRailVisible = viewport.width > 900`），**放在 RightRail 無法滿足驗收「375×667 視口皆可見」**。
- 因此**建議落點是 TopBar 正下方新增一條全寬（桌面時扣掉 rail 寬）薄 strip**，理由：`.hermes-topbar` 與 `.hermes-energy-deck`（stageBar）是唯二在所有斷點都必然可見的橫向區塊（`verify-mobile-geometry.mjs:122` `required` 陣列裡 `topbar`/`stageBar` 不受寬度門檻限制）。
- `frontend/src/hermes/hermesI18n.tsx:7-25`（zh-TW）/`26-43`（en）目前沒有 hero tagline 對應 key，需新增（如 `heroTagline`）及英文版對應句（en 版文案需另請 CEO 或依現有語氣自譯，不在本計劃自行認定）。
- 掛載點：`frontend/src/pages/HermesDashboard.tsx:515-517`（TopBar boot layer）與 `519`（beginner narrative 條件渲染的位置）之間，是最小侵入的插入點。

### 改法概要
1. `hermesI18n.tsx` 新增 `heroTagline`（zh-TW: CEO 定案文案；en: 對應英文）與可選的數字錨點 `heroAnchor`（如「150+ 來源 × 5 幣 × 5 模式 × 不可竄改血統」，題面已有的既有數字，需跟 `frontend/src/hermes/CurrencyGalaxy.tsx`/後端來源清單核對「150+」是否為現行真實可佐證的數字，不可沿用競賽文件裡未經本輪核實的舊數字）。
2. `HermesDashboard.tsx` 在 TopBar boot-layer 後新增一條 `hermes-hero-strip`（新 CSS class），內容為 `t('heroTagline')` + 徽章化的 `heroAnchor`。
3. `hermes.css` 新增 `.hermes-hero-strip` 規則：桌面時 `left: var(--hermes-rail) 或 0`（視覺定案）、行高控制在 22-28px，避免大幅壓縮 `--hermes-top` 之下的可用高度；`≤560px` 斷點需與 leftRail 隱藏規則協調，全寬顯示。

### 相依/順序
- 無相依，可獨立、快速執行（估時 CEO 已收斂至 ≤6h）。
- **必須先於 #362 排定**（CEO 已如此排）：因為 #362 的 mobile overflow 修復需要把這條新 hero strip 一併納入 375×667/390×844 的高度預算，避免 #362 修完後又被 #361 的新元素撐爆。若technically可行，建議：#361 落地後、#362 開工前，重跑一次 `npm run test:mobile-geometry` 取得含 hero strip 的最新失敗清單，而不是沿用本文附錄裡「不含 hero strip」的失敗清單去修。

### 驗收條件
- [ ] 桌面 1440×900 首屏（不捲動）可見完整標語
- [ ] Playwright `--viewport-size=375,667`：標語在首屏可見、不被裁切、不造成新的橫向溢出
- [ ] `hermesI18n.tsx` zh-TW/en 兩個 key 皆有值，語言切換按鈕（`HermesTopBar.tsx` 現有 EN/繁中 toggle）切換後標語同步換
- [ ] 「150+ 來源」等數字錨點需有可查證依據（貼出來源清單筆數的查證方式/檔案位置），不得沿用未經本輪核實的舊數字
- [ ] 截圖前後對照（1440×900 + 375×667）附 issue
- [ ] `npm run build`、既有 `HermesTopBar.test.tsx` 綠，新增至少 1 個測試斷言 hero tagline 文字存在

### 風險
- **視覺密度風險**：中高。艦橋式 UI 本來就資訊密度極高，新增一條 hero strip 若處理不好會讓首屏更擁擠、與 #362 mobile overflow 問題產生疊加效果（見第 0 節 #362 現況：mobile 目前已經 FAIL，topbar/stageBar 已經在跟 content 重疊，此時再加一條新 strip 風險不小）。
- **文案落地與 i18n 風險**：低，純文字改動，但需注意 en 版文案品質（不可機翻硬套，需符合既有英文語氣，如 `HermesTopBar.tsx` 既有的 `Find risks, reasons, traceable evidence` 語氣）。
- **與 #542 撞檔風險**：低（不同檔案，TopBar vs RightRail/StageDrilldown），但若最終落點改選 RightRail 需重新評估（見上「現況」段落已建議不要選 RightRail）。

---

## 4. #564 — Review follow-up：PR #561 clean install 問題

### 現況（file:line/證據）
- 原始問題：PR #561 head `5d3fe9ecd3cf5563e8bf30202df402c6b3ff5d23`（分支 `fix/539-training-status-fail-soft-v2`）在乾淨 `npm ci --include=dev` 失敗，報 `frontend/package.json`/`frontend/package-lock.json` 對不上，缺 `@emnapi/core@1.11.2`/`1.11.1` 等。
- PR #561 已於 2026-07-23 14:28:59（commit `e1f7ff3`）合併進 `develop`；PR #547（`fix: sync frontend lockfile for snapshot lineage`，commit `07cfb9d`）已於同日 13:39:14 更早合併，修好了 `frontend/package-lock.json` 的 `@emnapi/*` 對齊問題。
- **本輪重新驗證**（乾淨 clone `develop` HEAD `85fee2b`，非本機既有 `node_modules`）：
  - `npm ci --include=dev` → `added 176 packages, and audited 177 packages in 1s`，0 錯誤
  - `npm run build` → `tsc -b && vite build` 綠，`dist/` 產物正常輸出
  - `NODE_ENV=development npm test -- --run src/components/TrainingStatusCard.test.tsx` → `Test Files 1 passed (1)`、`Tests 4 passed (4)`
- `git show --stat e1f7ff3` 確認 #561 合併提交本身只動 `TrainingStatusCard.tsx`/`.test.tsx`，未動 lockfile——lockfile 是靠合併順序（#547 先進 develop）間接被修好的，不是 #561 自己修的。

### 改法概要
不需要程式改動。收尾動作：
1. 請 Samantha（原 review 意見提出者）或 CEO 在 develop HEAD 上重跑上述三條指令，確認一致後在 #564 留言附證據。
2. 關閉 #564，說明是「合併順序自然解決，非額外程式改動」，避免之後有人誤以為還要排工。

### 相依/順序
- 無相依，可與 #541 收尾動作同批處理（都是「驗證後關閉」的行政動作，不佔開發工時）。

### 驗收條件
- [ ] 乾淨環境 `npm ci --include=dev` 綠
- [ ] `npm run build` 綠
- [ ] `NODE_ENV=development npm test -- --run src/components/TrainingStatusCard.test.tsx` 綠
- [ ] #564 附上以上三條指令輸出，issue 關閉

### 風險
- 低。唯一風險是「CI 環境跟本機 clone 環境不同（例如 CI 用不同 node 版本、有 registry cache 差異）導致 CI 上仍會重現」——建議收尾時**在集團實際 CI（若此 repo 有跑 CI 的話）跑一次同樣指令**，不要只信任本機乾淨 clone 的結果。本輪未查此 repo 是否掛了 GitHub Actions CI，建議 CTO 收尾時一併確認。

---

## 5. #362 — 首頁 mobile overflow／scroll 修復

### 現況（file:line + 本輪重新產生的 fixture）
- 依 CEO 指示「實作前先 Playwright `--viewport-size=375,667` 重現確認」——**本輪已完成此步驟**，結果如下（`frontend/scripts/verify-mobile-geometry.mjs`，develop HEAD `85fee2b`，跑了兩次結果一致）：

```
- 375x667 home: topbar overlaps content
- 375x667 home: stageBar overlaps content
- 375x667 analyze-module: moduleDeck is not visible
- 375x667 analyze-module: stageBar overlaps content
- 390x844 home: topbar overlaps content
- 390x844 home: stageBar overlaps content
- 390x844 analyze-module: moduleDeck is not visible
```

- 對照 #540 close comment 聲稱「mobile geometry gate is green」，**目前不成立**，見第 0 節重大發現。
- `git log 0bdf020..HEAD -- frontend/src/hermes/hermes.css frontend/src/pages/HermesDashboard.tsx` 顯示 #540（commit `0bdf020`）之後有 4 個相關 commit：`bdd2a07`（新增 `HermesMobileDivergenceEntry` mobile 入口）、`dd3d2d1`、`2d258af`（`right rail` hit region 調整）、`5c06195`（beginner narrative 3-step 入口，`HermesDashboard.tsx:519` `{beginnerMode && !activeModule && <HermesBeginnerNarrative />}`），研判其中一或多個引入了目前的 overlap 回歸，需 CTO 對這 4 個 commit 做二分排查（`git bisect` 或逐一 revert 重跑 `verify-mobile-geometry.mjs`）定位根因，而非直接改 CSS 亂猜。
- 選擇器涉及檔案：`frontend/src/hermes/hermes.css`（`.hermes-topbar`/`.hermes-energy-deck`/`.hermes-module-deck` 相關規則，行號見 `grep` 結果：`hermes-module-deck` 486/511/520/…/2018，`hermes-energy-deck` 772/846/2022/2242/2315，`hermes-mobile-divergence` 2169-2242）、`frontend/src/pages/HermesDashboard.tsx` responsive 區塊。

### 改法概要
1. **先定根因，再改**：對 `0bdf020`（#540 綠燈時的 commit）與 `HEAD` 之間的 4 個 commit 做逐一 checkout + `npm run test:mobile-geometry`，找出第一個讓 geometry 斷言由綠轉紅的 commit，確認是 CSS 規則被覆蓋（如新 `HermesBeginnerNarrative`/`HermesMobileDivergenceEntry` 沒有正確納入既有 `≤560px` media query 的 z-index/定位系統）還是既有規則本身不夠 robust。
2. 依 #540 原始 fixture（`verify-mobile-geometry.mjs` 的 `home`/`analyze-module` 兩條 route、375×667/390×844/768/1440×900 四個視口）修正 overlap 與 `moduleDeck` 不可見的問題，**只修回歸範圍**，不擴大改動既有已通過的桌面版面。
3. **必須把 #361（新 hero strip）與 #542（composite drawer 新增 evidence rows/下載按鈕）已落地的內容一併納入這次驗證**，避免修好當下的回歸、卻漏掉這兩項本輪新增內容在 mobile 上的表現。

### 相依/順序
- 依 CEO 排序放最後（P2 墊底、桌面 demo 為主）。**必序列在 #361、#542 之後**開工（如上述理由）。
- 與 #541/#564 的收尾動作無關，可並行。

### 驗收條件（沿用 #362 原始驗收標準 + 本輪追加）
- [ ] `npm run test:mobile-geometry`（375×667、390×844）由目前 7 項失敗轉為全過
- [ ] 追加跑 768×1024、1440×900（如原 issue 要求）確認桌面版面未被 mobile 修復破壞
- [ ] `documentElement.scrollWidth <= clientWidth`（無橫向溢出）
- [ ] 375×667 下可垂直捲動看到：5 幣選擇、4-5 目的卡片、提交按鈕、composite/divergence drawer 新內容（#542）、hero tagline（#361，若已落地）
- [ ] touch target ≥44×44px
- [ ] 截圖（375/768/1440 三視口）附 issue
- [ ] 新增/更新 Playwright regression test 鎖定，防止再度回歸（鑑於這次就是「曾經綠燈又回歸」，建議把 `verify-mobile-geometry.mjs` 排進 pre-push 或 CI gate，而不是只在單一 issue 手動跑一次）

### 風險
- **回歸再發生風險：高**。這是本輪唯一一項「已經修過一次又壞掉」的項目，代表目前沒有自動化 gate 擋住未來新 commit 再度引入 overlap。強烈建議此票驗收範圍**額外加一條**：把 `test:mobile-geometry` 接進既有 pre-push/CI 流程（依 `CLAUDE.md` 之前提到的「⛔已明令(已作廢)：無 CI，用 pre-push test gate」原則，需與 CTO 確認目前 pre-push hook 涵蓋範圍）。
- **CEO 決策風險**：CEO 若仍要維持「demo 只用桌面、mobile 降級」的判斷，本項的「回歸」事實仍應讓 CEO 知情（見第 0 節），避免決賽現場臨時被要求手機展示時措手不及。

---

## 6. 排程總覽

```
可並行（互不相依）：
  ├─ #541 收尾（issue 關閉，非開發，~30min）
  ├─ #564 收尾（issue 關閉，非開發，~30min）
  └─ #361（≤6h，獨立）

必序列：
  #542（≤12h，依賴 #541"程式碼結果"已滿足→即可開工）
     └─→ #362（≤8h，需吃到 #542 composite drawer 新內容 + #361 hero strip 新內容 才能定案 mobile 修復範圍）

建議實際執行序：
  Day 1 上午：#541 收尾 + #564 收尾（並行，行政動作）+ #361 開工
  Day 1 下午～Day 2：#542 開工（不必等 #541 issue 正式關閉）
  Day 2～Day 3：#361、#542 都落地後 → #362 根因排查 + 修復 + 回歸測試
```

- 全部完成後跑一次 `npm run build` + `NODE_ENV=development npx vitest run`，確認不低於本文基準 48 files / 346 tests。
- #362 修復後務必重跑 `npm run test:mobile-geometry`，這是本輪唯一有「假綠燈」前例的項目。

---

## 7. 安全／資料面標註

- **#541/#542 下載 payload**：`data_lineage`/`evidence`/`trust_radar`/`execution_log` 皆為既有後端回應欄位（`AnalyzeData`，`frontend/src/lib/types.ts:367-377`）直接序列化，前端不新增、不推導、不捏造欄位；`data_lineage` 缺席時明確給 `null` 而非省略，維持既有「誠實顯示 unavailable」原則。
- **#542 分歧分組（`groupByStance`）**：沿用後端 `distinct_sources` 去重語意，前端只是換一個渲染位置，不改變任何計分或去重邏輯，不涉及新的資料寫入或權限問題。
- **#361 數字錨點**：若採用「150+ 來源」等具體數字，需可被現有來源清單佐證（見改法概要第 1 點），不得沿用競賽文件裡未經本輪核實的舊數字，避免對外宣稱失實。
- 本輪皆為前端展示層改動，不涉及 DB schema、密碼、API 權限變更，不觸發 CLAUDE.md 的 DB/密碼鐵律。

---

## 附錄：本輪重新驗證指令與輸出（供 CTO 開工前核對，不需要重跑）

### A. Baseline（develop HEAD `85fee2b`，本機 checkout，非乾淨 clone）
```
$ npm run build            # tsc -b && vite build → 綠，dist/ 正常
$ NODE_ENV=development npx vitest run
 Test Files  48 passed (48)
      Tests  346 passed (346)
```

### B. #564 乾淨安裝重現（獨立 clone，非本機 node_modules）
```
$ npm ci --include=dev
added 176 packages, and audited 177 packages in 1s
$ npm run build            # 綠
$ NODE_ENV=development npm test -- --run src/components/TrainingStatusCard.test.tsx
 Test Files  1 passed (1)
      Tests  4 passed (4)
```

### C. #362 mobile geometry 重現（`npx playwright install chromium` 後）
```
$ npm run test:mobile-geometry
- 375x667 home: topbar overlaps content
- 375x667 home: stageBar overlaps content
- 375x667 analyze-module: moduleDeck is not visible
- 375x667 analyze-module: stageBar overlaps content
- 390x844 home: topbar overlaps content
- 390x844 home: stageBar overlaps content
- 390x844 analyze-module: moduleDeck is not visible
```
（跑了兩次結果一致，非偶發）

### D. #541 溯源
```
$ git show --stat 07cfb9d   # PR #547 "Closes #541"，改動 SnapshotModal.tsx / .test.tsx / package-lock.json
$ gh pr view 547            # state: MERGED
```

### E. #564 溯源
```
$ git log --oneline --grep="561"     # e1f7ff3 fix(ui): make training status fail-soft (#561)
$ git show --stat e1f7ff3            # 只動 TrainingStatusCard.tsx / .test.tsx，未動 lockfile
$ gh pr view 561                     # state: MERGED
```

### F. #362 回歸範圍排查起點
```
$ git log --oneline 0bdf020..HEAD -- frontend/src/hermes/hermes.css frontend/src/pages/HermesDashboard.tsx
5c06195 feat(ui): beginner 3-step narrative entry linking the 3 context modules
2d258af fix(ui): separate right rail status hit regions
dd3d2d1 fix(ui): keep desktop divergence entry clickable
bdd2a07 fix(ui): expose divergence drilldown on mobile
```
