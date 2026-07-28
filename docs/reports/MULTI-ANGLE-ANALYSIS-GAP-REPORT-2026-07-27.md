# 五角度分析缺口與補強建議報告

> 日期：2026-07-27<br>
> 類型：開發用缺口分析／產品架構建議報告<br>
> 範圍：`analysis_flow.py` 五個分析角度、`QuestionType` 對應、單視角限制、Multi-angle Analysis Orchestrator 補強方案。<br>
> 基準分支：`main`（已拉到 `a02cbea`，相較稍早 develop 分支，主 repo main 已包含較多最新文件與修正，文件落點錯誤較少。）

## 1. 結論

目前 TrustForge **有五個分析角度入口**，而且不是純裝飾：`analysis_flow.py` 已把五個 mode 接到不同的 `QuestionType` 與提問模板。

但目前能力應定義為：

> 已支援「五種單視角分析模式」，尚未支援「同一輪五角度交叉綜合評估」。

也就是：使用者選「風險評估」就只得到風險視角；若要看市場情緒、新聞驗證、基本面或催化因素，需要各自重跑。這些報告之間目前沒有共用一次性的 multi-angle synthesis，也沒有自動標出「風險面與情緒面結論相反」這類交叉訊號。

## 2. 已做到的部分

`src/trustforge/analysis_flow.py:55-61` 定義五個模式：

| Mode | 中文角度 | QuestionType | Prompt template |
|---|---|---|---|
| `risk` | 風險評估 | `MULTI_SOURCE` | 評估整體信任狀態，並標記操縱風險。 |
| `sentiment` | 市場情緒 | `MULTI_SOURCE` | 分析市場情緒、分歧與反方訊號。 |
| `fundamentals` | 基本面 | `HYPOTHESIS` | 檢驗基本面與市場敘事是否獲得證據支持。 |
| `news` | 新聞驗證 | `MULTI_SOURCE` | 整理最新事件，區分事實、推論與未證實主張。 |
| `catalyst` | 價格催化因子 | `HYPOTHESIS` | 檢驗近期催化因素是否足以改變現有判斷。 |

`src/trustforge/schema.py:26-29` 定義的 `QuestionType` 目前是：

```text
MULTI_SOURCE
HYPOTHESIS
COMPARISON
```

`src/trustforge/agent/orchestrator.py` 也確實依 `qtype` 改變報告形狀：

- `qtype == QuestionType.HYPOTHESIS` 時，abstain / judgment 文案走 hypothesis 分支。
- `qtype == QuestionType.COMPARISON` 時，報告走 comparison 分支。
- `HYPOTHESIS` 題型會計算 `hypothesis_ledger`，非 HYPOTHESIS 題型則不產生。

因此，目前的「五角度」不是假 UI；它已有後端 mode → qtype → prompt → report shape 的實際連接。

## 3. 未達標缺口

### G-MA-1：一次分析只跑一個角度

目前 `analysis_flow` 的 mode 是 job 的單一屬性；每個 job 對應一個 `QuestionType` 與一個 prompt template。這代表一次分析只會跑：

```text
coin + selected mode + selected qtype + selected question
```

沒有同一個 run 同時跑：

```text
risk + sentiment + fundamentals + news + catalyst
```

### G-MA-2：缺少五角度共用 source snapshot

若未來只是讓前端連續打五次 `/api/analyze`，會造成每個角度可能讀到不同時間點的資料。這會破壞交叉綜合可信度。

正確做法是：五角度同輪分析必須共用同一份 immutable source snapshot。

### G-MA-3：缺少 AngleResult 契約

目前沒有一個標準資料結構可以把單角度報告正規化成：

```text
angle / qtype / decision_state / direction / confidence / key_basis / limits / evidence_refs
```

缺少這層，就難以做 deterministic synthesis。

### G-MA-4：缺少 cross-angle synthesis

目前沒有後端機制自動標示：

- 風險面偏空，但情緒面偏多。
- 基本面 abstain，但催化面 normal。
- 五角度都引用同一條新聞，代表證據獨立性不足。
- 某角度信心高，另一角度信心低，需要主報告標示分歧。

### G-MA-5：目前若對外說「多角度綜合評估」會過度宣稱

目前可以說：

> TrustForge 提供五種分析視角。

但不應說：

> TrustForge 會同時從五個角度交叉綜合評估。

除非完成本報告建議的 multi-angle orchestration。

## 4. 建議補強架構

新增一層：

```text
MultiAngleAnalysisOrchestrator
```

流程：

```text
User Question
  ↓
建立同一份 immutable source snapshot
  ↓
五個 angle jobs
  ├─ risk          → MULTI_SOURCE
  ├─ sentiment     → MULTI_SOURCE
  ├─ news          → MULTI_SOURCE
  ├─ fundamentals  → HYPOTHESIS
  └─ catalyst      → HYPOTHESIS
  ↓
Normalize AngleResult
  ↓
Deterministic Cross-angle Synthesis
  ↓
MultiAngleReport
```

## 5. 建議資料契約

### 5.1 AngleResult

```python
@dataclass
class AngleResult:
    angle: str
    qtype: QuestionType
    report: Report
    evidence: list[Evidence]
    decision_state: str
    direction: str
    confidence: float | None
    key_basis: list
    limits: list[str]
    hypothesis_ledger: dict | None
```

