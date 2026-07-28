# 五角度綜合分析（Multi-angle Analysis Orchestrator）可行性評估

> 日期：2026-07-28
> 類型：源碼分析 + 可行性評估 + 擴充修改計劃
> 範圍：`analysis_flow.py`、`orchestrator.py`、`scoring.py`、`pipeline.py`、`schema.py`、前端分析介面
> 基準分支：`main`

---

## 1. 結論

**高度可行，改動量可控。**

現有架構已具備約 80% 的基礎設施。主要缺的是「多 job 完成後的 synthesis 步驟」。
不需要大幅重構，只需要在現有 durable queue 上方加一層「聚合觸發 + 確定性比對 + 報告組裝」。

---

## 2. 現有有利條件

| 層 | 已具備 | 可直接複用 |
|---|---|---|
| **Snapshot** | `create_snapshot(coin)` 產出 immutable source snapshot | 五角度共用同一份 `snapshot_id`（G-MA-2 天然解決） |
| **Job Matrix** | `enqueue_matrix(snapshot_id)` 為同 snapshot 產生 5 mode × N questions 的 jobs | 五角度共用 snapshot 的 job 同時入列 |
| **Report 結構** | `Report` dataclass 有 `direction`, `calibrated_confidence`, `decision_state` | 正是 AngleResult 所需的核心欄位 |
| **Evidence** | `Evidence` 有 `trust`, `kind`, `source`, `flags` | synthesis 比對證據重疊度的基礎 |
| **Durable State** | `analysis_results` 表存完整 `payload_json` per (coin, mode, question) | 可從 DB 讀取同 snapshot 下五角度各自的完成結果 |
| **前端** | `AnalysisModeId` type 已定義五角度、`snapshot_id` 穿透到 UI | 新增 multi-angle 視圖不需改底層 type |

---

## 3. 缺口對照與解法

### G-MA-1：一次只跑一角度

**現狀**：`submit_manual(coin, mode, question)` 一次只提交一個 mode。

**解法**：新增 `submit_multi_angle(coin, question, locale)` 方法。
- 呼叫一次 `create_snapshot(coin)`
- 對五個 MODES 各呼叫一次 `enqueue_job(snapshot_id, mode, ...)`
- 回傳 `{snapshot_id, job_ids: {mode: job_id}}`

**工作量**：~30 行。直接複用現有方法，不碰 worker/stage 邏輯。

---

### G-MA-2：缺共用 source snapshot

**現狀**：`create_snapshot(coin)` 回傳基於 `sha256(docs_json)` 的 deterministic ID。

**解法**：已天然解決。`submit_multi_angle` 只呼叫一次 `create_snapshot`，五個 job 共用。

**工作量**：0。

---

### G-MA-3：缺 AngleResult 契約

**解法**：新增 `multi_angle.py`，定義 AngleResult 為現有 Report 的薄 projection：

```python
@dataclass
class AngleResult:
    angle: str                      # risk / sentiment / fundamentals / news / catalyst
    qtype: QuestionType
    report: Report
    evidence: list[Evidence]
    direction: str                  # report.direction
    calibrated_confidence: float    # report.calibrated_confidence
    decision_state: str             # report.decision_state
    key_basis_count: int
    evidence_refs: list[int]
```

**工作量**：~20 行定義。不改 Report 本身。

---

### G-MA-4：缺 cross-angle synthesis

**核心原則**：確定性演算法先比對，LLM 只負責敘事（符合反作弊鐵則）。

```python
def synthesize_angles(angles: list[AngleResult]) -> MultiAngleReport:
    conflicts = []
    
    # 1. 方向背離：兩兩比對 direction
    # 2. 信心差距：差 > 0.3 標為分歧
    # 3. 證據獨立性：source Jaccard overlap > 0.7 → 警示
    # 4. abstain 保護：任一角度 abstain，綜合不得硬拉 normal
    # 5. 共識推導：多數決 + 加權信心
    
    consensus = _derive_consensus(directions, confidences, conflicts)
    return MultiAngleReport(...)
```

