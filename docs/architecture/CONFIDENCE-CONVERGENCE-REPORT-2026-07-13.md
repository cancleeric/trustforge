# 信心值收斂技術報告 — 2026-07-13

> 對應：#179（研究票，已評估結論）、#178（決賽話術對齊：離線 no-op）
> 邊界：本報告只處理「信心值（confidence/reputation）收斂品質」，**不處理
> conformal prediction 預測力問題**（AUC≈0.49 那條線，見 `docs/qa/CONFORMAL-FINDING.md`，
> 由 #167 資料源改善追蹤）。CEO/老闆 2026-07-13 指示：「預測力是之後的事，現在是信心」。

## 1. 現況：信心值目前怎麼算

`src/trustforge/trust/scoring.py::score()` 對每條 `Claim` 算：

```
TrustScore = w_src · SourceReputation
           + w_corr · CrossSourceCorroboration
           + w_rec · RecencyDecay
           − w_manip · ManipulationPenalty
```

`SourceReputation` 有兩種模式：

- **靜態**（預設，`dynamic_reputation=False`）：固定查表 `KIND_REPUTATION`（依
  來源類型給基礎信譽，如 price=0.95、social=0.35）。
- **動態**（`dynamic_reputation=True`，W2 功能，`_iterate_source_reputation()`，
  約 1090-1247 行）：TruthFinder/CRH 式交替迭代——

  1. Step A：用當前 SR 重算每條 claim 的暫時 trust
  2. Step B：把「同標的、方向相容」的其他 claim 依 `stance_fn` 語意分類
     結果分成 `agree_union_of`（entailment）/`contra_union_of`（contradiction），
     以聯集去重（防重複貼文灌票，codex 對抗審 HIGH-1/HIGH-2）加總對方的
     暫時 trust 當「投票淨值」`net`，過 `_stable_sigmoid(net)` 得
     `agreement_score`
  3. `SR^t = α·SR⁰ + (1-α)·agreement_score`（`α=0.55`，樣本量 <3 時強制
     `α=1` 純先驗，小樣本守門）
  4. 迭代到 `max|SR^t - SR^(t-1)| < REPUTATION_CONVERGENCE_EPS`（0.01）或
     `MAX_REPUTATION_ITERATIONS`（5 輪硬上限）

  這是本工程目前唯一的「信心值統計收斂」機制，且有完整的反操縱對抗審修正
  歷史（同源去重、set 順序決定性化、logit clamp 防溢位等）。

## 2. 問題：LLM 不在線時收斂力弱

`_iterate_source_reputation` 的 `agree_union_of`/`contra_union_of` 完全來自
`_reputation_evidence()` → `_corroboration_detail(..., require_entailment=True)`
的判定結果，而該判定**只認真語意 `stance_fn` 回傳 `"entailment"`**：

- `stance_fn is None`（無 Bedrock client）：直接不計入任何集合（W2 保守排除）。
- `stance_fn` 存在但回傳 `"neutral"`：無法區分「真中立」與「fail-safe 降級」
  （離線/未設模型/timeout/malformed/cache miss/預算耗盡皆回 `"neutral"`），
  一律不採信。

結果（#178 已定性並有測試覆蓋
`test_run_agent_pipeline_dynamic_reputation_offline_is_honest_noop`）：
**生產預設 `llm_mode=off` 時，`agree_n`/`contradict_n` 恆為 0，
`final == prior`**——動態信譽收斂在離線情境下是「誠實的 no-op」，不是真的
在做統計收斂，只是誠實地不假裝算過。

這是刻意的工程紀律（不用假訊號冒充語意驗證），但代表：**離線/無 LLM 時，
TrustForge 目前沒有任何真正在運作的信心值收斂機制**，只剩靜態查表。

## 3. 解法：Dawid-Skene EM 作為平行 fallback 路徑

依 #179 研究結論（`docs/architecture/TRUTH-DISCOVERY-EVALUATION-2026-07-13.md`
評估表），四個候選方法中 **Dawid-Skene（1979, EM for categorical multi-rater
consensus）最適合當 Bedrock 離線時的 fallback**：

| 評估項 | Dawid-Skene 結論 |
|---|---|
| 需要 LLM？ | 否 |
| 收斂數學保證 | EM 保證似然單調不減，收斂到（局部）最優解；經典且工業界驗證成熟 |
| 需要歷史標籤？ | 否，完全無監督 |
| 與現有架構整合成本 | 低——`_dynamic_reputation` 本質已是「來源信譽 ↔ 主張可信度」互相迭代收斂的骨架，只需把「語意 entailment 投票」換成「類別標籤共識」即可套用 |
| 適用場景 | 離線 fallback：完全不靠語意 entailment，只靠「同一標的、同一時間窗內多來源給的方向票」做統計共識 |

其餘三個方法定位不同，不適合作為離線 fallback 的**替代方案**（可留作未來
漸進式改良）：

- **CRH**：現有 `_dynamic_reputation` 本身已是 CRH/TruthFinder 混血，agreement
  訊號來源不同（語意 vs 數值距離），對類別型方向判斷需另訂距離函式，非現成
  fallback。
- **CATD**：延續 CRH 框架，適合「量化少樣本來源的權重不確定性」，可作為
  `_dynamic_reputation` 既有小樣本守門（`MIN_INDEPENDENT_EVIDENCE=3`）的漸進式
  改良，而非獨立 fallback。
- **LTM**：全貝氏版本，理論保證完整但計算複雜度明顯高於 EM，不適合線上路徑，
  且貝氏先驗設計是額外的隱性超參數，對決賽時程風險較高。

### 為何是「平行路徑」而非取代

Dawid-Skene 不改動現有 `_dynamic_reputation` 的既有語意驗證邏輯——當
`stance_fn` 可用時，語意 entailment 收斂力更強（能判斷「表面詞不同但語意相同」
的佐證），應繼續優先使用。Dawid-Skene 只在 `stance_fn is None`（完全沒有可用
分類器，即離線/未設模型）時介入，**把現在的「no-op」換成「有數學保證的類別
共識收斂」**，讓信心值即使離線也是真的在收斂，而不是原地不動。

`Claim.direction`（bullish/bearish/neutral，既有欄位，見
`_infer_direction`/`_direction_compatible`）天然就是類別標籤，可直接餵給
Dawid-Skene：對同一標的（coin）、同一時間窗內的多來源 claim，把每個來源當
「標註者」，EM 交替估計 (a) 每個來源的混淆矩陣（各類別正確率/誤判率）、
(b) 每個標的-時間窗的真實類別後驗分布，用估出的來源可靠度回填
`SourceReputation`。

## 4. 邊界聲明

本報告與後續開發計劃（`docs/plans/DAWID-SKENE-CONFIDENCE-PLAN-2026-07-13.md`）
**只解決「信心值收斂品質」問題**：

- 目標是讓 `_dynamic_reputation` 在離線時也有真正的統計收斂機制，而不是
  no-op。
- **不涉及、不修改** conformal prediction 校準（`src/trustforge/trust/conformal.py`）
  或任何與「預測力」（predictive power，AUC 那條線）相關的邏輯、資料源、或
  文件——那是 #167（HOYA BIT 真實資料接線）與既有 `docs/qa/CONFORMAL-FINDING.md`
  的範圍，明確排除於本輪工作外。
- 「信心值收斂」與「預測力」是兩個獨立問題：前者問「這個信心分數本身算得
  穩不穩、有沒有數學保證」，後者問「這個信心分數跟未來真實漲跌有沒有相關」。
  今天只做前者。
