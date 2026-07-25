# PLAN-570 三軌 learning event 接入真實 analysis flow

> 日期：2026-07-25
> 擬定：gray（CPO）
> 狀態：**待 CEO 審批；未獲審批前不授權開工、不動程式碼、不碰 DB、不 merge、不上生產。**
> Issue：#570「三軌 learning event 接入真實 analysis flow」
> 基底：最新合法 `develop`（commit `7eb867e`，2026-07-25）
> 相關：`PLAN-THREE-TRACK-REMEDIATION-2026-07-24.md`（處置總計劃）、`PLAN-TRUSTFORGE-THREE-TRACK-LEARNING-SYSTEM-2026-07-23.md`（原始架構）

---

## 0. 計劃定位與授權邊界

本文件是 CPO 的**分析與開發計劃**，不是執行授權。所有實作須：
1. CEO 審批本計劃後才派工；
2. 每一執行輪由 CEO 確認當輪範圍；
3. ⛔ 本計劃全程 **no-DB、no-migration、no-secret**（三軌設計本就是 file-based append-only event store，不觸網）；唯若執行輪要跑 integration／真 DB 親測，仍需 Eric 當次 purpose token。

---

## 1. 現況判定（CPO 親自查證）

### 1.1 三軌模組「定義完整但未接線」屬實

grep 確認 production pipeline 三個入口檔**完全不 import** 三軌模組：

| 檔案 | import 三軌？ | 證據 |
|---|---|---|
| `src/trustforge/analysis_flow.py`（daemon 五階段管線） | ❌ 否 | 僅 import `budget_guard`/`orchestrator`/`ledger`/`trust.scoring`/`feature_store`；無 `analysis_quality_emission`/`learning_event_store`/`delayed_outcome_labeler` |
| `src/trustforge/agent/orchestrator.py`（sync `/api/analyze`） | ❌ 否 | `run_agent_pipeline()` 純函式回 `(Report, list[Evidence])`，無任何學習事件呼叫 |
| `src/trustforge/web.py`（HTTP 層） | ❌ 否 | 無三軌 import |

→ 三軌目前是「單元測試全綠、但 production 從未產生任何 learning event」的游離狀態。

### 1.2 接線邏輯其實已寫好——但只在 test 檔裡

`tests/test_three_track_real_flow_e2e.py`（#512 E2E）**已含完整接線範本**，且通過 CEO hard gate（07-23）：

| test-only helper | 角色 | production 對應 |
|---|---|---|
| `real_result_to_quality_snapshot(job_row, *, tenant_id)` | 真實 result payload → `(snapshot, trusted_pit, trusted_provenance)` 映射 | **需提升為 production module** |
| `ThreeTrackLearningGate` | feature-flag kill switch（`emission_enabled=False` → 零事件、管線不變） | **需提升為 production** |
| `emit_analysis_quality_event(...)` | 既有 emission boundary（已在 `analysis_quality_emission.py`） | 已是 production，直接用 |
| `FileLearningEventStore` / `LearningEventAppendLog` | append-only sink（已在 `learning_event_store.py`） | 已是 production，直接用 |

**核心結論：#570 不是「從頭寫接線」，而是「把已驗證的 test helper 提升為 production module，掛進 `analysis_flow._worker()` 的兩個 completion point」。** 風險遠低於一般新功能。

### 1.3 既有 feature flag 慣例（CPO 盤點，供設計決策）

| 慣例 | 模式 | 適用 |
|---|---|---|
| `bedrock_enabled` | env `BEDROCK_MODEL_ID` AND admin config，**雙 fail-closed** | 會花錢／觸網的決策 |
| `TRUSTFORGE_HERMES_AUTONOMY_ENABLED` | `_parse_bool(os.getenv(...))`，純 env | 行為開關 |
| `TRUSTFORGE_CW_METRICS` | 純 env truthy，預設 off | 觀測性上報 |
| `TRUSTFORGE_BACKFILL_ENABLED` | `_parse_bool(os.getenv(...))`，預設 off | 行為開關 |

三軌 learning event 是 **append-only 觀測性、不花錢、不觸網、不碰 DB** → 比照 `TRUSTFORGE_CW_METRICS` / `TRUSTFORGE_HERMES_AUTONOMY_ENABLED`：**純 env、預設 OFF**，不需 admin config 雙閘（過度工程）。

---

