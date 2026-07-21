# PLAN: 訓練資料重新產出 (#353)

> 日期：2026-07-21
> 前置：PR #348（方向判定修復，BTC=偏空 ✅）、PR #351（training data 進版控）
> 目標：用修復後的回填系統，產出有三態分佈（偏多/中性/偏空）的訓練資料
> 約束：不刪 DB、append 模式、抽樣即可

---

## 現狀

- `data/training/` 共 1531 筆，direction 全為「不明」（舊回填 bug）
- 修復後 `_persist_to_training_data()` 會寫入正確的 direction（偏多/中性/偏空）
- 新紀錄 append 到同檔案尾部，舊資料不刪（後續 train 時自動覆蓋——新的有方向的會 eligible，舊的 direction=不明 在 hit 判定時視為中性）

---

## Step 1：重新回填（抽樣 300 天 × 5 幣 = ~1500 筆新資料）

### 前置條件
- [x] PR #348 已 merge（方向判定修復）
- [x] `data/training/` 路徑存在且可寫
- [ ] Bedrock credential 有效（`--mode offline` 不需要）
- [ ] `TRUSTFORGE_BACKFILL_ENABLED` 未設 false

### 執行

```bash
# 啟動回填（offline 模式，不耗 Bedrock quota）
# --sample 300 = 均勻抽 300 天跨 2021-07~2026-07 全時間範圍
python -m trustforge.cli backfill start \
    --sample 300 \
    --batch-size 50 \
    --daemon

# 或分幣種跑（可控、可平行）：
python -m trustforge.cli backfill start --coin BTC --sample 60 --batch-size 50 --daemon
python -m trustforge.cli backfill start --coin ETH --sample 60 --batch-size 50 --daemon
python -m trustforge.cli backfill start --coin SOL --sample 60 --batch-size 50 --daemon
python -m trustforge.cli backfill start --coin BNB --sample 60 --batch-size 50 --daemon
python -m trustforge.cli backfill start --coin XRP --sample 60 --batch-size 50 --daemon
```

### 狀態追蹤

```bash
python -m trustforge.cli backfill status --json
```

### 驗收標準
1. `data/training/BTC.jsonl` 新增 ≥ 50 筆（wc -l 比對前後）
2. 新筆記錄 direction ∈ {偏多, 中性, 偏空}（不全是「不明」）
3. 三態至少各出現 1 次：
   ```bash
   tail -300 data/training/BTC.jsonl | python3 -c "
   import sys, json, collections
   c = collections.Counter(json.loads(l)['direction'] for l in sys.stdin)
   print(c)
   assert '偏多' in c or '偏空' in c, '三態不足'
   "
   ```
4. backfill status 顯示 completed（或至少 progress > 90%）

---

## Step 2：訓練 Isotonic 校準模型

### 前置條件
- Step 1 完成（training data 已有三態分佈記錄）
- `data/data/` 目錄有 OHLCV CSV（判定 T+7 hit/miss 用）

### 執行

```bash
python -m trustforge.cli train-calibration \
    --training-dir data/training \
    --data-dir data/data \
    --out out/model-artifacts/calibration-model.json
```

### 驗收標準
1. 命令回傳 exit 0
2. `out/model-artifacts/calibration-model.json` 存在且 JSON 合法
3. 輸出顯示「可用樣本 ≥ 50」（eligible ≥ 50）
4. 模型 JSON 含 `calibration_points` 陣列（≥ 3 個點）

---

## Step 3：提交到 ModelHub

### 前置條件
- Step 2 產出模型 artifact
- ModelHub 在 localhost:8950 可達
- `src/trustforge/modelhub_client.py` 和 `modelhub_submit.py` 已實作（spec #351 T2/T3）

### 執行

```bash
# 先確認 ModelHub 健康
curl -s http://localhost:8950/v1/models | python3 -m json.tool

# 若 modelhub_submit 模組已就位：
python -m trustforge.cli modelhub-train --all

# 若模組尚未實作，手動走 prepare_calibrator_training.py：
python scripts/prepare_calibrator_training.py \
    --labels out/model-artifacts/calibration-model.json \
    --out out/modelhub-proposals/calibrator-package.json
# 再手動 POST 到 ModelHub（curl / httpie）
```

### 驗收標準
1. ModelHub 回傳 training job accepted（HTTP 200/202）
2. 輪詢 training-result 狀態為 `completed`
3. `out/modelhub-proposals/` 下有 proposal JSON
4. proposal `status` ∈ {ready_for_modelhub_dry_run, submitted}

---

## Step 4：驗證外框模組升級流程

### 前置條件
- Step 3 完成（或至少 Step 2 的模型可用）
- 外框模組升級控制在 web 介面或 CLI 可觀測

