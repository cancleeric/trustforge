# Spec：ModelHub 整合 (#351)

> Issue: #351
> Priority: P1
> 前置：#343（isotonic 校準模型）、#335（校準升級執行）
> ModelHub API: localhost:8950

---

## Requirements（需求）

### R1: 訓練資料搬移進版控 ✅ DONE
- 將 `out/training-data/*.jsonl` 複製到 `data/training/`（版控可追蹤）
- 5 幣種各一檔：BTC.jsonl / ETH.jsonl / SOL.jsonl / BNB.jsonl / XRP.jsonl
- 格式：每行 JSON，含 date/coin/direction/trust_score/confidence/evidence_count/sources/model_id/generated_at
- `data/training/` 不在 .gitignore（確認完成）
- 後續 backfill 產出也寫入此目錄（單一真相來源）

### R2: ModelHub 提交介面（calibrator retrain）
- 新模組 `src/trustforge/modelhub_client.py`：封裝 ModelHub REST API
- 支援端點：
  - `GET /v1/models` → 列出可用模型（健康檢查 + 模型清單）
  - `POST /api/submissions/{req_no}/retrain-lightning` → 觸發快速再訓練
  - `GET /api/submissions/{req_no}/training-result` → 輪詢訓練結果
  - `GET /api/external-models/{product}/{name}/path` → 取得模型 artifact 路徑
- 所有呼叫有 timeout（預設 30s）、retry（最多 2 次）、結構化錯誤回傳
- 不引入 requests 第三方依賴 → 用 `urllib.request`（與專案慣例一致）
- `MODELHUB_BASE_URL` 環境變數控制（預設 `http://localhost:8950`）
- ModelHub 不可達時 → graceful fallback，不影響既有 pipeline

### R3: 端到端訓練流程
- 新模組 `src/trustforge/modelhub_submit.py`：編排完整流程
- 流程：
  1. 讀取 `data/training/{coin}.jsonl` → 組合為 `eligible_calibrator_rows()`
  2. 經過 `evaluate_calibrator_gate()` 門檻檢查（≥100 筆有標記 outcomes）
  3. Gate 通過 → 呼叫 `build_calibrator_training_package()` 產出 training package
  4. 呼叫 ModelHub `retrain-lightning`，附帶 dataset SHA256 + split 資訊
  5. 輪詢 `training-result` 直到完成或 timeout（最長 5 分鐘）
  6. 完成後呼叫 `external-models/.../path` 取得 artifact
  7. 比對 holdout 效能 → 若改善 > threshold（ECE 降低 ≥ 0.02）→ 標記候選
  8. **不自動啟用** — 寫入 proposal 到 `out/modelhub-proposals/` 等待人工審查
- CLI 入口：`python -m trustforge.cli modelhub-train --coin BTC`（或 `--all`）
- 執行紀錄寫入 execution_log（遵循 15 分鐘預算）

---

## Design（設計）

### 架構圖

```
data/training/{coin}.jsonl          ← R1: 版控中的訓練資料
        │
        ▼
modelhub_training.py                ← 既有：build_calibrator_training_package()
  │ eligible rows + gate check
  ▼
calibrator_gate.py                  ← 既有：evaluate_calibrator_gate()
  │ gate.eligible == True?
  ▼
modelhub_client.py [NEW]            ← R2: REST client
  │ POST retrain-lightning
  │ GET  training-result (poll)
  │ GET  external-models/.../path
  ▼
modelhub_submit.py [NEW]            ← R3: 編排邏輯
  │ holdout 比對 → proposal
  ▼
out/modelhub-proposals/{coin}.json  ← 候選模型 proposal（人工審查）
```

### modelhub_client.py 介面設計

