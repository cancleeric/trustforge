# 下一步世界第一深度優化計劃（非-gated 專案）

> CPO gray 撰｜2026-07-04｜範圍：**只排非-gated**（不需老闆 key／Bedrock／新
> 資料的項目）。前後端分離已 **100% LIVE**（`curl https://trustforge.hurricanesoft.com.tw/`
> 200，React 靜態檔由 nginx 直出；`/api/health` 回 `v0.5.16`；`git log`
> 頭 6 commit 為 Phase1-3 cutover + task#28 全系列）。全文逐項 grep 實證，
> 行號/函式名皆對照本輪 checkout 的原始碼，非憑記憶。

---

## 0. 方法論與範圍聲明

- 每項先寫「現況（grep 實證）」，再寫「缺口」「改法」「credit-safe/#24
  影響」「驗收標準」「工作量」。
- **明確排除 gated**（列在 §5，不排進本輪）：資料密度 Batch3（需老闆申請
  key）、W2 真深度 materialize（需額外 Bedrock 預算）、conformal/W3 全圖
  （需異質歷史資料/帳號級 author 欄位）。
- 排序原則：對「世界第一信任誠實性」CP 值最高者優先——**修一個會讓使用者
  對「多少獨立來源支持某論點」產生錯誤認知的計數 bug，比新增一個好看的
  功能更貼近誠實性核心承諾**。

---

## 1. 【P0，本輪最優先】#13 跨源分歧「來源數」按 source 去重

### 現況（grep 實證）

- 產生分歧配對的入口：`src/trustforge/agent/orchestrator.py:267-314`
  `_detect_stance_pairs()`——掃描情緒類主張中「不同來源、方向相反」的候選
  配對，交給 `stance_fn` 判斷是否為真矛盾。**去重鍵是 `claim.id`**
  （`orchestrator.py:305-307` `seen_claim_ids`），**不是 `source`**：
  ```
  if sc.claim.id not in seen_claim_ids:
      seen_claim_ids.add(sc.claim.id)
      pairs.append({"source": ..., "stance": ..., "claim_id": ..., "text": ...})
  ```
  同一個來源若有兩則「不同 claim_id」的主張，各自跟不同的對手配對成功，
  會被各自加入 `pairs` 兩次——**該來源在最終清單裡出現兩次**。
- 消費端 **React 即時 UI**：`frontend/src/components/CrossSourceSignalPanel.tsx:3-11`
  `groupByStance()` 直接把 `signal.stance_pairs` 依 `stance` 分兩堆，
  `SideColumn` 逐筆 `items.map()` 渲染（第 13-36 行），**沒有任何依
  `source` 去重的邏輯**——若同一來源出現兩筆，畫面上 BULLISH／BEARISH
  欄會列出兩張卡片、視覺上等同「兩個獨立來源支持」，但實際只有一個來源。
- 舊 SSR（`src/trustforge/web.py:2251-2320`，**已確認不在生產路徑**——
  `deploy/nginx.conf:66-132` 對 `/` 走 `root /opt/trustforge/frontend/dist`
  靜態檔，只有 `/api/*` 才轉給 8080 後端）曾有更直白的數字化版本：
  `web.py:2320` `count_label = f"看漲 {len(bullish)} 來源 · 看跌 {len(bearish)} 來源"`
  ——`len(bullish)` 是「配對筆數」不是「不同來源數」，此為使用者原始回報
  這個 bug 時所指的具體現象。React 重寫時雖然拿掉了這個數字徽章，但
  **底層資料結構的去重缺陷原封不動繼承過去**，只是換了個更隱晦的呈現
  方式（同來源卡片重複出現，而非顯性數字虛高）。
- **對照組**：同一份程式碼裡已經有「做對」的例子可以直接抄——
  `trust/scoring.py:1408` `n_contrarian_sources = len({sc.claim.doc.source for sc in contrarian})`
  （set comprehension 去重）、`agent/orchestrator.py:232`
  `sources = {ev.source for ev in kind_evidence}`（雷達分維 `n_sources`，
  已正確去重）。**#13 是全 repo 三處「來源計數」邏輯中唯一沒去重的一處**。

### 缺口

