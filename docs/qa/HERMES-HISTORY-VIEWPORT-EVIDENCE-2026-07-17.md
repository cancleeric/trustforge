# Hermes 歷史頁 viewport 驗收

## 方法

依使用者要求，未使用 Codex 內建瀏覽器。驗收透過本機一般 Google Chrome
headless CDP，先以 `Emulation.setDeviceMetricsOverride` 設定真實 CSS viewport，
再載入 `http://127.0.0.1:4174/?workspace=history&coin=BTC`。

## 證據

- Desktop `1600×1200`：`HERMES-HISTORY-DESKTOP-1600x1200-2026-07-17.png`
- Mobile `390×844`：`HERMES-HISTORY-MOBILE-390x844-2026-07-17.png`
- 前端 `/` 與後端 `/api/health` 均為 HTTP 200。
- `/api/analysis-flow` 為 HTTP 200，五階 continuous pipeline 有目前工作與排隊狀態。
- Desktop 左右 rail、歷史圖表與五節點能量條均在 viewport 內。
- Mobile 隱藏次要左右 rail；幣種、區間、歷史圖與五個 pipeline 節點／engine
  全部在 390px viewport 內，沒有把手機工作插入核心開發中段。

第一次只用 Chrome `--window-size=390` 的截圖會受到 headless 最小 layout width
影響，不能當成 mobile evidence；本文件只採 CDP device metrics 後的 390px 截圖。
