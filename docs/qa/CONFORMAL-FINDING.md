# W4 Split Conformal Prediction — 研究發現與 roadmap

> master 計劃 Axis B #1（gray 細案，CEO 已審）。
> 狀態：**單階段 conformal 數學實作完成，但本輪不 wire 進 production**。
> 決策日期：2026-07（CEO）。

---

## 一句話結論

用 gray 細案指定的方法論（同一條 HOYA OHLCV 價格序列衍生出多個技術訊號，
餵給既有 `_evidence_strength()`）算出的 conformal τ 在數學上是正確、可
重現的 split conformal prediction，JOINT coverage 保證確實達標
（P(方向錯 且 evidence_strength>τ) = 0.0400 ≤ α=0.10；⚠️ 是嚴格 `>`，
見下方「邊界語義修正」）。但這個代理訊號
集合對「3 個交易日後方向是否正確」**幾乎沒有真實判別力**（pseudo-AUC≈
0.49，等同隨機），coverage 達標的唯一原因是 τ 被回測校準得極嚴、系統
幾乎總是選擇 abstain（held-out 實測 abstain 率 0.9405）。若把這個 τ
接進 production，會讓系統對絕大多數查詢都拒答，等於實質廢掉功能。

因此本輪決定：**保留研究工件（回測腳本 + τ + 完整方法論/誠實聲明），
不改變 production 行為**。production 三態 abstain 門檻維持原本的簡化
分位數校準（`trust.scoring._CALIBRATION_TABLE`，已誠實標示非嚴謹
conformal coverage 保證）。

---

## (a) 完成的部分：單階段 conformal 數學實作

- `scripts/backtest_conformal.py`：一次性離線回測，讀 `data/data/*.csv`
  （HOYA BIT 官方 BTC/ETH/SOL/BNB/XRP OHLCV，2021-06-01~2026-05-31）。
  - 方向判斷沿用 `prices.py::price_facts()` 規則（14 日窗口報酬
    ret>+1% 上漲、ret<-1% 下跌、其餘盤整，盤整不計入回測）。
  - 從同一價格窗口衍生「多個獨立技術訊號」：3/7/21/30 日動量、成交量
    趨勢、波動率穩定度，組成 supporting/contrarian claim 餵給既有
    `trust.scoring._evidence_strength()`（原封不動複用，不重寫評分）。
  - 往後看 N=3 交易日實際方向 vs 判斷方向 → 對/錯 label。
  - 時間切分（非隨機，防洩漏）：5 幣共用同一日期索引切點，前 70% train
    （本方法無自由參數可訓練，此段僅保留供未來擴充）／中 15% 校準集／
    後 15% held-out test。
  - nonconformity score = 校準集裡「錯誤」樣本的 evidence_strength；
    τ = 這些錯誤樣本由小到大排序後第 ⌈(n+1)(1-α)⌉ 大的順序統計量，
    α=0.1。
- `trust/conformal.py`：把回測出的 τ 硬編成常數（`_CONFORMAL_TAU =
  0.9154`，比照 `_CALIBRATION_TABLE` 模式：版控可審、非黑箱），
  `conformal_abstain_threshold()` 供未來調用；docstring 完整記錄回測
  參數/結果/誠實聲明。
- `tests/test_w4_conformal.py`：確定性測試、`compute_tau()` 順序統計量
  公式手算驗證、coverage 性質測試（重跑真實資料驗證 held-out JOINT
  coverage ≤ α+餘裕，並比對硬編常數是否仍與最新回測同量級）。

**可重現性**：`python3 scripts/backtest_conformal.py` 跑兩次輸出逐字
相同。

### 邊界語義修正（codex 對抗審發現，PR #45 內已修）

codex 審查發現初版 `compute_tau()` 有兩個邊界問題會推翻「數學正確、
保守 abstain」的核心主張：

1. **fallback 值不安全**：校準集裡沒有錯誤樣本、或所需名次超出樣本數
   時，初版 fallback 回傳 `1.0`。但 `evidence_strength` 的合法值域是
   `[0, 1]`（上界含 1.0）——若未來真的出現 `evidence_strength == 1.0`
   的錯誤樣本，用 `>=` 判斷仍會判定「通過門檻」，讓本該「保守到一律
   abstain」的 fallback 場景被鑽漏洞放行。
2. **比較運算子錯誤**：標準 split conformal 的順序統計量結果
   （P(s_{n+1} ≤ qhat) ≥ 1-α）在等價的「超過門檻」形式下是對**嚴格
   不等式** `>` 成立的，不是 `>=`。用 `>=` 沒有這個結果宣稱的有限樣本
   上界保證，ties（分數剛好等於 τ）時尤其會失真。

**修正**（已套用，見 `scripts/backtest_conformal.py`）：
- fallback 改回傳 `math.inf`（不是合法分數範圍內的值，任何有限
  `evidence_strength` 都不可能大於它，配合下一點的嚴格 `>`，效果是
  「一律 abstain」，這才是真正保守）。
