# feat: 推理軌跡箭頭視覺化 + 全站中文優先翻譯

## 目標

兩件事：

### 1. 推理軌跡用箭頭一目了然

目前報告和執行面板的推理流程用文字堆疊，不夠直觀。改為箭頭視覺化：

**報告中的推理鏈：**
```
事實（客觀資料）→ 推論（Agent 推理）→ 結論（市場判斷）
```

**執行節點：**
```
來源蒐集 → 主張抽取 → 信任推理 → 證據組裝 → 報告交付
```

**UI 呈現方式：**
- 用 `→` 或 SVG 箭頭連接各步驟
- 每步一個短標籤，hover 或點擊才展開詳細說明
- 當前執行到的步驟高亮，已完成的打勾
- 讓操作者 3 秒內看懂「系統在做什麼、做到哪了」

### 2. 英文翻中文 — 中文為主、英文括號在後

目前 UI 很多地方中英混雜，對一般操作者不友善。規則：

**格式：中文名稱（English Term）**

範例：
| 現在 | 改為 |
|------|------|
| Evidence List | 證據清單（Evidence List） |
| Trust Score: 0.63 | 信任分數（Trust Score）：0.63 |
| Cross-source divergence | 跨來源分歧（Cross-source Divergence） |
| Execution Log | 執行紀錄（Execution Log） |
| Source Reputation | 來源信譽（Source Reputation） |
| Corroboration | 交叉佐證（Corroboration） |
| Recency Decay | 資料時效（Recency Decay） |
| Manipulation Penalty | 操縱懲罰（Manipulation Penalty） |
| Information Completeness | 資訊完整度（Information Completeness） |
| Contrarian Evidence | 反方證據（Contrarian Evidence） |
| Whale Alert | 鯨魚警報（Whale Alert） |
| Fear & Greed Index | 恐懼貪婪指數（Fear & Greed Index） |
| Multi-source | 多源整合（Multi-source） |
| Hypothesis | 假設驗證（Hypothesis） |
| Comparison | 比較分析（Comparison） |

**原則：**
- 中文放前面，英文用小字或括號放後面
- 幣種名稱（BTC/ETH）維持英文不翻
- 來源名稱（CoinGecko/SEC EDGAR）維持英文
- 操作按鈕全中文
- 標題和說明文字中文為主

## 影響的檔案

- `frontend/src/hermes/hermesI18n.tsx` — i18n 翻譯字典
- `frontend/src/components/AnalysisReportView.tsx` — 報告顯示
- `frontend/src/components/EvidenceTable.tsx` — 證據表
- `frontend/src/hermes/StageBar.tsx` — 執行節點列
- `frontend/src/hermes/StageDrilldown.tsx` — 節點展開
- `frontend/src/components/TrustBreakdown.tsx` — 信任拆解
- `frontend/src/components/CrossSourceSignalPanel.tsx` — 跨源面板

## 驗收標準

- [ ] 推理軌跡有箭頭視覺化（報告+執行面板）
- [ ] 3 秒內能看懂流程走到哪
- [ ] 所有面向操作者的英文都改為「中文（English）」格式
- [ ] 幣種和來源名稱維持英文
- [ ] 按鈕文字全中文
