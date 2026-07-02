"""W4：Split Conformal Prediction abstain 門檻——**研究工件（master 計劃
Axis B #1，2026-07 CEO 決策：本輪不 wire 進 production）**。

⚠️ 本模組目前**不被** `agent.orchestrator` 呼叫。production 的三態
abstain 門檻已改回 `trust.scoring._CALIBRATION_TABLE` 那套簡化分位數
校準（`agent.orchestrator._ABSTAIN_CALIBRATED_THRESHOLD = 0.35`）。原因
與完整記錄見 `docs/CONFORMAL-FINDING.md`：gray 細案指定的「同一條 OHLCV
衍生多技術訊號」不是真異質多來源代理，對方向判斷幾乎無判別力
（pseudo-AUC≈0.49），套用此模組算出的 τ 會讓 held-out abstain 率衝到
~94%（見下方回測結果），等同廢掉功能。本模組保留作為**已完成、可重現
的研究工件**：單階段 split conformal 的數學實作與 coverage 計算本身
正確且經測試驗證（見 `tests/test_w4_conformal.py`），未來若拿到真正
異質的歷史多來源資料（news/onchain/social 等；目前連接器只 cache
現值、無歷史序列），可重跑 `scripts/backtest_conformal.py` 重新評估。

以下取代對象/意義說明沿用原設計文字（僅供理解本模組意圖，**取代尚未
實際發生**）：取代 `trust.scoring._CALIBRATION_TABLE` 那套「工程判斷、
非統計估計」的簡化分位數表（見該檔上方誠實聲明）。這裡的 τ 是用真實
HOYA BIT 歷史 OHLCV
資料（`data/data/*.csv`，BTC/ETH/SOL/BNB/XRP，2021-06-01~2026-05-31）跑
`scripts/backtest_conformal.py` 離線回測出來的，**硬編成常數**（比照
`_CALIBRATION_TABLE` 的模式：寫死在程式碼、可版控可審，不是每次啟動重跑，
也不是訓練出來的黑箱模型——純粹是「錯誤樣本 evidence_strength 的一個
分位數順序統計量」，沒有任何自由參數需要學習）。

----------------------------------------------------------------------------
τ 的意義與 distribution-free 保證（single-stage split conformal，α=0.1）
----------------------------------------------------------------------------
`τ` 是「錯誤方向判斷（判斷方向 ≠ 往後 3 個交易日實際方向）」的
`trust.scoring._evidence_strength()` 分數，取校準集裡這些錯誤樣本由小到大
排序後第 ⌈(n+1)(1-α)⌉ 大的順序統計量（名次超出樣本數或校準集無錯誤樣本
時，τ=`math.inf`）。若 held-out test 與校準集對「錯誤樣本的
evidence_strength」這個量可交換（exchangeable），則有 distribution-free
的**聯合機率**保證——⚠️ 保證是對**嚴格不等式**成立的：

    P(方向判斷錯誤 且 evidence_strength > τ) ≤ α

也就是 `evidence_strength > τ`（嚴格大於，不是 `≥`——見下方誠實聲明的
邊界修正說明）时，「同時是錯的」這件事發生的機率有嚴謹上界，不再是
`_CALIBRATION_TABLE` 那種工程判斷的武斷門檻。

----------------------------------------------------------------------------
⚠️ 誠實聲明（不可省略，任何引用此模組的地方都要保留）
----------------------------------------------------------------------------
- **單階段 conformal**：只對「evidence_strength → 方向判斷是否錯誤」這單一
  環節做校準。真正的 pipeline-aware joint coverage（claim 抽取、跨源訊號、
  narrative 忠實度…全部環節的聯合覆蓋保證）**明列 roadmap，本輪不做**。
- **歷史回測校準，非線上未來保證**：coverage 保證只在「校準集與未來資料
  對此指標可交換」的假設下成立；加密市場 regime 會變，這不是「上線後
  永遠 ≤ α」的保證，只是「歷史這段資料上，這套校準程序在數學上該有的
  行為」。
- **N=3 交易日、α=0.1 是主觀選擇**，未做敏感度掃描。
- **技術訊號是價格代理，不是真實多來源**：回測用的「多個獨立技術訊號」
  （動量週期／成交量趨勢／波動率）全部衍生自同一條 OHLCV 價格序列，是
  `_evidence_strength()` 所需「多來源」輸入的簡化代理，不是 news/social/
  onchain/regulatory 那種真正異質的多來源。
- **回測實測發現（誠實揭露、非隱藏）**：此代理訊號集合對「3 個交易日後
  方向是否正確」幾乎沒有判別力（held-out 上 P(錯|strength>τ) 遠高於
  α——joint 機率保證仍成立，但那是因為 τ 訂得很嚴、幾乎總是 abstain，
  不是訊號本身準。實際 held-out abstain 率見 `scripts/backtest_conformal.py`
  輸出與 PR 說明；這代表用真實 pipeline 的異質多來源證據（而非單一價格
  序列代理）重跑這套回測，τ 與 abstain 率很可能截然不同——本常數僅是
  這次特定代理訊號集合、這段歷史資料下的校準結果。
- **邊界語義修正（codex 對抗審發現，已修）**：初版 `compute_tau()` 在
  「校準集無錯誤樣本／樣本不足」時 fallback 回傳 `1.0`，且判斷「是否
  通過門檻」全用 `>=`。但 `evidence_strength` 值域上界含 1.0，這個組合
  會讓 fallback 場景（理論上該一律 abstain）被剛好等於 1.0 的樣本鑽
  漏洞算「通過」，且標準 split conformal 順序統計量的有限樣本保證本來
  就是對嚴格 `>` 成立、不是 `>=`。已訂正為 fallback=`math.inf` ＋ 全面
  改用嚴格 `>`（見 `scripts/backtest_conformal.py::compute_tau()` 與
  `main()`），並補上反例測試（全 1.0 錯誤樣本、ties、空校準集）鎖住
  這個邊界。**下方回測數字是用修正後的嚴格 `>` 重跑驗證過的**——這次
  資料集裡沒有測試樣本 evidence_strength 恰好等於 τ 的邊界情形，所以
  修正前後數字本身沒變，但修正是必要的（保證原本的數學宣稱要對，不能
  只是巧合沒踩到邊界）。

回測參數與結果（`scripts/backtest_conformal.py`，可重現，同資料同輸出）：
    date range   : 2021-06-01 ~ 2026-05-31（BTC/ETH/SOL/BNB/XRP）
    split        : 前 70% train（未使用，無自由參數可訓練）/
                   中 15% calib / 後 15% held-out test（依日期索引，
                   5 幣共用同一切點，防跨幣同日相關性洩漏）
    alpha        : 0.10
    forward_days : 3（判對錯的往後看窗口）
    calib samples: 1249（其中 wrong=649）
    tau (raw)    : 0.9153692142145569（硬編時無條件進位到 0.9154，
                   保守方向——調高 τ 只會讓 P(wrong AND strength>τ) 更小，
                   不會破壞上面的聯合機率保證）
    held-out test: n=1226, pass(不 abstain)=73, confidently-wrong=49
                   （用嚴格 `>` 計得，見上方「邊界語義修正」）
                   JOINT P(wrong AND strength>tau) = 0.0400 (<= alpha=0.10，達標)
                   CONDITIONAL P(wrong | strength>tau) = 0.6712（參考用，
                   本輪程序不保證此量；數字偏高原因見上方「回測實測發現」）
                   abstain rate = 0.9405（見 PR 說明 / CEO 判讀是否要調整
                   α、N 或訊號設計）
"""
from __future__ import annotations

# 硬編 τ：見上方模組 docstring「回測參數與結果」。無條件進位到 4 位小數
# （保守方向，不縮小 τ）。
_CONFORMAL_TAU = 0.9154


def conformal_abstain_threshold() -> float:
    """回傳硬編的 conformal abstain 門檻 τ（研究工件，⚠️ 目前未被
    `agent.orchestrator` 呼叫，見本模組上方 2026-07 CEO 決策說明與
    `docs/CONFORMAL-FINDING.md`）。

    確定性、免 LLM、零 credit：純常數查詢，同輸入（無輸入）必同輸出。
    比較對象設計上是 `trust.scoring._evidence_strength()` 的**原始值**
    （不是 `_calibrate_confidence()` 校準過的顯示值），供未來若重新評估
    wire 進 production 時參考此設計決定。
    """
    return _CONFORMAL_TAU
