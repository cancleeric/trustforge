# TrustForge — 世界第一開發計劃（重寫版，2026-07-02）

> 作者：CPO（gray）｜ 觸發：老闆 Eric 親看 EC2 LIVE（http://3.106.220.68/）評語「**一點都不專、離世界第一差很遠**」
> 依據：老闆親測第一印象 + CEO 兩路批判分析（真缺口 A 產品呈現層 / B 資料誠實層），已對照
> `src/trustforge/web.py`、`src/trustforge/pipeline.py`、`src/trustforge/ingestion/*`、`docs/archive/plans/WORLD-FIRST-ANALYSIS.md` grounded 逐條核實（見各階段「證據」）。
>
> ⚠️ **本計劃定位：產品專業度重寫，不是功能打勾清單**。目的是把「判審 3 秒內的第一印象」從
> 「半成品 demo」翻正成「像世界第一團隊做的東西」。**不碰 `docs/archive/plans/WORLD-FIRST-ANALYSIS.md` 的
> W1-W4 演算法深度軌（跨源佐證/truth-discovery/bridging/校準）**——那條是另一條並行的「引擎深度」
> 軸線，由 CEO 主責；本計劃是「呈現層 + 資料誠實層」軸線，兩條軸線互不覆蓋、互不阻擋。

---

## 0. 為什麼要重寫（不是修修補補）

CEO 兩路批判分析 grounded 到 code 的結論：**判審打開首頁的前 3 秒看到什麼，直接決定「這是不是世界第一」的第一印象——目前那 3 秒是空白 + dev 內部資訊，跟核心引擎做得多深完全無關。** 這是呈現層的系統性缺口，不是單點 bug，所以用「重寫計劃」而非「加幾個 issue」處理。

逐條 grounded 核實（避免憑空捏造）：

| CEO 發現 | 程式碼證據 |
|---|---|
| 首頁完全空 div | `web.py` `do_GET`：`if u.path == "/": return self._send(200, page(""))` —— `body=""` 直接灌進 `<main class="tf-dashboard">{body}</main>` |
| header 露 dev artifacts | `web.py` header 樣板：`<span class="tf-version">{version}</span>` + `{mode}`（三檔徽章）+ `<a class="tf-costlink" href="/costs">cost ledger {cost_display}</a>`，`_version.py` 目前 `VERSION = "dev"` fallback |
| 預設 offline sample | `pipeline.py` `_resolve_modes`：`offline=True` → `data_mode="sample", llm_mode="off"`；`web.py` `page(body, active_mode="offline", ...)` 為首頁/`/costs` 預設值；真資料要 `?real=1`（`_is_real_request`） |
| HOYA OHLCV 過期 | `data/data/BTC_daily_ohlcv.csv` 等 5 檔最後一列 `2026-05-31`；今天 2026-07-02 → **實際落後 32 天**，`web.py` 表單預設文案硬寫「近兩週市場狀況」（`textarea` 預設值、`/analyze` 預設 `q`），32 天前的資料配「近兩週」文案 = 判審一對日期就抓包 |
| 差異化訊號稀疏 | `orchestrator.py` `flags=list(sc.manip_flags)`／`info_flags=list(sc.info_flags)` 由 `trust.scoring._manipulation_flags` 算出，`docs/archive/plans/PLAN-w2-wiring.md` 明文「本輪不動 `MIN_INDEPENDENT_EVIDENCE`、不加/改樣本資料」→ 真資料下多數查詢確實可能空 |
| `/status` 已有健康快照可用 | `web.py` `_render_status_page_cached` → `get_freshness_snapshot(backend=cache_backend)`，回傳 `fresh/stale/missing` 矩陣（來源 × 幣種）——**Phase 2 的健康 gate 不用新建，直接復用這個既有機制** |

---

## Phase 1 — 拔 Dev Chrome + 首頁不空白（ROI 最高，先做）

**目標**：判審打開首頁的第一眼，看到的是「一個產品」而不是「一個開發中的表單 + 空白區」。**完全不碰資料/演算法**，純呈現層，風險最低、對第一印象影響最大，優先序最前。