```python
class ModelHubClient:
    """ModelHub REST API client (stdlib only)."""

    def __init__(self, base_url: str | None = None, timeout: float = 30.0):
        ...

    def health_check(self) -> bool:
        """GET /v1/models — 回傳 ModelHub 是否可達。"""

    def list_models(self) -> list[dict]:
        """GET /v1/models — 回傳可用模型列表。"""

    def trigger_retrain(self, req_no: str, payload: dict) -> dict:
        """POST /api/submissions/{req_no}/retrain-lightning"""

    def poll_training_result(self, req_no: str, *, max_wait: float = 300) -> dict:
        """GET /api/submissions/{req_no}/training-result — 輪詢至完成或 timeout。"""

    def get_model_path(self, product: str, name: str) -> str:
        """GET /api/external-models/{product}/{name}/path"""
```

### modelhub_submit.py 流程

```python
def submit_calibrator_training(
    coin: str | None = None,
    *,
    training_dir: Path = Path("data/training"),
    dry_run: bool = False,
) -> dict:
    """端到端：讀資料 → gate → 提交 ModelHub → 輪詢 → 比對 → proposal。

    dry_run=True 只做 gate 檢查 + package 建構，不實際呼叫 ModelHub。
    """
```

### 錯誤處理策略

| 場景 | 行為 |
|------|------|
| ModelHub 不可達 | 回傳 status=unavailable，不崩 pipeline |
| Gate 未通過（<100 outcomes） | 回傳 status=blocked + 差多少筆 |
| 訓練 timeout（>5 min） | 回傳 status=timeout + req_no（可手動查） |
| Holdout 未改善 | 回傳 status=no_improvement，不產 proposal |
| 網路中斷重試耗盡 | 回傳 status=error + last_exception |

### 安全約束

- ModelHub 呼叫 **僅限本機 localhost**（競賽環境）
- 不傳送任何 AWS credential 給 ModelHub
- training package 含 dataset SHA256 可事後審計
- 模型啟用永遠需要人工審查（`automatic_apply: False`）

---

## Tasks（實作任務）

### T1: 訓練資料搬移 ✅
- [x] `mkdir -p data/training`
- [x] `cp out/training-data/*.jsonl data/training/`
- [x] 確認不在 .gitignore
- [x] `git add data/training/`

### T2: ModelHub REST Client
- [ ] 新增 `src/trustforge/modelhub_client.py`
- [ ] 實作 `ModelHubClient` class（urllib.request，無第三方依賴）
- [ ] health_check / list_models / trigger_retrain / poll_training_result / get_model_path
- [ ] timeout + retry 邏輯
- [ ] 單元測試 `tests/test_modelhub_client.py`（mock HTTP 不依賴實際 ModelHub）
- [ ] 整合測試用 `@pytest.mark.integration` 標記（需 ModelHub 跑起來才過）

### T3: 端到端提交流程
- [ ] 新增 `src/trustforge/modelhub_submit.py`
- [ ] 整合 modelhub_training.py + calibrator_gate.py + modelhub_client.py
- [ ] dry_run 模式（不呼叫 ModelHub）
- [ ] proposal 輸出格式設計（含 holdout 比較、SHA256、timestamp）
- [ ] CLI 子命令 `modelhub-train`
- [ ] 測試：gate 未通過 / dry_run / 正常流程（mock）

### T4: 文件與整合
- [ ] 更新 README.md 加入 ModelHub 整合說明
- [ ] 更新 ARCHITECTURE.md 加入 ModelHub 互動圖
- [ ] 確認 `budget_guard.py` 計入 ModelHub 呼叫時間

---

## 驗收標準

1. `data/training/` 進版控且含 5 幣種 JSONL（R1 ✅）
2. `python -m trustforge.cli modelhub-train --coin BTC --dry-run` 能跑完不報錯
3. ModelHub 可達時，`--coin BTC` 能完成 retrain 流程並產出 proposal
4. ModelHub 不可達時，graceful 回傳 status=unavailable 不崩
5. 所有新模組有 ≥80% 測試覆蓋率
6. 不引入新第三方依賴（urllib.request only）
