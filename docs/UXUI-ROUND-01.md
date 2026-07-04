# UX/UI 批判稽核 — Round 1（12 輪計劃之第 1 輪）

> 稽核基準：世界第一 / 商業頂級應用（Nansen / Messari / Linear / Vercel 級）
> 稽核方式：grep 實證 `src/trustforge/web.py`（全部頁面 render）+ `scripts/fetch_scheduler.py::_render_overview_html`，不憑空
> 已知/已修（不重複列）：hero CTA、多幣卡 pointer-events、比較表單、錯誤頁品牌化、logo 回首頁、幣別/來源原廠 logo、來源品牌名、多維度信任雷達本體、loading:active

## 弱點清單（6 項，依「視覺衝擊 × 可行性」排序）

### 1. 【P0】無障礙對比失敗 + 零 focus/aria/role（可能擋住無障礙審查/評審扣分）
- **位置**：`web.py:193-194`（`--tf-muted2:#6e7681` dark / `#6e7781` light），套用點遍布全檔：`tf-ev-date`(285)、`tf-hdr-status-link`(217)、`tf-stat-k`(227)、`_render_trust_breakdown` 的 WHY 說明行（多處 `.68rem`/`.7rem`）、`_render_trust_radar` 的「無資料」文字(1776)。
- **grep 實證**：全檔 `grep -n "focus\|aria-\|role=\|tabindex"` → **零筆命中**。對比計算：`#6e7681` on `#0d1117` 相對亮度比 ≈ **4.12:1**；WCAG AA 對 <18px 一般字重要求 **4.5:1**——這批文字多在 `.65rem-.72rem`（≈10-11.5px），全部未達標。
- **為什麼扣分**：世界級金融儀表板（Nansen/Messari）的次要文字對比至少壓在 AA 線之上，且互動元素（`<details>/<summary>`、卡片連結、按鈕）都有可見的 `:focus-visible` 樣式；本站鍵盤 Tab 走訪完全依賴瀏覽器預設外框，暗色卡片背景下常規預設 outline 觀感生硬且未經設計。
- **修法方向**：`--tf-muted2` 調亮至符合 4.5:1（如 `#8b949e` 等級，剛好等於現有 `--tf-muted`）；全站補 `:focus-visible{outline:2px solid #1f6feb;outline-offset:2px}`；`<details>` summary 補 `role`/或至少非純視覺的可讀 aria-label。
- **影響**：買家/投資人若用鍵盤或螢幕閱讀器抽測會立刻發現「連基本 a11y 都沒做」，對「世界第一」定位是硬傷。
- **優先序**：P0（阻擋級——評審/驗收常規抽測項目）。

### 2. 【P1】分析頁仍是「一路到底」單欄長條，未分優先序（IA）
- **位置**：`_render_report()`（`web.py:2358-2481`）。實際渲染順序：`price_provenance` → hero(判斷+gauge+breakdown並排) → **radar** → 事實 → 推論 → 結論 → 限制 → cross_signal → 反方 → **證據表格** → cost → json 連結，全部是獨立 `.tf-section` 各自 100% 寬、由上到下堆疊，`tf-hero-row` 只解決了 hero 那一格的並排，其餘 9+ 個區塊仍是純垂直單欄。
- **grep 實證**：`grep -n "tf-section\|tf-hero-row\|grid-template-columns" web.py` 顯示除 `.tf-hero-row`(292) 與 `.tf-layout`(220，左側 query panel) 外，內容區沒有任何 grid/多欄配置；`.tf-dashboard{min-width:0}` 只是單欄容器。
- **為什麼扣分**：世界級分析頁（Messari Pro/Nansen）把「證據強度／反方訊號／來源獨立性」放進可視覺掃描的並排區塊或側欄摘要，讓最重要判斷第一屏就看完；本頁要一路滾輪捲過事實/推論/結論/限制/cross/反方才到證據表，資訊優先序等同「照生成順序印」而非「照重要性排版」。
- **修法方向**：至少把「反方/低信任」「cross_signal」「限制」三塊收進右側或摺疊式次要面板，主欄只留 判斷→radar→關鍵依據→證據表。
- **影響**：判審快速掃視時容易漏看反方訊號（本來是誠實性賣點），變成要素被稀釋在長捲軸裡。
- **優先序**：P1。

### 3. 【P1】badge/pill 元件各做各的，圓角/padding 無統一 token
- **位置**：`.badge`(272,radius 6px)、`.tf-low`/`.tf-info`(280-281,radius 4px)、`.tf-src-pill`(284,radius **12px** 膠囊)、`.tf-tier-pill`(296,radius 4px)、`.tf-mode-badge`(207,radius 5px)、`.tf-version`(206,radius 5px)、`.tf-div-tag`(302,radius 4px)。
- **grep 實證**：`grep -n "border-radius" web.py` 逐一比對上述 7 個同類「徽章/標籤」元件，圓角分裂成 4 種數值（4/5/6/12px），padding scale 也各自 `.05rem~.2rem` 不等，無 `--tf-radius-pill` 之類共用變數。
- **為什麼扣分**：Linear/Vercel 級的設計系統對「同一視覺語意層級」（都是小標籤）只會有 1-2 種圓角規格；本站同一頁面（如證據列同時出現 `tf-src-pill` 膠囊 + `tf-tier-pill` 方角 + `tf-low` 方角）三種圓角並存，掃過去有「各元件各自實作、沒有統一美術規範」的業餘感。
- **修法方向**：訂 `--tf-radius-pill:999px`（膠囊）與 `--tf-radius-tag:4px`（方標籤）兩種 token，依語意（可信度標籤 vs 來源膠囊）收斂成 2 類，其餘全部改用共用 class。
- **影響**：細節控的投資人/評審會注意到「同類元件不同圓角」，觀感是半成品。
- **優先序**：P1。