### 改哪些檔
- `src/trustforge/web.py`
  - header 樣板（約 L168-176）：拔掉判審看得到的 dev artifacts —— 版號 `tf-version`、模式徽章 `{mode}`（離線示範/真資料·$0/未設 BEDROCK_MODEL_ID 那串）、`cost ledger {cost_display}` 連結，這三樣**移出首屏可見區**，改放進 `/status`（已有頁面，本來就該是給營運看的技術頁）或摺疊在不顯眼的角落（如 footer 極小字 + tooltip），而不是跟 logo 平排在最上方。
  - `do_GET` 的 `if u.path == "/": return self._send(200, page(""))` —— `page("")` 改成 `page(_render_home_page())`，新增 `_render_home_page()` 函式輸出：
    - Hero：一句話定位（「多源市場情報的信任提煉——不只給分數，給你為什麼」）+ CTA（導向 Query Console，已在左側 `tf-query-panel`，右側首屏不該空）
    - 產品總覽：三層架構一句話說明（事實 → 推論 → 結論，呼應 `_render_report` 既有 `步驟1/3、2/3、3/3` 語彙，不用新發明）
    - 範例卡：1-2 個預先渲染好的範例結果縮圖/連結（見 Phase 4 差異化 demo case，兩階段可合併）
  - `_version.py`：`VERSION = "dev"` 的 fallback 若判審看得到必須是語意化字串（如「beta / preview」）而非工程用 `dev`——但版號本身移到 `/status` 後，首頁不再是問題，這條降為 P1 非阻塞。

### 可派 CTO 範圍
- `_render_home_page()` 新函式（純 HTML 字串組裝，比照現有 `_render_status_page`/`_render_costs_page` 寫法，不新增依賴）
- header 樣板重排（CSS + HTML 位置調整，`tf-mode-badge`/`tf-version`/`tf-costlink` 三個既有 class 沿用，不用重寫視覺系統）
- 對應新增/更新 `tests/test_web.py`：斷言 `/` 回應內容不再是空字串、必須包含 hero 文案關鍵字

### CEO 驗收標準（可 Chrome 對稿）
1. 開 `http://<host>/`，**3 秒內**右側 `.tf-dashboard` 不是黑色空白——有 hero 文案/總覽/CTA。
2. header 最上方一行**看不到**「v0.5.3」「離線示範」「未設 BEDROCK_MODEL_ID」「cost ledger $」字樣（這些移到 `/status`）。
3. `/status` 頁面仍可查到版號/模式/成本（沒被刪掉，只是移位——營運/技術可查證性不能丟）。
4. mobile 寬度（375px）首頁不塌陷、hero 文案可讀。

### 風險
- header 移除欄位若被既有測試斷言（`tests/test_web.py`、`tests/test_status_page.py`）直接檢查字串位置，需同步改測試斷言位置而非刪除斷言本身（不能為了過測試假裝功能還在原位）。
- `_render_home_page()` 若引用 pipeline/connector 會拖慢首頁 TTFB——**首頁必須是純靜態渲染**，不觸發任何連接器/Bedrock 呼叫（credit-safe：首頁瀏覽次數最高，不能是計費熱點）。

### credit-safe / #24 守則
- 首頁範例卡若用真實資料截圖，**必須是 Phase 4 產出的、標註時間戳的真實 demo case 快照**，不得為了畫面好看虛構數字（違反 #24 誠實原則）。過渡期可先用「示意用途，非即時資料」標註的靜態圖，待 Phase 4 產出後替換。

---

## Phase 2 — 預設切真資料，且與健康檢查/日期修正同一輪綁定（不可拆單獨做）

**目標**：訪客打開首頁看到的不再是假樣本（fixture），而是 pipeline 已支援、免 Bedrock 的 `data_mode=live, llm_mode=off`（真資料·$0）。**這是差異化賣點「真多源信任提煉」第一次被看見**，但 CEO 明確警告：**順序錯了，真資料比假樣本更難看**——本階段三件事必須同一輪一起做，不能只切預設就上。

