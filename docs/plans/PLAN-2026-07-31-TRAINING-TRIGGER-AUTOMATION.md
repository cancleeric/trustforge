# 開發計劃：訓練觸發自動化（保留 fail-closed + 人工啟用）

> 日期：2026-07-31
> 前置文件：`docs/reports/REPORT-2026-07-31-TRAINING-AUTO-TRIGGER-ANALYSIS.md`
> 狀態：待 CEO 審查後執行
> 估計總工時：6–9 小時（3 個 Phase，可拆 3–4 個 PR）
> 關聯 issue：#704（SageMaker 後端，CLOSED）、#503（ModelHub 唯讀複驗，CLOSED）

---

## 目標

在**不破壞**既有 fail-closed 與人工啟用治理的前提下，把「備料 → 觸發訓練 → 產 candidate」這段從「純手動 CLI」升級為「可排程自動觸發」。**啟用（activation）仍須人工**，不在本計劃範圍。

明確對齊三軌架構第 5–7 節：自動化只能到 candidate 產出為止。

---

## 現狀（待解缺口）

1. `sagemaker_submit._run_pipeline` Stage 8 ECE 比對為簡化版（只檢 `len(points) < 2`），完整 ECE 比較邏輯標註「可從 modelhub_submit 移植」→ **未接**。
2. 無任何排程入口觸發 `submit_calibrator_training` / `submit_sagemaker_training`。
3. `data/training/*.jsonl` 由 `prepare_calibrator_training.py` 手動產生，無自動化。
4. ModelHub read-only 複驗仍 `unverified`（#503），不可寫入。

---

## Phase 0：前置確認（0.5h）

| Task | 內容 | 驗收條件 |
|------|------|----------|
| T0.1 | 確認是否要自動化（產品決策） | 本計劃被 CEO 批准 |
| T0.2 | 確認 SageMaker 憑證就緒（`SAGEMAKER_TRAINING_BUCKET` / `SAGEMAKER_ROLE_ARN`） | 非 offline 模式下 `SageMakerBackend` 不 raise |
| T0.3 | 確認 ModelHub 是否先完成 #503 複驗 | 若走 ModelHub，需 `unverified` → 可讀寫；否則本計劃只啟用 SageMaker 路徑 |

**blocker**：T0.1 未批 → 全 blocked。T0.3 未過 → ModelHub 路徑排除，僅 SageMaker。

---

## Phase 1：SageMaker ECE 比對補完（2–3h）

> 把 `modelhub_submit` 的 holdout ECE 比較邏輯移植到 `sagemaker_submit`，使自動觸發出的 candidate 能獨立證明改善。

| Task | 內容 | 依賴 | 驗收條件 |
|------|------|------|----------|
| T1.1 | 在 `sagemaker_submit` 引入 `weighted_ece` + holdout 預測對齊（仿 `modelhub_submit._candidate_predictions`） | — | 函式可 import |
| T1.2 | Stage 8 由「≥2 點」升級為「baseline_ece − candidate_ece ≥ MIN_ECE_IMPROVEMENT (0.02)」 | T1.1 | 未達門檻回 `no_improvement` |
| T1.3 | proposal 補 `baseline_ece` / `candidate_ece` / `improvement` 欄位 | T1.2 | 與 ModelHub proposal 欄位一致 |
| T1.4 | 單元測試（mock backend，三幣 e2e 含 no_improvement 分支） | T1.1–T1.3 | coverage ≥ 85%，zero network |

---

## Phase 2：排程觸發器（2–3h）

> 新增「只觸發、不啟用」的排程入口。可選兩種實作，T2.0 決定：

