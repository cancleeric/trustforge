# TrustForge 前端顯示/效能異常分析報告

> 處置：根因已由 `7e4b949` 修復並加入 layout contract regression tests；
> frontend build、252 tests 與 lint 均通過。本文件保留為事故分析與防回歸證據。

日期：2026-07-17  
範圍：`trustforge/frontend`，特別是首頁 HERMES overlay workspace 與 `history` 顯示路徑。

## 結論

本次異常不是單一圖表元件壞掉，而是 **首頁 overlay workspace 模式在顯示 `history` 模組時，仍保留整個 HERMES 主艦橋背景渲染、動畫與輪詢**。在一般瀏覽器會表現為卡頓、顯示延遲或動畫掉幀；在 headless Chrome 截圖/QA 流程中會放大成 CPU 暴衝。

現場程序證據顯示，當 URL 為：

```text
http://127.0.0.1:4174/?workspace=history&coin=BTC
```

Chrome renderer 一度達到約 `240% CPU`。該 URL 不是 React Router 的獨立 `/history` 頁，而是首頁 `HermesDashboard` 透過 `workspace=history` 打開 overlay 模組。

## 主要發現

### HIGH：overlay 模式沒有停用背景主艦橋

`HermesDashboard` 在 `activeModule` 存在時仍照常渲染：

- Top bar
- Left rail
- `CurrencyGalaxy`
- Right rail
- Stage bar
- Module deck

代碼證據：

- `frontend/src/pages/HermesDashboard.tsx:476-484`：`CurrencyGalaxy` 仍渲染。
- `frontend/src/pages/HermesDashboard.tsx:487-502`：右側 rail 與底部 `StageBar` 仍渲染。
- `frontend/src/pages/HermesDashboard.tsx:512`：`HermesModuleDeck` 是額外疊加，不是替換主畫面。

這代表 `/?workspace=history` 實際上是「完整首頁 + history page + module hologram」三層同時跑，不是單純打開歷史頁。

### HIGH：背景 galaxy 含多個長時間 CSS animation/filter/3D transform

`CurrencyGalaxy` 包含多層星場、掃描線、3D orbit、核心發光與 filter transition。這些即使被 overlay 蓋住，也仍由瀏覽器持續計算。

代碼證據：

- `frontend/src/hermes/CurrencyGalaxy.tsx:92`：orbit 每 40 秒無限旋轉。
- `frontend/src/hermes/CurrencyGalaxy.tsx:130-134`：三層 starfield 與 radar sweep 無限動畫。
- `frontend/src/hermes/CurrencyGalaxy.tsx:187-210`：3D galaxy assembly、核心 glow animation。
- `frontend/src/hermes/hermes.css:319-322`：只有 `prefers-reduced-motion: reduce` 才停動畫；QA/headless 預設不會套用。

這是 headless Chrome renderer 變成最高 CPU 的主要前端原因。

### MEDIUM：overlay 本身又新增一組 hologram 動畫與毛玻璃效果

`HermesModuleDeck` 不只是容器，還新增 module hologram 背景、beam、ring、core、caption，再把功能頁塞進 scroll 區。

代碼證據：

- `frontend/src/hermes/HermesModuleDeck.tsx:45-52`：每個 module 都額外渲染 hologram。
- `frontend/src/hermes/hermes.css:253`：module 內卡片使用 `backdrop-filter: blur(7px)`。
- `frontend/src/hermes/hermes.css:254-258`：module hologram 使用 blur、box-shadow、無限 breathe animation。

在 `history` 頁中，Recharts 本身還要做 `ResponsiveContainer` layout，疊加後更容易造成截圖流程卡住。

### MEDIUM：首頁輪詢在 overlay 開啟時仍全量執行

即使 `workspace=history` 已打開，首頁仍持續執行多個背景輪詢：

- `getAnalysisJourney`：每 5 秒。
- `getAnalysisFlow`：每 1.5 秒。
- `getOverview`：每 30 秒。
- `getHealth + getCosts`：每 15 秒。
- `/api/status` + `/api/history?coin=BTC&days=30` 自檢：每 10 秒。
- `getAnalysisQuestionContext`：預設 query 非空時啟動。

