# TrustForge 升級面板 — 完整 Issue 排程

> 產出：2026-07-19 | CPO: Gray | 審查：CEO
> 目標：12 天內 31 個模組全部接上 AgentCore + UI

---

## Issue 清單

### DATA PLANE (7 issues)

| # | Issue 標題 | Size | 依賴 | 指派 |
|---|-----------|------|------|------|
| 1 | 來源連接器接入 AgentCore upgrade_status | S | 無 | Eric |
| 2 | 連接器路由與 fallback 狀態上報 | S | #1 | Eric |
| 3 | 來源頻率與 timeout 參數可觀測 | S | #1 | Eric |
| 4 | 正規化與去重模組版本追蹤 | S | 無 | Fanny |
| 5 | 不可變快照生成狀態暴露 | M | #4 | Fanny |
| 6 | 快取與鮮度策略監控接入 | M | #5 | Fanny |
| 7 | 排程器 backpressure/DLQ 狀態 | M | #1 | Eric |

### INTELLIGENCE (11 issues)

| # | Issue 標題 | Size | 依賴 | 指派 |
|---|-----------|------|------|------|
| 8 | Prompt 模板版本與 diff 追蹤 | S | 無 | Sophia |
| 9 | Agent 工具與技能路由狀態 | S | #8 | Sophia |
| 10 | 題目 RAG 與對話記憶模組接入 | M | #11 | Nicholas |
| 11 | Embedding 與索引策略 model-gate | L | 無 | Nicholas |
| 12 | Reranker 與分面生成 model-gate | M | #11 | Nicholas |
| 13 | 主張抽取模組狀態上報 | S | #8 | Sophia |
| 14 | 反方證據搜尋狀態接入 | M | #11 | Sophia |
| 15 | 操縱與協同偵測模組接入 | M | #13 | Sophia |
| 16 | 分析策略 outer skill 追蹤 | S | #8 | Sophia |
| 17 | 模型選擇與 active route gate | L | #11 | Eric |
| 18 | 校準器與 abstain model-gate | L | #17 | Eric |

### DELIVERY (5 issues)

| # | Issue 標題 | Size | 依賴 | 指派 |
|---|-----------|------|------|------|
| 19 | Evidence 組裝品質追蹤 | M | 無 | Fanny |
| 20 | 報告敘事與交付版本追蹤 | M | #19 | Fanny |
| 21 | 引用語言格式 outer skill | S | #20 | Fanny |
| 22 | 評測題庫與品質 gate 接入 | M | 無 | Sophia |
| 23 | 歷史回放與回歸門檻接入 | L | #22 | Fanny |

### OPERATIONS (8 issues)

| # | Issue 標題 | Size | 依賴 | 指派 |
|---|-----------|------|------|------|
| 24 | 成本與預算治理狀態暴露 | S | 無 | Eric |
| 25 | 速率限制與資源配額監控 | S | #24 | Eric |
| 26 | 觀測與管理介面版本鎖定 | S | 無 | Nicholas |
| 27 | 告警與操作流程接入 | S | 無 | Nicholas |
| 28 | h-obsidian 記憶策略模組 | M | 無 | Sophia |
| 29 | 權限審計與遮罩 core-adjacent | M | 無 | Eric |
| 30 | Schema migration 相容性追蹤 | M | #29 | Eric |
| 31 | 改善診斷器完整接入 | S | 無 | Sophia |

---

## 依賴關係（關鍵路徑）

```
Day 1 可並行啟動（無依賴）：
#1, #4, #8, #11, #19, #22, #24, #26, #27, #28, #29, #31

關鍵路徑：
#11 (rag-index, L) → #10, #12, #14, #17 → #18

瓶頸：#11 必須先完成，否則 4 條路徑被阻塞
```

---

## Sprint 排程

### Week 1 (Day 1-6): 基礎 + 無依賴模組

| Day | Eric | Nicholas | Fanny | Sophia |
|-----|------|----------|-------|--------|
| 1 | #1 連接器 + vite proxy | #11 rag-index (L) 開始 | #4 正規化 + #19 evidence | #8 prompt + #31 診斷器 |
| 2 | #2 路由 + #3 頻率 | #11 繼續 | #5 快照 + #20 報告 | #9 工具路由 + #13 主張 |
| 3 | #7 排程器 + #24 成本 | #11 完成 + #26 觀測 | #6 鮮度 + #21 引用 | #16 分析策略 + #28 記憶 |
| 4 | #25 速率 + #29 權限 | #10 題目RAG + #27 告警 | #23 歷史回放(L) 開始 | #22 評測題庫 + #15 操縱 |
| 5 | #30 schema + #17(L) 開始 | #12 reranker | #23 繼續 | #14 反方證據 |
| 6 | #17 繼續 | E2E 整合測試 | #23 完成 | 全模組 fixture 資料 |

### Week 2 (Day 7-12): 整合 + 收尾

| Day | Eric | Nicholas | Fanny | Sophia |
|-----|------|----------|-------|--------|
| 7 | #17 完成 + #18(L) 開始 | 前端整合調整 | QA 全面驗收 | Demo 腳本撰寫 |
| 8 | #18 繼續 | 效能調校 | Bug 修正 | 簡報素材 |
| 9 | #18 完成 | Production build | E2E 全流程 | Dry run |
| 10 | Code review | Code review | 離線 demo 測試 | Dry run |
| 11 | 最終整合 | 最終整合 | 最終驗收 | 最終排練 |
| 12 | Buffer | Buffer | Buffer | Buffer |

---

## 人員負載

| 成員 | S | M | L | 總 issues | 風險 |
|------|---|---|---|-----------|------|
| Eric | 5 | 2 | 3 | 10 | ⚠️ W2 雙 L (#17+#18) |
| Nicholas | 2 | 2 | 1 | 5 | ✅ |
| Fanny | 1 | 3 | 1 | 7 (+QA) | ✅ |
| Sophia | 4 | 4 | 0 | 9 | ⚠️ W1 密集 |

**減緩措施：**
- Eric #17/#18：如果 Day 9 前沒完成，降級為 stub（UI 顯示 "model-gate: locked"）
- Sophia：W1 Day 4-5 如果太趕，#14/#15 可延到 W2

---

## GitHub Label 建議

- `plane:data` / `plane:intelligence` / `plane:delivery` / `plane:operations`
- `size:S` / `size:M` / `size:L`
- `channel:sandbox-policy` / `channel:reviewed-release` / `channel:model-gate`
- `milestone:week1` / `milestone:week2`

---

## 前置條件（Phase 0，CEO 已完成）

- [x] AgentCore dev 能跑 (8080)
- [x] Feature store 寫入正常
- [x] Auto analyze 事件驅動
- [x] Cost tracking
- [ ] **vite proxy 雙路由（Phase 1.1 — 阻塞所有 UI issue）**
- [ ] **web.py 能在 worktree 啟動（Phase 1.2 — 阻塞所有 API issue）**