| Task | 內容 | 依賴 | 驗收條件 |
|------|------|------|----------|
| T2.0 | 決定載體：A) 擴充 `hourly-release-train.yml` 的 dry 段；B) 新增獨立 `scripts/scheduled_calibrator_retrain.py` + cron | T0.1 | 選定並記錄 |
| T2.1 | 實作排程腳本：對五幣池 call `submit_*_training(dry_run=False)`，彙總 candidate / blocked / no_improvement | T1 | 跑完不 raise，產 `out/*-proposals/` |
| T2.2 | 觸發前先自動跑 `prepare_calibrator_training.py` 備料（或明確要求備料已存在） | T2.1 | 資料缺失時 fail-closed 不送訓 |
| T2.3 | 輸出排程報表（哪些幣產出 candidate、哪些 blocked） | T2.1 | JSON 報表可讀 |
| T2.4 | 單元/整合測試（mock backend + fake clock） | T2.1–T2.3 | 無 AWS 依賴可跑 |

### 設計決定

- **絕不**在排程內呼叫 activation / rollback / 寫 production。
- candidate 產出後，依現有第三軌治理走人工 review。
- 排程腳本本身不持 `automatic_apply` 權限；它只是「更方便的手動」。

---

## Phase 3：治理與文件（1–2h）

| Task | 內容 | 依賴 | 驗收條件 |
|------|------|------|----------|
| T3.1 | 更新 `ARCHITECTURE.md` 訓練段落：標註「觸發可排程，啟用仍人工」 | Phase 1–2 | 含自動觸發邊界說明 |
| T3.2 | 更新本报告 §4 展望為「已落地」 | Phase 1–2 | 狀態同步 |
| T3.3 | 確保 `automatic_apply: false` + `requires_human_approval: true` 在自動路徑下仍成立 | Phase 1–2 | 程式碼斷言 |
| T3.4 | pre-push gate 通過 | Phase 1–3 | tests + lint + build + diff-check |

---

## 依賴圖

```
Phase 0（決策 + 憑證）
    │
    ▼
Phase 1（SageMaker ECE 補完）──┐
    │                          │
    ▼                          ▼
Phase 2（排程觸發器）◄─────────┘
    │
    ▼
Phase 3（治理與文件）
```

---

## PR 拆分建議

| PR | 內容 | Size |
|----|------|------|
| PR-1 | SageMaker ECE 比對（T1.1–T1.4） | M |
| PR-2 | 排程觸發腳本 + 測試（T2.0–T2.4） | M |
| PR-3 | 文件更新 + gate（T3.1–T3.4） | S |

---

## 風險與緩解

| 風險 | 機率 | 影響 | 緩解 |
|------|------|------|------|
| 自動觸發被誤解為「自動上線」 | 中 | 治理破口 | 程式碼斷言 `automatic_apply: false`；文件明示邊界 |
| SageMaker 憑證缺失 | 中 | T0.2 blocker | 先確認，不假設有 |
| 排程產出大量 candidate 淹沒人工 review | 低 | 人力 | 報表彙總 + 只對五幣池 |
| ModelHub 仍 unverified 卻被排程呼叫 | 低 | 寫入失敗 | T0.3 排除或先修 #503 |

---

## 驗收標準（Done Definition）

1. 排程腳本對五幣池跑完，產出 candidate / blocked / no_improvement 報表。
2. SageMaker candidate 含 `baseline_ece` / `candidate_ece` / `improvement`，且未達 0.02 改善回 `no_improvement`。
3. 自動路徑下 `automatic_apply: false` + `requires_human_approval: true` 仍成立（程式碼斷言）。
4. 不引入新第三方依賴。
5. coverage ≥ 85%，pre-push gate 全綠。
6. 文件同步「觸發可排程、啟用仍人工」。

---

## 不做的事（明確排除）

- 不做 automatic activation / 自動 rollback。
- 不讓排程腳本持有 production 寫權限。
- 不繞過 reviewer gate / 安全審查 / 回滾門檻。
- 不在本計劃修 #503（ModelHub 複驗），僅作為 T0.3 前置條件。
- 不改動推論路徑與校準模型本地邏輯。
