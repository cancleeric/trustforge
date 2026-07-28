# Requirements: 動畫效能自適應品質系統 + FPS 即時顯示

Issue: #796

## 背景
HERMES 艦橋介面動畫渲染成本 16ms/frame（優化後），剛壓在 60fps 邊緣。
中低端機器仍會卡頓。需要自適應系統在低端裝置自動降級動畫品質。

## 功能需求

### FR-1: FPS 即時顯示器
- 左下角固定位置，HERMES 風格（深色半透明底 + 等寬字型）
- 顯示即時 FPS 數值 + 品質等級標籤
- FPS 顏色隨數值變化：綠(>=50) / 黃(30-49) / 紅(<30)
- 不遮擋主要 UI（pointer-events: none）

### FR-2: 自動品質偵測
- 啟動後以 requestAnimationFrame 量測 2 秒 FPS
- 根據平均 FPS 自動選擇品質等級
- 偵測期間顯示 "DETECTING…"

### FR-3: 三級漸進降級
- `high` (>=45fps)：全部動畫
- `medium` (30-44fps)：停止星空視差 + 軌道旋轉，保留 glow/pulse/energy
- `low` (<30fps)：等同 reduced-motion，全部動畫停止

### FR-4: 偏好持久化
- 品質等級存入 localStorage
- 下次載入直接套用已知等級（跳過偵測）
- 提供 reset 功能重新偵測

### FR-5: 與現有系統相容
- reduced-motion toggle (cookie) 優先於自適應系統
- 若 reduced-motion = on，品質系統不介入
- qa 模式下自適應系統照常運行

## 非功能需求

- hook 本身的 FPS 量測不得消耗超過 0.1ms/frame
- 不引入第三方依賴
- FpsMeter 元件可 unmount（生產部署時可選擇不顯示）
