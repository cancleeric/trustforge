# Truth Discovery 統計收斂法補強評估（CRH / Dawid-Skene / LTM / CATD）

> 對應 issue #179。研究性文件，**不含任何程式碼改動**。
> 日期：2026-07-13　作者：CTO

## 1. 問題重述

`_dynamic_reputation`（`src/trustforge/trust/scoring.py` ~1090-1247 行）是 TruthFinder/CRH
式「來源信譽 ↔ 主張可信度」互迭代收斂機制，收斂良好且已通過多輪對抗審（HIGH-1/HIGH-2、
第 2/4 輪 hash-seed 確定性修正）。但其 Step B 的 agreement/contradiction 判定依賴
`_corroboration_detail(..., require_entailment=True)`：只有 `stance_fn`（Bedrock 語意
entailment）明確回 `"contradiction"` 才計入矛盾集合；`stance_fn is None`（離線/未設模型）
時，`_reputation_evidence` 算出的 agree/contra 聯集會回退成「純 overlap + 方向相容」的弱訊號
（見 #178）；`MIN_INDEPENDENT_EVIDENCE` 小樣本守門在來源獨立佐證不足 3 時強制 `α=1`，
於是離線場景下大量來源直接變成「純先驗、不受迭代影響」的誠實 no-op——不是 bug，是刻意設計的
fail-safe，但代表**離線時完全沒有收斂機制**。

另一條路 conformal 校準（`trust/conformal.py`）held-out pseudo-AUC≈0.49，`docs/qa/CONFORMAL-FINDING.md`
已誠實記錄：問題不在校準數學本身，是餵進去的底層代理訊號（同一條 OHLCV 衍生的技術指標）跟
「3 個交易日後方向是否正確」沒有真實統計相關性。

已讀的相關程式碼：`src/trustforge/trust/scoring.py`（`Claim` dataclass、`_infer_direction`、
`_corroboration_detail`、`_iterate_source_reputation`／即 `_dynamic_reputation`、
`_evidence_strength`）、`src/trustforge/trust/conformal.py`、`docs/qa/CONFORMAL-FINDING.md`。

關鍵既有資料結構：`Claim.direction ∈ {bullish, bearish, neutral}` 是**已存在的類別型標籤**，
`claims_by_source` 已依 canonical source 分組——這對 Dawid-Skene 這類「多標註者對同一 item
投類別票」的模型是現成、不需 LLM 就能餵的輸入。

## 2. 四個方法評估

| 方法 | 核心機制 | 需要 LLM？ | 收斂/理論保證 | 需要歷史標籤？ | 與現有架構整合成本 | 適用場景 |
|---|---|---|---|---|---|---|
| **CRH**（Li et al. 2014, *Resolving Conflicts in Heterogeneous Data by Truth Discovery and Source Reliability Estimation*） | 交替最小化：固定來源權重求 truth（加權中位數/均值），固定 truth 求來源權重（誤差越小權重越高），迭代到收斂 | 否（純數值/類別距離函式） | 每步都是凸子問題最優解，目標函式單調不增，收斂到局部最優（非全域）；無機率模型，無不確定性量化 | 否，無監督 | **低**——`_dynamic_reputation` 本質已是 CRH/TruthFinder 混血（先驗+agreement 加權迭代），只是 agreement 訊號來源不同 | 數值型/連續型 claim 值的多來源融合；對類別型方向判斷需另訂距離函式 |
| **Dawid-Skene**（Dawid & Skene 1979, EM for categorical multi-rater consensus） | 把每個來源當「標註者」，對同一 item 投類別票；EM 交替估計 (a) 每個來源的混淆矩陣（各類別的正確率/誤判率）(b) 每個 item 的真實類別後驗分布，直到收斂 | 否 | EM 保證似然單調不減、收斂到（局部）最優解；經典且工業界（Amazon Mechanical Turk 等）驗證成熟；小樣本時可能對初始化敏感，需多次隨機重啟 | 否，完全無監督（不需任何歷史真值） | **中**——`Claim.direction` 已是現成類別標籤，但需要新定義「item」分組 key（如 asset+time-window，而非目前以 claim 為單位），且需重寫 agree/contra 判定邏輯為 EM 迭代，不能直接插入現有 `_iterate_source_reputation` 迴圈 | **離線時最適合的 fallback**：完全不靠語意 entailment，只靠「同一標的、同一時間窗內多來源給的方向票」做統計共識 |
| **CATD**（Li et al. 2014, *A Confidence-Aware Approach for Truth Discovery on Long-Tail Data*） | 在 CRH 基礎上，用 source 的觀測樣本數估計信心區間寬度（類似 t 分布/卡方），少樣本來源的權重估計本身帶不確定性，防止「長尾少樣本來源」被過度信任或過度懲罰 | 否 | 延續 CRH 的交替最優化框架，理論保證與 CRH 同級（局部最優）；額外提供權重的信心區間，非完整貝氏後驗 | 否 | **中**——概念上可直接補強現有 `MIN_INDEPENDENT_EVIDENCE` 守門（目前是硬閾值 3，二元切換 α=1 vs α），CATD 式信心區間可把這個 cliff 換成連續的信心加權，但仍是數值型底子，套進類別型 direction 需要額外映射 | 少來源/長尾場景的權重不確定性量化，**可作為現有 `_dynamic_reputation` 小樣本守門的漸進式改良**，而非獨立 fallback |
| **LTM**（Latent Truth Model，Zhao et al. 2012, *A Bayesian Approach to Discovering Truth from Conflicting Sources for Data Integration*） | 全貝氏版本：真值與來源可靠度都設先驗，用變分推論（VB）或 Gibbs sampling 求後驗分布，天然給出可信賴區間而非點估計 | 否 | 貝氏推論理論保證完整（後驗一致性），但數值方法（VB/Gibbs）本身只保證收斂到局部最優/近似後驗，非全域最優；計算複雜度明顯高於 EM | 否，無監督（但貝氏先驗設計本身是額外的建模自由度，等同隱性超參數） | **高**——需引入變分推論或 MCMC sampler，`_dynamic_reputation` 目前是輕量純函式（無隨機性、單次呼叫 O(K·N) 迭代），LTM 的取樣型推論會破壞「同輸入必同輸出」的 determinism 承諾（docstring 明文要求），且延遲/複雜度都不符合線上信譽計算的即時性需求 | 需要正式信賴區間、且能接受較高計算成本與非確定性的批次/離線分析場景，不適合線上路徑 |