### 改哪些檔
1. **預設模式切換**：`web.py` 中 `page(body, active_mode="offline", ...)` 的預設值、以及 `_active_mode(qs)` 對「未帶任何 mode 參數」時的 fallback，從 `"offline"` 改為對應 `data_mode="live", llm_mode="off"` 的檔位（即目前 `?real=1` 那條路徑变成無參數時的預設，`?sample=1` 或等效參數保留給想看 demo 樣本的人，而非反過來）。
2. **健康 gate（切換前置條件，非事後補救）**：上線前**先查 `/status` 的 `get_freshness_snapshot`**，確認：
   - news/onchain/social（尤其 reddit 兩源）過去 24-48h 至少完整跑過一輪、`fresh` 狀態不是全 `missing`/`stale`。
   - `scripts/fetch_scheduler.py` 排程確認有在跑（cron/定時任務存在且近期有成功紀錄，非只是程式碼存在）。
   - 若健康檢查不過，**本階段不上線**，先修連接器/排程（歸 CTO）。
3. **HOYA OHLCV 過期 32 天的文案破綻**（實測：`data/data/*.csv` 最後一列 `2026-05-31`，今天 2026-07-02）：
   - `web.py` 表單預設 `<textarea name="q">分析該幣種近兩週市場狀況，整合多源資料</textarea>`（L184）與 `/analyze` 預設 `q`（L2039/L2071）—— 「近兩週」在真資料模式下對照 32 天前的 OHLCV 是錯的，判審一比對日期立刻抓包。改法二選一（可並行）：
     a. 文案改絕對日期區間（如「分析該幣種近期市場狀況（資料涵蓋至 {last_ohlcv_date}）」，`{last_ohlcv_date}` 從 `ingestion/prices.py` 讀出的 CSV 最後一列日期動態帶入，不寫死）。
     b. 用既有 `coingecko.py` 連接器（已存在，見 `ingestion/coingecko.py`）補「即時現貨價」欄位，跟官方 OHLCV 基準（信譽權重 0.95）並列顯示，標明兩者各自的資料時間戳——讓判審看到「官方基準資料到 5/31、即時價另外標示」而非含糊帶過。
   - **兩者都要做**：a 解決文案破綻，b 解決「判審想知道現在多少錢」的真實需求；缺一則要嘛文案仍模糊、要嘛判審仍會去外部查價對比抓包。
4. **缺源優雅處理**：`_render_freshness_table`（`web.py` L390）目前輸出「來源 × 幣種」矩陣，`missing` 狀態要有清楚、非驚慌的文案（如「該來源本輪未取得資料，不納入計算」），**不能讓分析結果頁面因為缺一源就整頁報錯或塞滿一排「無法取得」**——`orchestrator.py` 的 `OBJECTIVE_KINDS` 已有 kind 集合可判斷該次分析實際用了哪些來源，缺的來源在結果頁應直接不顯示該行，而非顯示空/錯誤佔位。

### 可派 CTO 範圍
- `web.py`：預設 mode 切換（`_active_mode` fallback、`page()` 預設參數）
- `web.py` + `ingestion/prices.py`：讀取 CSV 最後日期、動態文案模板
- `ingestion/coingecko.py` 串接即時價欄位到結果頁（若既有函式已可直接呼叫，優先復用不重寫）
- 缺源優雅處理：`_render_freshness_table` 附近 + 結果頁渲染邏輯（`_render_report` 一帶）調整「來源缺席」的顯示分支