## 2. 接線點分析（核心）

### 2.1 唯一合法的 completion point：`analysis_flow.py::_worker()`

`AnalysisFlow` 是 daemon 五階段管線（`source_ingestion → claim_extraction → trust_reasoning → evidence_assembly → report_delivery`）。每個 job 在 `_worker()` 跑完所有 stage 後落於**恰好兩個終態**：

#### ✅ SUCCESS completion point（`analysis_flow.py` 第 784-789 行）

```python
# _worker() 中，最後一個 stage (report_delivery) completed 之後：
pos = STAGES.index(stage)
if pos + 1 < len(STAGES):
    next_stage = STAGES[pos + 1]; self._checkpoint(job_id, next_stage, "queued")
    self._put_package(next_stage, package)
else:
    # ← SUCCESS HOOK POINT：result 剛由 _stage_report_delivery 寫入 analysis_results
    self._conn().execute(
        "UPDATE analysis_jobs SET state='completed',error=NULL,updated_at=? WHERE job_id=?",
        (time.time(), job_id),
    )
    self._adopted.discard(job_id)
```

此時 `analysis_results` 已有該 job 的 `payload_json`（含 report/evidence/execution_log），即 `extract_completed_jobs()` 所讀的完整資料。**這是 success emission 的精確切入點。**

#### ❌ FAILURE completion point（`analysis_flow.py` 第 828-837 行）

```python
# _worker() except 分支，retry 耗盡（not retryable or retry >= 3）：
else:
    self._checkpoint(job_id, stage, "failed", ...)
    job = self._job(job_id)
    self._conn().execute(
        "INSERT OR REPLACE INTO analysis_dead_letters VALUES(?,?,?,?,?,?,?,?,?)",
        (job_id, stage, job["coin"], job["mode"], job["question"], job["snapshot_id"],
         retry, str(exc)[:1000], time.time()),
    )
    self._adopted.discard(job_id)
    # ← FAILURE HOOK POINT：dead-lettered，terminal failure
```

此時無 result payload（分析在某 stage 炸掉），但有 `job` row + `stage` + `exc`（錯誤訊息）。failure event 用 `analysis-quality.v1` schema 的 `failure` block 承載（見 §3.3）。

> **註**：retry 中的暫態失敗（`retryable and retry < 3`，第 807-827 行）**不發 event**——那不是 terminal completion，job 還會重跑。只在「retry 耗盡進 dead letter」才發 failure event，避免同一 job 連發多次。

### 2.2 為何不接 sync 路徑（`run_agent_pipeline`）

`orchestrator.run_agent_pipeline()` 是純函式 `→ (Report, list[Evidence])`，無 job 狀態機、無 durable completion point、無 dead letter。web.py 呼叫後也只 `append_run()` 記帳。要接 sync 路徑得在 `web.py` 的 `/api/analyze` handler 另設 hook，屬不同範圍。

**本計劃範圍＝daemon `analysis_flow.py` 兩個 completion point**（與 issue「analysis flow」字面一致）。sync 路徑接線若需要，另開 issue 追蹤，不在此輪。

### 2.3 hook 不影響主分析流程的保證（fail-soft 前置條件）

兩個 hook point 都在 `_worker()` 的 **stage 已完成/已 dead-letter 之後**：
- SUCCESS hook 在 `UPDATE ... state='completed'` **之後**（job 已成功收尾，主分析結果已持久化）；
- FAILURE hook 在 dead letter `INSERT` **之後**（job 已正確標記失敗）。

→ 即使 emission 丟任何例外，**主分析的 durable 狀態已落地**，不會被 emission 拖垮。這是 fail-soft 的結構前提（§5 詳述）。

---

## 3. 接線範圍與設計

### 3.1 新增 production module：`src/trustforge/learning_emitter.py`

把 test-only helper 提升為 production，集中三件事：

1. **映射**：`completed_job_to_quality_inputs(job_row, *, tenant_id) → (snapshot, pit, provenance)` —— 從 `real_result_to_quality_snapshot` 提升，success 版（`failure.status="complete"`）+ failure 版（`failure.status="failed"`，填 `failed_stage`/`code`/`message`）。
2. **flag gate**：`three_track_learning_enabled() → bool` 讀 env（§4），預設 OFF。
3. **fail-soft emitter**：`emit_for_analysis_completion(flow, job_id, *, outcome)` —— 包「映射 → gate → emit → catch」。

