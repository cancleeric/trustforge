# Design: 自適應品質系統

## 架構

```
useAdaptiveQuality (hook)
├── 啟動量測 (2s RAF loop)
├── 持續 FPS 追蹤 (rolling average)
├── 品質等級決策 (high/medium/low)
├── DOM attribute: <html data-quality="...">
└── localStorage 持久化

FpsMeter (component)
├── 即時 FPS 數值
├── 品質等級 badge
└── 狀態指示燈

CSS (hermes.css)
├── :root[data-quality='medium'] → 停止裝飾動畫
└── :root[data-quality='low'] → 全部凍結
```

## 資料流

1. `HermesDashboard` mount → `useAdaptiveQuality()` 啟動
2. 若 localStorage 有值 → 直接套用 `data-quality`，同時啟動持續 FPS 追蹤
3. 若無值 → 2 秒量測 → 決定等級 → 寫入 localStorage + 套用 DOM
4. `<FpsMeter>` 讀取 hook 回傳的 `fps` / `quality` / `measuring` render

## CSS 降級策略

### data-quality="medium"
選擇性停止裝飾動畫：
- 星空 drift-1/2/3：`animation-play-state: paused`
- 軌道旋轉 orbit-spin/rev 內層：`animation: none`
- 保留：core-glow, pulse, energy-flow, flicker, breathe

### data-quality="low"
全域凍結（與 reduced-motion 同規則）：
```css
:root[data-quality='low'] .hermes-root *,
:root[data-quality='low'] .hermes-root *::before,
:root[data-quality='low'] .hermes-root *::after {
  animation-duration: 0.001ms !important;
  animation-iteration-count: 1 !important;
  transition-duration: 0.001ms !important;
}
```

## 與 reduced-motion 的關係

```
if (reducedMotion) → 使用 data-reduced-motion 規則（已有）
else → 使用 data-quality 規則（本次新增）
```

hook 內部不判斷 reduced-motion，由 CSS specificity 自然分層：
- `data-reduced-motion` 的規則比 `data-quality` 更強（已有 `!important`）
- 兩者同時存在時 reduced-motion 勝出
