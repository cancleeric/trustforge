# QA-PLAN.md — 連結/CTA/端到端測試補強計劃

> 起因：2026-07 生產事故 —— 首頁 hero CTA「立即開始分析」在桌面版點擊零視覺
> 反應。事故當下 `pytest -q`（937 passed / 6 skipped）與離線 smoke test 全綠，
> 完全沒抓到，因為**現有測試裡沒有任何一項會驗證「連結/按鈕真的會把使用者
> 帶到有意義的結果」**。本文件盤點缺口、定優先序、給可執行的補強方案。
>
> 撰寫：QA Manager　最後更新：2026-07-03

---

## 0. 根因回放（先看事實，別靠記憶）

`src/trustforge/web.py`：

```
1453:  <a class="tf-hero-cta" href="#tf-query-console">立即開始分析 &#8594;</a>
...
 218:  .tf-layout{display:grid;grid-template-columns:290px minmax(0,1fr);gap:1.2rem;align-items:start}
 309:  <aside class="tf-query-panel" id="tf-query-console">
 320:  <main class="tf-dashboard">
```

- Hero CTA 是純錨點跳轉 `href="#tf-query-console"`，目標是**兩欄式版面
  （`.tf-layout` CSS grid）裡的側欄**，跟 hero 同時在頁面最上方、桌面上
  本來就同時可見。
- 桌面上點下去：瀏覽器原生錨點跳轉「已經在可視範圍內 → 零位移 → 零視覺
  回饋」。使用者判定為「按了沒反應」，是**真實 UX bug**，不是誤報。