「同一來源的多個矛盾主張」被當成「多個獨立來源支持某方向」呈現，直接
違反「信任誠實性」核心承諾——這比 UI 好不好看更嚴重，是**資訊正確性
bug**。

### 改法

1. `_detect_stance_pairs()` 回傳前，在組出 `pairs` list 之後、回傳之前
   加一層「同一 stance 陣營內按 source 去重」：同一來源在同一方向
   （bullish 或 bearish）只保留一筆代表（建議取 `trust` 最高或最新的
   `claim_id`），**跨陣營不去重**（同一來源自己左右互搏、一則偏多一則
   偏空，這是另一個「來源自我矛盾」訊號，不該被吃掉，反而該獨立標記——
   但本輪先只修「同陣營重複」這個明確虛高問題，跨陣營自我矛盾列 follow-up
   觀察，不在本輪擴大範圍）。
2. 新增一個明確欄位而非改變既有 `stance_pairs` 語意：例如
   `"distinct_sources": {"bullish": [...], "bearish": [...]}`（去重後的
   來源清單），前端優先讀這個欄位做計數／去重渲染，`stance_pairs` 保留
   原始逐筆明細供展開查看（向後相容，不砍既有欄位語意）。
3. React `CrossSourceSignalPanel.tsx` 的 `groupByStance()` 改用後端給的
   去重清單，或前端自行以 `source` 為 key 做 `Map` 去重（後者不需等
   後端改動，可先做——但**後端才是真正的資料源頭**，前端去重只是治標，
   建議前後端一起修，後端負責語意正確、前端負責呈現）。

### credit-safe / #24 影響

零成本、確定性邏輯（純 Python set/dict 操作），不觸發任何新 API 呼叫、
不改變 `stance_fn`（Bedrock 呼叫）本身的呼叫次數或預算分配
（`_StanceBudget` 配額邏輯不受影響，去重發生在 `stance_fn` 呼叫**之後**）。
不違反 #24——不是「為了好看而製造資料」，反而是「拿掉一個讓資料看起來
比實際更多的假象」，方向完全一致。

### 驗收標準

- 新增/擴充測試（`tests/test_cross_source_signal.py` 現有 12 個測試 +
  `tests/test_stance_budget_sharing.py` 現有 6 個 + `tests/test_tier2_divergence.py`
  現有 25 個，共 43 個既有測試需回歸跑綠）：
  1. 同一來源兩則不同 claim、皆與不同對手配對成功且同陣營 → 去重後只
     出現一次。
  2. 同一來源兩則不同 claim、分屬不同陣營（自我矛盾）→ 兩陣營各自保留
     （不誤刪），並可選擇性附加「來源自我矛盾」旗標供未來擴充。
  3. 既有「不同來源各一則」案例 → 行為逐字不變（回歸鎖）。
- 前端 `frontend/src/lib/*.test.ts` 補一個 `groupByStance`/去重函式的
  vitest 單元測試（現況 2 個測試檔 53 個測試全綠為基準）。
- 人工驗收：構造一個「同來源兩則相反方向新聞」的假想輸入跑一次
  `detect_cross_source_signal`，確認回傳的去重清單長度符合預期。

### 工作量

**小**（半天內）。改動集中在 `orchestrator.py` 一個函式 + 前端一個
小組件，無架構變更、無新資料流。

---

## 2. 【P1，第二優先】#20 主題切換（dark/light）——React 架構下已無 SSR 時代的持久化障礙

### 現況（grep 實證）

- 舊 SSR 拆掉 theme toggle 的原始理由（`src/trustforge/web.py:2212-2220`，
  PR #39）：**process-local render cache**（`rtok`）在無狀態 SSR 架構下
  「切主題不重跑 pipeline」與「不遺失已產出報告」兩者無法兼得——切主題
  等同一次新的 GET 請求，若 cache miss 就要重新真的打一次連接器/Bedrock，
  等於「切個顏色也要重新分析」，或退而求其次接受「使用者已看到的真報告
  被主題切換清空」，兩者都不可接受，於是收斂成 dark-only，明文寫下
  「等 #20（結果持久化）做對後再重新開放」。
