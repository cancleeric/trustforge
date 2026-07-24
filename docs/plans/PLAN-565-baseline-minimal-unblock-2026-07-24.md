# PLAN-565：Baseline 最小解鎖計劃

- 建立日期：2026-07-24
- 撰寫人：gray（CPO）
- 核定人：CEO
- 對應 Issue：#565（前端 build 破 1 個 + Python 26 fail，全部 pre-existing）
- 狀態：待 CTO 副手排入執行

---

## 1. 目標與非目標

### 目標

讓 baseline（`main` 分支現況）恢復「build 過、pytest 可全綠（含明確標記的 xfail）」，解鎖 pre-push 全測閘，讓後續 PR 不再被 pre-existing 問題卡住。具體：

- 前端 build 恢復可過（1 處缺 prop）。
- 群2（workflow 改名）、群3（OpenAPI spec 缺一條路由）、群4（backfill 缺欄位）三群測試轉綠，共 6 個 test。
- 群1（校準模型漂移）20 個測試明確隔離為 `xfail`，附 reason + 追蹤 issue，不是默默跳過，也不是產品決策下修正。

### 非目標（明確不做）

- **不修復群1 的「值」問題**：不決定 `_calibrate_confidence()` 該用新校準模型還是舊 fallback 表、不調整 `calibration-model.json`、不改 `scoring.py` 校準邏輯任何一行。這是模型/產品決策，需 CDO 或模型 owner 定案，本計劃只做「隔離＋追蹤」。
- 不做任何非本次 26 fail 範圍內的重構或優化。
- 不做效能調校、不擴大 diff 範圍。
- 不涉及部署、CI 排程策略調整（那是 CTO/COO 職權）。

---

## 2. PR 拆解

共 **5 張 PR**，其中 PR-A/B/C/D 彼此完全獨立、可平行；PR-E（xfail 群1）與其他四張也互不依賴，可平行。

### PR-A：前端 build 修復（單行）

- **標題**：`fix(frontend): pass required onOpenDivergence prop to HermesRightRail`
- **改動檔案**：`frontend/src/pages/HermesDashboard.tsx`（僅新增 1 行，:564 `onOpenComposite` 下方對稱補 `onOpenDivergence={() => setSelectedStage('divergence')}`）
- **判斷建議（source of truth）**：`HermesRightRail.tsx:17` 已將 `onOpenDivergence` 宣告為必填 prop，且 drilldown 目標 `StageDrilldown.tsx:21` 已存在並可用 → 正確方向是「呼叫端補傳 prop」，而非放寬 prop 為 optional。若 CTO 副手發現 `StageDrilldown` 對 divergence 分支尚未完整可用，應回報而非強行接上。
- **驗收條件**：`npm run build`（或專案對應 build 指令）成功；既有 frontend test/lint 不新增失敗。
- **預估工時**：0.5 小時
- **Reviewer**：CTO 線前端負責人（eye 掃）
- **平行性**：可與 PR-B/C/D/E 完全平行
- **契約風險**：無對外契約，內部 prop 傳遞，不需 harper 雙審

### PR-B：群2 workflow 測試路徑修復（2 個 fail）

- **標題**：`fix(tests): align CI/deploy workflow tests with .disabled rename`
- **涉及**：`tests/test_ceo_sweep_schedule.py`、`tests/test_deploy_health_monitor.py`（讀取 `ci.yml` / `deploy-production.yml`）
- **判斷建議（source of truth）**：workflow 檔改名為 `.disabled`是既有決策（非本次 diff 產生，屬既定現況），且 `.github/workflows/**` 明列在本計劃禁改清單內 → 預設方向是**測試改讀 `.disabled` 檔名**，而非還原 workflow 檔名。若 CTO 副手發現「改名為 .disabled」本身是意外／待復原的錯誤操作，而非有意決策，應暫停並回報 CEO/CTO 確認，不可自行決定還原 workflow 啟用狀態（那屬於 CI 是否啟用的營運決策，非測試修復範圍）。
- **驗收條件**：`pytest tests/test_ceo_sweep_schedule.py tests/test_deploy_health_monitor.py -v` 全過。
- **預估工時**：1 小時
- **Reviewer**：CTO 線 CI/DevOps 負責人
- **平行性**：與 PR-A/C/D/E 平行
- **契約風險**：無對外契約；若涉及「是否要真的復原 CI 啟用」則有營運風險，已在判斷建議中標出交由 CEO/CTO 決定

### PR-C：群3 OpenAPI spec 補登路由（1 個 fail）