公開 API（草案，非最終）：

```python
# learning_emitter.py
def three_track_learning_enabled() -> bool: ...
def emit_analysis_completion(
    *, job_row: dict, payload: dict | None, tenant_id: str,
    store: AnalysisQualityAppendSink, outcome: Literal["success", "failure"],
    failed_stage: str | None = None, error: str | None = None,
) -> str | None:
    """Fail-soft: 任何例外都 catch + log，回 None，不影響主分析。"""
```

> 模組**只做映射 + 呼叫既有 `emit_analysis_quality_event`**，不重寫 builder/sink/contract——那些已是 production 且測試覆蓋完整。

### 3.2 編輯 `analysis_flow.py`：兩處 hook（~15 行）

採 **lazy import**（比照 `_bedrock_live_attempt` 第 150 行 `from .web import _bedrock_allowed` 慣例，避免頂層循環匯入）：

**SUCCESS hook（第 789 行後）**：
```python
self._conn().execute("UPDATE analysis_jobs SET state='completed',...", ...)
self._adopted.discard(job_id)
# --- #570 三軌 learning event（fail-soft，預設 OFF）---
self._emit_learning_event_safely(job_id, outcome="success")
```

**FAILURE hook（第 837 行後）**：
```python
self._conn().execute("INSERT OR REPLACE INTO analysis_dead_letters ...", ...)
self._adopted.discard(job_id)
# --- #570 三軌 learning event（fail-soft，預設 OFF）---
self._emit_learning_event_safely(job_id, outcome="failure", failed_stage=stage, error=str(exc)[:1000])
```

新增 private method：
```python
def _emit_learning_event_safely(self, job_id, *, outcome, failed_stage=None, error=None):
    """#570：fail-soft wrapper。任何例外都 catch + log + 可觀測記錄，不抛回 _worker。"""
    try:
        from .learning_emitter import three_track_learning_enabled, emit_analysis_completion
        if not three_track_learning_enabled():
            return  # flag OFF → 完全 no-op，零成本
        store = self._learning_event_store()  # lazy FileLearningEventStore
        job_row = dict(self._job(job_id))
        payload = self._result_payload(job_id)  # success 才有；failure 為 None
        emit_analysis_completion(
            job_row=job_row, payload=payload, tenant_id=self._learning_tenant_id(),
            store=store, outcome=outcome, failed_stage=failed_stage, error=error,
        )
    except Exception:
        logging.getLogger(__name__).exception(
            "analysis_flow: 三軌 learning event emission 失敗（fail-soft，主分析不受影響）job=%s outcome=%s",
            job_id, outcome,
        )
        self._record_learning_emission_failure(job_id, outcome)  # §5.2 可觀測記錄
```

### 3.3 failure event 的 schema 處理

`analysis-quality.v1` 的 snapshot 已有 `failure` block（e2e test 第 278-284 行）：
```python
"failure": {"status": "complete", "failed_stage": None, "code": None, "message": None, "retryable": False}
```
failure event 只是把 `status` 改 `"failed"`、填 `failed_stage`/`message`（from `error`）。**不新增 schema 欄位**，沿用既有 builder。confidence/evidence_stats 對 failure job 填 0/空（job 沒產出 report）。

### 3.4 接哪些三軌模組（範圍邊界）

| 模組 | 本輪接？ | 理由 |
|---|---|---|
| `analysis_quality_emission`（emit_analysis_quality_event） | ✅ **核心，必接** | 這是「分析完成→學習事件」的唯一入口，issue 主體 |
| `learning_event_store`（FileLearningEventStore sink） | ✅ **接（作為 sink）** | emission 需要 sink；用既有 file store，免 DB |
| `delayed_outcome_labeler` | ❌ **不接（後續 issue）** | 需 T+N 市場資料來源（fixture only），生產無資料源，另開 issue |
| `analysis_anomaly_baseline` | ❌ **不接（後續 issue）** | 需先累積夠多 event 才能跑 baseline，本輪先產 event |
| `calibration_dataset` | ❌ **不接（後續 issue）** | 同上，依賴 outcome label |
| `wrapper_artifact_control` | ❌ **不接（後續 issue）** | 受控改善軌，依賴 anomaly + approval 流程，最後接 |