## 3. 建議

**離線 fallback：優先評估 Dawid-Skene**。理由：

1. **完全不需要 LLM**——直接解決 #178/#179 的核心痛點（Bedrock 離線時收斂機制失效）。
2. **有嚴謹收斂保證**（EM 似然單調不減），比現有 `_dynamic_reputation` 的 sigmoid+clamp 啟發式
   更「可解釋、可證明」，符合 issue 提到「犧牲純數學可解釋性」的取捨考量。
3. **不需要歷史標籤**，與 TrustForge 現有「無監督、純函式」的設計哲學一致。
4. **現成資料結構可用**：`Claim.direction` 已是類別票，不用額外抽取語意特徵；缺的只是「item
   分組 key」的定義（例如 `(asset, time_bucket)`）與一個新的 EM 收斂迴圈，可以做成
   `_dynamic_reputation` 的**平行 fallback 路徑**（`stance_fn is None` 時切換到 Dawid-Skene，
   有 `stance_fn` 時維持現行 CRH 式邏輯），而不需要重寫現有已通過多輪對抗審的主路徑。
5. 整合成本評為「中」而非「低」，主要卡點是：(a) 需要新設計 item 分組（現有程式碼以 claim/來源
   為主要分組單位，沒有「同一標的同一窗口」的既有抽象）；(b) 小樣本（單一來源對單一 item 只投
   1 票）時 EM 的混淆矩陣估計會退化，需要類似現行 `MIN_INDEPENDENT_EVIDENCE` 的守門機制。

**CATD** 不建議當獨立 fallback，但**建議作為 `_dynamic_reputation` 既有小樣本守門的漸進式優化**
候選：目前 `MIN_INDEPENDENT_EVIDENCE=3` 是二元硬 cliff（樣本數 3 以下 α 強制=1、3 以上用完整
alpha），CATD 的信心區間思路可以把這個 cliff 換成連續函數，屬於低風險、增量式改動，可另開小任務
評估，不屬於本次 fallback 決策範圍。

**LTM** 不建議：計算複雜度與非確定性都與現有「無隨機性純函式、同輸入必同輸出」的架構承諾（docstring
明文寫「W2：bounded 迭代動態來源信譽。純函式、無隨機性 → 同輸入必同輸出」）直接衝突，貝氏取樣推論
引入的隨機性會破壞這個不變量，除非改用純變分推論（仍比 EM 複雜且無額外實質收益），性價比低。

**CRH** 本質上已經是現有 `_dynamic_reputation` 的近親（交替加權迭代），不建議另外引入——真正的
缺口不是「要不要 CRH」，是「CRH/TruthFinder 式迭代需要 agreement/contradiction 訊號，而這個訊號
目前綁定 LLM entailment」。

## 4. 能否同時補強 conformal 校準的底層訊號問題？

**評估結論：不能直接補強，但方向不同、不衝突。** `docs/qa/CONFORMAL-FINDING.md` 的根因是「同一條
OHLCV 衍生的技術訊號跟未來 3 日方向沒有統計相關性」——這是**訊號來源本身的資訊量問題**（garbage in,
garbage out），CRH/Dawid-Skene/CATD/LTM 四者全部是「**給定多個訊號，如何加權融合出共識**」的方法，
前提假設是「多數訊號至少部分正確」（多數投票/加權有意義的前提）。如果底層訊號集體跟真值無關（現況
pseudo-AUC≈0.49，等同隨機），任何 truth-discovery 融合法都無法無中生有生出判別力——這點與
`_dynamic_reputation` 現有 docstring 對 W3 協同操縱訊號「informational-only、不做根本解」的態度一致。
唯一能救 conformal 校準的路是換一批**真正異質、與方向有相關性**的多來源訊號（news/social/onchain 等
真實多來源，而非同一價格序列衍生代理），這需要新的資料連接器，超出本次 truth-discovery 方法選型範圍，
建議另開 issue 追蹤。

## 5. 後續建議（若 CEO/CPO 核准往下走）

1. 開一個新 issue，範圍限定在「Dawid-Skene 離線 fallback 原型」：定義 item 分組 key、EM 迴圈、
   小樣本守門，目標是 `stance_fn is None` 時取代目前的誠實 no-op。
2. 原型完成後需要離線資料集做驗證（可能可重用 `data/data/*.csv` 衍生的方向標籤做初步 sanity check，
   但注意這批資料本身 pseudo-AUC≈0.49，不能拿來當「準確率」驗證，只能驗證 EM 收斂行為與程式碼正確性）。
3. CATD 式連續信心守門列為獨立、優先級較低的小任務。
4. LTM 不建議投入，暫不列入 roadmap。