- **標題**：`fix(api): register /api/module-telemetry in OpenAPI spec`
- **涉及**：OpenAPI spec 定義檔（web.py 內或獨立 spec 檔，依現況而定）、對應 `tests/test_ai_friendly_api.py::test_openapi_spec_covers_every_real_handled_path`
- **判斷建議（source of truth）**：路由已在 `web.py` 有實作且對外可呼叫，測試是在檢查「spec 是否誠實反映實作」→ 預設方向是**補登 spec**，而非刪除/隱藏該路由的實作。若補登時發現該路由本不該對外曝露（例如僅供內部診斷），應回報 CEO/CTO 決定是否要下線該路由而非登錄。
- **驗收條件**：`pytest tests/test_ai_friendly_api.py -v` 全過。
- **預估工時**：1 小時
- **Reviewer**：CTO 線 API 負責人
- **平行性**：與 PR-A/B/D/E 平行
- **契約風險**：**是** — OpenAPI spec 屬對外 API 契約，若此路由曝露敏感資訊或影響外部整合，建議標記給 CEO 決定是否加開 harper 安全複審。本計劃先標出此風險點，不預設加審。

### PR-D：群4 backfill 產出補欄位（3 個 fail）

- **標題**：`fix(backfill): include confidence/model_id fields in training JSONL output`
- **涉及**：backfill 產出邏輯（非 scoring.py 校準邏輯本身，而是輸出組裝那一段）、`tests/test_backfill.py`
- **判斷建議（source of truth）**：測試斷言的欄位（`confidence`、`model_id`）是下游訓練資料的既定 schema 需求，且 3 個測試都是在檢查「產出完整性」而非任意期望值 → 預設方向是**產出端補欄位**，而非弱化測試期望。若 CTO 副手發現這兩個欄位在目前架構下無法可靠取得（例如 `confidence` 值恰好落在群1 校準模型爭議範圍內），應暫停、回報，不可為了讓測試過而填入假值或硬編碼常數。
- **驗收條件**：`pytest tests/test_backfill.py -v` 全過。
- **預估工時**：1.5 小時
- **Reviewer**：CTO 線資料管線負責人
- **平行性**：與 PR-A/B/C/E 平行
- **契約風險**：訓練資料 schema 變更可能影響下游模型消費者，建議標記給 CEO 判斷是否需通知 CDO／模型 owner（非安全風險，但屬跨團隊契約，建議至少知會）。

### PR-E：群1 隔離為 xfail ＋ 開追蹤 issue

- **標題**：`test: mark pre-existing calibration drift tests as xfail (tracked, not fixed)`
- **涉及檔案（僅新增 `pytest.mark.xfail` 裝飾，不改任何斷言值、不改 scoring.py、不改 model artifact）**：
  - `tests/test_w4_calibration.py`（16 個測試，需列出具體 test 名稱，由 CTO 副手依 pytest 收集結果逐一標記）
  - `tests/test_calibration_model.py`（2 個）
  - `tests/test_security.py`（1 個，僅該涉及校準值的那一個 test function，不影響其他安全測試）
  - `tests/test_stress.py`（1 個）
- **標記格式**：
  ```python
  @pytest.mark.xfail(
      reason="pre-existing 校準模型漂移，commit 9017a09 引入新 isotonic 模型後未同步更新測試期望值；追蹤 #<新issue>",
      strict=False,
  )
  ```
- **硬性限制**：本 PR **不得修改** `scoring.py` 任何一行、**不得修改** `data/model-artifacts/calibration-model.json`、**不得修改**任何測試的斷言值或期望數字，只能新增 `xfail` 裝飾與對應追蹤 issue 連結。
- **追蹤 issue 骨架（掛給 CDO / 模型 owner）**：

  > **標題**：校準模型與測試期望值不一致（commit 9017a09 引入新 isotonic 模型，20 個測試未同步）
  >
  > **內文骨架**：
  > - **背景**：2026-07-21 commit `9017a09` 將 `data/model-artifacts/calibration-model.json`（以 1980 筆 ground-truth 重訓的 isotonic 校準模型）加入版控。`scoring.py:1684 _calibrate_confidence()` 會優先載入此模型，但目前 20 個測試仍釘住舊的硬編碼 fallback 表期望值。
  > - **待決策**：
  >   1. 新校準模型的輸出是否為「正確且應採納」的目標值？若是，應更新這 20 個測試的期望值以對齊新模型。
  >   2. 或者新模型本身有問題（例如訓練資料代表性、過擬合、與 fallback 表設計意圖不符），應回退使用 fallback 表，或修正模型後再重訓？
  > - **範圍**：`tests/test_w4_calibration.py`（16）、`tests/test_calibration_model.py`（2）、`tests/test_security.py`（1）、`tests/test_stress.py`（1），共 20 個測試目前標記為 `xfail`，等待此議題定案後移除標記並修正期望值或模型。
  > - **不在此 issue 範圍**：任何程式碼修改，此 issue 僅記錄決策待辦。
  > - **Owner**：CDO / 模型負責人
  > - **關聯**：Issue #565、PLAN-565

