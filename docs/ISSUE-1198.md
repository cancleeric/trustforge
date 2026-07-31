# feat: 點點頭像動畫 — Hermes 右下角三狀態表情切換

## 目標

在 Hermes 呈現框右下角加入**點點**（TrustForge AI 助手角色）頭像，具備三種狀態的表情切換與 CSS 動畫，讓報告回應看起來像點點在說話。

## 點點三種狀態

| 狀態 | 表情 | 動畫效果 | 觸發時機 |
|------|------|----------|----------|
| **日常（active）** |  眼微笑 | 點頭（translateY ±3px，2 秒循環） | 平時瀏覽、回應顯示中 |
| **思考（thinking）** | 閉眼一字嘴 | 左右歪頭（rotate ±8°，左歪→回正→右歪→回正，3 秒循環）+ 點頭 | 分析執行中、載入中 |
| **閒置（idle）** | ¥¥ 眼 | 緩慢點頭（translateY ±2px，3 秒循環） | 超過 **1 分鐘**沒有操作 |

## 素材

從手繪圖裁出三張點點頭部（去背景方格紙和旁邊文字），存為 PNG 透明背景：

- frontend/public/diandian/active.png —  眼微笑
- frontend/public/diandian/thinking.png — 閉眼一字嘴
- frontend/public/diandian/idle.png — ¥¥ 眼

原始素材圖在 issue comment 或團隊群組取得。

## 實作規格

### 元件

新建 frontend/src/components/DiandianAvatar.tsx：

- 放在 Hermes 呈現框右下角
- position: absolute; bottom: 16px; right: 16px;
- 頭像大小約 64x64px（桌面）/ 48x48px（手機）

### CSS 動畫

- 點頭（日常）：translateY ±3px，2 秒循環
- 緩慢點頭（閒置）：translateY ±2px，3 秒循環
- 左右歪頭（思考）：rotate ±8°（左歪→回正→右歪→回正），3 秒循環

### 狀態判斷邏輯

1. 如果 analysis 正在執行（loading state）→ thinking
2. 如果超過 1 分鐘沒有 user interaction（click/scroll/keypress）→ idle
3. 其餘 → active

### 對話泡泡（選配）

報告結論區可選擇用對話泡泡框包住，讓回應看起來像點點在說話。

## 驗收標準

- [ ] 點點頭像出現在 Hermes 呈現框右下角
- [ ] 三種表情根據狀態正確切換
- [ ] 日常狀態有點頭動畫（2 秒循環）
- [ ] 思考狀態有左右歪頭動畫（3 秒循環）
- [ ] 閒置狀態有緩慢點頭（1 分鐘無操作後觸發）
- [ ] 響應式：桌面 64px / 手機 48px
- [ ] 不影響現有 Hermes UI 佈局和互動
- [ ] 圖片總大小 < 200KB（三張合計）
