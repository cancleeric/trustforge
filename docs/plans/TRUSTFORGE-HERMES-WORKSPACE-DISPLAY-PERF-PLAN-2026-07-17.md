# TrustForge HERMES Workspace 顯示/效能改善計劃

> 實作狀態：desktop 核心修正已由 `7e4b949` 完成。模組開啟時卸載背景
> `CurrencyGalaxy`、降低背景 polling、同步 URL workspace、提供 QA 低動態模式，
> 並以原子快照切換維持右欄一致。Mobile 視窗驗收依產品優先序留在最後發版 gate。

日期：2026-07-17  
來源分析：`docs/qa/TRUSTFORGE-FRONTEND-DISPLAY-BUG-ANALYSIS-2026-07-17.md`  
目標：修復 `/?workspace=history&coin=BTC` 等 overlay workspace 顯示卡頓、headless Chrome CPU 暴衝，以及 URL state 不一致風險。

## 背景

`/?workspace=history&coin=BTC` 目前不是獨立 `/history` 路由，而是在 HERMES 首頁上疊加 `HermesModuleDeck`。因此使用者看到 history workspace 時，背後仍同時渲染：

- `CurrencyGalaxy`
- 左右 rail
- 底部 `StageBar`
- 多組 decorative animation
- 多組首頁輪詢
- module deck 自身 hologram
- `HistoryPage` + Recharts

現場觀測中，headless Chrome renderer 曾達約 `240% CPU`，整機 load 衝高。這屬於前端顯示/效能 bug，不是單一後端 API 問題。

## 改善目標

1. `workspace` overlay 開啟時，背景主艦橋不再全量渲染昂貴動畫。
2. overlay 開啟時，非必要輪詢降頻或暫停。
3. URL query `workspace` 與 React state `activeModule` 保持一致。
4. QA/headless 截圖有穩定低動態模式。
5. 保留 HERMES 視覺語言，但把「展示效果」與「可操作工作區」分層。

## Phase 1：止血修復

### 1.1 overlay 開啟時停用背景 `CurrencyGalaxy`

修改 `frontend/src/pages/HermesDashboard.tsx`：

- 當 `activeModule !== null` 時，不渲染 `CurrencyGalaxy`。
- 或改成低成本 static backdrop。

建議先採用最小修法：

```tsx
{!activeModule && (
  <div className="hermes-boot-layer" ...>
    <CurrencyGalaxy ... />
  </div>
)}
```

驗收：

- `/?workspace=history&coin=BTC` 可以正常顯示 history module。
- 打開 module 時 CPU 不再因 galaxy 背景長時間高佔用。
- 關閉 module 後 galaxy 正常恢復。

### 1.2 overlay 開啟時暫停 decorative stage/rail 動畫

新增 dashboard class：

```tsx
className={`hermes-root hermes-dashboard${activeModule ? ' is-module-open' : ''}`}
```

CSS：

```css
.hermes-dashboard.is-module-open .hermes-boot-layer:not(:has(.hermes-module-deck)) {
  animation: none;
}
```

若 `:has()` 相容性要保守，改用明確 class 包裝各區塊，例如 `data-region="galaxy"`、`data-region="stage"`。

驗收：

- module 開啟時背景不閃爍、不持續掃描。
- module 可讀性提高。

### 1.3 同步 URL `workspace` 與 `activeModule`

目前 `activeModule` 只從 `searchParams` 初始化一次。新增 effect：

```tsx
const isWorkspaceModule = (value: string | null): value is HermesWorkspaceModule =>
  value === 'analyze' || value === 'compare' || value === 'history' || value === 'status' || value === 'costs'

useEffect(() => {
  setActiveModule(isWorkspaceModule(requestedModule) ? requestedModule : null)
}, [requestedModule])
```

驗收：

- 直接進 `/?workspace=history&coin=BTC` 會開 history。
- browser back/forward 正確開關 module。
- 手動改 URL query 後畫面與 URL 一致。

## Phase 2：輪詢降載

### 2.1 `activeModule` 存在時暫停/降頻首頁輪詢

調整以下 effect：

- `getAnalysisFlow`：module 開啟時從 1.5 秒降到 10 秒，或暫停。
- `getAnalysisJourney`：module 開啟時從 5 秒降到 15 秒。
- service monitor 的 `/api/history`：當 `activeModule === 'history'` 時避免重複查同一端點。
- `getAnalysisQuestionContext`：除非左側 query 取得 focus 或使用者正在輸入，否則 module 開啟時暫停。

驗收：

- 打開 history module 後 Network 面板不應每 1.5 秒持續打 analysis-flow。
- module 內資料讀取不受影響。

### 2.2 補 telemetry 狀態

如果暫停背景輪詢，UI 需要明確顯示：

- `WORKSPACE FOCUS`
- `BACKGROUND POLLING THROTTLED`

避免使用者誤認為系統離線。

## Phase 3：QA / headless 低動態模式

### 3.1 支援 `?qa=1` 或 `?reducedMotion=1`

在 `HermesDashboard` 讀 query：

```tsx
const qaMode = searchParams.get('qa') === '1' || searchParams.get('reducedMotion') === '1'
```

套 class：

```tsx
className={`hermes-root hermes-dashboard${activeModule ? ' is-module-open' : ''}${qaMode ? ' is-qa-mode' : ''}`}
```

CSS：

```css
.hermes-dashboard.is-qa-mode * {
  animation: none !important;
  transition: none !important;
}
```

驗收：

- `/?qa=1&workspace=history&coin=BTC` 截圖穩定。
- 不影響一般使用者預設動畫。

### 3.2 補 headless smoke

建立可重複 smoke：

- 啟動 Vite preview/dev server。
- 打開 `/?qa=1&workspace=history&coin=BTC`。
- 等待頁面完成渲染。
- 截圖並檢查非空。
- 記錄 CPU 不做硬性 gate，但留作診斷輸出。

## Phase 4：測試覆蓋

新增/調整 frontend test：

1. `HermesDashboard` 在 `workspace=history` 時不渲染 `CurrencyGalaxy`。
2. `activeModule` 會跟隨 query 變化。
3. `qa=1` 時 root class 包含 `is-qa-mode`。
4. `activeModule === 'history'` 時 service monitor 不重複打 `/api/history`。

建議檔案：

- `frontend/src/hermes/hermesLayoutContract.test.ts`
- 或新增 `frontend/src/pages/HermesDashboard.workspace.test.tsx`

## 風險與注意

- 直接卸載 `CurrencyGalaxy` 會讓 module 開啟時背景視覺少一層，但這是可接受取捨；workspace 的可操作性應優先於裝飾。
- 如果暫停輪詢，右側 rail 的 journey/flow 可能短暫不更新；需以 `WORKSPACE FOCUS` 文案交代。
- `:has()` CSS 不宜作為核心控制，建議用 React class/data attr 明確標記區塊。
- 不要改 `HistoryPage` 作為第一步；目前證據顯示它不是主要 CPU 來源。

## 完成標準

- `/?workspace=history&coin=BTC` 一般瀏覽器操作不卡頓。
- `/?qa=1&workspace=history&coin=BTC` headless 截圖穩定完成。
- overlay 開啟時不再全量渲染 `CurrencyGalaxy`。
- URL back/forward 與 query 修改後，workspace state 正確同步。
- frontend 測試覆蓋上述行為。

## 建議執行順序

1. Phase 1.1 + 1.3：最小修復，先消除主要顯示/效能 bug。
2. Phase 3.1：讓 QA 截圖穩定。
3. Phase 2：輪詢降載。
4. Phase 4：補測試與 smoke。
