# feat: 星系 UI 視覺升級 — 星空色系 + 地球球體 + 流星執行動畫

## 目標

升級全域幣種星系（Galaxy UI）的視覺風格，讓整體更像真實星空，並改善底部執行節點的互動提示。

## 變更項目

### 1. 星系背景色系 → 真實星空

**現在**：偏深紫/深藍漸層
**改為**：

- 主色調：深黑（#0a0e1a）+ 深藍（#0d1b2a）漸層
- 點綴：微小白色/淡黃色星點散佈（像星星）
- 偶爾有淡藍色的星雲霧氣效果（opacity 很低的 radial gradient）
- 整體感覺：NASA 拍的深空照片那種氛圍

### 2. 幣種球體 → 暗色調低彩度地球

**現在**：藍綠色發光圓球
**改為**：

- 像從太空看地球的感覺，但彩度降低（不是鮮豔的藍，是深邃暗沉的藍+暗綠+棕）
- 球體表面可以有隱約的陸地/海洋紋理（用 radial-gradient 模擬）
- 邊緣保留一圈淡淡的大氣光暈（淡藍，opacity 0.3）
- 不發螢光，不搶眼，融入星空背景
- 5 顆球可以略有色差（代表不同幣種），但整體色調統一暗沉

### 3. 底部執行節點（來源掃描 等）→ 按鈕化 + 流星動態

**現在**：看起來像靜態文字標籤，不明顯可點擊
**改為**：

靜態：
- 明確的按鈕外觀：有邊框（1px solid rgba(255,255,255,0.2)）、圓角（8px）、hover 時亮起
- 背景微微有顏色（rgba(255,255,255,0.05)）
- hover 時 border 變亮 + 背景亮一點 + cursor pointer
- 加一個微小的箭頭或展開圖示暗示「可以點」

動態（分析跑行中）：
- 當前正在執行的節點有流星拖尾效果：
  - 一道淡白/淡黃的光從左向右掃過按鈕（像流星劃過）
  - 用 CSS `linear-gradient` + `animation` 實現
  - 類似 skeleton loading 的掃光效果，但更像流星
- 已完成的節點靜態發出柔和的綠色光暈
- 未執行的節點保持暗色

```css
/* 流星掃光效果 */
@keyframes meteor-sweep {
  0% { background-position: -200% 0; }
  100% { background-position: 200% 0; }
}

.stage-running {
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(255, 240, 180, 0.3) 50%,
    transparent 100%
  );
  background-size: 200% 100%;
  animation: meteor-sweep 2s ease-in-out infinite;
}
```

## 不動的部分

- 幣種行星的位置/大小邏輯不變
- 點擊行為不變
- 響應式佈局不變
- 文字內容不變

## 相關檔案

- `frontend/src/hermes/CurrencyGalaxy.tsx` — 星系主元件
- `frontend/src/hermes/StageBar.tsx` — 底部執行節點
- `frontend/src/hermes/hermes.css` — 樣式

## 驗收標準

- [ ] 背景色系改為深黑+深藍，有星點散佈
- [ ] 幣種球體改為暗色調低彩度地球風格
- [ ] 底部節點看起來像按鈕（邊框、hover 效果）
- [ ] 分析執行中時，當前節點有流星掃光動畫
- [ ] 已完成節點有柔和綠色光暈
- [ ] 整體視覺一致、不違和
- [ ] 不影響功能和互動邏輯