**工作量**：~150 行純演算法 Python。不碰 Bedrock、不碰 scoring、不碰 trust layer。

---

### G-MA-5：文案過度宣稱

**解法**：在 multi-angle 功能上線前，前端/文件措辭收斂為「提供五種分析視角」。
功能上線後才可說「五角度交叉綜合評估」。

---

## 4. 反作弊合規分析

| 步驟 | 做法 | 是否依賴 LLM |
|------|------|:---:|
| 方向判定 `_direction()` | OHLCV 趨勢 + 客觀 facts，確定性演算法 | ❌ |
| 信任評分 `score()` | 公式：信譽×0.5 + 佐證×0.25 + 時效×0.15 − 操縱×0.40 | ❌ |
| 三態判斷 `decision_state` | calibrated_confidence 門檻 + 獨立來源數門檻 | ❌ |
| 跨源背離偵測 | 演算法比對方向極性 + stance cache | ❌（cache miss 才用 LLM，有硬上限） |
| **五角度交叉比對（新增）** | 比對 direction 相反、confidence 差距、evidence overlap | ❌ 純程式 |
| **衝突偵測（新增）** | Jaccard overlap、方向矛盾配對、abstain 碰撞 | ❌ 純程式 |
| **共識推導（新增）** | 多數決 + 加權信心 | ❌ 純程式 |
| Step3 narrative（既有） | 把已算好的結論寫成人話 | ✅ 但只是行文 |
| Synthesis 敘事（新增，選填） | 把已算好的衝突清單寫成摘要 | ✅ 但只是行文 |

**結論**：synthesis 層的交叉比對 100% 確定性程式碼，完全符合競賽反作弊鐵則。

---

## 5. 成本與預算影響

| 項目 | 影響 |
|---|---|
| Bedrock 呼叫 | 五角度 = 5×(Step1 + Step3) = 10 次呼叫 per multi-angle run |
| budget_guard | 現有 `try_reserve_request_budget()` per-job，cap 機制不變 |
| 時間預算 | daemon parallel worker 消化，不 serial；不突破 15 分鐘 |
| 存儲 | synthesis 結果存一筆 `analysis_results`（mode='multi_angle'），增量極小 |

**建議**：`submit_multi_angle` 前做 `5 × request_max_cost_usd()` 預檢。

---

## 6. Synthesis 觸發機制

**推薦方案**：在 `_stage_report_delivery` 的 INSERT 之後，檢查同 snapshot 下五角度是否全部完成：

```python
completed_modes = self._conn().execute(
    "SELECT DISTINCT mode FROM analysis_results WHERE snapshot_id=? AND coin=?",
    (job["snapshot_id"], job["coin"])
).fetchall()
if len(completed_modes) >= len(MODES):
    self._trigger_synthesis(job["snapshot_id"], job["coin"])
```

最後完成的 job 觸發 synthesis。不需要改 STAGES tuple 或 worker 架構。

---

## 7. 前端新增元件

| 元件 | 說明 |
|---|---|
| `MultiAngleOverview.tsx` | 五角度摘要表格（方向/信心/狀態/分歧標記） |
| `ConflictBadge.tsx` | 角度間衝突的視覺提示 |
| `AngleDrilldown` | 點開展開到既有 AnalysisReportView |
| API endpoint | `/api/multi-angle?coin=BTC&snapshot_id=xxx` 回傳 MultiAngleReport JSON |

---

## 8. 風險

1. **成本翻倍**：一次 multi-angle = 5× 成本，前端需明確提示使用者
2. **向後相容**：現有單角度流程完全不動，multi-angle 是 additive 新功能
3. **不過度宣稱**：五角度中任一角度 abstain，綜合結論不可硬拉 normal
4. **LLM 敘事邊界**：LLM 不得自行發明交叉訊號，只能敘述已算出的結構化結論
