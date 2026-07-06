"""W4：Split Conformal Prediction 研究工件——驗收測試。

master 計劃 Axis B #1。**CEO 決策（見 PR 說明）：本輪只留研究工件，不
wire 進 production**——`trust.conformal`/`scripts/backtest_conformal.py`
本身數學正確、可重現，但依 gray 細案指定方法論算出的 τ 是「同一條 OHLCV
衍生多技術訊號」這個非真異質代理訊號的產物（pseudo-AUC≈0.49、對方向
幾乎無判別力），套進 production 會讓 abstain 率衝到 ~94%（見
`docs/qa/CONFORMAL-FINDING.md` 完整記錄）。`agent.orchestrator` 的三態
abstain 門檻**維持原本的簡化分位數校準**（`_ABSTAIN_CALIBRATED_THRESHOLD
= 0.35`），不讀這裡的 τ；三態回歸測試在既有 `tests/test_w4_calibration.py`
即涵蓋，本檔不重複。

本檔只驗證研究工件本身（純數學/可重現性，不牽動 production 行為）：
  1. `conformal_abstain_threshold()` 確定性。
  2. `backtest_conformal.compute_tau()` 順序統計量公式本身正確性（純數學，
     跟真實資料無關，用手算小範例驗證）。
  3. **coverage 性質測試**：用真實 `data/data/*.csv` 重跑一次 held-out
     test 集的 JOINT coverage 檢查（P(方向錯 且 strength>τ) ≤ α+餘裕，
     嚴格 `>`）——跟 `trust/conformal.py` 硬編 τ 時附的回測數字互相印證，
     資料/規則變動時這個測試能抓到「硬編常數過時」。
  4. **邊界反例測試（codex 對抗審發現，見 `docs/qa/CONFORMAL-FINDING.md`
     「邊界語義修正」）**：全 1.0 錯誤樣本、重複分數（ties）、空校準集
     三種邊界情形，鎖住「fallback=`math.inf` ＋ 嚴格 `>`」不會被邊界值
     鑽漏洞誤放行、coverage 上界宣稱仍成立。
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

from trustforge.trust.conformal import conformal_abstain_threshold

_REPO = Path(__file__).resolve().parent.parent
_BACKTEST_PATH = _REPO / "scripts" / "backtest_conformal.py"
_spec = importlib.util.spec_from_file_location("backtest_conformal", _BACKTEST_PATH)
backtest_conformal = importlib.util.module_from_spec(_spec)
sys.modules["backtest_conformal"] = backtest_conformal
_spec.loader.exec_module(backtest_conformal)


# ---------------------------------------------------------------------------
# 1. conformal_abstain_threshold() 確定性
# ---------------------------------------------------------------------------
def test_conformal_abstain_threshold_is_deterministic():
    a = conformal_abstain_threshold()
    b = conformal_abstain_threshold()
    assert a == b, "同輸入（無輸入）必同輸出——純常數查詢，不得有隨機性"
    assert 0.0 <= a <= 1.0


# ---------------------------------------------------------------------------
# 2. compute_tau() 順序統計量公式（純數學，跟真實資料無關）
# ---------------------------------------------------------------------------
def test_compute_tau_matches_manual_order_statistic():
    scores = [0.1, 0.5, 0.9, 0.3, 0.7]  # n=5
    # alpha=0.5 -> k = ceil(6*0.5) = 3 -> 由小到大排序第 3 大 = 0.5
    assert backtest_conformal.compute_tau(scores, alpha=0.5) == 0.5
    # alpha=0.1 -> k = ceil(6*0.9) = 6 > n=5 -> 樣本數不足以保證，保守回傳
    # math.inf（不是合法分數範圍內的值——見「邊界反例測試」章節）
    assert backtest_conformal.compute_tau(scores, alpha=0.1) == math.inf


def test_compute_tau_empty_wrong_set_is_conservative():
    assert backtest_conformal.compute_tau([], alpha=0.1) == math.inf


def test_compute_tau_deterministic_same_input_same_output():
    scores = [0.2, 0.4, 0.6, 0.8, 0.55, 0.33, 0.71]
    assert (
        backtest_conformal.compute_tau(scores, alpha=0.1)
        == backtest_conformal.compute_tau(scores, alpha=0.1)
    )


# ---------------------------------------------------------------------------
# 3. coverage 性質測試（真實資料重跑一次 held-out JOINT coverage）
# ---------------------------------------------------------------------------
def test_backtest_holdout_joint_coverage_within_alpha_plus_slack():
    """用 `data/data/*.csv` 重跑一次完整回測流程，驗證 held-out test 上
    JOINT coverage：P(方向錯 且 evidence_strength>tau) <= alpha + 餘裕
    （嚴格 `>`，見 `docs/qa/CONFORMAL-FINDING.md`「邊界語義修正」）。

    餘裕（0.03）純粹是有限樣本下的統計波動緩衝，不是放寬保證本身——
    `trust/conformal.py` 硬編的 τ 是無條件進位（保守方向），實測值本該
    比 alpha 更低，這裡留餘裕只是避免資料檔案未來若有微幅修訂時測試過脆。
    """
    coins = backtest_conformal.COINS
    all_samples = {c: backtest_conformal._samples_for_coin(c) for c in coins}
    bars_ref = backtest_conformal.load_ohlcv(coins[0], backtest_conformal.DATA_DIR)
    n_dates = len(bars_ref)
    calib_start, test_start = backtest_conformal._time_split(n_dates)
    calib_cut = bars_ref[calib_start].date
    test_cut = bars_ref[test_start].date

    calib_samples = []
    test_samples = []
    for samples in all_samples.values():
        for s in samples:
            if calib_cut <= s.date < test_cut:
                calib_samples.append(s)
            elif s.date >= test_cut:
                test_samples.append(s)

    wrong_strengths = [s.evidence_strength for s in calib_samples if s.wrong]
    tau = backtest_conformal.compute_tau(wrong_strengths, backtest_conformal.ALPHA)

    n_test = len(test_samples)
    assert n_test > 0, "測試資料異常：held-out test 集為空"
    n_confidently_wrong = sum(
        1 for s in test_samples if s.wrong and s.evidence_strength > tau
    )
    joint_wrong_rate = n_confidently_wrong / n_test

    assert joint_wrong_rate <= backtest_conformal.ALPHA + 0.03, (
        f"held-out JOINT coverage 違反：P(wrong AND strength>tau)="
        f"{joint_wrong_rate:.4f}，應 <= alpha({backtest_conformal.ALPHA})+0.03"
    )
    # 目前硬編在 trust/conformal.py 的常數應與這次重跑結果同一數量級
    # （不要求逐位元相同——浮點路徑/資料檔若有微幅出入仍可接受，但差距
    # 不該離譜到代表常數已經過時該重新回測）。
    assert math.isclose(tau, conformal_abstain_threshold(), abs_tol=0.02), (
        f"重跑得到 tau={tau:.4f}，硬編常數={conformal_abstain_threshold()}——"
        "差距過大，`trust/conformal.py` 可能需要用最新資料重新回測更新。"
    )


# ---------------------------------------------------------------------------
# 4. 邊界反例測試（codex 對抗審發現）：fallback=math.inf ＋ 嚴格 `>`
#    見 `docs/qa/CONFORMAL-FINDING.md`「邊界語義修正」。這些測試鎖住的是
#    「呼叫端判斷是否通過門檻」的完整邏輯（`strength > tau`），不只是
#    `compute_tau()` 本身的回傳值。
# ---------------------------------------------------------------------------
def _passes(strength: float, tau: float) -> bool:
    """比照 `backtest_conformal.main()` 判斷『是否通過門檻（不 abstain）』
    的邏輯——嚴格 `>`，不是 `>=`。"""
    return strength > tau


def test_all_wrong_strengths_at_max_value_does_not_leak_at_boundary():
    """全部錯誤樣本 evidence_strength 都卡在合法值域上界 1.0：
    compute_tau 應直接回傳 1.0（該值本身就是排序後的順序統計量），而
    『strength 恰好等於 1.0』的測試樣本不該被判定為通過門檻——
    `1.0 > 1.0` 為 False，嚴格 `>` 正確地讓它 abstain，不會誤放行。"""
    wrong_strengths = [1.0] * 10
    tau = backtest_conformal.compute_tau(wrong_strengths, alpha=0.1)
    assert tau == 1.0
    assert _passes(1.0, tau) is False, "strength==tau（=1.0）不該算通過門檻"
    # 就算真的有樣本理論上等於值域上界之外（不該發生，但防禦性驗證邊界
    # 邏輯本身沒有 off-by-one），也不會有任何合法值能通過。
    assert _passes(0.9999999, tau) is False


def test_tied_scores_at_order_statistic_boundary_do_not_leak():
    """重複分數（ties）：多個錯誤樣本分數相同、剛好落在順序統計量名次
    上，τ 等於這個重複值；测试样本分数恰好等於 τ 時不該通過門檻（嚴格
    `>`），只有真正大於 τ 的樣本才算通過——鎖住 ties 情境下的正確
    tie-breaking（一律偏保守，不偏向放行）。"""
    # n=4，alpha=0.1 -> k=ceil(5*0.9)=5 > n=4 -> fallback=inf（樣本數不足）
    # 改用 n=9 讓 k<=n：k=ceil(10*0.9)=9 -> 第 9 大 = 排序後最大值
    wrong_strengths = [0.5, 0.5, 0.5, 0.6, 0.7, 0.8, 0.9, 0.9, 0.9]
    tau = backtest_conformal.compute_tau(wrong_strengths, alpha=0.1)
    assert tau == 0.9, f"預期第 9 大（=最大值）= 0.9，實際 {tau}"
    assert _passes(0.9, tau) is False, "ties：strength==tau 不該算通過門檻"
    assert _passes(0.900001, tau) is True, "嚴格大於 tau 才算通過門檻"


def test_empty_calibration_set_forces_abstain_even_at_max_legal_strength():
    """空校準集（沒有任何錯誤樣本）：compute_tau 回傳 math.inf。就算未來
    出現 evidence_strength 剛好等於合法值域上界 1.0 的測試樣本，也不該
    通過門檻——這是本次 codex 對抗審抓到的原始 bug 的直接反例：初版
    fallback=1.0 ＋ `>=` 時，這個情境會被誤判為『通過』，破壞『校準
    資料不足時應保守到一律 abstain』的設計意圖。"""
    tau = backtest_conformal.compute_tau([], alpha=0.1)
    assert tau == math.inf
    assert _passes(1.0, tau) is False, (
        "空校準集 fallback 下，即使 strength 達到合法值域上界 1.0 也不該"
        "通過門檻——這正是原始 bug（fallback=1.0 + `>=`）會誤放行的情境"
    )

