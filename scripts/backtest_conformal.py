#!/usr/bin/env python3
"""W4：Split Conformal Prediction 一次性離線回測（master 計劃 Axis B #1）。

⚠️ 2026-07 CEO 決策：本腳本產出的 τ **未 wire 進 production**（見
`trust/conformal.py` 模組上方說明與 `docs/CONFORMAL-FINDING.md`）——
gray 細案指定的「同一條 OHLCV 衍生多技術訊號」代理對方向判斷幾乎無
判別力，套用會讓 abstain 率衝到 ~94%。本腳本作為已完成、可重現的研究
工件保留。

用法：
    python3 scripts/backtest_conformal.py

目的：用 `data/data/*.csv`（HOYA BIT 官方 5 幣 OHLCV，2021-06-01~2026-05-31）
回測出一個有 **distribution-free coverage 保證**的 conformal abstain 門檻 τ，
取代 `trust.scoring._CALIBRATION_TABLE` 那套「工程判斷、非統計估計」的簡化分位數
表（見該檔上方誠實聲明）。純確定性、不呼叫任何 LLM/Bedrock，零 credit。

----------------------------------------------------------------------------
方法（single-stage split conformal，α=0.1）
----------------------------------------------------------------------------
1. 對每個 (coin, date)：
   - **判斷方向**：沿用 `prices.py::price_facts()` 的規則（14 日窗口報酬
     ret>+1% → 上漲、ret<-1% → 下跌、其餘 盤整）。盤整（無明確方向）樣本
     不計入回測——系統本來就不會對盤整下方向性結論，沒有「對/錯」可言。
   - **多個獨立技術訊號**（誠實聲明：這些訊號全部衍生自同一條 OHLCV 價格
     序列，是「多來源佐證」的**代理**，不是真的多個獨立資料源；見下方
     `_build_signals()` docstring）：
       * 動量：3/7/21/30 日報酬方向（4 個訊號，跟主判斷窗口 14 日不同期）
       * 成交量趨勢：主判斷窗口內近 3 日均量 vs 前 3 日均量（同 `price_facts`
         的 vol_trend 算法）——量增被視為方向確認、量縮視為動能減弱
       * 波動率：近 30 日日報酬標準差 vs 前 30 日——波動率下降視為方向確認
         （趨勢更可信）、波動率上升視为雜訊增加
     每個訊號依「是否同意主判斷方向」歸類進 supporting／contrarian，餵給
     既有 `trust.scoring._evidence_strength()`（原封不動複用，不重寫評分
     邏輯）算出 evidence_strength ∈ [0, 1]。
   - **label**：往後看 N=3 個交易日的實際漲跌方向（收盤價正負號）跟主判斷
     方向比對，一致＝對，不一致＝錯。
2. **時間切分（非隨機，5 幣共用同一組日期索引切點，防跨幣同日相關性洩漏）**：
   全部日期依索引切成前 70%（train，本方法無可訓練自由參數，此段保留供
   慣例對齊／未來若改用可調參模型時使用，本輪不使用）／中 15%（校準集）／
   後 15%（held-out test）。
3. **nonconformity score = 校準集中 label=錯 的樣本的 evidence_strength**。
   τ = 這些「錯誤樣本」evidence_strength 由小到大排序後，第
   ⌈(n+1)(1-α)⌉ 大的順序統計量（α=0.1，標準 split conformal 有限樣本
   修正公式）。直覺：τ 訂在「就算是錯的判斷，也很少能把 evidence_strength
   衝到這麼高」的門檻之上——evidence_strength ≥ τ 時，錯誤同時發生的機率
   有 distribution-free 保證上界 α（前提：校準集與 held-out test 對此指標
   可交換／同分布，且歷史≈未來——見下方誠實聲明）。
4. **held-out test 驗證**：在 test 集上實測
   P(方向錯 且 evidence_strength ≥ τ) 是否 ≤ α，印出 τ、n_calib_wrong、
   coverage 實測值供人工核對。

----------------------------------------------------------------------------
⚠️ 誠實聲明（不可省略，PR 說明務必附上）
----------------------------------------------------------------------------
- **單階段 conformal**：本輪只對 `evidence_strength → 是否方向錯誤` 這單一
  判斷做 split conformal 校準。真正的 pipeline-aware joint coverage（同時
  涵蓋 claim 抽取、跨源訊號、narrative 忠實度等下游環節的聯合覆蓋保證）
  **明列 roadmap，本輪不做**。
- **假設歷史≈未來**：coverage 保證只在校準集與未來資料同分布（exchangeable）
  的假設下成立。加密市場 regime 會變，這是「歷史回測校準」而非「線上未來
  保證」。
- **N=3 交易日視窗是主觀選擇**：換一個 N 會產生不同的 label 分布與不同的
  τ，本輪未對 N 做敏感度掃描。
- **技術訊號是價格代理，不是多來源**：4 個動量週期＋成交量趨勢＋波動率，
  全部衍生自同一條 OHLCV 序列；用它們模擬 `_evidence_strength()` 所需的
  「多來源」輸入，是本次回測方法論的簡化，不代表真實 pipeline 的多來源
  異質性（news/social/onchain/regulatory）。
- 本腳本產出的 τ 之後**手動**抄進 `trust/conformal.py`（連同回測日期範圍/
  α/coverage 一併寫成註解，可版控可審——比照 `_CALIBRATION_TABLE` 的模式），
  不是每次啟動自動重跑；資料/規則變動時需重新執行本腳本並更新常數。
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trustforge.ingestion.base import Document  # noqa: E402
from trustforge.ingestion.prices import Bar, _pct, _volatility, load_ohlcv  # noqa: E402
from trustforge.trust.scoring import Claim, ScoredClaim, _evidence_strength  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "data"
COINS = ["BTC", "ETH", "SOL", "BNB", "XRP"]

ALPHA = 0.10
FORWARD_DAYS = 3          # N：往後看幾個交易日判對錯
PRIMARY_WINDOW = 14       # 主判斷窗口，跟 price_facts() 預設一致
MOMENTUM_WINDOWS = (3, 7, 21, 30)
VOL_STABILITY_WINDOW = 30  # 波動率訊號前後各看幾天

TRAIN_FRAC = 0.70
CALIB_FRAC = 0.15  # 剩下 0.15 是 held-out test


@dataclass
class Sample:
    coin: str
    date: str
    evidence_strength: float
    wrong: bool  # True：主判斷方向與 N 日後實際方向不符


def _direction_from_ret(ret: float) -> str:
    """跟 `price_facts()` 完全一致的三態方向規則（ret 為百分比）。"""
    if ret > 1:
        return "up"
    if ret < -1:
        return "down"
    return "flat"


def _clamp_trust(magnitude: float, lo: float = 0.5, hi: float = 0.95) -> float:
    return max(lo, min(hi, magnitude))


def _make_signal_claim(coin: str, source: str, kind: str, ts_tag: str) -> Claim:
    doc = Document(
        id=f"backtest-{coin}-{source}-{ts_tag}", kind=kind, source=source,
        text=f"{coin} {source} 技術訊號（回測合成，非真實文本）", url="", ts=0.0,
        meta={"backtest": True},
    )
    return Claim(id=doc.id, text=doc.text, doc=doc, claim_type="inference")


def _build_signals(coin: str, bars: list[Bar], idx: int, primary_dir: str, ts_tag: str) \
        -> tuple[list[ScoredClaim], list[ScoredClaim]]:
    """算 idx（含）為止的多個技術訊號，依是否同意 `primary_dir` 分成
    supporting / contrarian（純技術訊號代理多來源，見模組上方誠實聲明）。

    純函式、確定性：只用 `bars[:idx+1]`（不看未來資料），同輸入必同輸出。
    """
    supporting: list[ScoredClaim] = []
    contrarian: list[ScoredClaim] = []

    # 1) 動量：多個週期的報酬方向，跟主判斷窗口（14 日）不同期
    for p in MOMENTUM_WINDOWS:
        if idx - p + 1 < 0:
            continue
        window = bars[idx - p + 1: idx + 1]
        ret_p = _pct(window[0].close, window[-1].close)
        d = "up" if ret_p > 0 else ("down" if ret_p < 0 else "flat")
        if d == "flat":
            continue
        trust = _clamp_trust(0.5 + abs(ret_p) / 20.0)
        claim = _make_signal_claim(coin, f"mom{p}d", "price", ts_tag)
        sc = ScoredClaim(claim=claim, trust=trust)
        (supporting if d == primary_dir else contrarian).append(sc)

    # 2) 成交量趨勢（同 price_facts 的 recent3 vs first3 算法，主判斷窗口內）
    seg = bars[max(0, idx - PRIMARY_WINDOW + 1): idx + 1]
    if len(seg) >= 6:
        vol_recent = sum(b.volume for b in seg[-3:]) / 3.0
        vol_earlier = sum(b.volume for b in seg[:3]) / 3.0
        vol_trend = _pct(vol_earlier, vol_recent)
        if abs(vol_trend) > 1e-9:
            trust = _clamp_trust(0.5 + min(abs(vol_trend), 40.0) / 80.0, hi=0.9)
            claim = _make_signal_claim(coin, "voltrend", "price_volume", ts_tag)
            sc = ScoredClaim(claim=claim, trust=trust)
            # 量增＝確認（同意主方向）；量縮＝動能減弱（反方）
            (supporting if vol_trend > 0 else contrarian).append(sc)

    # 3) 波動率穩定度：近 30 日 vs 前 30 日日報酬標準差
    if idx - 2 * VOL_STABILITY_WINDOW + 1 >= 0:
        now_win = bars[idx - VOL_STABILITY_WINDOW + 1: idx + 1]
        prev_win = bars[idx - 2 * VOL_STABILITY_WINDOW + 1: idx - VOL_STABILITY_WINDOW + 1]
        vol_now = _volatility([b.close for b in now_win])
        vol_prev = _volatility([b.close for b in prev_win])
        if vol_prev > 1e-9 or vol_now > 1e-9:
            trust = _clamp_trust(0.5 + min(abs(vol_prev - vol_now), 20.0) / 40.0, hi=0.9)
            claim = _make_signal_claim(coin, "volstability", "price_volatility", ts_tag)
            sc = ScoredClaim(claim=claim, trust=trust)
            # 波動率下降＝更可信（同意主方向）；波動率上升＝雜訊增加（反方）
            (supporting if vol_now <= vol_prev else contrarian).append(sc)

    return supporting, contrarian


def _samples_for_coin(coin: str) -> list[Sample]:
    bars = load_ohlcv(coin, DATA_DIR)
    if not bars:
        return []
    samples: list[Sample] = []
    last_idx = len(bars) - 1
    # 需要：primary window(14) 的歷史、波動率訊號需要 2*30=60 天歷史、
    # 動量最長週期 30 天歷史、以及往後 FORWARD_DAYS 天的實際結果。
    min_idx = max(PRIMARY_WINDOW - 1, max(MOMENTUM_WINDOWS) - 1, 2 * VOL_STABILITY_WINDOW - 1)
    for idx in range(min_idx, last_idx - FORWARD_DAYS + 1):
        seg = bars[idx - PRIMARY_WINDOW + 1: idx + 1]
        ret14 = _pct(seg[0].close, seg[-1].close)
        primary_dir3 = _direction_from_ret(ret14)
        if primary_dir3 == "flat":
            continue  # 系統本來就不對盤整下方向性結論，無對/錯可言
        supporting, contrarian = _build_signals(coin, bars, idx, primary_dir3, bars[idx].date)
        confidence = (sum(sc.trust for sc in supporting) / len(supporting)) if supporting else 0.0
        strength = _evidence_strength(supporting, contrarian, confidence)

        fut = bars[idx + FORWARD_DAYS]
        fut_ret = _pct(bars[idx].close, fut.close)
        actual_dir = "up" if fut_ret > 0 else ("down" if fut_ret < 0 else primary_dir3)
        wrong = actual_dir != primary_dir3
        samples.append(Sample(coin=coin, date=bars[idx].date, evidence_strength=strength, wrong=wrong))
    return samples


def _time_split(n_dates: int) -> tuple[int, int]:
    """回傳 (calib_start_idx, test_start_idx)（依日期索引，5 幣共用同一切點）。"""
    train_end = int(n_dates * TRAIN_FRAC)
    calib_end = int(n_dates * (TRAIN_FRAC + CALIB_FRAC))
    return train_end, calib_end


def compute_tau(wrong_strengths: list[float], alpha: float = ALPHA) -> float:
    """標準 split conformal 有限樣本分位數：第 ceil((n+1)(1-alpha)) 大順序統計量。

    n=0（校準集裡沒有任何「錯」樣本）或需要的名次超出樣本數時，無法在此
    校準集規模下給出 distribution-free 保證——保守地回傳 1.0（相當於「幾乎
    總是 abstain」，見模組 docstring 對此 fallback 的説明）。
    """
    n = len(wrong_strengths)
    if n == 0:
        return 1.0
    ordered = sorted(wrong_strengths)
    k = math.ceil((n + 1) * (1 - alpha))
    if k > n:
        return 1.0
    return ordered[k - 1]  # 1-indexed k -> 0-indexed


def main() -> None:
    all_samples: dict[str, list[Sample]] = {c: _samples_for_coin(c) for c in COINS}
    n_dates = max((len(load_ohlcv(c, DATA_DIR)) for c in COINS), default=0)
    calib_start, test_start = _time_split(n_dates)

    # 用「日期索引」切點對應回日期字串，再用日期字串切樣本（樣本已排除掉
    # 前後緣資料不足的 idx，用日期字串比對比重新算 idx 更穩健）。
    bars_ref = load_ohlcv(COINS[0], DATA_DIR)
    calib_date_cut = bars_ref[calib_start].date if calib_start < len(bars_ref) else bars_ref[-1].date
    test_date_cut = bars_ref[test_start].date if test_start < len(bars_ref) else bars_ref[-1].date

    calib_samples: list[Sample] = []
    test_samples: list[Sample] = []
    for samples in all_samples.values():
        for s in samples:
            if calib_date_cut <= s.date < test_date_cut:
                calib_samples.append(s)
            elif s.date >= test_date_cut:
                test_samples.append(s)

    wrong_strengths = [s.evidence_strength for s in calib_samples if s.wrong]
    tau = compute_tau(wrong_strengths)

    n_test = len(test_samples)
    n_test_pass = sum(1 for s in test_samples if s.evidence_strength >= tau)
    n_test_confidently_wrong = sum(1 for s in test_samples if s.wrong and s.evidence_strength >= tau)
    # 主指標（跟 gray 細案文字一致）：JOINT 機率 P(方向錯 且 strength>=tau)，
    # 這是 split conformal 對「錯誤樣本 score 分位數」做校準時能拿到
    # distribution-free 保證的量（見腳本上方 docstring 第 3 點的推導）。
    joint_wrong_rate = (n_test_confidently_wrong / n_test) if n_test else 0.0
    # 附帶指標（非本輪保證對象，僅供參考）：CONDITIONAL 機率
    # P(方向錯 | strength>=tau, 即「不 abstain 時」)——這個量沒有本輪
    # split conformal 程序的理論保證，可能明顯偏離 alpha（信號本身預測力
    # 有限時尤其如此），印出來是為了誠實揭露、不是拿來宣稱達標。
    conditional_wrong_rate = (n_test_confidently_wrong / n_test_pass) if n_test_pass else 0.0
    abstain_rate_test = 1.0 - (n_test_pass / n_test) if n_test else 0.0

    print("=== W4 Conformal Backtest ===")
    print(f"date range: {bars_ref[0].date} ~ {bars_ref[-1].date} | coins: {', '.join(COINS)}")
    print(f"split cutoffs: calib>={calib_date_cut}, test>={test_date_cut}")
    print(f"alpha={ALPHA}, forward_days={FORWARD_DAYS}")
    print(f"calib samples: {len(calib_samples)} (wrong={len(wrong_strengths)})")
    print(f"tau = {tau:.4f}")
    print(f"test samples: {n_test} (pass/not-abstain={n_test_pass}, confidently-wrong={n_test_confidently_wrong})")
    print(f"held-out JOINT coverage: P(wrong AND strength>=tau) = {joint_wrong_rate:.4f} (target <= alpha={ALPHA}) "
          f"{'OK' if joint_wrong_rate <= ALPHA else 'VIOLATED'}")
    print(f"held-out CONDITIONAL (參考用、非本輪保證對象): P(wrong | strength>=tau) = {conditional_wrong_rate:.4f}")
    print(f"held-out abstain rate at tau: {abstain_rate_test:.4f}")

    # 額外供比對：舊簡化分位數表隱含的 abstain 門檻（calibrated_confidence
    # 0.35）換算回 evidence_strength 空間大約落在哪裡（線性反插值 _CALIBRATION_TABLE）。
    old_threshold_raw = 0.30  # _CALIBRATION_TABLE 裡 (0.30, 0.20) 是最接近 0.35 校準值以下的錨點附近
    print(f"(對照：舊簡化門檻 calibrated<0.35 約對應 evidence_strength≈{old_threshold_raw:.2f} 附近，非精確反函數)")


if __name__ == "__main__":
    main()