### CEO 驗收標準
1. 上線前先貼 `/status` 截圖佐證健康檢查通過（近 24-48h freshness 矩陣多數 `fresh`，非全 `stale`/`missing`）——**這是本階段的前置交付物，不是事後補**。
2. 開首頁不帶任何 query string，直接觀察：`active_mode` 已是真資料檔位（非離線樣本），且沒有明顯「無法取得」洗版。
3. 任跑一次分析，結果頁 OHLCV 相關敘述**不再寫「近兩週」卻對到 32 天前資料**——要嘛顯示絕對日期、要嘛有即時 CoinGecko 價格並列且標明時間戳差異。
4. 手動製造一個缺源情境（如暫時斷某連接器），確認結果頁優雅降級、不整頁報錯、不留一排刺眼的「無法取得」。
5. 仍可用參數切回離線樣本（demo/測試用途保留，但不再是預設）。

### 風險
- 預設切真資料 = 預設觸發真連接器呼叫，**流量若暴衝要重新評估限流**（`_check_live_rate_limit` 現有機制是否也套用到真資料·$0 模式，需確認 `_parse_real` 那段已有比照 live 限流——已核實 `_parse_real` 確實會呼叫 `_check_live_rate_limit`，機制已在，風險降低但仍需壓測）。
- 若健康 gate 顯示連接器不穩（如 reddit 常態性失敗），**寧可延後本階段上線，也不要切預設真資料然後被判審看到一堆缺源**——這正是 CEO 警告「真資料比假樣本更難看」的具體情境，必須避免。

### credit-safe / #24 守則
- `data_mode=live, llm_mode=off` 本身就是 CEO/pipeline 已設計好的免 Bedrock 真資料路徑（$0），預設切這個不新增 Bedrock 呼叫成本。
- 即時 CoinGecko 價格是**免費公開 API 的真實回傳值**，不得用任何方式模擬/假造（違反 #24）；若 API 當下失敗，優雅顯示「即時價暫不可用，以官方基準 {日期} 資料為準」，不得留空造成誤解也不得編數字。
- 缺源顯示遵守既有原則（`web.py` 多處註解已有的 #24 紅線）：不用正則/規則從文字反推假裝結構化資料，缺就是缺，明說。

---

## Phase 3 — 資訊架構 / 視覺可信度打磨

**目標**：從「一次一幣一問的表單」變成「像產品」——有總覽、有密度、有基本的視覺可信度信號。此階段開始涉及較多 UI 改動，**排在 Phase 1/2 之後**是因為對「30 秒可信度」影響不如前兩階段直接（前兩階段解決「一眼看穿是半成品」，本階段解決「看起來像認真做的產品」）。

### 改哪些檔
- `web.py` header/layout 樣板（`tf-hdr`、`tf-layout`、`tf-query-panel`、`tf-dashboard` 一帶 CSS + HTML）
  - 多幣總覽/導航：Phase 1 首頁已加總覽卡，本階段補「切幣種快速比較」的輕量導航（複用既有 `comparison` 題型與 `_render_comparison`，不用新開一條資料路徑）。
  - loading 狀態：`/analyze` 目前是同步請求整頁刷新，判審點下去到結果出現之間**沒有任何進度提示**——加最小可行的 loading 指示（純前端 CSS/JS，不改後端邏輯，例如 submit 時按鈕變 loading 態 + 一句「正在整合多源資料…」文案）。
  - 來源 logo/trust marks：`_OFFICIAL_KINDS = {"price", "hoyabit", "regulatory"}`（`web.py` L1206）已有官方來源分類，可視覺化成小標籤（「官方來源」vs「公開來源」），不需外部設計資源，純文字標籤即可先做（logo 圖檔需 CBO/設計資源配合，本階段先做文字版）。
  - 字體階層：`.tf-coin-badge`/`.tf-step-badge`/`.badge` 等既有 class 已有基礎，補 h1-h3 與內文的字級/字重差異化（純 CSS，不動結構）。
  - mobile：目前只有 `header.tf-hdr{{flex-direction:column}}` 等基本斷點（L160-166），需補 `tf-query-panel`/結果表格在 375px 下的可讀性（表格橫向捲動或關鍵欄位優先顯示）。

### 可派 CTO 範圍
- 全部為 CSS/HTML 樣板調整 + 最小前端 JS（loading 態），**不動 `pipeline.py`/`ingestion/*`/`trust/*` 任何演算法邏輯**，風險集中在視覺回歸，用既有 `tests/test_web.py`/`tests/test_web_dark_theme.py` 快照式斷言把關。