→ **本輪只接 emission + store**（最小可觀測閉環：分析完成 → 產生一個 immutable learning event 落地）。其餘四軌依「先有資料才能跑」順序，各開後續 issue。

### 3.5 檔案變更清單與工時

| 檔案 | 動作 | 行數 | 工時 |
|---|---|---|---|
| `src/trustforge/learning_emitter.py` | **新增** | ~180 | 3h |
| `src/trustforge/analysis_flow.py` | 編輯（2 hook + 1 private method + 2 lazy helper） | ~+30 | 1.5h |
| `tests/test_learning_emitter_wiring.py` | **新增**（§6 測試） | ~250 | 3h |
| `tests/test_three_track_real_flow_e2e.py` | （可選）重構 import 共用 helper | ~-30/+10 | 0.5h |
| 文件/docs | 更新 issue/plan 引用 | — | 0.5h |
| **合計** | | | **~8.5h** |

---

## 4. Feature Flag 設計

### 4.1 控制方式：純環境變數，預設 OFF

```python
# learning_emitter.py
_TRUTHY = {"1", "true", "yes", "on"}

def three_track_learning_enabled() -> bool:
    """三軌 learning event emission 總閘。預設 OFF。
    比照 TRUSTFORGE_CW_METRICS / TRUSTFORGE_HERMES_AUTONOMY_ENABLED 純 env 慣例。
    """
    return os.getenv("TRUSTFORGE_THREE_TRACK_LEARNING_ENABLED", "").strip().lower() in _TRUTHY
```

**為何不用 admin config 雙閘（像 `bedrock_enabled`）**：三軌 emission 不花錢、不觸網、不碰 DB、append-only——沒有「誤開造成費用/安全風險」的損害面，admin config 雙閘是過度工程。env 單閘足夠，且部署時顯式 `export TRUSTFORGE_THREE_TRACK_LEARNING_ENABLED=1` 才開，符合「預設關閉」硬要求。

### 4.2 flag ON / OFF 行為

| flag | 行為 |
|---|---|
| **OFF（預設）** | `three_track_learning_enabled()` → False → `_emit_learning_event_safely` 在第一行就 return，**零成本、零事件、管線逐字不變**。e2e G4 gate 已證（test 第 888-924 行） |
| **ON** | 讀 job_row + payload → 映射 → `emit_analysis_quality_event` → 落地 `out/learning_events/<sha>.json`。event immutable/idempotent（既有 sink 保證） |

### 4.3 相關 env（一併定義）

| env | 預設 | 用途 |
|---|---|---|
| `TRUSTFORGE_THREE_TRACK_LEARNING_ENABLED` | （空＝OFF） | emission 總閘 |
| `TRUSTFORGE_LEARNING_TENANT_ID` | `trustforge` | event 的 `tenant_id`（replay 隔離用；TrustForge 單租戶，預設值） |
| `TRUSTFORGE_LEARNING_EVENT_DIR` | `<home>/out/learning_events` | sink 目錄（`default_learning_event_directory()` 已支援，免新增） |

> 後兩者都用既有 `default_learning_event_directory()` / 既有讀法，**不新增 env 解析邏輯**，只在 emitter 讀一次 tenant id。

---

## 5. Fail-Soft 設計（issue 硬要求）

### 5.1 三層防護

| 層 | 機制 | 保證 |
|---|---|---|
| **L1 結構前提** | hook 在 durable 狀態落地**之後**（§2.3） | emission 例外不回溯污染主分析結果 |
| **L2 flag gate** | OFF 時第一行 return，連 import 都 lazy | 零風險 |
| **L3 broad catch** | `_emit_learning_event_safely` 整段包 `try/except Exception` | 任何例外（映射錯、sink 滿、fsync 失敗、lock timeout）都吞掉，不抛回 `_worker` |

### 5.2 可觀測錯誤（issue：「必須留下可觀測錯誤」）

L3 吞掉例外後，**不能靜默**。兩道可觀測記錄：

