# Tasks

## Task 1：LeftRail 加 overflow-y: auto
- 檔案：`frontend/src/hermes/HermesLeftRail.tsx`
- 在外層容器 style 加 `overflowY: 'auto'`
- 驗證：縮小視窗高度，底部按鈕可捲動觸及

## Task 2：RightRail 加 overflow-y: auto
- 檔案：`frontend/src/hermes/HermesRightRail.tsx`
- 在外層容器 style 加 `overflowY: 'auto'`
- 驗證：縮小視窗高度，底部分歧區塊可捲動觸及

## Task 3：新增 1024px breakpoint
- 檔案：`frontend/src/hermes/hermes.css`
- 在 900px 之前加 `@media (max-width: 1024px)` 規則
- 縮減 rail 寬度，給中間區域更多空間

## Task 4：確認 lint / test / build 通過
- `npm run lint`
- `npm run test -- --run`
- `npm run build`
- 確認 hermesLayoutContract.test.ts 通過

## Task 5：你看畫面驗收
- 跑 dev server
- 你在 1024px 寬度下確認左右都正常