### 執行

```bash
# 1. 確認 upgrade_control 能讀到新模型
python3 -c "
from trustforge.upgrade_control import upgrade_status
import json
status = upgrade_status()
print(json.dumps(status, ensure_ascii=False, indent=2))
"

# 2. 確認 calibrator 已切換（或產出 proposal 等人工審查）
python3 -c "
from trustforge.calibration_model import load_calibration_model
model = load_calibration_model()
print(f'校準點數: {len(model.get(\"calibration_points\", []))}')
print(f'樣本數: {model.get(\"sample_count\", 0)}')
"

# 3. 跑一次 analyze 確認端到端正常
python -m trustforge.cli analyze \
    --coin BTC --type multi_source \
    --query "分析 BTC 近期市場信任狀態" --offline --out out/btc-verify

# 4. 確認報告有方向判定（不再全是「不明」）
grep -o '"direction":[^,]*' out/btc-verify/evidence.json | head -5
```

### 驗收標準
1. `upgrade_status()` 回傳無報錯
2. calibration model 可正常載入
3. `out/btc-verify/report.md` 生成成功
4. 報告中 direction 不再只有「不明」

---

## 時程估計

| Step | 耗時 | 備註 |
|------|------|------|
| 1. 回填 300天×5幣 | ~30 min（offline） | batch-size=50 配 daemon |
| 2. 訓練校準模型 | < 1 min | 純 CPU isotonic |
| 3. 提交 ModelHub | ~5 min | 含輪詢等待 |
| 4. 驗證 | ~3 min | 單次 analyze |
| **合計** | **~40 min** | |

---

## 注意事項

- **不刪 DB**：回填用 `seed_tasks()` 的 `INSERT OR IGNORE`，已完成的日期不會重跑
- **不刪舊訓練資料**：新紀錄 append 到 `data/training/{COIN}.jsonl` 尾部
- **如果 offline 方向仍全為中性**：改用 `--mode live` 走真 Bedrock（消耗 quota 但方向更準）
- **session token 過期**：offline 模式不需 credential；live 模式需先 `aws sts get-caller-identity` 確認
- **rollback**：若新模型表現差，保留舊 `calibration-model.json` backup 即可恢復

---

## 後續（完成後）

- [ ] 確認 `data/training/` 新資料已 commit（版控追蹤）
- [ ] 更新 ROADMAP.md 標記 #353 完成
- [ ] 若 eligible rows ≥ 100 → 正式提交 ModelHub（非 dry-run）

---

## E. 檢核條件（每步執行後自動檢查）

| 步驟 | 檢核項 | 通過標準 | 失敗處理 |
|------|--------|---------|---------|
| Step 1 回填 | 方向分佈 | 偏多 ≥5%, 偏空 ≥5%, 中性 ≤80% | 停止回填，回報「方向判定可能回歸」 |
| Step 1 回填 | 失敗率 | failed/total < 5% | 超過 → 停止，回報 credential 或來源問題 |
| Step 1 回填 | 寫入路徑 | `data/training/*.jsonl` 有新增行數 | 無新增 → 路徑問題，停止 |
| Step 2 訓練 | eligible 數量 | ≥ 50 筆有方向預測 | 不足 → 回報「訓練資料三態不足」|
| Step 2 訓練 | calibration error | > 0（非全零） | 全零 → 可能 confidence 無變化，回報 |
| Step 3 ModelHub | API 可達 | localhost:8950 health=ok | 不可達 → 回報「ModelHub down」|
| Step 4 驗證 | 即時分析方向 | 5 幣至少 2 種不同方向 | 全同 → 回報「方向判定可能異常」|

## F. 異常回報標準

| 嚴重度 | 條件 | 動作 |
|--------|------|------|
| 🔴 P0 | 回填 failed > 10% | 立即停止，通知「Bedrock credential 過期或來源全掛」|
| 🔴 P0 | 方向全是同一種（>95%）| 停止，通知「方向判定邏輯回歸」|
| 🟡 P1 | eligible < 50（訓練不足）| 繼續回填但通知「需更多資料」|
| 🟡 P1 | ModelHub API 不可達 | 跳過 Step 3，通知「ModelHub down，手動處理」|
| 🟢 P2 | 回填速度 < 1 筆/分 | 通知「回填速度異常，可能 Bedrock throttle」|

## G. 系統自動檢查（加入 daemon/backfill 迴圈）

需要開 issue 實作：
- backfill 每 batch 結束後自動檢查方向分佈
- 異常時寫入 `out/anomaly-report.json` + log ERROR
- daemon 每輪自動驗證 snapshot 寫入成功
- /api/training-status 加入異常狀態欄位
