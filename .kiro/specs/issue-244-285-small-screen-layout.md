# Spec：小螢幕佈局修復（Issue #244 + #285）

## 概述

修復 HERMES 艦橋在小螢幕電腦（1024–1080px）上左側輸入區被截斷無法操作、右側面板內容溢出看不到的問題。

---

## 一、需求（Requirements）

### R1：左側 LeftRail 可捲動（#244）
- 左側面板內容超出可視高度時，必須可垂直捲動
- 底部的「交付 Hermes 的任務」輸入框和「立即重新分析」按鈕在任何視窗高度都可觸及
- 不影響現有的 clip-path 視覺效果

### R2：右側 RightRail 可捲動（#285）
- 右側面板內容超出可視高度時，必須可垂直捲動
- 信任分數圓圈、分項拆解、跨來源分歧區塊都要看得到

### R3：中間地帶 breakpoint（900–1024px）
- 現有 breakpoint 只有 900px（隱藏右側）和 560px（隱藏左側）
- 在 900–1024px 之間的螢幕需要更合理的 rail 寬度分配
- 確保三欄共存時中間區域不被壓到無法閱讀

### R4：不破壞現有佈局
- 大螢幕（≥1280px）行為不變
- ≤900px 響應式行為不變
- 前端 lint、tests、build 通過
- hermesLayoutContract.test.ts 通過

---

## 二、設計（Design）

### 問題根因

```css
.hermes-frame {
  --hermes-rail: clamp(230px, 20.84vw, 300px);
  --hermes-right-rail: var(--hermes-rail);
}
```

在 1024px 上：左右各 230px，中間只剩 564px。
LeftRail 和 RightRail 都用 absolute 定位 + 固定 height，但沒有 overflow-y: auto。

### 修法

1. **LeftRail 外層**：加 `overflowY: 'auto'`，讓內容超出時可捲動
2. **RightRail 外層**：同樣加 `overflowY: 'auto'`
3. **新增 @media (max-width: 1024px) breakpoint**：
   - 左側 rail 縮小到 clamp(180px, 18vw, 210px)
   - 右側 rail 縮小到 clamp(170px, 17vw, 200px)
   - 保留三欄結構，900px 以下才隱藏右側
4. **確保 scrollbar 樣式一致**（已有 hermes-root ::-webkit-scrollbar 設定）

---

## 三、任務（Tasks）

### Task 1：LeftRail 加 overflow-y: auto
- 檔案：`frontend/src/hermes/HermesLeftRail.tsx`
- 在外層容器 style 加 `overflowY: 'auto'`
- 驗證：縮小視窗高度，底部按鈕可捲動觸及

### Task 2：RightRail 加 overflow-y: auto
- 檔案：`frontend/src/hermes/HermesRightRail.tsx`
- 在外層容器 style 加 `overflowY: 'auto'`
- 驗證：縮小視窗高度，底部分歧區塊可捲動觸及

### Task 3：新增 1024px breakpoint
- 檔案：`frontend/src/hermes/hermes.css`
- 在 900px 之前加 `@media (max-width: 1024px)` 規則
- 縮減 rail 寬度，給中間區域更多空間

### Task 4：確認 lint / test / build 通過
- `npm run lint`
- `npm run test -- --run`
- `npm run build`
- 確認 hermesLayoutContract.test.ts 通過

### Task 5：你看畫面驗收
- 跑 dev server
- 你在 1024px 寬度下確認左右都正常

---

## 四、驗收標準（來自 Issue）

- [x] 左側面板在 1080p 下可捲動，輸入框/按鈕可觸及
- [x] 右側面板在 1024px 下可捲動，信任分數完整可見
- [x] 大螢幕（≥1280px）行為不受影響
- [x] ≤900px 響應式（右側隱藏）行為不受影響
- [x] frontend lint、tests、build 通過
