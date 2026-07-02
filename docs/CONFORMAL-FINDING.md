# W4 Split Conformal Prediction — 研究發現與 roadmap

> master 計劃 Axis B #1（gray 細案，CEO 已審）。
> 狀態：**單階段 conformal 數學實作完成，但本輪不 wire 進 production**。
> 決策日期：2026-07（CEO）。

---

## 一句話結論

用 gray 細案指定的方法論（同一條 HOYA OHLCV 價格序列衍生出多個技術訊號，
餵給既有 `_evidence_strength()`）算出的 conformal τ 在數學上是正確、可
重現的 split conformal prediction，JOINT coverage 保證確實達標
（P(方向錯 且 evidence_strength≥τ) = 0.0400 ≤ α=0.10）。但這個代理訊號
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
held-out JOINT coverage: P(wrong AND strength>=tau) = 0.0400 (target <= alpha=0.1) OK
held-out CONDITIONAL（參考用、非本輪保證對象）: P(wrong | strength>=tau) = 0.6712
held-out abstain rate at tau: 0.9405
```

診斷過程（非猜測，實測驗證）：

- **JOINT coverage 達標**（0.0400 ≤ 0.1）——這是 gray 細案唯一要求的數學
  保證，確實成立。
- 但 **CONDITIONAL** P(錯 | strength≥τ) = 0.6712，遠高於 α。這代表就算
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

## 測試現況

`tests/test_w4_conformal.py`（4 個測試，只驗研究工件本身，不牽動
production 行為）：
1. `test_conformal_abstain_threshold_is_deterministic`
2. `test_compute_tau_matches_manual_order_statistic`
3. `test_compute_tau_empty_wrong_set_is_conservative`
4. `test_compute_tau_deterministic_same_input_same_output`
5. `test_backtest_holdout_joint_coverage_within_alpha_plus_slack`（重跑
   真實資料驗證 JOINT coverage，並比對硬編常數與最新回測是否同量級）

既有測試套件（含 `tests/test_w4_calibration.py`）因 production 行為
revert 回原狀，理論上應全綠——實測結果見 PR 說明。