### CEO 驗收標準
1. Chrome 開結果頁，能一眼分辨「官方來源」vs「公開來源」的證據。
2. 點 Run analysis 到結果出現之間有明確等待中提示，不是白屏/看起來卡死。
3. 375px mobile 寬度下，Query Console 表單與結果表格都可正常操作與閱讀（不需要橫向捲動整頁）。
4. 多幣比較導航可從首頁/結果頁直接觸發，不用手動修改 URL 參數。

### 風險
- CSS 大改動易牽動既有 `tests/test_web_dark_theme.py` 的字串斷言，需同步更新而非刪測試。
- loading 態如果用 JS 攔截 form submit，需確認不破壞既有 `/analyze` GET 表單行為（`_mode_extra_params` 等既有連結拼接邏輯）與 `/analyze.json` API 呼叫方式。

### credit-safe / #24 守則
- 純前端視覺變更，不觸發額外連接器/Bedrock 呼叫，$0 增量成本。

---

## Phase 4 — 差異化證明：已知觸發 demo case + 誠實的「未觸發」文案

**目標**：操縱🚩/協同訊號是核心賣點（「情報的情報」，`docs/archive/plans/WORLD-FIRST-ANALYSIS.md` §1B 結論），但真資料下多數查詢是空的——賣點沒有自然證明的機會。本階段**不改演算法**（`MIN_INDEPENDENT_EVIDENCE` 等閾值維持不動，遵守 `docs/archive/plans/PLAN-w2-wiring.md` 既有紅線），只做兩件事：找到/固定一個已知會觸發的展示案例、把「未觸發」講清楚而非留白。

### 改哪些檔
1. **已知觸發 demo case**：用真資料跑過去一段時間各幣種的分析，找出**真實**（非造假）觸發過 `manip_flags`/`info_flags`（`orchestrator.py` L90/94，來自 `trust.scoring._manipulation_flags`）的查詢組合，記錄下來（幣種、題型、查詢文字、觸發時間點），固定成 Phase 1 首頁範例卡 / `/analyze` 表單的「試試這個」快捷連結。**不得為了觸發而放寬閾值或加樣本資料**（`docs/archive/plans/PLAN-w2-wiring.md` 已有明文紅線，本計劃重申並沿用）。
2. **未觸發時的誠實文案**：結果頁若 `flags`/`info_flags` 為空，目前渲染邏輯需確認是否已有明確「未偵測到操縱/協同訊號」的文案，而非該區塊直接消失讓判審以為沒做這功能。若目前是「空 list 就不渲染整段」，改成「本次分析未偵測到操縱／協同訊號」的中性明說（呼應 `orchestrator.py` L239 附近「不代客決策，中性提醒措辭」的既有原則，同一套語氣延伸到這裡）。

### 可派 CTO 範圍
- 一次性資料調查腳本（跑歷史/真資料找觸發案例，可用 `scripts/` 下既有排程腳本模式，不需新建持久化服務）
- `web.py` 結果頁 flags 渲染邏輯：補「空 list 明說未觸發」分支

### CEO 驗收標準
1. 首頁/表單有至少 1 個「已知會觸發操縱🚩/協同訊號」的一鍵展示連結，點下去確實看到紅旗徽章（非事後加樣本資料湊出來的，需附真實觸發時間戳/查詢參數紀錄佐證）。
2. 任跑一個未觸發的正常查詢，結果頁明確寫「未偵測到操縱/協同訊號」而非該區塊消失/留白。
3. 抽查 CTO 提供的觸發案例紀錄，確認資料來源與觸發時間點可回溯（非事後編造）。

### 風險
- 若真資料下始終找不到任何已知觸發案例（訊號確實稀疏），**不得為了「有東西展示」放寬閾值或加樣本資料**——若找不到，如實回報「目前真資料樣本量下尚未自然觸發，作為已知限制列入 roadmap」，比造一個假案例更符合誠實原則，也更符合 #24 紅線精神。