- 判斷是否通過門檻全面從 `strength >= tau` 改為 `strength > tau`（腳本
  的 `main()` 與所有相關 docstring/註解同步訂正）。
- 新增反例測試（`tests/test_w4_conformal.py`）：全 1.0 錯誤樣本、
  重複分數（ties）、空校準集三種邊界情形，斷言不會誤放行、coverage
  上界仍成立。
- 用修正後的邏輯重跑真實回測驗證：**數字與修正前完全一致**
  （τ=0.9154、JOINT=0.0400、CONDITIONAL=0.6712、abstain=0.9405）——
  這次歷史資料集裡沒有測試樣本 evidence_strength 恰好等於 τ 的邊界
  情形，所以沒踩到這個 bug 的實際影響，但修正本身仍是必要的：文件
  宣稱「數學正確」就必須經得起邊界情形檢驗，不能只是這次資料剛好沒
  暴露問題。

---

## (b) 核心發現：價格代理訊號對方向判別力≈隨機

實測數字（`scripts/backtest_conformal.py` 輸出）：

```
date range: 2021-06-01 ~ 2026-05-31 | coins: BTC, ETH, SOL, BNB, XRP
split cutoffs: calib>=2024-11-30, test>=2025-08-31
alpha=0.1, forward_days=3
calib samples: 1249 (wrong=649)
tau = 0.9154
test samples: 1226 (pass/not-abstain=73, confidently-wrong=49)
held-out JOINT coverage: P(wrong AND strength>tau) = 0.0400 (target <= alpha=0.1) OK
held-out CONDITIONAL（參考用、非本輪保證對象）: P(wrong | strength>tau) = 0.6712
held-out abstain rate at tau: 0.9405
```
（用修正後的嚴格 `>` 重跑得到，數字跟修正前一致——這次資料集沒有測試
樣本剛好卡在邊界，但修正本身仍是必要的，見下方「邊界語義修正」。）

診斷過程（非猜測，實測驗證）：

- **JOINT coverage 達標**（0.0400 ≤ 0.1）——這是 gray 細案唯一要求的數學
  保證，確實成立。
- 但 **CONDITIONAL** P(錯 | strength>τ) = 0.6712，遠高於 α。這代表就算
  strength 通過了 τ 這道嚴格門檻，實際判斷仍有 67% 機率是錯的——JOINT
  保證能成立純粹是因為分母（test 全體樣本）夠大、通過 τ 的樣本
  （73/1226 ≈ 6%）夠少，不是訊號本身有預測力。
- **pseudo-AUC ≈ 0.492**（right vs wrong 樣本的 evidence_strength
  分布幾乎重疊，中位數/平均數幾乎相同）——判別力等同隨機猜測。
- **α 敏感度掃描**（α 從 0.5 掃到 0.1）：abstain 率隨 α 收緊從 ~49%
  單調上升到 ~94%，但 conditional-wrong-rate 全程持平在 50~67% 區間，
  不隨 α 改善——證明這不是「α 調鬆一點就解決」的調參問題，是訊號集合
  本身對這個預測目標缺乏區分能力。

**根因**：gray 細案要求「從當日價格窗口衍生多個獨立技術訊號」，但這些
訊號（動量/成交量趨勢/波動率）全部衍生自**同一條** OHLCV 價格序列，
彼此高度相關，不是 `_evidence_strength()` 的 `indep_factor`/
`diversity_factor` 原本設計要衡量的「真正異質多來源」（news/onchain/
social/regulatory）。用同源訊號餵給為異質來源設計的評分函式，得到的
「證據強度」在統計上仍然只反映「有幾個同源訊號一致同意」，跟「這個
方向判斷對不對」之間缺乏因果連結——這正是回測分數幾乎無法區分對錯的
原因。

---

## (c) 真正的 conformal 需要什麼：roadmap（本輪不做）

- **歷史異質多來源資料**：需要 news/onchain/social/regulatory 等真正
  獨立來源的**歷史時序**資料（不是現值快照），才能讓
  `_evidence_strength()` 的多來源假設成立，訊號才有機會真正預測方向。
- **現況**：現有連接器（`ingestion/`）只 cache 各來源的**現值**，沒有
  歷史序列可回填過去任意日期做回測——這是資料基礎設施的缺口，不是
  可以靠調整回測方法論繞過的問題（且依既有反作弊規範 #24，不可捏造
  歷史多來源資料來湊出一個好看的 τ）。
- **pipeline-aware joint coverage**：本輪連 single-stage 都只做了
  「evidence_strength → 方向判斷」這一環節；claim 抽取、跨源訊號、
  narrative 忠實度等全 pipeline 的聯合覆蓋保證仍是更遠的 roadmap 項目，
  未變動。