### 4. 【P1】375px 手機：信任雷達/信任拆解摘要列無 flex-wrap，只能靠橫向 overflow 頂
- **位置**：`_render_trust_radar` 的 `<summary style="cursor:pointer;display:flex;align-items:center;gap:.5rem">`（`web.py:1798`）；同款無 wrap 的還有 `_render_trust_breakdown` 各分項 `white-space:nowrap` 行（1582 起多處）。手機修法只在 `@media (max-width:480px)` 對 `.tf-section{overflow-x:auto}`（340-346）+ `.tf-section table{min-width:640px}`，這條規則是為「表格」設計的，卻套用到整個 `.tf-section` 容器，radar/breakdown 這類**非表格**內容也一併被裹上橫捲屬性。
- **grep 實證**：`grep -n "flex-wrap" web.py` 只命中 `header.tf-hdr`(203)、`.tf-dash-hdr`(288)、`.tf-conf-wrap`(525) 三處；radar 的 `<summary>` 與四個信任分項的 `<span style="white-space:nowrap">` 行完全沒有 wrap 規則。
- **為什麼扣分**：label(6.5em)＋trust bar(flex:1)＋`{trust:.2f}（N 源／M 筆）`（nowrap）＋單一來源橘色徽章，四段加總在 375px（扣 padding 後可用寬約 320px）極易超版；世界級 app 在窄螢幕會讓次要文字換行到第二行，而不是逼使用者橫向捲動一段本來就該直讀的資訊列。
- **修法方向**：480px media query 內針對 `.tf-section .tf-bar-wrap` 所在的 flex row 加 `flex-wrap:wrap`，並把 nowrap 文字改為可換行；`overflow-x:auto` 只保留給真正含 `<table>` 的 `.tf-section`。
- **影響**：手機（多數初次訪客裝置）看信任雷達這個核心賣點時體驗打折。
- **優先序**：P1（核心判斷區塊，手機流量佔比通常高）。

### 5. 【P2】表格數字全部靠左、無 tabular-nums，掃描性差
- **位置**：`table{{...}} td,th{{border:1px solid var(--tf-border);padding:.4rem;text-align:left}}`（`web.py:274`），套用到所有表格：證據清單信任分數欄、成本帳本（`_render_costs_page`）、Model token 表（`_render_model_token_table`）、連接器用量表、資料鮮度矩陣。
- **grep 實證**：`grep -n "text-align:right\|tabular-nums" web.py` → **零筆命中**，代表全站沒有任何欄位針對數字做右對齊或等寬數字字體。
- **為什麼扣分**：Nansen/Messari 級財務型儀表板的通則是「文字左靠、數字右靠＋等寬數字」，讓使用者垂直掃視就能比大小；本站信任分數/成本/token 數全部跟文字一樣左靠、比例字寬，多幣比較或多列成本要靠肉眼對齊，違反基本資訊設計常識。
- **修法方向**：為數字欄位加 `.tf-num{{text-align:right;font-variant-numeric:tabular-nums}}`，套用到信任分數/成本/token 欄。
- **影響**：多列數字比較時（尤其 comparison 頁「相對強弱比較」表）判斷效率下降。
- **優先序**：P2（打磨級，但成本極低、CP 值高）。

### 6. 【P2】時間戳一律裸 ISO8601，未做人性化格式
- **位置**：`fetch_scheduler.py::_render_overview_html` 首頁多幣卡 `generated_at` 欄（`e(str(snap.get("generated_at", ...)))`，未做任何格式轉換）；`web.py:1933` 證據列 `ev.fetched_at`、`web.py:1985/1991` 價格溯源區塊的「基準資料時間／擷取時間」同樣直接印 `ev.fetched_at`。
- **grep 實證**：`grep -n "generated_at\b" scripts/fetch_scheduler.py` 與測試資料（`tests/test_snapshot_history.py:43,458`）確認欄位格式為 `"2026-07-01T19:00:00Z"` 這種帶 `T`/`Z` 的機器格式，沒有 `strftime`/相對時間轉換的程式碼路徑。
- **為什麼扣分**：世界級 app 一律顯示「2 分鐘前」或「07/01 19:00」，不會把機器可讀格式直接丟給終端使用者；本站首頁卡片與證據時間戳都是給工程師看的格式，字級又壓到 `.7rem`（289: `tf-coin-badge`旁）、`.72rem`(285 `tf-ev-date`)更難辨識。
- **修法方向**：純 stdlib（`datetime.fromisoformat` + 簡單相對時間計算，zero-JS 也能在 server 端算好字串再塞進 HTML）轉成「HH:MM」或「N 分鐘前」。
- **影響**：專業感被拉低，使用者要自行心算 UTC 時間差。
- **優先序**：P2。

## 建議這輪先修 3 個
1. **#1 對比+focus/aria**（P0，阻擋級，且是全站共用 CSS 變數，改一處全站生效，CP 值最高）
2. **#4 手機雷達 flex-wrap**（P1，核心信任判斷區塊在窄螢幕會出包，可行性高、只改一段 CSS）
3. **#5 數字右對齊+tabular-nums**（P2 但成本極低、視覺衝擊大，加一個 class 就能全站套用，屬於「花小力氣換明顯專業感」的項目）

（#2 IA 重排、#3 badge 統一、#6 時間格式化留待 Round 2/3，需要動 HTML 結構或新增格式化函式，改動面較大。）