### credit-safe / #24 守則
- demo case 必須是**真實發生過的觸發**，附時間戳/查詢參數可回溯，不得後補樣本資料製造觸發（直接違反 `docs/archive/plans/PLAN-w2-wiring.md` 已有紅線與 #24）。
- 若判審現場重跑同一 demo case 因為市場資料已變化而不再觸發，**誠實標註「歷史觸發快照，即時重跑結果依當下市場資料可能不同」**，不得偽裝成即時保證觸發。

---

## Phase 5 — 既有 follow-up 收尾（結果持久化 / UI polish / 主題）

**目標**：把先前 PR #39 因「無狀態 SSR 架構下切主題會弄丟已產出報告」而暫時收斂掉的功能（dark-only、拆掉 theme toggle），在有持久化基礎後重新評估開放。此階段優先序最低（對 30 秒第一印象影響最小），排在最後。

### 改哪些檔
- 結果持久化（既有 follow-up，程式碼註解見 `web.py` L1337/L2134「等（結果持久化）做對後再重新開放 theme toggle」）：需先決定持久化方案（如輕量 KV/DB 存 render 結果，取代目前 process-local 的 rtok render cache），屬於架構決策，**建議獨立立項評估**，不在本階段直接動工，本階段先確認範圍與依賴關係。
- UI polish：Phase 3 完成後的收尾項（如 `.tf-mode-badge`/`.badge` 等既有元件的一致性微調）。
- 主題（theme toggle 重新開放）：**依賴結果持久化完成**，不可在持久化方案落地前重新開放（會重演 PR #39 已明文記錄的問題：使用者已產出的真報告被切主題弄丟）。

### 可派 CTO 範圍
- 持久化方案技術選型評估（先出方案不動工）
- Phase 3 UI polish 收尾項

### CEO 驗收標準
1. 持久化方案有明確技術選型文件（存哪、TTL、成本估算），CEO/CTO 審過再排入下一輪開發計劃。
2. 本階段**不要求**重新開放 theme toggle（前置條件未滿足前不驗收此項）。

### 風險
- 若倉促重新開放 theme toggle 而未先做持久化，會重演 PR #39 記錄的「切主題弄丟已產出報告」問題——**明確排最後、有前置依賴**，避免被誤排到前面搶資源。

### credit-safe / #24 守則
- 持久化方案若涉及新增儲存服務（如 DynamoDB），成本需先估算並經 CEO/老闆核准，不得默默啟用計費資源。

---

## 執行順序建議

1. **Phase 1（拔 Dev Chrome + 首頁不空白）**— 立即開始，風險最低、ROI 最高，純呈現層不碰資料。
2. **Phase 2（預設真資料，三件事同輪：健康 gate + 日期修正/即時價 + 缺源優雅處理）**— 緊接 Phase 1，**三個子任務必須同一輪一起驗收，不可只切預設就上線**，這是 CEO 特別強調的順序風險點。
3. **Phase 3（資訊架構/視覺可信度）**— Phase 1/2 上線、判審不再看到半成品/破綻後，再打磨「看起來像認真做的產品」這層。
4. **Phase 4（差異化 demo case + 誠實未觸發文案）**— 依賴 Phase 2 真資料已預設上線（需要真資料環境才能找/驗證觸發案例），因此排在 Phase 2 之後、可與 Phase 3 並行。
5. **Phase 5（結果持久化/UI polish/主題）**— 最後，且主題重新開放明確依賴持久化方案落地，不可提前。

> 每階段結束比照既有 SOP：CPO 計劃（本文件）→ CEO 審 → CTO 執行（分支 + PR）→ CEO Chrome 親測驗收（不可只信副手測試綠燈，`docs/archive/plans/WORLD-FIRST-ANALYSIS.md` §6 決策日誌已有慘痛教訓：副手測試綠仍需 CEO 親測抓到真 bug）→ merge → 回報下一階段。