- **建議下一步**（供 CEO/CPO 排入未來規劃，非本輪承諾）：若要讓 W4
  conformal 真正上線，需要先有歷史多來源資料管線，再重跑
  `scripts/backtest_conformal.py`（可直接複用其切分/τ 計算邏輯，只需
  替換 `_build_signals()` 的訊號來源）評估屆時的 AUC/coverage 是否
  有意義。

---

## (d) production 現況：維持簡化校準，未套用 conformal τ

`agent.orchestrator._ABSTAIN_CALIBRATED_THRESHOLD = 0.35`（未變動，
revert 回本輪開始前的原值），三態 abstain 骨架與 `_ABSTAIN_MIN_
SUPPORTING` 全部未動。比較基準是 `trust.scoring._calibrate_confidence()`
產出的 `calibrated_confidence`（`_CALIBRATION_TABLE` 分位數映射表），
該函式 docstring 已誠實標示「簡化版分位數校準，非嚴謹 conformal
coverage 保證」——這個誠實標註本身在本輪之前已存在，未被移除或弱化。

`trust/conformal.py` / `scripts/backtest_conformal.py` 保留在 repo 中
作為文件化的研究工件，`agent.orchestrator` 不 import、不呼叫。

---

## 2026-07-27 #752 時序切分 remediation

後續稽核確認早期研究工件仍有兩個會讓結果被高估的方法問題，現已修正：

- `conformal_on_samples.py` 不再 `random.shuffle()`；改用所有幣種的
  global unique ISO dates 切分。同一天的所有幣種與來源一定落在同一
  partition，且 calibration 最晚日期嚴格早於 held-out 最早日期。
- `backtest_conformal.py` 不再拿 BTC bars index 代理其他幣種的 boundary；
  不同交易日曆一律由實際 sample dates 的聯集決定。
- `auc_proxy=max(accuracy, 1-accuracy)` 已刪除。該值不是 ROC AUC，歷史文件
  中的 pseudo-AUC 敘述只保留為舊實驗背景，不再是 promotion check。
- Conformal threshold 直接取 calibration 錯誤樣本 `evidence_strength` 的
  finite-sample upper quantile，與 `backtest_conformal.compute_tau()` 一致；
  不再錯誤地對 `1-strength` 取 quantile 後反轉。有限樣本名次不足時回傳
  `Infinity`，一律 abstain。
- 所有 offset timestamps 先 normalize 為 UTC 才決定日期 partition。
  Calibration 尾端另作 outcome-aware purge：其最晚
  `outcome_observed_at` 必須嚴格早於 held-out 最早 `as_of`；舊 OHLCV
  backtest 同樣 purge 最後 `FORWARD_DAYS` 的交界樣本。
- 新 JSON report 只誠實報 joint error、conditional error、abstain、
  passed support，並包含輸入 SHA-256、split boundaries、per-family 與
  per-coin counts。
- 空、過小、malformed 或時間欄位不合法的資料集 fail-closed；outcome 必須
  嚴格晚於 `as_of`。研究腳本不寫 production 設定，輸出固定標示
  `research-only`，即使所有 exploratory checks 通過也需要另一個經核准的
  production change。
- Calibration 與 held-out 必須各自實際包含至少兩個 source families。
  舊回測若 FNG／Blockchain input 缺失或 malformed，或任一 partition
  沒有實際異質 family support，P5 必定 FAIL，不能只靠 OHLCV 指標得到
  promotion-eligible 結論。

`tests/test_conformal_chronological.py` 同時覆蓋直接 unit 與 subprocess CLI，
包含不同 coin calendars、同日隔離、future outcome 不得影響 signal、
malformed/small dataset fail-closed，以及 report lineage/counts。

## 測試現況

`tests/test_w4_conformal.py`（8 個測試，只驗研究工件本身，不牽動
production 行為）：
1. `test_conformal_abstain_threshold_is_deterministic`
2. `test_compute_tau_matches_manual_order_statistic`
3. `test_compute_tau_empty_wrong_set_is_conservative`
4. `test_compute_tau_deterministic_same_input_same_output`
5. `test_backtest_holdout_joint_coverage_within_alpha_plus_slack`（重跑
   真實資料驗證 JOINT coverage（嚴格 `>`），並比對硬編常數與最新回測是否
   同量級）
6. `test_all_wrong_strengths_at_max_value_does_not_leak_at_boundary`
7. `test_tied_scores_at_order_statistic_boundary_do_not_leak`
8. `test_empty_calibration_set_forces_abstain_even_at_max_legal_strength`

第 6-8 項是本輪 codex 對抗審發現「fallback=1.0 + `>=`」邊界 bug 後新增
的反例測試，見上方「邊界語義修正」章節。

既有測試套件（含 `tests/test_w4_calibration.py`）因 production 行為
revert 回原狀，理論上應全綠——實測結果見 PR 說明。