1. **Python logging**（必做）：`logging.getLogger(__name__).exception(...)` → 進 daemon log（stderr/log file），含完整 traceback + job_id + outcome。
2. **Lineage event**（best-effort）：`_record_learning_emission_failure()` 寫一筆 `analysis_lineage_events`：
   ```python
   self._append_lineage(
       "learning_emission_failed", entity_type="learning_event", entity_id=job_id,
       job_id=job_id, metadata={"outcome": outcome, "error_type": type(exc).__name__, ...},
   )
   ```
   - `analysis_lineage_events` 是 **append-only（trigger 防 UPDATE/DELETE，見 schema 第 337-344 行）**，與既有 stage lineage 同表，在 `journey()`/`status()` UI 可見。
   - best-effort：若連這筆 lineage 寫都失敗（例如 DB 問題），L1 的 logging 已留住證據——雙保險。

> **不**用 learning event store 記「emission 失敗」——store 壞了就是壞了，不能用壞的 store 記錄自己壞了。logging + lineage 是獨立通道。

### 5.3 效能/延遲風險與對策

`FileLearningEventStore.append()` 做 `portalocker` exclusive lock + cross-directory fsync（`DEFAULT_LOCK_TIMEOUT_SECONDS=10.0`）。在 worker thread 同步呼叫：
- 正常：ms 級，可接受。
- 磁碟慢/lock 競爭：最壞 10s stall 該 worker。

**v1 對策**：同步 + flag 預設 OFF（只會在顯式開啟的部署受影響）。**後續優化（另開 issue）**：改 bounded background queue（fire-and-forget），emission 從 worker thread 剝離。本計劃不做，記為 follow-up。

---

## 6. 測試計劃

新增 `tests/test_learning_emitter_wiring.py`，三類測試對應 issue 三要求：

### 6.1 flag OFF 回歸測試（主分析不受影響）

| 測試 | 斷言 |
|---|---|
| `test_flag_off_emits_zero_events` | env 未設 → 跑完 real pipeline → `FileLearningEventStore.replay()` 空 |
| `test_flag_off_pipeline_identical` | flag OFF 跑兩次 → report/evidence/confidence 逐字相同（呼應 e2e G4 `test_analysis_pipeline_identical_with_flag_off`） |
| `test_flag_off_no_lineage_learning_emission_failed` | flag OFF → 連 lineage 都不該有 `learning_emission_failed` 事件 |

### 6.2 flag ON 正向測試（產生 learning event）

| 測試 | 斷言 |
|---|---|
| `test_flag_on_success_emits_quality_event` | flag ON + real pipeline 完成 → 每個 completed job 恰好一個 `analysis-quality.v1` event，immutable、unique identity |
| `test_flag_on_event_is_idempotent_on_replay` | 同一 job 重跑 emission → `status=idempotent`（呼應 e2e `test_emitted_event_is_append_only_immutable`） |
| `test_flag_on_failure_emits_failure_event` | 製造 terminal failure（monkeypatch stage 丟不可 retry 例外）→ dead letter 後產生一筆 `failure.status="failed"` event |
| `test_flag_on_retry_in_progress_no_event` | 暫態失敗（retry 中）→ **不發 event**（避免連發），只在 dead letter 才發 |

### 6.3 fail-soft 測試（emission 失敗不影響主分析）

| 測試 | 斷言 |
|---|---|
| `test_sink_broken_main_analysis_unaffected` | flag ON + monkeypatch sink.append 丟例外 → real pipeline 仍完成、result 正常落地、job state=completed |
| `test_sink_broken_leaves_observable_trace` | 同上 → logging 留 exception（caplog 斷言）+ lineage 留 `learning_emission_failed` 一筆 |
| `test_mapping_broken_main_analysis_unaffected` | flag ON + monkeypatch 映射函式丟例外 → 主分析不受影響（L3 catch） |

### 6.4 既既有測試不回歸

- `tests/test_three_track_real_flow_e2e.py` 全綠（共用 helper 若重構，import 路徑跟著改）。
- `tests/test_analysis_quality_emission.py` / `test_learning_event_store.py` 不受影響（未動這些模組）。
- 全套 `pytest` + `.githooks/pre-push` gate（lint/build/data checks）須綠。

---

## 7. 安全考量