- **React 架構下這個前提已經不存在**：分析結果現在是 **client-side
  React state**（`frontend/src/pages/AnalyzePage.tsx`，`fetch` 一次
  `/api/analyze` 後存進元件 state），主題只是切換 `<html data-theme>`
  屬性 + CSS 變數，**完全不觸發任何 API 呼叫、不重新 fetch、不影響已存在
  的 React state**——舊 SSR 的「持久化」問題本質是「HTML 是 server 生
  出來的、換膚要重新產生 HTML」，React SPA 沒有這個問題，這是前後端分離
  的直接紅利，而非需要另外做的新工程。
- **但 React 的 CSS token 目前只有 dark、沒有 light 變體**：
  `frontend/src/index.css:5-25` 的 Tailwind v4 `@theme` block 直接寫死
  `--color-tf-bg:#0d1117` 等 dark 色票，**沒有 `data-theme="light"`
  對應的 override 區塊**（跟舊 SSR 不同——舊 SSR 有把 light 色票保留在
  `web.py:278` `:root[data-theme="light"]{--tf-bg:#f6f8fa;...}`，React
  重寫時沒有原樣搬過去，是**淨損失**，需要補回）。
- 前端目前完全沒有 theme 相關程式碼：`grep -rn "theme" frontend/src`
  只命中 `index.css:5`（`@theme` Tailwind 語法本身）跟一行註解，
  無 `ThemeToggle` 元件、無 `localStorage`/`prefers-color-scheme` 邏輯。

### 缺口

1. 需要在 `index.css` 補回 light 色票（可直接沿用 `web.py:278` 已存在、
   曾實際上線過的色值，不是新設計，零市場調查成本）。
2. 需要一個 `ThemeToggle` 元件 + 簡單的 `localStorage` 持久化（跨 session
   記住偏好）+ 初始值抓 `prefers-color-scheme`（尊重系統偏好）。
3. 需要過一次**對比檢查**：light 色票是 2026 年初（PR #39 之前）就存在
   的舊值，套用到現在 React 重寫後的元件（badge/pill/表格）上要重新肉眼
   檢查一次對比度，不能假設舊色票在新排版下依然達標。

### 改法

- `index.css` 新增 `:root[data-theme="light"]` 或 Tailwind v4 的
  `@theme` 條件變體（依 Tailwind v4 語法選一種、跟現有 `@theme` block
  風格一致）；色值來源：`web.py:278`
  `#f6f8fa/#ffffff/#d0d7de/#1f2328/#57606a/#6e7781`。
- `Header.tsx`（`frontend/src/components/Header.tsx:11-41`）右側現有
  版號徽章旁加一顆極簡切換按鈕（圖示即可，不需文字，符合現有 nav 精簡
  風格）。
- 狀態管理：不需要引入新狀態庫（無 Redux/Zustand），一個
  `useState` + `useEffect` 寫 `localStorage` + 設定 `document.documentElement.dataset.theme`
  即可，符合現有專案「無額外狀態庫」的精簡選型。
- 初始值：`localStorage` 有值用它；沒有則讀
  `window.matchMedia('(prefers-color-scheme: light)')`；都沒有預設
  dark（維持現狀行為，向後相容截圖/E2E 基準）。

### credit-safe / #24 影響

零成本，純前端 CSS/localStorage，不觸發任何後端呼叫、不影響
分析結果本身或信任演算法。**#24 不適用**（不涉及資料真實性宣稱）。

### 驗收標準

- 新增 vitest 元件測試：切換後 `document.documentElement.dataset.theme`
  正確變化、`localStorage` 正確寫入/讀回。
- 人工 QA：全站每頁（首頁/分析/比較/狀態/成本/歷史）在 light 模式下
  跑一次視覺檢查，確認 badge/pill/表格文字對比可讀（沿用 UXUI-ROUND-01
  的 4.5:1 標準複查一次，不是重新做一輪稽核，只是「舊色票套新排版」
  的一次性複查）。
- 不需要新增後端測試（此項純前端範圍，`pytest` 基準不受影響）。

### 工作量

**小-中**（1 天內）。CSS 補值是機械工作，主要時間花在切換元件 + 全站
light 模式人工複查對比度。

---

## 3. 【P2，可選加分】Axis D #3 跨幣信任×操縱風險排行

