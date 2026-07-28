# Tasks: 自適應品質系統

## Task 1: useAdaptiveQuality hook ✅
- [x] 建立 `src/hermes/useAdaptiveQuality.ts`
- [x] 實作 2 秒初始量測
- [x] 實作持續 FPS 追蹤（rolling average 30 幀）
- [x] fpsToQuality 決策邏輯
- [x] localStorage 讀寫
- [x] DOM attribute `data-quality` 套用
- [x] 匯出 resetAutoDetect 方法

## Task 2: FpsMeter 元件 ✅
- [x] 建立 `src/hermes/FpsMeter.tsx`
- [x] 顯示即時 FPS（顏色隨值變化）
- [x] 顯示品質等級 badge
- [x] HERMES 視覺風格
- [x] pointer-events: none

## Task 3: CSS 降級規則 ✅
- [x] `:root[data-quality='medium']` — 停止星空 + 軌道
- [x] `:root[data-quality='low']` — 全部凍結

## Task 4: 整合至 HermesDashboard ✅
- [x] import hook + FpsMeter
- [x] 在 dashboard 最外層呼叫 useAdaptiveQuality
- [x] 在 JSX 底部 render FpsMeter
- [x] 確認 TypeScript build 通過

## Task 5: 驗證 ✅
- [x] dev server 畫面確認 FPS meter 顯示（截圖確認：120 FPS HIGH）
- [x] 確認 medium CSS 規則有效（手動設 data-quality）
- [x] lint 通過（oxlint 零錯誤）