| 項目 | 保證 |
|---|---|
| **Trust Kernel / Evidence binding** | ⛔ 不動。emission 只「讀」result payload 做映射，不碰 `trust.scoring`/evidence 组装邏輯 |
| **time boundary（PIT）** | ⛔ 不動。PIT 欄位（event_time/available_time/as_of_time）由 `real_result_to_quality_snapshot` 從 `published_at`/doc ts 衍生，沿用 e2e 已驗證邏輯；contract 層 `_validate_event` 仍强制 `available_time ≤ as_of_time` |
| **主分析結果不被修改** | emission 是 append-only（新 event），result payload 在 hook 前已持久化且不再變動 |
| **tenant 隔離** | tenant_id 從 env 讀（單值），不從 request/外部輸入；replay 時 `store.replay(trusted_tenant_id=...)` 强制 match |
| **不新增攻擊面** | emission 不觸網、不接外部輸入、file store 走既有 `safe_fs`（pinned_directory/NO_FOLLOW/owner check） |
| **RAG 歷史僅 non-evidentiary context** | event `kind="historical_non_evidentiary"`（contract 第 14-20 行），不進 evidence chain；`question_context()` 的 retrieval 早已標 `source_tier="historical_non_evidentiary"`（analysis_flow 第 522 行） |

### 雙審觸發

依 `AGENTS.md`：本改動涉及 **analysis pipeline 接線**（雖 fail-soft + flag OFF，但掛在主分析路徑）→ **合併前須 harper(CISO) + gray(CPO) 雙審**，重點查：
1. fail-soft 是否真的 catch 所有路徑（含 `BaseException`? 否——只 catch `Exception`，`KeyboardInterrupt`/`SystemExit` 不吞，正確）；
2. lineage `learning_emission_failed` 是否會洩漏 PII（error message 截斷 1000 字 + 不含 docs 內容）；
3. flag OFF 路徑是否真的零副作用（連 lazy import 都不觸發）。

---

## 8. 實作順序（建議派工）

> 每步 CEO 審過再進下一步；副手 sonnet 交付皆須 CEO 親驗（grep/Read/真 build/親測），不輕信「完成」。

1. **Step 1（CTO）**：新增 `learning_emitter.py`（映射 + flag gate + fail-soft emitter），**含完整 unit test**（mock sink，不跑 real pipeline）。→ CEO 親驗：grep 確認無 DB/網路呼叫、flag 預設 OFF、catch 範圍。
2. **Step 2（CTO）**：編輯 `analysis_flow.py` 兩處 hook + private method。→ CEO 親驗：Read diff 確認 hook 位置在 durable 落地之後、lazy import 正確。
3. **Step 3（CTO）**：新增 `test_learning_emitter_wiring.py` 三類測試。→ CEO 親驗：真跑 `pytest`、刻意 break sink 驗 fail-soft、flag ON/OFF 各跑一次。
4. **Step 4（harper CISO + gray CPO）**：安全雙審（§7）。
5. **Step 5**：`.githooks/pre-push` gate 全綠 → PR（base develop）→ `/codex-review` 對抗審 → 修完 finding → squash merge。
6. **Step 6（CEO 親測）**：本機 flag ON 跑一輪 daemon，親看 `out/learning_events/` 產出檔案、`journey()` UI 見 event。
7. **Step 7**：關 issue #570，記錄後續四軌（outcome/anomaly/calibration/wrapper）為新 issue。

---

## 9. 風險與開放問題

| 風險 | 等級 | 對策 |
|---|---|---|
| emission stall worker（磁碟慢/lock 競爭） | 中 | flag 預設 OFF；v1 同步可接受；後續 issue 改 background queue |
| failure event 的 evidence_stats/confidence 填值語意 | 低 | 沿用 e2e 已驗證的 0/空填法；雙審確認 |
| 共用 helper 從 test 提升到 production，test 是否該重構 import | 低 | 可選；不重構則 test 與 prod 各有一份映射（容忍少量重複） |
| `run_agent_pipeline` sync 路徑未接 | 低（已知範圍外） | 另開 issue，本輪明確不含 |

---

## 10. 驗收門檻（Definition of Done）

- [ ] `learning_emitter.py` 存在，flag 預設 OFF，無 DB/網路呼叫
- [ ] `analysis_flow.py` 兩處 hook 在 durable 落地之後
- [ ] 三類測試（flag OFF 回歸 / flag ON 正向 / fail-soft）全綠
- [ ] harper(CISO) + gray(CPO) 雙審通過，無 unresolved finding
- [ ] `/codex-review` 對抗審通過
- [ ] `.githooks/pre-push` gate 全綠
- [ ] CEO 本機親測：flag ON 產出 event、flag OFF 管線不變
- [ ] PR merge 到 develop，issue #570 附完整證據關閉
- [ ] 後續四軌開 issue 追蹤