> 補充項目，非使用者原始點名的 3 項之一，但 grep 確認**現在就能做、
> $0、不需持久化**，且是既有 master plan（`WORLD-FIRST-MASTER-PLAN.md`
> D 段第 3 項）已排入但尚未執行的項目，一併納入本輪供老闆一起決策。

### 現況（grep 實證）

- `scripts/fetch_scheduler.py:661-682` `_snapshot_dict()`：目前只寫
  `coin/trust_score/direction/calibrated_confidence/decision_state/generated_at`
  （+ 選填 `reputation_trace`），**完全沒有任何 kind 分項或操縱風險
  （`manip`）欄位**。
- `agent/orchestrator.py:189-242` `aggregate_trust_by_kind()`（雷達已用
  的函式）已經算好 `n_sources/n_evidence/trust` 分維資料，**操縱風險
  分項** 在 `trust/scoring.py:1279` 附近的 `ScoredClaim.components` 也
  已存在（`reputation/corroboration/rec/manip`），只是**沒有任何一處
  把 `manip` 寫進 snapshot**。
- `grep -rn "ranking\|排行\|leaderboard" frontend/src` **零命中**——
  前端沒有任何排行/排序頁面或元件，首頁 `OverviewCard` 是無序 grid。

### 缺口

信任分快照已經逐幣定期寫入（Axis C 既有機制），但缺一個「操縱風險」
欄位跟一個「橫向排序」呈現層——兩者皆是小改動，非架構級工程。

### 改法

1. `_snapshot_dict()` 加一行：從 `report`/`evidence` 既有欄位取出
   `manip` 分項平均值（或沿用雷達已算好的聚合邏輯），寫入
   `snap["manip_risk"]`（沒資料則不寫鍵，向後相容，遵循既有 #24 原則：
   「該幣本輪沒算出來就不補假值」）。
2. `/api/overview` 端點回傳的多幣總覽資料補這個欄位。
3. 前端 `HomePage.tsx` 加一個「操縱風險排行」小區塊，用既有 `OverviewCard`
   資料源做 client-side sort，不需新 API、不需資料庫。

### credit-safe / #24 影響

用「當下最新一筆」snapshot 做即時排行，不需歷史資料，**不觸碰持久化
架構決策**（跟 Axis D #1 PIT 趨勢是兩件獨立的事——#1 已做，這個不依賴
它）。`manip_risk` 缺資料時嚴格不寫鍵、不補 0，前端排行榜對缺資料的幣
明確標示「暫無操縱風險評分」而非悄悄排最後或排最前造成誤導。

### 驗收標準

- `tests/test_snapshot_history.py` 或新測試檔驗證 `manip_risk` 欄位
  在有/無資料兩種情況下的行為。
- 前端排行榜元件測試：正確排序、正確處理缺資料幣種的顯示。

### 工作量

**中**（1-2 天）。涉及 scheduler + API + 前端三處小改動，但每處都是
在既有資料結構上加欄位，不是新建管線。

---

## 4. 【P3，較高風險，需審慎排期】#15 W3 單源爆量 per-window 重新設計

### 現況（grep 實證，`trust/scoring.py:585-661` `_coordination_burst_flags()`）

- 目前**已寫好但停用**（`scoring.py:715-716` `_coordination_signals()`
  裡明確註解 `# W3 burst 指標降級 follow-up #15：per-window anomaly
  需正確重設計，暫不啟用` 並把呼叫該函式的迴圈整段留白/comment out）。
- 演算法本體（`_max_distinct_in_rolling_window`、
  `_distinct_text_count_in_range`、`_coordination_burst_flags`，
  `scoring.py:540-661`）：對每個來源獨立找出「60 分鐘滾動視窗內最大
  相異文本數」（`cnt`），再對齊到**同一段時間窗**去看其他來源在那個
  具體時段各自發了幾則、取中位數（leave-one-out，候選自己不計入），
  `cnt ≥ median × 3` 才判定爆量。
