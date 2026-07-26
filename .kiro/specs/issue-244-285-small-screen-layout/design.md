# Design

## 問題根因

```css
.hermes-frame {
  --hermes-rail: clamp(230px, 20.84vw, 300px);
  --hermes-right-rail: var(--hermes-rail);
}
```

在 1024px 上：左右各 230px，中間只剩 564px。
LeftRail 和 RightRail 都用 absolute 定位 + 固定 height，但沒有 overflow-y: auto。

## 修法

1. **LeftRail 外層**：加 `overflowY: 'auto'`，讓內容超出時可捲動
2. **RightRail 外層**：同樣加 `overflowY: 'auto'`
3. **新增 @media (max-width: 1024px) breakpoint**：
   - 左側 rail 縮小到 clamp(180px, 18vw, 210px)
   - 右側 rail 縮小到 clamp(170px, 17vw, 200px)
   - 保留三欄結構，900px 以下才隱藏右側
4. **確保 scrollbar 樣式一致**（已有 hermes-root ::-webkit-scrollbar 設定）
