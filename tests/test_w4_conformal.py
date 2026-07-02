"""W4：Split Conformal Prediction abstain 門檻——驗收測試。

master 計劃 Axis B #1，取代 `trust.scoring._CALIBRATION_TABLE` 那套簡化
分位數表。涵蓋範圍（見 PR 說明 / `trust/conformal.py` 與
`scripts/backtest_conformal.py` 模組上方誠實聲明，本檔不重複貼）：
  1. `conformal_abstain_threshold()` 確定性。
  2. `backtest_conformal.compute_tau()` 順序統計量公式本身正確性（純數學，
     跟真實資料無關，用手算小範例驗證）。
  3. **coverage 性質測試**：用真實 `data/data/*.csv` 重跑一次 held-out
     test 集的 JOINT coverage 檢查（P(方向錯 且 strength≥τ) ≤ α+餘裕）——
     跟 `trust/conformal.py` 硬編 τ 時附的回測數字互相印證，資料/規則
     變動時這個測試能抓到「硬編常數過時」。
  4. **三態回歸鎖**：`agent.orchestrator` 的 abstain/low_confidence/normal
     三態骨架機制本身（不是某個特定門檻數值）在真實（當前硬編）τ 下仍
     正確運作——用手造 `TrustedBrief`（直接指定 `evidence_strength`／
     `calibrated_confidence`），鎖住三態邊界行為，不管 τ 未來因重新回測
     而調整多少，只要機制正確這組測試就該一直通過。

⚠️ 不在本檔測試範圍內、需要人工判讀的重大發現：用本輪 gray 細案指定的
「OHLCV 技術訊號代理多來源」方法論算出的真實 τ（見 `trust/conformal.py`）
遠比舊版簡化門檻嚴格，套用到既有測試資料的實際效果是 abstain 率大幅
上升（held-out backtest ~94%，見 PR 說明的 alpha 敏感度掃描與 pseudo-AUC
分析）——這是本次誠實回測的真實結果，不是本檔測試要掩蓋或迴避的對象。
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

from trustforge.agent.orchestrator import (
    _ABSTAIN_CALIBRATED_THRESHOLD,
    _ABSTAIN_MIN_SUPPORTING,
    build_report,
)
from trustforge.bedrock import BedrockClient
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import Document
from trustforge.schema import QuestionType
from trustforge.trust.conformal import conformal_abstain_threshold
from trustforge.trust.scoring import Claim, ScoredClaim, TrustedBrief

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


def test_orchestrator_reads_threshold_from_conformal_module():
    """orchestrator 的 `_ABSTAIN_CALIBRATED_THRESHOLD` 必須直接來自
    `trust.conformal.conformal_abstain_threshold()`，不是另外寫死的數字
    （回歸鎖：防止之後有人不小心把它改回手動硬編）。"""
    assert _ABSTAIN_CALIBRATED_THRESHOLD == conformal_abstain_threshold()


# ---------------------------------------------------------------------------
# 2. compute_tau() 順序統計量公式（純數學，跟真實資料無關）
# ---------------------------------------------------------------------------
def test_compute_tau_matches_manual_order_statistic():
    scores = [0.1, 0.5, 0.9, 0.3, 0.7]  # n=5
    # alpha=0.5 -> k = ceil(6*0.5) = 3 -> 由小到大排序第 3 大 = 0.5
    assert backtest_conformal.compute_tau(scores, alpha=0.5) == 0.5
    # alpha=0.1 -> k = ceil(6*0.9) = 6 > n=5 -> 樣本數不足以保證，保守回傳 1.0
    assert backtest_conformal.compute_tau(scores, alpha=0.1) == 1.0


def test_compute_tau_empty_wrong_set_is_conservative():
    assert backtest_conformal.compute_tau([], alpha=0.1) == 1.0


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
    JOINT coverage：P(方向錯 且 evidence_strength>=tau) <= alpha + 餘裕。

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
        1 for s in test_samples if s.wrong and s.evidence_strength >= tau
    )
    joint_wrong_rate = n_confidently_wrong / n_test

    assert joint_wrong_rate <= backtest_conformal.ALPHA + 0.03, (
        f"held-out JOINT coverage 違反：P(wrong AND strength>=tau)="
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
# 4. 三態回歸鎖（機制本身，不綁定特定門檻數值）
# ---------------------------------------------------------------------------
def _signal_claim(idx: int, kind: str, text: str, trust: float) -> ScoredClaim:
    doc = Document(id=f"lock-{idx}", kind=kind, source=f"src-{idx}", text=text, ts=0.0)
    claim = Claim(id=doc.id, text=text, doc=doc, claim_type="fact")
    return ScoredClaim(claim=claim, trust=trust)


def _supporting(n_sources: int, text: str = "BTC 上漲。") -> list[ScoredClaim]:
    return [_signal_claim(i, "price", text, 0.8) for i in range(n_sources)]


def _brief(evidence_strength: float, calibrated_confidence: float, n_sources: int = 3) -> TrustedBrief:
    return TrustedBrief(
        query="分析 BTC",
        supporting=_supporting(n_sources),
        contrarian=[],
        confidence=0.8,
        calibrated_confidence=calibrated_confidence,
        evidence_strength=evidence_strength,
    )


def _run(brief: TrustedBrief):
    return build_report(
        query="分析 BTC", coin="BTC", qtype=QuestionType.MULTI_SOURCE, brief=brief,
        client=BedrockClient(offline=True),
        log=ExecutionLog(now_fn=lambda: 1_000_000.0),
        now_fn=lambda: 1_000_000.0,
    )


def test_abstain_when_evidence_strength_just_below_tau():
    tau = _ABSTAIN_CALIBRATED_THRESHOLD
    brief = _brief(evidence_strength=max(0.0, tau - 0.01), calibrated_confidence=0.95)
    report, _ = _run(brief)
    assert report.decision_state == "abstain"
    assert report.direction == "不明"


def test_normal_when_evidence_strength_at_tau_and_calibrated_high():
    tau = _ABSTAIN_CALIBRATED_THRESHOLD
    brief = _brief(evidence_strength=min(1.0, tau + 0.001), calibrated_confidence=0.9)
    report, _ = _run(brief)
    assert report.decision_state == "normal"
    assert report.direction == "偏多"


def test_low_confidence_when_pass_tau_but_calibrated_below_half():
    tau = _ABSTAIN_CALIBRATED_THRESHOLD
    brief = _brief(evidence_strength=min(1.0, tau + 0.001), calibrated_confidence=0.45)
    report, _ = _run(brief)
    assert report.decision_state == "low_confidence"
    assert report.direction != "不明"  # 低信心仍出結論，只是標註


def test_abstain_when_min_supporting_not_met_even_with_max_strength():
    """`_ABSTAIN_MIN_SUPPORTING`（獨立來源數門檻）不因 W4 conformal 改動——
    即使 evidence_strength/calibrated 都拉滿，來源數不足一樣 abstain。"""
    assert _ABSTAIN_MIN_SUPPORTING == 2
    brief = _brief(evidence_strength=1.0, calibrated_confidence=1.0, n_sources=1)
    report, _ = _run(brief)
    assert report.decision_state == "abstain"
