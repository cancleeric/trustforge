# TrustForge 當前開發重點

> 更新日期：2026-07-21
> 距決賽：11 天（8/1）
> 版本：v0.16.19

---

## 三大目標（按重要性排序）

### 🔴 目標 1：手動分析能正確回答

使用者輸入問題 → 系統用真 Bedrock 產出有意義的回答（非 offline placeholder）。

**現況**：✅ 已通（用 converse API + us.anthropic.claude-haiku-4-5）
**條件**：需有效 AWS credential

### 🔴 目標 2：自動分析持續執行

5 幣 × 多題型，使用最新抓到的多源資料持續分析。每輪分析完自動寫入 trust snapshot。

**現況**：✅ live backfill / calibration runner 已合進 `develop`，#328 已關閉
**注意**：需持續確認 daemon 現場資料與 snapshot 寫入 evidence

### 🔴 目標 3：五年歷史回填（累積訓練資料）

用真 Bedrock 跑 5 年歷史，累積有方向預測的 snapshots → 觸發校準升級 → 外框模組自我迭代。

**現況**：✅ #328 主要功能已完成；live backfill 已累積訓練資料
**注意**：model artifacts 可攜與乾淨 `npm ci` lockfile 風險若仍需要，另開 hygiene/ops issue 追蹤

---

## 關鍵約束

| 約束 | 說明 |
|------|------|
| **Bedrock 開放中** | 這兩天趕快跑，成本帳號負擔 |
| **AWS session token 會過期** | 生產用 IAM role 不過期；本機需定時刷新 |
| **不寫散亂腳本** | 所有功能進系統（CLI/daemon），可在任何環境重新執行 |
| **模型 artifacts 可攜** | 升級後的數據能搬到新部署環境 |

---

## 目前阻塞鏈

```
有效 AWS credential
    → Bedrock 可用
        → 手動分析 ✅
        → 自動分析 / live backfill ✅
        → 歷史回填 live mode（Issue #328）✅
            → 累積 ≥100 有方向預測 ✅
                → calibration runner / diagnose 產出校準指標 ✅
                    → review 通過
                        → 外框模組升級
                            → model artifacts 可攜化（另行追蹤）
```

---

## Issue 追蹤

| # | 標題 | 優先 | 狀態 |
|---|------|------|------|
| **#328** | 五年歷史真實分析回填系統 | P0 | 已關閉，主要功能已合併 |
| **#324** | Bedrock Knowledge Base | 低 | 決賽後 |
| **#312** | Bedrock Live Run artifact | M | 被 #328 涵蓋 |
| **#313** | 決賽投稿封裝 | M | 7/28-31 |
| **#204** | Live Demo 錄影 | M | 7/28-31 |

---

## 開發流程提醒

1. Issue → Kiro Spec → Feature Branch → PR → /codex-review → Merge
2. 不寫一次性腳本，功能進 CLI/daemon
3. 產出的資料和模型可匯出/匯入
4. PR 必須指定 reviewer + eye scan

---

## 更新：v0.17.1（2026-07-21 深夜）

### 已完成
- PIT 標籤重建（ground truth 三態均衡）
- 統一語意分析管線
- Trust Kernel facade Phase 1
- Provider ports/adapters
- 架構盤點總報告
- Runtime telemetry
- Policy executor

### 現在的優先順序
1. 用 ground truth 重訓校準模型
2. 方向模型 shadow test
3. #385 台灣監管資料源（blocked）
4. Trust Kernel Phase 2/3（漸進搬移）