- **經 4 輪 codex 對抗審已知的根本缺陷**（`scoring.py:667-720` 大段
  註解自陳）：
  1. 中位數比較基準是「候選來源自己的爆量時段」對齊其他來源在**同一
     時段**的產出量——但各來源的「最大視窗」本身是各自獨立找的極值，
     不是同步滑動視窗下的同時比較，統計上有「用自己的極值時刻去比較
     別人的平常時刻」的偏誤風險。
  2. 「其餘來源在候選爆量當下同窗內完全沒有主張」時保守取 median=0 →
     `cnt/median` 除零，目前用「任何 ≥1 都不算爆量」規避，但這代表
     **極端稀疏資料下的爆量偵測完全失效**（真實新聞連接器密度目前
     10-20 源，稀疏時段常見）。
  3. 固定牆鐘視窗（60 分鐘）不會隨資料密度動態調整，密度低的來源
     （如監管類單源）跟密度高的來源（新聞 12 源）用同一把尺，統計
     意義不對等。

### 缺口

這不是「還沒做」而是「做了但正確性有已知缺陷，主動降級」——**重新
設計需要解決統計方法論問題**（如何定義「同步」比較基準、如何處理
稀疏資料的除零/退化案例、視窗大小是否該依來源密度動態調整），這比
單純的程式碼修改複雜得多，屬於「演算法研究」而非「工程修 bug」。

### 改法（方向性，非定案，需要先做小型方法論驗證）

1. **候選方向 A**：改成「全域同步滑動視窗」——不是各來源各自找最大
   視窗再對齊比較，而是對**所有來源**用同一組固定時間切點（如整點
   對齊的 60 分鐘桶）分桶計數，同一桶內跨來源比較，避免「用自己的
   極值時刻」的偏誤。缺點：固定切點可能把一個真實爆量事件切成兩半，
   稀釋訊號。
2. **候選方向 B**：稀疏時段明確回傳「樣本不足，無法判定」而非默默用
   `cnt≥1` 短路規避，誠實反映「這個訊號在低密度連接器上目前不可靠」
   （符合 #24 誠實原則精神——寧可不判定，不要用會除零的規則硬湊）。
3. 兩個方向都需要先用**現有真實資料**（不新增資料源）跑一次模擬分佈，
   看實際的來源密度/時間分佈長什麼樣子，再決定哪種設計在目前資料規模
   下站得住腳——這步驟本身不需要新資源，但需要時間做資料探索與統計
   驗證，不能像 #13/#20 一樣直接動手改。

### credit-safe / #24 影響

**高風險項目**：W1.5 2b 的前車之鑑（`WORLD-FIRST-ANALYSIS.md:114`
「三輪後 revert」）顯示，這類「用規則湊出判定」的模組容易在對抗審查
下被抓到新的邊角案例，反覆修補的成本可能超過效益。**建議本輪不直接
動工實作，先做一輪小型資料探索/方法論驗證**（半天到一天），確認候選
方向在真實資料分佈下不會重演「規則脆弱、越修越糟」的模式，再決定是否
排入下一輪實作。

### 驗收標準（若進入實作階段）

- 新方法論需通過至少 3 輪對抗審（比照 W1.5 的既有品管流程），且不能
  比現有「保守停用」的狀態更容易誤判（寧可繼續 informational-only 不
  上線，也不能上線一個新的、同樣脆弱的判定）。
- 需要针對稀疏資料（單源、低密度連接器）跟密集資料（新聞 12 源）各自
  設計獨立測試案例。

### 工作量

**大**（研究型，工作量不確定，估至少 3-5 天含對抗審查往返，且有「做完
發現方法論仍不站得住腳、決定繼續 wontfix」的風險）。**本輪建議只排
「資料探索驗證」半天工作量，不排「實作上線」**。

---

## 5. Gated 清單（本輪明確排除，等資源）

| 項目 | 卡在哪 |
|---|---|
| 資料密度 Batch3（CryptoPanic/Etherscan/Reddit OAuth） | 需老闆申請 API key，非技術缺口 |
| W2 真深度（token-gated materialize） | 需額外 Bedrock 預算評估 |
| 真 Split Conformal（W4 全量） | pseudo-AUC≈0.49 等同隨機，缺異質歷史資料，非資源問題是資料本質不足 |
| W3 真帳號二部圖+Louvain | 連接器無 author 欄位，屬資料卡，非技術問題 |
| Axis D #4 來源動態信譽榜 | 依賴 #1 PIT 持久化 + 接出 `reputation_trace`，架構依賴鏈較長 |
| 信任分資訊正確性判別力驗證 | 需人工標註歷史真偽事件集，尚未開始，非本輪範圍 |