- **驗收條件**：`pytest tests/test_w4_calibration.py tests/test_calibration_model.py tests/test_security.py tests/test_stress.py -v` 顯示 20 個 `xfail`（非 error/fail），追蹤 issue 已建立並在 PR 描述中連結。
- **預估工時**：1 小時（含開 issue）
- **Reviewer**：CPO（gray，本人覆核 xfail reason 是否清楚可追溯）＋ CTO 線 eye 掃（確認未動到 scoring.py / model artifact）
- **平行性**：與 PR-A/B/C/D 完全平行，彼此無依賴
- **契約風險**：無對外契約風險；但需提醒 CEO 這是「延後決策」而非「解決」，20 個 xfail 需設定合理的重審期限（建議 2 週內，由 CDO 排入）

---

## 3. 收斂驗收（全部 PR 合完後）

- `pytest -q` 於 repo 根目錄執行：**0 fail**（PR-E 產生的 20 xfail 不算 fail，需在報告中明確列出 xfail 數量與清單一致）。
- 前端：`npm run build` / 對應 test / lint 全綠。
- Pre-push 全測試閘（既有 hook）可完整跑過，不再因 pre-existing 問題被擋。
- 最終驗收由 CTO 副手彙整一份簡短測試摘要（總數/通過/xfail/失敗=0）回報 CEO。

---

## 4. Gate 與 Reviewer 總表

| PR | 內容 | Reviewer | 額外 gate | 是否需 harper 雙審 |
|----|------|----------|-----------|---------------------|
| PR-A | 前端 build 補 prop | CTO 線前端負責人 + eye 掃 + `/codex-review` | 無 | 否（無安全相關） |
| PR-B | 群2 workflow 測試 | CTO 線 CI/DevOps 負責人 + eye 掃 + `/codex-review` | 若涉及 CI 啟用狀態變更需先回報 CEO/CTO | 否 |
| PR-C | 群3 OpenAPI spec | CTO 線 API 負責人 + eye 掃 + `/codex-review` | **建議 CEO 判斷**是否因對外契約風險加開 harper 複審 | **待 CEO 裁示** |
| PR-D | 群4 backfill 欄位 | CTO 線資料管線負責人 + eye 掃 + `/codex-review` | 建議知會 CDO（下游 schema 影響） | 否（非安全，但建議告知） |
| PR-E | 群1 xfail + 追蹤 issue | CPO（gray）+ CTO 線 eye 掃 + `/codex-review` | 需 CDO 認領追蹤 issue | 否 |

本批次整體判斷：**沒有安全相關修改**，預設不需 harper 雙審；唯 PR-C（OpenAPI 對外契約）建議 CEO 額外確認是否加審。

---

## 5. 執行順序建議與相依

- **無強制先後順序** — PR-A、PR-B、PR-C、PR-D、PR-E 五張互相獨立，改動檔案完全不重疊，**可五線同時開工**。
- 建議唯一的軟性順序：PR-E 的追蹤 issue 應**在 PR-E 送審前**先建立，以便 PR 描述中可連結 issue 編號。
- 若人力有限需排序，建議優先度：PR-A（最快、解鎖前端 CI）→ PR-B/PR-C（各 1 小時、機械修）→ PR-D（稍複雜）→ PR-E（獨立處理，可與其他四張同時由不同人負責）。
- 全部 PR 合併後，執行第 3 節「收斂驗收」作為最終關卡。

---

## 一頁摘要（給 CEO）

- **PR 數量**：5 張（PR-A 前端build／PR-B 群2 workflow／PR-C 群3 OpenAPI／PR-D 群4 backfill／PR-E 群1 xfail+追蹤issue）
- **總工時估算**：0.5 + 1 + 1 + 1.5 + 1 = **5 小時**
- **平行後 wall-clock**：五張互不依賴、檔案不重疊，可完全平行 → 理論 wall-clock ≈ **最長單張耗時 1.5 小時**（若人力足夠五線同開）
- **各 PR Reviewer**：
  - PR-A：CTO 線前端負責人
  - PR-B：CTO 線 CI/DevOps 負責人
  - PR-C：CTO 線 API 負責人
  - PR-D：CTO 線資料管線負責人
  - PR-E：CPO（gray）+ CTO 線 eye 掃
- **需 CEO 額外裁示的點**：
  1. PR-C（OpenAPI spec 補登 `/api/module-telemetry`）是否因對外契約風險需加開 harper 安全複審。
  2. PR-B 若 CTO 副手在執行中發現「workflow 改名 .disabled」並非有意決策而是待復原的疏失，需暫停回報，不由計劃預先裁定。
  3. PR-E 追蹤 issue 的重審期限建議 2 週內由 CDO 排入，是否核准此期限。
