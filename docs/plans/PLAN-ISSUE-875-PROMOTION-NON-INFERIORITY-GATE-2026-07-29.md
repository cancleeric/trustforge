# #875 子工單 G：Promotion / Non-Inferiority Gate 與停止條件 — Scoped Plan

- 日期：2026-07-29
- Issue：[#875](https://github.com/cancleeric/trustforge/issues/875)（#748 子工單 G，預估 8h）
- 分支：`feat/875-promotion-gate`（worktree `/private/tmp/trustforge-875`，HEAD `11bcecd4`）
- Depends on：E(#871 shadow observation + provenance)、F(#874 多資產 PIT benchmark) — 已完成
- 角色：gray（CPO）計劃，**只產出計劃，不寫 / 不改任何 code**；待 CEO 審核通過後由 CTO 實作。
- 預期結論：**BLOCK**（observation dataset 尚未達 200 筆 / 30 日 / 5 資產；holder_concentration=unknown；
  ETH/BNB coverage 不足）。Gate 必須 fail-closed 並在 receipt 寫明確切缺口 reason。

## 0. 一句話定位

G 是**政策引擎（policy engine）**，不是量測儀器、不是 scorer、不是自動 promote 開關。
它讀已累積的 intrinsic shadow observations，對照**評估前先 versioned 的 thresholds**，
emit 一份 machine-readable、content-addressed 的 decision receipt
（`PASS` / `CONDITIONAL` / `BLOCK` + reason codes）。**recommend-only**：永不自動切 production。

## 1. 現況與缺口（基於實讀 source）

| 既有資產 | 角色 | 與 G 的關係 |
|---|---|---|
| `asset_intrinsic_shadow.py` `assess_intrinsic_shadow` | 單一 view 的 per-assessment 量測 | 已含 per-view coverage gate（`known_count≥3`、`source_family_count≥2`、`total_delta_cap=0.08`）。G **重用**這些 per-view 數字，不在 gate 內重算分。 |
| `asset_intrinsic_shadow.py` `build_intrinsic_shadow_observation` | 單筆 observation payload builder | 產出 G 的**輸入單元**（含 `facts_hash`、`gate`、`trust_delta`、`total_delta`、dimensions）。 |
| `agent/shadow_evidence_store.py` | 持久 SQLite ledger（observations / decisions / receipts…） | intrinsic payload 以 `ShadowObservation.intrinsic_shadow` 巢狀嵌於 parity observation 列。`shadow_dashboard.build_shadow_dashboard_report` 已示範 read-only 抽取 intrinsic 欄位的正確讀徑。G 沿用此讀徑，**不新增 table**（除非設計決策選 B，見 §5）。 |
| `agent/shadow_contracts.py` `ShadowPolicy`/`ShadowDecision`/`ShadowBlocker`/`ShadowDecisionAction`/`evaluate_shadow` | **kernel release parity** 契約（凍結 v1、parity-based、action=continue/stop/eligible） | **與 G 不同關注點**。G 不得修改此契約。G 定義**獨立**的 intrinsic promotion 契約，但鏡射其 digest-bound / versioned / fail-closed 紀律。 |
| `asset_intrinsic_benchmark.py` | 單次 PIT 量測儀器（4 measurements + manifest，`disposition="remain-shadow"`） | 是 G 的**敏感度/操縱量測來源**之一（AC4 stop conditions 的 sensitivity/single-source），但 benchmark 是 snapshot，G 是跨時間累積的政策評估。G 引用 benchmark manifest digest 作 receipt 附件，不重跑 benchmark。 |

**缺口（greenfield）**：全倉目前**無** promotion / non-inferiority / decision-receipt scaffolding
（已 grep 確認）。G 需新增：政策契約 + 門檻常數檔 + gate 引擎 + receipt schema + dataset-bound 評估。

## 2. Scope

### In scope
1. versioned promotion policy（thresholds + digest）與其載入/驗證（鏡射 `load_policy`/`policy_digest` 紀律）。
2. pure gate 引擎：`observations + policy → receipt`，deterministic、fail-closed、無 I/O。
3. machine-readable decision receipt（`PASS`/`CONDITIONAL`/`BLOCK` + 有序 reason codes + 綁定 policy_digest / observation_root_digest / evaluated_at / benchmark_manifest_digest）。
4. 五項 stop conditions 的具體可量測定義與觸發（AC4）。
5. non-inferiority 與 calibration 兩條件分支（AC3、AC5），含「無標籤不得宣稱校準改善」。
6. 對**真實現況 dataset** 跑一次評估並產出 commit-bound receipt，誠實記錄 BLOCK 與缺口。
7. 完整回歸測試（reproducibility / fail-closed / identity-invariance / version-immutability / BLOCK→PASS 禁止）。

### Out of scope（明確禁止）
- 任何 scorer / calibration / decision-state / direction / official Report 的讀寫（沿用 shadow 邊界）。
- 自動切 feature flag、自動 promote、自動 route（K 才做）。
- 修改 kernel parity 的 `ShadowPolicy`/`ShadowDecision`/`ShadowBlocker`/`evaluate_shadow`。
- 採購資料、補缺失 asset、補 holder_concentration 數值（D 的範圍；G 只誠實標 unknown）。
- 改 benchmark 引擎邏輯（G 只**消費**其 manifest digest）。
- UI / 使用者文案（I 的範圍）。

## 3. 設計總則（不可妥協）

1. **純函式**：gate 引擎簽名 `evaluate_intrinsic_promotion(observations, policy, *, now, benchmark_manifest_digest) -> IntrinsicPromotionReceipt`。無檔案、無網路、無 DB 寫入。
2. **versioned before evaluation（AC6）**：policy 為 frozen dataclass，欄位集合與值在載入後不可變；以獨立 digest domain（如 `trustforge.intrinsic.promotion.policy.v1`）算 `policy_digest`。receipt 必須先記 `policy_digest` 再記任何評估數字。**不允許「先看結果再調門檻」**。
3. **fail-closed**：任何 nonfinite、型別錯誤、觀測缺欄、未登錄 policy → 退化为 BLOCK 並附 `receipt_malformed`/`policy_unversioned` reason，**不得** raise 中斷或落到 PASS/CONDITIONAL。
4. **identity-blind**：gate 不得讀 `asset_id` 字面值做判斷（除「不同 asset_id 計數」這類結構性統計）；同名事實跨 symbol 必須不變（AC3 invariant，由測試保證）。
5. **recommend-only**：receipt 不含任何 mutate 動作；`PASS` 僅代表「證據達標，建議進入 operator review（H/J/K）」，不等於 promote。
6. **與 parity shadow 物理分離**：新契約置於獨立模組（建議 `src/trustforge/asset_intrinsic_promotion.py` 或 `agent/intrinsic_promotion.py`），不 import parity 的 mutable 列舉。

## 4. AC 逐條對應設計

### AC1 — 最低 evidence：200 PIT observations, ≥5 assets, ≥30 days
- policy thresholds：`minimum_observations=200`、`minimum_assets=5`、`minimum_observation_days=30`。
- 引擎計算：跨 observation 的 `asset_id` 去重數、`observed_at` 跨度（max−min，UTC）、總筆數。
- 任一不足 → 加 `INSUFFICIENT_OBSERVATIONS` / `INSUFFICIENT_ASSET_COVERAGE` / `INSUFFICIENT_OBSERVATION_SPAN` reason → BLOCK。
- 觀測必須 PIT-safe：`pit_epoch ≤ observed_at` 且在 window 內（沿用 `evaluate_shadow` 的 PIT 檢查概念，獨立實作）。

### AC2 — 每 promotion-eligible assessment ≥3/5 known + 2 source families；不足=BLOCK
- per-view 數字已由 `assess_intrinsic_shadow` 的 `gate` 提供（`known_count`、`source_family_count`、`passed`）。
- 引擎彙總：對「被視為 promotion-eligible 的 observation」（即 `gate.passed=True`）逐一檢查；若任一 eligible obs 的 `known_count<3` 或 `source_family_count<2` → `INELIGIBLE_ASSESSMENT_IN_WINDOW`（理論上 per-view gate 已擋，此為防回歸的雙保險）。
- 視窗內 eligible 比例低於門檻（如 `< minimum_eligible_fraction`） → `INSUFFICIENT_ELIGIBLE_FRACTION` → BLOCK。

### AC3 — abs delta ≤0.08；nonfinite 零容忍；identical facts across symbols invariant
- `abs(total_delta) ≤ 0.08` 對所有 obs 成立（與既有 `TOTAL_DELTA_CAP` 一致）；超標 → `DELTA_EXCEEDS_NON_INFERIORITY_MARGIN`。
- nonfinite 零容忍：任何 obs 的 `total_delta`/`trust_delta` nonfinite → 該 obs 視為 corrupt，視窗 corrupt 率超 `corrupt_rate_max` → `CORRUPT_OBSERVATIONS` → BLOCK；單筆即記錄但不致整體 raise。
- **invariant（核心 metamorphic）**：相同 `facts_hash` 在不同 `asset_id` 下，`total_delta` 必須 byte-equal。引擎以 `facts_hash → {total_delta}` 分組驗證；衝突 → `IDENTICAL_FACTS_DIVERGENT_DELTA` → BLOCK（這是構造性誠失信號，直接 BLOCK）。

### AC4 — 停止門檻（五項，具體可量測）
| Stop condition | 量測 | 觸發 reason |
|---|---|---|
| direction/decision flips | 視窗內（依時間排序）`total_delta` 變號次數，或 per-asset gate 決策由 pass↔fail 翻轉次數 | `DIRECTION_OR_DECISION_FLIP` |
| coverage disparity | `max(known_count) − min(known_count)` 或 source-family count 的跨 asset 極差 | `COVERAGE_DISPARITY` |
| missingness | unknown/stale/conflicted/unavailable 維度佔比（跨 obs 加總） | `MISSINGNESS_RATE_EXCEEDED` |
| sensitivity | 引用 benchmark manifest 的 extreme-value sweep：任一維度響應偏離預期線性（單維不應使 total_delta 越界） | `SENSITIVITY_OUT_OF_BOUND` |
| single-source dependency | 任 asset eligible facts 來自 <2 family，或全 corpus 主導家族佔比 > cap | `SINGLE_SOURCE_DEPENDENCY` |
- 任一 stop condition 觸發即為 BLOCK（這些是「研究過程出現不可信號」，非「資料不足」）。

### AC5 — 若有成熟 outcome labels：Brier/ECE 各不得惡化 >0.01；無標籤不得宣稱校準改善
- policy 含 `calibration_check` 區塊：`labels_mature: bool`、`brier_degradation_max=0.01`、`ece_degradation_max=0.01`。
- 引擎分支：
  - `labels_mature=False`（現況）→ receipt 欄 `calibration_claim="withheld_no_mature_labels"`，**禁止**任何「校準改善」字樣；不產生 Brier/ECE blocker，但 receipt 明示未驗證。
  - `labels_mature=True` → 需外部傳入 baseline vs candidate 的 Brier/ECE；惡化 > 0.01 → `CALIBRATION_REGRESSION` → BLOCK；否則納入 CONDITIONAL/PASS 條件。
- 「無標籤」不是 BLOCK 理由（資料尚早），但「宣稱校準改善」是禁止事項（receipt 文案鎖死）。

### AC6 — 門檻在評估結果前先 versioned
- policy 檔（建議 `data/contracts/intrinsic-promotion-policy.v1.json`）與 parity policy 同目錄、同載入紀律。
- 引擎**第一個動作**：載入 policy、算 `policy_digest`、寫入 receipt 的 `policy_digest` 欄，**之後**才讀 observations。
- receipt 序列化順序在測試中以位元級 assert 固定（policy 欄必在 result 欄之前），防止未來重排偷渡「先看結果」。

### AC7 — Emit commit-bound policy + decision receipt；禁止手動 BLOCK→PASS
- receipt 為 append-only、content-addressed（`receipt_id = sha256(receipt_domain, canonical_json(receipt))`），鏡射 `shadow_evidence_store` 的 event/digest 紀律。
- receipt 組成：`policy_digest` + `observation_root_digest`（有序 observation event/digest 的 root）+ `benchmark_manifest_digest` + `evaluated_at` + `decision` + `reasons[]` + `summary_counters`。
- **BLOCK→PASS 不變式**：同一組 `(policy_digest, observation_root_digest, evaluated_at)` 的 decision 由內容決定、不可手改；要改 decision 必須換 policy version（新 digest）並重 emit，舊 receipt 保留（append-only）。測試以「同輸入兩次評估 byte-equal」+「改任一門檻 → digest 變 → receipt_id 變」保證。

## 5. 輸入取得（設計決策，交 CTO 擇一，推薦 A）

gate 引擎本身只吃「intrinsic observation 序列」。**抽取來源**有兩條低耦合路徑：

- **A（推薦）**：沿用 `shadow_dashboard` 已驗證的讀徑——`ShadowEvidenceStore(read_only=True).read_only_evaluate(...)` 取 observations，再抽 `observation.intrinsic_shadow`（非 None 者）餵 gate。**零新 table、零 schema migration**，與既有 read-only snapshot 安全模型一致。
- **B（僅當 A 耦合度被質疑）**：在 ledger 新增獨立 intrinsic observation table。**代價高**（schema v3 migration、immutability triggers、fingerprint 重算、v2→v3 升級路徑、新 retention）——非 8h 工單合理範圍，**不建議在本 issue 做**；若需要另開 issue。

> 不論 A/B，gate 引擎介面不變；差異僅在「adapter 層」。CTO 應優先 A 並在 PR 說明理由。

## 6. Deliverables（具體產物清單）

1. **policy 契約模組**（新檔）：frozen dataclass `IntrinsicPromotionPolicy` + `policy_digest` + `load_intrinsic_promotion_policy`，欄位涵蓋 AC1–AC6 所有 threshold（含 calibration 區塊）。獨立 digest domain。
2. **versioned thresholds 檔**：`data/contracts/intrinsic-promotion-policy.v1.json`，值與既有常數對齊（`0.08`、`3`、`2`、`200`、`5`、`30d`…），並含 stop-condition caps 與 `labels_mature=false`。
3. **gate 引擎**（新檔）：`evaluate_intrinsic_promotion(...)` pure 函式 + receipt dataclass + reason enum（`IntrinsicPromotionReason`，字串列舉，**獨立於** `ShadowBlocker`）。
4. **receipt 序列化**：canonical JSON（重用 `canonical_json`）、`serialize_receipt` / `receipt_digest`；policy 欄先於 result 欄。
5. **dataset-bound 評估腳本/entrypoint**：對真實 observations 跑一次，產出 `data/intrinsic_promotion/receipt-<evaluated_at>.json`（commit-bound），decision 預期 `BLOCK`，reason 至少含 `INSUFFICIENT_OBSERVATIONS` + `INSUFFICIENT_ASSET_COVERAGE`（ETH/BNB 不足）+ holder_concentration unknown 的 honest 標註。
6. **測試套件**（見 §7）。
7. **manifest/receipt digest 串接**：gate 接受 `benchmark_manifest_digest`（來自 #874 `data_version`）寫入 receipt，證明敏感度結論可追溯。

## 7. Test Plan（鏡射 `test_asset_intrinsic_benchmark.py` 紀律）

- **T1 reproducibility**：同一組 observations + policy → receipt byte-equal；canonical 序列化固定；golden receipt 對 commit-bound 檔位元級比對。
- **T2 fail-closed**：nonfinite `total_delta`、缺欄、未登錄 policy、型別錯誤 → receipt 退化為 BLOCK + 對應 reason，不 raise、不 PASS。
- **T3 identity-invariant（AC3 核心）**：相同 facts 換 asset_id → 相同 `total_delta`；相同整組輸入 → 相同 receipt。建構一個「相同 facts、不同 symbol」fixture 直接驗 invariant，衝突時必 BLOCK。
- **T4 version-immutability（AC6）**：policy 欄位集合 / 值不符 v1 → 載入失敗；receipt JSON 中 `policy_digest` 出現位置早於任何 result 欄。
- **T5 BLOCK→PASS 禁止（AC7）**：同 `(policy_digest, observation_root_digest, evaluated_at)` decision 由內容決定；只調降門檻使其「通過」→ `policy_digest` 變 → `receipt_id` 變 → 證明無手改路徑。
- **T6 真實現況=BLOCK**：對現況 dataset 評估，assert `decision=="BLOCK"` 且 reasons 含預期缺口碼；明確測「不宣稱校準改善」（`calibration_claim=="withheld_no_mature_labels"`）。
- **T7 stop conditions 各擊發一次**：對 AC4 五項各造一個最小 fixture 觸發對應 reason（含 sensitivity 經由傳入的 benchmark_manifest_digest 模擬越界）。
- **T8 import-surface guard**：gate 模組不得 import scorer/calibration/decision/direction/web（仿 benchmark 的 import 守衛）。
- 全域：跑 `.githooks/pre-push`（test+lint+build+`git diff --check`）必綠；本 issue 為 judgment-integrity sensitive，merge 前加 harper(CISO) + `/codex-review` 雙審（依 AGENTS.md）。

## 8. Risks

- **R1 過早 PASS 的壓力**：現況資料明顯不足，若 gate 意外吐 CONDITIONAL/PASS 即為嚴重缺陷。Mitigation：T6 直接 assert BLOCK；reason 為「資料不足」時 policy 強制 BLOCK（無 CONDITIONAL 逃逸路徑）。
- **R2 與 parity shadow 概念混淆**：誤把 intrinsic promotion 接到 `ShadowPolicy`/`evaluate_shadow`。Mitigation：物理分模組、獨立 enum、import guard（T8）。
- **R3 sensitivity 數據滯後**：AC4 sensitivity 依賴 benchmark manifest，若 manifest 未更新則 receipt 過時。Mitigation：receipt 綁 `benchmark_manifest_digest` + `evaluated_at`，讓過時一目了然；不在 gate 內重跑 benchmark。
- **R4 calibration 宣稱越界**：在無標籤期誤寫「改善校準」。Mitigation：receipt 文案鎖死 `withheld_no_mature_labels`，測試 T6 斷言。
- **R5 holder_concentration 被當阻擋全維度的藉口**：D 允許該維長期 unknown。Mitimization：gate 只標 holder_concentration 為 per-asset missingness 計數的一環，**不**因其 unknown 就 BLOCK 整體（BLOCK 來自證據量/不變性/stop conditions，非單一維 unknown）。
- **R6 設計決策 B（新 table）擴大範圍**：若 CTO 誤選 B 會超 8h 並動 schema。Mitigation：本計劃明定推薦 A、B 需另開 issue。

## 9. Non-goals / 禁止事項（再次明列）

- 不寫 code（CPO 角色）；不 commit；不改 scorer/calibration/direction/Report。
- 不修改 parity `ShadowPolicy`/`ShadowDecision`/`ShadowBlocker`/`evaluate_shadow`。
- 不自動 promote、不切 flag、不 route production。
- 不補缺資料、不採購、不為 holder_concentration 編數值。
- 不在 receipt 出現「BTC>BNB」或任何 symbol 排序斷言。
- 不在無標籤期宣稱校準改善。

## 10. 完工定義（Definition of Done for CEO 驗收）

- policy 契約 + versioned thresholds 檔 + gate 引擎 + receipt schema 全部存在且測試綠。
- 真實現況 receipt 已 commit，`decision=="BLOCK"`，缺口 reason 誠實可讀。
- AC1–AC7 各有一條以上測試覆蓋；pre-push + harper + `/codex-review` 全綠。
- 本計劃標註之「不宣稱校準改善」「不自動 promote」「BLOCK→PASS 禁止」三項以測試斷言固化。