- 對應既有測試（`tests/test_web.py:404-407`）：

  ```python
  def test_render_home_page_has_query_console_cta():
      """Hero CTA 導向左側 Query Console（錨點 `#tf-query-console`）。"""
      htmlout = web._render_home_page()
      assert 'href="#tf-query-console"' in htmlout
  ```

  這個測試**把 bug 的設計本身斷言成「預期行為」**——只驗證 href 字串存在，
  完全沒驗證「錨點目標是否可達」「點了以後使用者會不會感覺到任何事發生」。
  這是本次事故沒被攔下的直接原因，不是巧合。

---

## 1. 現況覆蓋缺口盤點（grep 實證）

| 類別 | 現況 | 證據 |
|---|---|---|
| 測試檔案數 | 35 個檔案、838 個 `def test_*`，`pytest -q` 937 passed / 6 skipped，0 failed | `tests/` 目錄、`pytest -q` 實跑 |
| 信任評分/信任層邏輯 | 覆蓋深、密集（trust scoring、cross-source signal、stance budget、conformal calibration 等） | `test_trust_scoring.py`、`test_w4_calibration.py`（1129 行）等 |
| 連接器/資料源 | 覆蓋深（cache、scheduler、safe_fetch、coingecko 等） | `test_connector_cache.py`、`test_fetch_scheduler_source_calls.py`、`test_safe_fetch.py` |
| Web render 字串斷言 | 大量，但**只驗證「某字串/CSS class/href 屬性存在於輸出 HTML」**，不驗證「這個元素背後的互動有沒有效果」 | `test_web.py`（622 行）、`test_home_overview.py`、`test_status_page.py`、`test_web_dark_theme.py` |
| **連結完整性**（href 是否可達） | **無**。grep `tests/*.py` 裡 `href` 共 43 處命中，全部是「斷言某個 href 屬性值等於某字串」，沒有一處反過來去 `GET` 那個 href 驗證回應 | 全檔逐一核對，見下方明細 |
| **CTA 可用性**（點了會不會有效果） | **無**。既有測試對 hero CTA 只斷言 `'href="#tf-query-console"' in htmlout`（`test_web.py:404-407`），沒有任何「錨點目標是否存在」「目標是否已在首屏可見」的檢查 | 同上 |
| **端到端使用者旅程**（首頁→分析→結果） | **部分**。`test_render_home_page_example_link_uses_real_analyze_query`（`test_web.py:410-422`）驗證「看範例報告」CTA 解析出的 query 餵給 `_do_analyze` 能出報告——這是本專案**唯一**一條接近「CTA→結果」端到端的測試，但只測了這一個 CTA，且是呼叫內部函式 `_do_analyze`，**沒有經過 `Handler.do_GET` 真的走一次 HTTP 路由** | `test_web.py:410-422` |
| **表單提交端到端**（選幣+送出→結果頁） | **有，但分散**。`_do_analyze`/`_do_comparison` 被大量測試直接呼叫驗證回傳值，但「使用者從 `/` 的 `<form action="/analyze">` 送出」這個路徑本身，沒有測試模擬「解析 form 的 method/action/欄位 → 組出等效請求 → 打 `/analyze` → 驗證結果頁含表單所選的幣種」 | 全檔搜尋 `action="/analyze"` 只在 `web.py:312` 出現，`tests/` 無對應斷言 |
| **瀏覽器/視覺互動**（scroll、click 視覺回饋、mobile breakpoint 實際渲染） | **無，且架構上做不到**——本專案 zero-JS 純 SSR、零外部 runtime 依賴，`test_web.py:536` 明文寫「未含 Playwright/Selenium」 | `test_web.py:536` |
| 覆蓋率量測工具 | **未安裝**。`python3 -m pip show pytest-cov coverage` → `WARNING: Package(s) not found`；`pyproject.toml` `[project.optional-dependencies] dev = ["pytest>=8"]` 僅此一項 | 實跑確認 |
| CI（`.github/workflows/ci.yml`） | 兩件事：`pytest -q` 全套跑；離線 smoke test 跑一次 CLI `analyze` 驗證 4 個交付件檔案存在。**沒有覆蓋率門檻、沒有連結檢查、沒有起 web server 打任何 HTTP 路徑** | `.github/workflows/ci.yml` |

**結論**：現有 838 個測試把「信任評分演算法」「資料連接器」「成本帳本」這些
*後端邏輯層*測得非常扎實（這也是為什麼 curl API 測試全綠），但整個**使用者
互動層**（連結是否可達、CTA 是否真的把人帶到有意義的地方、表單旅程是否
走得通、視覺回饋是否存在）幾乎是空白。這正是這次 bug 的根因分類，不是單一
個案，是一整個測試金字塔缺了一層。

---

## 2. 連結 & CTA 完整性測試（最高優先 P0 —— 直接防這次 bug）

### 設計原則（zero-dep 前提下）

專案是純 SSR、無外部 runtime 依賴（無 Playwright/Selenium/瀏覽器）。但
`tests/test_web.py:375-392` 已有一個**現成、好用的 test client 樣式**：

```python
def _do_get(path: str) -> tuple[int, str]:
    """端到端呼叫 Handler.do_GET（不開真 socket），回傳 (status_code, body)。"""
    h = web.Handler.__new__(web.Handler)
    h.client_address = ("127.0.0.1", 12345)
    h.path = path
    h.wfile = BytesIO()
    h.headers = Message()
    captured = []
    h.send_response = lambda code: captured.append(code)
    h.send_header = lambda name, val: None
    h.end_headers = lambda: None
    h.do_GET()
    return captured[0], h.wfile.getvalue().decode("utf-8")
```

這個 harness 直接呼叫 `Handler.do_GET`，完整走過真實路由邏輯（限流、模式判斷、
`_do_analyze`/`_do_comparison`、`_send`），只是不開 TCP socket——這就是我們
在 zero-dep 限制下能做到的「test client」，不需要瀏覽器就能驗證「連結真的
route 得到」。**建議把它從 `test_web.py` 私有函式提升成 `tests/_harness.py`
共用工具**（CTO 實作項，見第 7 節）。

### 測試矩陣

對 `/`、`/analyze`（預設參數）、`/status`、`/costs` 四個頁面各自渲染出的
HTML，抽出所有 `<a href="...">`，依類型分流驗證：

| href 類型 | 驗證規則 |
|---|---|
| `#fragment` | (a) 目標 `id="..."` 必須存在於**同一次回應的完整頁面 HTML**（含 header + aside + main，不能只測 `_render_home_page()` 片段，因為側欄 `id="tf-query-console"` 是在 `render_page()` 才組進去）——**死錨點**檢查 |
| 內部路徑（`/analyze`、`/analyze.json`、`/costs`、`/status` 等，含 query string） | 用 `_do_get(href)` 真的打一次，斷言：狀態碼 200、body 不含 `Traceback`/例外錯誤頁樣式、body 含與該頁面身分相符的關鍵字（如 `/status` 頁要含「系統狀態」等既有斷言慣例） |
| 外部連結（`https://...`） | 建立 allowlist（目前僅 Google Fonts 兩個 preconnect），不在 allowlist 內的外部連結測試直接 fail，逼開發者明確登記，避免未來新增外部連結漏審 |
| `javascript:`/`data:`/其他危險 scheme | 應該完全不存在（`_safe_href` 已有 XSS 防護測試覆蓋，這裡只是交叉確認不會漏網） |

### CTA「非死錨點但零視覺回饋」的可測部分

這次 bug 的本質不是死錨點（`#tf-query-console` id 確實存在），而是**目標
與觸發元素在桌面版面下同時可見、跳轉零位移**。SSR 字串測試做不到真正的
視覺位置計算，但可以做一個**結構性代理檢查（proxy check）**：

> 若某個 CTA 的錨點目標 id，落在頁面最上方 `.tf-layout` 兩欄式 grid 容器
> 內的「另一欄」，且頁面中找不到任何 scroll-margin / focus 動畫等視覺
> 回饋 hook（`scroll-margin`、`data-tf-scroll-behavior`、`tf-cta-flash` 等
> 慣例命名），就標記為高風險，測試 `xfail` 留痕，逼修法（改連真頁面、
> 加 CSS scroll-margin + 過場動畫、或把 CTA 移出首屏同步可視區）。

**已寫成骨架**：`tests/test_link_integrity.py`（見第 8 節，已標明 DRAFT，
目前跑起來是 2 passed + 1 xfail，xfail 那條就是精準對應本次事故的
regression 標記）。

---

## 3. 表單提交端到端測試（P0，本次一併補齊）

現況：`_do_analyze`/`_do_comparison` 有大量單元測試，但**沒有一條測試是
「模擬使用者在 `/` 頁面的 Query Console 表單選幣、選題型、輸入問題、按下
Run analysis」這個完整旅程**（`<form action="/analyze" method="get">`，
`web.py:312-317`）。

補齊項目：
1. 從 `/` 首頁渲染結果解析出 `<form action="/analyze" ...>` 的欄位預設值
   （`<select name="coin">`、`<select name="type">`、`<textarea name="q">`）。
2. 用解析出的欄位值組出等效 `/analyze?coin=...&type=...&q=...` query string
   （GET 表單語義），透過 `_do_get` 真的打一次。
3. 斷言結果頁：狀態碼 200、含所選 coin 名稱、含三層架構（事實/推論/結論）
   關鍵字、不是 error 頁。
4. 額外情境：切換到 `comparison` 題型、`coin=BTC,ETH` 這種複選格式，確認
   comparison 分流（`web.py:2756` 起）走得通。

（`test_render_home_page_example_link_uses_real_analyze_query` 已經是這個
模式的雛形，只是測「看範例報告」CTA 而非「Query Console 表單」本身，且未
經過 `Handler.do_GET`——直接擴充/仿造即可，不用重新設計。）

---

## 4. 關鍵使用者旅程 e2e（SSR 層級可測部分）

用 `_do_get` harness 串接完整旅程，每一步都斷言「上一步產出的連結，下一步
真的能到」：

| 旅程 | 步驟 |
|---|---|
| 首次訪客 | `/` (200，含 hero 文案) → 解析 hero CTA/範例 CTA href → 逐一 GET → 200 |
| 完整分析旅程 | `/` → 解析 Query Console 表單預設值 → GET `/analyze?...` → 200，含報告三層架構 → 解析結果頁裡的「下載 JSON」`/analyze.json` 自我連結（`_analyze_json_href`，`web.py:2553`）→ GET → 200 + 合法 JSON |
| 系統狀態旅程 | `/` → header 極簡連結 `/status`（`web.py:1829`）→ GET → 200 → 頁內 `/costs` 連結（`web.py:1861`）→ GET → 200 |
| 錯誤路徑旅程 | `/analyze?type=不存在的題型` → 應 fallback 到 `multi_source`（既有邏輯，`web.py:2708` 起）而非 500；`/analyze?coin=` 空值 → 應有合理錯誤處理而非 Traceback 洩漏 |

這批測試建議放 `tests/test_user_journeys.py`（新檔，本次未建立，列入 CTO
Phase 2 待辦，理由：需要先把 `_do_get` 從 `test_web.py` 抽成共用 harness，
避免三個新測試檔各自重複定義）。

---

## 5. 覆蓋率量測

- **現況**：`coverage`/`pytest-cov` 均未安裝（`pip show` 確認 not found），
  `pyproject.toml` dev 依賴只有 `pytest>=8`，CI 沒有任何覆蓋率步驟。
- **建議**：
  1. `pyproject.toml` `[project.optional-dependencies].dev` 加入
     `pytest-cov>=5`。
  2. CI 步驟改為 `pytest -q --cov=trustforge --cov-report=term-missing
     --cov-fail-under=<門檻>`。
  3. 門檻建議**分層設定**而非全域單一數字（因為信任層邏輯已經測得很深，
     web 互動層才是新戰場，用同一個全域數字會掩蓋 web.py 的真實缺口）：
     - `src/trustforge/web.py`：目前互動層測試補齊前，先設一個保守值
       （例如 75%）並每季調高，避免一次卡死 CI。
     - 其他模組（trust/、ingestion/、agent/）：現況已高，直接沿用現有
       水準訂為門檻下限，防止未來新增程式碼降低既有覆蓋率。
  4. `--cov-fail-under` 門檻值需 CTO/QA 依實際跑出的數字再校準，本文件先
     訂方向、不先射飛鏢式訂死數字。

---

## 6. UI 視覺 / 真瀏覽器 —— SSR 測試的天花板（需人工介入）

**明確聲明**：本文件第 2～5 節全部是 SSR 層級（HTML 字串/HTTP 狀態碼）的
自動化測試，**抓不到**：
- 桌面 vs 手機的實際版面位移（本次 bug 正是這一類）
- 點擊後的視覺回饋（動畫、focus outline、scroll 位移量）
- 字型/CSS 是否真的載入生效
- Loading 狀態的真實觀感（是否有轉圈圈、按鈕是否 disable 防止重複點擊）

**因此**：任何牽涉首頁/CTA/表單的改動，上生產前**必須**過一次人工 Chrome
checklist（比照 CLAUDE.md「UI 優化工作流程」：Chrome MCP 截圖審查 + 人工
確認），不能只看 CI 全綠就上線。

### 上生產前必跑 Chrome 手動 checklist

- [ ] 桌面版（≥1024px）：點首頁每一個 CTA（hero「立即開始分析」、「看範例
      報告」、header「系統狀態」、頁尾/表格內所有連結），確認**每次點擊都有
      可觀察的視覺反應**（頁面跳轉、內容變化、或至少捲動位移/高亮）
- [ ] 手機版（≤480px，實際縮小視窗或用裝置模擬）：同上，額外確認單欄
      版面下錨點跳轉的捲動距離合理（不是跳到看不出差異的位置）
- [ ] Query Console 表單：選不同幣種/題型、輸入問題、按 Run analysis，
      確認結果頁與所選內容一致
- [ ] Loading/等待狀態：`real`/`live` 模式下送出分析，確認等待期間有
      明確視覺提示（而非畫面凍結讓使用者以為當機）
- [ ] 深色主題（本專案固定 dark，`web.py` 註解已載明 toggle 已拆除）：
      確認所有文字對比度、按鈕邊界在 dark 背景下清楚可辨識
- [ ] 錯誤情境：故意打錯 query（如不存在的 coin）、429 限流觸發，確認
      錯誤頁文案清楚、不是白屏/Traceback
- [ ] 用瀏覽器「檢查元素」確認本次事故同款問題已修：hero CTA 點擊後，
      若目標與觸發元素本就同框可見，是否已改為有效互動（改連真頁面 /
      加捲動位移 / 加高亮動畫，三選一，見第 7 節 CTO 選項）

---

## 7. 分階段執行計劃

| 階段 | 內容 | 負責 | 驗收標準 | 優先序 |
|---|---|---|---|---|
| Phase 0（已完成，本輪） | 缺口盤點、`docs/QA-PLAN.md`、`tests/test_link_integrity.py` 骨架（3 條測試：死錨點檢查、內部連結 200 檢查、hero CTA regression xfail 標記） | QA | 本文件 + 骨架測試檔可執行且不破壞現有 937 綠燈 | 已完成 |
| Phase 1 | (1) 把 `_do_get` 從 `test_web.py` 抽成 `tests/_harness.py` 共用；(2) 把 `test_link_integrity.py` 骨架擴充到涵蓋 `/analyze`、`/costs`、`/status` 四頁全連結矩陣；(3) **修正生產 bug 本身**：hero CTA 改為有意義導向（建議：直接連 `/analyze?...` 帶預設參數的真實分析頁，而非錨點跳轉——同時解掉「桌面零反應」與「死錨點風險」兩個問題根源）；(4) 移除/重寫 `test_web.py:404-407` 那條把 bug 斷言成預期行為的測試 | CTO 實作，QA 驗收 | `test_hero_cta_is_not_pure_dead_end_anchor` 從 xfail 轉為 pass（拿掉 xfail 標記後綠燈）；Chrome checklist 第一項全過 | **P0** |
| Phase 2 | 表單提交端到端（第 3 節）+ 關鍵使用者旅程 e2e（第 4 節），新增 `tests/test_user_journeys.py` | CTO 實作，QA 驗收 | 至少 4 條旅程測試（首次訪客/完整分析/系統狀態/錯誤路徑）全綠 | P1 |
| Phase 3 | 覆蓋率量測接入（第 5 節）：`pytest-cov` 依賴、CI gate、分層門檻 | CTO 實作，QA 訂門檻 | CI 顯示覆蓋率報表；`web.py` 覆蓋率較 Phase 0 基準提升（因 Phase 1/2 新測試而自然提升），CI 有 `--cov-fail-under` gate | P1 |
| Phase 4 | Chrome 手動 checklist 正式納入發版 SOP（console/ 或颶風官網既有 UI 優化工作流程比照辦理，寫入 release checklist） | QA + COO（發版流程） | 下一次任何觸及首頁/表單的 PR，checklist 有簽核紀錄 | P1 |

---

## 8. 本輪已交付的骨架（順手加，非正式驗收項）

`tests/test_link_integrity.py`（新檔，檔頭已標明 `[DRAFT / 骨架]`）：

```
tests/test_link_integrity.py::test_home_page_has_no_dead_fragment_anchors    PASSED
tests/test_link_integrity.py::test_home_page_internal_links_route_to_200    PASSED
tests/test_link_integrity.py::test_hero_cta_is_not_pure_dead_end_anchor     XFAIL
```

- 前兩條是可直接留用的死錨點/內部連結 200 檢查，用既有 `_do_get` harness
  同款寫法（不開真 socket），零新依賴。
- 第三條刻意用 `pytest.xfail` 標記本次事故的根因模式（CTA 目標與 hero 同
  框可見、無視覺回饋 hook）——**這是一個「已知缺陷留痕」測試，不是通過
  的測試**，CTO 修完 bug 本身後，把 xfail 條件拿掉，這條測試會自然轉綠，
  變成永久 regression guard。
- 全套 `pytest -q` 跑過，含新骨架在內全部通過（2 passed + xfail），未破壞
  既有 937 passed / 6 skipped 基準。
- **未做**：未修改 `src/trustforge/web.py` 任何一行（遵守本次任務範圍：
  QA 只寫計劃 + 測試骨架，不碰 production code，bug 修復留給 CTO Phase 1）。
