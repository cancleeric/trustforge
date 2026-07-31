# 報告：TrustForge 訓練路徑是否自動啟動（ModelHub / SageMaker）

> 日期：2026-07-31
> 範圍：靜態程式碼分析 + 倉庫治理配置核對
> 結論：**訓練路徑不會自動啟動。從備料、觸發到啟用，全鏈皆為顯式/人工。**

---

## 1. 結論（TL;DR）

- **不會自動訓練。** 沒有任何定時器、webhook、cron 或「資料達閾值自動送訓」的邏輯掛在 ModelHub / SageMaker 訓練路徑上。
- 訓練觸發點**只在 CLI**（`trustforge modelhub-train` / `trustforge sagemaker-train`），必須有人手動下指令。
- 訓練資料 `data/training/<COIN>.jsonl` 的**產生也是手動腳本**（`scripts/prepare_calibrator_training.py`，需顯式傳 `--labels`）。
- 即便手動觸發且訓練成功，產出也只是 **candidate proposal**（`automatic_apply: False, requires_human_approval: True`），不會自動套用。
- 當前兩個後端都還沒到「可無人值守運作」狀態：
  - ModelHub：read-only 複驗仍 `unverified`（#503，401 + 無 tenant scope）。
  - SageMaker：缺 `SAGEMAKER_TRAINING_BUCKET` / `SAGEMAKER_ROLE_ARN` 即 raise `TrainingBackendConfigError`。

---

## 2. 證據鏈

### 2.1 觸發點只在 CLI（無自動排程）

| 函式 | 位置 | 進入方式 |
|------|------|----------|
| `submit_calibrator_training()` | `src/trustforge/modelhub_submit.py:119` | `cli.py:553` `cmd_modelhub_train` |
| `submit_sagemaker_training()` | `src/trustforge/sagemaker_submit.py:41` | `cli.py:524` `cmd_sagemaker_train` |

- 全倉搜尋 `schedule|cron|APScheduler|IntervalTrigger|while True`：命中項目均與訓練無關（如 `peer_metrics_scheduler.py`）。
- `.github/workflows/hourly-release-train.yml` 看似「每小時自動」，**但它是 Release Train（發布/部署 develop→production），跑 `scripts/hourly_release_train.py --execute`，與校準器訓練無關**；且 AGENTS.md 明定 GitHub Actions 為 disabled，非必要檢查。

### 2.2 資料源頭也是手動

- `scripts/prepare_calibrator_training.py` 是 argparse CLI，必須顯式傳 `--labels <label JSON>` + `--out` 才會產出 training package。
- `modelhub_training.eligible_calibrator_rows()` 從 T+1/T+7/T+14 **已成熟**的 delayed outcome label 抽取樣本；label 本身由 #507 的 labeler 產生，仍屬顯式管線，非「收集即訓練」。

### 2.3 即便觸發也 fail-closed + 需人工啟用

`modelhub_submit._submit_calibrator_training` 與 `sagemaker_submit._run_pipeline` 的共同結構：

1. `load_flat_training_rows` → `build_flat_training_package`（跑 calibrator gate）
2. gate 未過 → 回 `blocked`，**不送出**
3. 觸發訓練（上傳 → trigger → poll → 下載 artifact）
4. 獨立評分：ModelHub 路徑算 holdout ECE，要求 candidate 比 baseline 改善 ≥ 0.02（`MIN_ECE_IMPROVEMENT`）；SageMaker 路徑目前只做「artifact ≥ 2 校準點」簡化檢查（ECE 比對標註「可從 modelhub_submit 移植」，即尚未完整接上）
5. 回傳 `status: candidate`，寫 `out/*-proposals/`，明確 `automatic_apply: False, requires_human_approval: True`

啟用權在第三軌治理迴圈（diagnostics → proposal → sandbox → reviewer gate → **human activation** → rollback），由架構文件 `TRUSTFORGE-THREE-TRACK-LEARNING-SYSTEM-ANALYSIS-2026-07-23.md` 第 5–7 節硬規定。

### 2.4 相關 issue 已全部 CLOSED

- SageMaker 全線：#701/#702/#703/#704/#705/#706/#707/#708/#709
- ModelHub：#351（整合）、#503（唯讀複驗）、#507/#508（delayed outcome / calibration dataset）
- 若曾存在「自動訓練」意圖，應有對應 open issue 或常駐 scheduler；目前均無。

---

## 3. 常見誤解釐清

| 誤解 | 事實 |
|------|------|
| 「收集資料以後系統自動去訓練」 | 否。備料（jsonl）與觸發（CLI）皆顯式。 |
| `hourly-release-train.yml` 是自動訓練 | 否。它是發布/部署管線，與校準器訓練無關。 |
| 訓練完會自己上線 | 否。只產 candidate，需人工批准 + rollback 證據。 |
| ModelHub / SageMaker 已可無人值守運作 | 否。ModelHub read-only 仍 unverified；SageMaker 缺憑證即報錯。 |

---

## 4. 若未來要「自動化觸發」該怎麼做（僅展望，非當前狀態）

若產品需要週期性重訓，應在**不違反 fail-closed 與人工啟用**前提下，於第三軌治理內新增：

1. 一個**只負責「觸發 → 產 candidate」**的排程（cron / Release Train 擴充），**絕不包含 activation**。
2. candidate 仍走人工 review gate + commit-bound 證據 + 已知良好版本 rollback。
3. SageMaker 路徑需先補完 ECE 比對（從 `modelhub_submit` 移植），否則自動觸發出的 candidate 無法獨立證明改善。
4. ModelHub 需先完成 #503 的 read-only 複驗（tenant scope + artifact provenance），否則不可寫入。

詳見開發計劃 `docs/plans/PLAN-2026-07-31-TRAINING-TRIGGER-AUTOMATION.md`。

---

## 5. 權威參考

- `src/trustforge/modelhub_submit.py`
- `src/trustforge/sagemaker_submit.py`
- `src/trustforge/sagemaker_client.py`
- `src/trustforge/modelhub_training.py`
- `scripts/prepare_calibrator_training.py`
- `docs/architecture/TRUSTFORGE-THREE-TRACK-LEARNING-SYSTEM-ANALYSIS-2026-07-23.md`
- `docs/plans/PLAN-sagemaker-training-backend-dev.md`（#704 開發計劃，已 CLOSED）
- `.github/workflows/hourly-release-train.yml`（Release Train，非訓練）
- AGENTS.md（GitHub Actions disabled 聲明）