---

## 6. 附帶發現（housekeeping，非本輪待辦，僅記錄避免誤判）

- **`fix/ui-commercial` 分支已過時**：`git diff main fix/ui-commercial
  --stat` 顯示 105 個檔案、+342/-20644 行差異——這個分支是在前後端
  分離（Phase 1-3, task #28）**之前**切出的，若照舊 master plan
  「D 段第 1 項：merge fix/ui-commercial」執行，等同**用舊 SSR 版本
  覆蓋掉已上線的 React 前端**，是嚴重回歸風險。經抽查，該分支當初要修
  的 4 個問題（表單斷/裸錯誤頁/無回首頁/卡片 hover）在 React 重寫中
  已獨立解決：`frontend/src/pages/NotFoundPage.tsx` 有「回首頁」連結、
  `frontend/src/components/OverviewCard.tsx:27` 已有 `hover:` 樣式。
  **建議**：確認無誤後關閉此分支，不併入 main，master plan 該項目
  應標記「已被 React 重寫取代，非待辦」。
- **UXUI-ROUND-01.md 稽核項目多數已被 React 重寫吸收**：P0 無障礙
  （`ConfidenceGauge.tsx:34-35` 已有 `role="img"`/`aria-label`，
  `QueryConsole.tsx` 表單已有 `htmlFor`/`id` 完整配對，`index.css:45-48`
  已有 `:focus-visible`）、P1 badge 圓角統一（`Badges.tsx:109-110`
  明確註解「膠囊 vs 方標籤」兩類 token，對照 UXUI-ROUND-01 #3 建議）、
  P1 手機 flex-wrap（`TrustRadarChart.tsx:66` 已有 `flex-wrap`）、P2
  時間人性化（`frontend/src/lib/format.ts:22-29` 已有「N 分鐘前」邏輯）
  皆已到位。**唯一未見對照的是 P1 IA 重排（分析頁單欄變並排）**——
  React 版面是否已解決需另外抽查 `AnalysisReportView.tsx` 排版，不在
  本輪 grep 範圍內，列為下一輪稽核起點。
- **舊 SSR（`web.py`）中的 zero-JS 限制產生的已知小缺陷**（表單防重複
  送出只是視覺 best-effort、query string 部分參數未 percent-encode）
  **在生產環境已無關緊要**：`deploy/nginx.conf` 確認只有 `/api/*` 走
  8080 後端，`/` 等路徑一律由 React 靜態檔處理，這些 SSR HTML 渲染路徑
  已非公開可達。技術債清理（是否移除 `web.py` 內已死的 HTML render
  程式碼）屬 CTO 範圍，非本次品質規劃重點，僅記錄避免誤判為待修 bug。

---

## 7. 本輪建議順序

1. **#13 分歧來源去重**（§1）——先做。理由：這是三處「grep 對照組」中
   唯一沒去重的資訊正確性 bug，直接關係「信任誠實性」核心承諾，工作量
   小（半天），風險低（純確定性邏輯、43 個既有測試可回歸鎖），且已在
   **生產環境**可被使用者實際看到（React `CrossSourceSignalPanel` 同源
   重複卡片）。CP 值全計劃最高。
2. **#20 主題切換**（§2）——緊接著做。理由：前後端分離拿掉了 PR #39
   當初擋住這個功能的根本障礙（process-local render cache），現在補
   一個功能等於「兌現已還完的技術債」，工作量小-中，且是使用者/評審
   會直接感知到的體驗完整度（世界級 dashboard 標配）。
3. #3 跨幣操縱排行、#15 burst 重新設計，列第二輪視前兩項完成後的餘裕
   排入；**#15 本輪最多只做「資料探索驗證」半天，不做實作**，避免
   重演 W1.5 「規則脆弱、越修越糟」的教訓。

檔案：`/Users/apple/HurricaneSoft/trustforge/docs/PLAN-next-worldfirst-depth.md`
（新建）；已同步更新 `docs/README.md` 索引（見下方 diff）。