代碼證據：

- `frontend/src/pages/HermesDashboard.tsx:96-108`
- `frontend/src/pages/HermesDashboard.tsx:110-135`
- `frontend/src/pages/HermesDashboard.tsx:190-228`
- `frontend/src/pages/HermesDashboard.tsx:233-262`
- `frontend/src/pages/HermesDashboard.tsx:264-310`

這些輪詢本身不一定是 bug，但在 overlay 模式中沒有降頻或暫停，會增加渲染與後端壓力。

### MEDIUM：URL state 只在初始化時讀取 `workspace`

`activeModule` 使用 `useState` 初始值讀 `searchParams.get('workspace')`，但沒有 effect 持續同步 URL。

代碼證據：

- `frontend/src/pages/HermesDashboard.tsx:55-59`

如果使用者或測試工具直接改 query string、瀏覽器 back/forward、或外部腳本只更新 URL，畫面 state 可能與 URL 不一致。這會造成「URL 看起來是 history，但畫面沒有切到/沒有關掉」這類顯示錯覺。

## 非主要原因

`HistoryPage` 本身沒有看到明顯造成 240% CPU 的直接代碼。它主要做：

- 讀取 `/api/history`
- 顯示 coin/day filter
- lazy import `TrustHistoryChart`
- 用 Recharts 畫兩條 line

這些在獨立 `/history` 路由下應該可控。問題主要來自首頁 overlay 把它放進已經很重的 HERMES 儀表板中。

## 建議修法

### 1. overlay 開啟時暫停或卸載背景 `CurrencyGalaxy`

建議最高優先級。可選策略：

- `activeModule ? null : <CurrencyGalaxy ... />`
- 或保留靜態背景，但停止 orbit/starfield/sweep animation。
- 或加 class，例如 `.hermes-dashboard.is-module-open`，讓背景動畫全部 `animation:none`。

驗收標準：

- `/?workspace=history&coin=BTC` headless 截圖時 Chrome renderer 不應長時間超過 100% CPU。
- 使用者打開 history overlay 時，主畫面不應掉幀。

### 2. overlay 開啟時降低首頁輪詢頻率

建議：

- `activeModule` 存在時暫停 `analysis-flow` 1.5 秒輪詢，或降到 10-15 秒。
- 暫停預設 query 的 `getAnalysisQuestionContext`，除非使用者正在互動。
- `history` module 已經會讀 `/api/history`，首頁 service monitor 可避免同時每 10 秒再讀一次 history。

### 3. 同步 URL `workspace` 與 `activeModule`

新增 effect：

```tsx
useEffect(() => {
  const valid = requestedModule === 'analyze' || requestedModule === 'compare' ||
    requestedModule === 'history' || requestedModule === 'status' || requestedModule === 'costs'
  setActiveModule(valid ? requestedModule : null)
}, [requestedModule])
```

避免 browser back/forward 或測試工具改 URL 後 UI 狀態不同步。

### 4. 建立 QA 專用低動態模式

建議支援 query 或環境條件：

```text
?qa=1
?reducedMotion=1
```

在該模式下：

- 停止 decorative animations。
- 停止非必要輪詢。
- 保留資料與 layout，方便截圖/視覺回歸。

這比依賴 OS `prefers-reduced-motion` 更穩定，因為 headless Chrome 預設不一定會啟用。

## 建議測試

新增 frontend 測試：

1. `HermesDashboard` 在 `workspace=history` 時不渲染或暫停 `CurrencyGalaxy`。
2. `activeModule` 會跟隨 `workspace` query 變化。
3. `qa=1` 或 reduced-motion 模式會套用停動畫 class。
4. Playwright/headless smoke：打開 `/?workspace=history&coin=BTC`，等待 3 秒後截圖，確認頁面非空且程序未長時間高 CPU。

## 優先級

1. 先修「overlay 開啟仍全量渲染背景」。
2. 再修「URL workspace state 同步」。
3. 最後做 QA/reduced-motion 模式與輪詢降頻。

若只做第一項，使用者感知的顯示卡頓應該會明顯下降。