### 5.2 AngleConflict

```python
@dataclass
class AngleConflict:
    angle_a: str
    angle_b: str
    conflict_type: str
    summary: str
    evidence_refs: list[str]
```

### 5.3 MultiAngleReport

```python
@dataclass
class MultiAngleReport:
    coin: str
    question: str
    snapshot_id: str
    angles: list[AngleResult]
    consensus: str
    conflicts: list[AngleConflict]
    agreement_matrix: dict
    synthesis_summary: str
    limits: list[str]
    evidence_index: list[Evidence]
```

## 6. 綜合層原則

Multi-angle synthesis 不應讓 LLM 直接自由發揮，而應採用：

```text
deterministic comparison first
LLM narration second
```

後端先用程式比對：

| 比對項 | 範例 |
|---|---|
| direction 是否相反 | risk 偏空，但 sentiment 偏多。 |
| confidence 差距 | fundamentals 高信心，news 低信心。 |
| evidence 是否重疊 | 五個角度都引用同一條新聞，代表獨立性不足。 |
| contrarian evidence 是否集中 | catalyst 有反方證據，但 risk 沒提。 |
| decision_state 是否 abstain | news 資料不足，但 sentiment 給方向。 |

LLM 只能把已算出的分歧寫成人話，例如：

```text
風險面與情緒面出現分歧：情緒面偏多主要來自社群熱度，但風險面因鏈上大額流出與新聞來源可信度不足而維持保守。
```

LLM 不得自行發明交叉訊號。

## 7. 前端建議呈現

新增「五角度總覽」區塊：

| 角度 | 結論 | 信心 | 狀態 | 主要依據 | 分歧 |
|---|---|---:|---|---|---|
| 風險 | 偏空 | 0.62 | normal | Manipulation risk | 與 sentiment 相反 |
| 情緒 | 偏多 | 0.58 | low_confidence | 社群熱度 | 與 risk 相反 |
| 新聞 | 中性 | 0.41 | low_confidence | 來源不足 | — |
| 基本面 | abstain | — | abstain | 證據不足 | — |
| 催化 | 偏多 | 0.66 | normal | 事件支撐 | 與 risk 分歧 |

綜合結論範例：

```text
五角度綜合：目前市場情緒與催化面偏正向，但風險面尚未確認解除。TrustForge 不給強方向性結論，建議標記為「分歧狀態」。
```

## 8. 測試建議

### 8.1 後端測試

- `multi_angle` 會產生 5 個 `AngleResult`。
- 五個角度共用同一個 `snapshot_id`。
- `HYPOTHESIS` 角度保留 `hypothesis_ledger`。
- `MULTI_SOURCE` 角度不產生 `hypothesis_ledger`。
- risk 偏空、sentiment 偏多時，會產生 `AngleConflict`。
- evidence refs 不能遺失。
- 任一角度 abstain 時，綜合層不能硬拉成 normal。
- live mode / Bedrock 模式必須受 budget guard 控制，不能因五角度直接五倍燒成本而無提示。

### 8.2 前端測試

- 顯示五角度總覽。
- conflict badge 正確出現。
- mobile table / card layout 不爆版。
- fixture / live / sample 狀態清楚標示。
- 點開角度可 drilldown 到原本單角度報告。

## 9. 建議優先序

| 優先 | 工作 |
|---|---|
| P0 | 先修文件／UI 文案，避免說成已支援五角度綜合。 |
| P1-A | 後端新增 `multi_angle` orchestrator，先 serial 跑五角度。 |
| P1-B | 新增 deterministic synthesis / conflict detection。 |
| P1-C | 前端五角度總覽與 drilldown。 |
| P1-D | 再做 parallel / queue optimization。 |

若目前對外已宣稱「多角度綜合評估」，P0 必須立即修正措辭；若只說「提供五種分析視角」，則本功能可列為 P1。

## 10. 建議拆 PR

### PR1：資料契約與 serial backend

- 新增 `AngleResult`
- 新增 `AngleConflict`
- 新增 `MultiAngleReport`
- serial 執行五角度
- deterministic conflict detection
- 後端 tests

### PR2：API / UI

- 新增 `multi_angle` API 或 mode
- 前端五角度總覽
- angle drilldown
- mobile eye scan

### PR3：queue / parallel / snapshot hardening

- 共用 immutable snapshot
- angle job lineage
- retry / DLQ
- 成本控管
- stage telemetry

## 11. 裁示建議文字

建議裁示：

> 將「五個分析角度」重新定義為兩層能力：現有為單視角分析；新增 P1「五角度綜合評估」能力。實作時必須共用同一 source snapshot，五角度分別產出 AngleResult，再由 deterministic synthesis 層標示共識、分歧與 abstain；LLM 只能負責敘事，不可自行發明交叉訊號。正式完成前，文件與 UI 不得宣稱已支援五角度交叉綜合。

## 12. 一句話

這不是小修。正確補法是新增 **Multi-angle Analysis Orchestrator**，把現在五個單視角模板提升成「同輪五角度 → 分歧偵測 → 綜合報告」的能力。
