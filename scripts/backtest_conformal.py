#!/usr/bin/env python3
"""W4：Split Conformal Prediction 離線回測 — TrustForge #197 異質多源 Conformal Backtest。

Phase A/B/C：從 Alternative.me FNG + Blockchain.com Charts 歷史 JSONL 加入
異質訊號（sentiment + onchain），與既有 OHLCV 技術訊號合併後跑 split conformal，
評估是否達到 #197 指定 promotion threshold。

用法：
    python3 scripts/backtest_conformal.py

目的：用 `data/data/*.csv`（HOYA BIT 官方 5 幣 OHLCV，2021-06-01~2026-05-31）
+ `out/history/` 下異質資料回測 compliance 門檻 τ，**不改 conformal.py 數學實作**，
純確定性、不呼叫任何 LLM/Bedrock，零 credit。

---------------------------------------------------------------------------
方法（single-stage split conformal，α=0.1）
---------------------------------------------------------------------------
同原版 W4 流程，差異在 `_samples_for_coin(extra_signals=True)` 會在建完既有
OHLCV 技術訊號後，追加 Phase B 的異質訊號（FNG + blockchain），其餘 split/
tau 計算/held-out 驗證流程逐字不變。

---------------------------------------------------------------------------
⚠️ 誠實聲明（不可省略，PR 說明務必附上）
---------------------------------------------------------------------------
- **單階段 conformal**：本輪只對 `evidence_strength → 是否方向錯誤` 做校準。
- **假設歷史≈未來**：coverage 保證只在 exchangeable 假設下成立。
- **N=3、α=0.1 是主觀選擇**。
- **FNG 為 market-wide**：所有幣別共用同一值，非各幣獨立信號。
- **blockchain.com 僅 BTC**：hash-rate/difficulty/n-transactions vs SHA256 網路。
- **不改 conformal.py 數學**：既有 `compute_tau()` 與 `_evidence_strength()` 原封不動複用。
- 邊界語義（codex 對抗審修正）：fallback=`math.inf` + 嚴格 `>`（非 `>=`）。
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime as _dt, timedelta as _td
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trustforge.ingestion.base import Document  # noqa: E402
from trustforge.ingestion.prices import Bar, _pct, _volatility, load_ohlcv  # noqa: E402
from trustforge.trust.scoring import Claim, ScoredClaim, _evidence_strength  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "data"
COINS = ["BTC", "ETH", "SOL", "BNB", "XRP"]

# Phase A：異質多源歷史資料路徑
HISTORY_DIR = Path(__file__).resolve().parents[1] / "out" / "history"
FNG_PATH = HISTORY_DIR / "alternative-me-fng-2021-07-17_2026-07-17.jsonl"
BLOCKCHAIN_PATH = HISTORY_DIR / "blockchain-com-charts-2021-07-17_2026-07-17.jsonl"

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
    source_families: frozenset[str] = frozenset({"price"})


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
        text=f"{coin} {source} 訊號（回測合成，非真實文本）", url="", ts=0.0,
        meta={"backtest": True},
    )
    return Claim(id=doc.id, text=doc.text, doc=doc, claim_type="inference")


# ——— Phase A：異質多源歷史資料載入 ———————————————————————————————

def _load_fng_index(fng_path: Path | None = None) -> dict[str, dict]:
    """從 Alternative.me FNG JSONL 建 `{date: {value, classification}}` index。

    FNG 為 market-wide（scope="market-wide"），所有幣別共用同一值。
    不存在或解析錯誤時 graceful skip（回空 dict，不炸回測）。
    """
    fpath = fng_path or FNG_PATH
    if not fpath.exists():
        return {}
    index: dict[str, dict] = {}
    try:
        with fpath.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                date_str = rec.get("published_at", "")[:10]
                if not date_str:
                    continue
                # FNG 是 market-wide，每日期多筆記錄（coin 欄位僅供參考），
                # 取第一筆為準（所有幣同值）。
                if date_str not in index:
                    index[date_str] = {
                        "value": float(rec["value"]),
                        "classification": rec.get("classification", ""),
                    }
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        pass  # graceful skip
    return index


def _load_blockchain_index(bc_path: Path | None = None) -> dict[str, dict[str, float]]:
    """從 Blockchain.com Charts JSONL 建 `{date: {metric: value}}` index。

    僅 BTC（coin="BTC"）；每天 3 metrics：n-transactions, hash-rate, difficulty。
    不存在或解析錯誤時 graceful skip。
    """
    fpath = bc_path or BLOCKCHAIN_PATH
    if not fpath.exists():
        return {}
    index: dict[str, dict[str, float]] = {}
    try:
        with fpath.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("coin", "") != "BTC":
                    continue
                date_str = rec.get("published_at", "")[:10]
                if not date_str:
                    continue
                metric = rec.get("metric", "")
                if not metric:
                    continue
                index.setdefault(date_str, {})[metric] = float(rec["value"])
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        pass
    return index


# 模組層級 lazy init（首次存取時才載入，只在 _samples_for_coin(extra_signals=True) 時觸發）
_fng_cache: dict[str, dict] | None = None
_bc_cache: dict[str, dict[str, float]] | None = None


def _get_fng_index() -> dict[str, dict]:
    global _fng_cache
    if _fng_cache is None:
        _fng_cache = _load_fng_index()
    return _fng_cache


def _get_bc_index() -> dict[str, dict[str, float]]:
    global _bc_cache
    if _bc_cache is None:
        _bc_cache = _load_blockchain_index()
    return _bc_cache


# ——— Phase B：異質多源訊號擴充 ———————————————————————————————————————

def _build_fng_signal(
    coin: str, date_str: str, primary_dir: str, ts_tag: str,
    fng_index: dict[str, dict] | None = None,
) -> tuple[list[ScoredClaim], list[ScoredClaim]]:
    """FNG 訊號：FNG 0-25=fear(bearish), 75-100=greed(bullish), 45-55=skip。

    trust = clamp(0.5+abs(value-50)/100, 0.5, 0.85)。
    market-wide：所有幣別共用同一值（不要假獨立）。
    日期不在 index 中時 graceful skip（空 list）。
    """
    idx_map = fng_index if fng_index is not None else _get_fng_index()
    rec = idx_map.get(date_str)
    if rec is None:
        return [], []
    value = rec["value"]
    if 45 < value < 55:
        return [], []  # neutral zone, skip

    signal_dir = "up" if value >= 75 else "down"
    trust = max(0.5, min(0.85, 0.5 + abs(value - 50) / 100.0))
    claim = _make_signal_claim(coin, "fng", "sentiment", ts_tag)
    sc = ScoredClaim(claim=claim, trust=trust)
    if signal_dir == primary_dir:
        return [sc], []
    else:
        return [], [sc]


def _build_blockchain_signals(
    coin: str, date_str: str, primary_dir: str, ts_tag: str,
    bc_index: dict[str, dict[str, float]] | None = None,
) -> tuple[list[ScoredClaim], list[ScoredClaim]]:
    """Blockchain.com onchain 訊號：n-transactions, hash-rate, difficulty。

    ⛔ BTC only（守門：非 BTC 直接跳過）。
    7 日 MA vs 30 日前趨勢。trust = clamp(0.5+abs(pct)/50, 0.5, 0.90)。
    資料不足以算 7/30 MA 時 graceful skip。
    """
    if coin != "BTC":
        return [], []
    idx_map = bc_index if bc_index is not None else _get_bc_index()

    try:
        d = _dt.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return [], []

    metrics = ["n-transactions", "hash-rate", "difficulty"]
    supporting: list[ScoredClaim] = []
    contrarian: list[ScoredClaim] = []

    for metric in metrics:
        # 7 日 MA（當日往前 7 天）
        recent_vals: list[float] = []
        for i in range(7):
            day = (d - _td(days=i)).strftime("%Y-%m-%d")
            rec = idx_map.get(day, {})
            val = rec.get(metric)
            if val is not None:
                recent_vals.append(val)
        if len(recent_vals) < 4:  # 至少要有 4 天資料才合理
            continue
        ma7 = sum(recent_vals) / len(recent_vals)

        # 30 日前的 7 日 MA（日期 [d-36, d-30]）
        prev_vals: list[float] = []
        for i in range(7):
            day = (d - _td(days=30 + i)).strftime("%Y-%m-%d")
            rec = idx_map.get(day, {})
            val = rec.get(metric)
            if val is not None:
                prev_vals.append(val)
        if len(prev_vals) < 4:
            continue
        ma_prev = sum(prev_vals) / len(prev_vals)

        if ma_prev == 0:
            continue
        pct = (ma7 - ma_prev) / ma_prev * 100.0
        if abs(pct) < 1e-9:
            continue

        trust = max(0.5, min(0.9, 0.5 + abs(pct) / 50.0))
        claim = _make_signal_claim(coin, f"bc-{metric}", "onchain", ts_tag)
        sc = ScoredClaim(claim=claim, trust=trust)
        signal_dir = "up" if pct > 0 else "down"
        (supporting if signal_dir == primary_dir else contrarian).append(sc)

    return supporting, contrarian


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


def _samples_for_coin(
    coin: str,
    extra_signals: bool = False,
    fng_index: dict[str, dict] | None = None,
    bc_index: dict[str, dict[str, float]] | None = None,
) -> list[Sample]:
    bars = load_ohlcv(coin, DATA_DIR)
    if not bars:
        return []
    samples: list[Sample] = []
    last_idx = len(bars) - 1
    min_idx = max(PRIMARY_WINDOW - 1, max(MOMENTUM_WINDOWS) - 1, 2 * VOL_STABILITY_WINDOW - 1)
    for idx in range(min_idx, last_idx - FORWARD_DAYS + 1):
        seg = bars[idx - PRIMARY_WINDOW + 1: idx + 1]
        ret14 = _pct(seg[0].close, seg[-1].close)
        primary_dir3 = _direction_from_ret(ret14)
        if primary_dir3 == "flat":
            continue
        supporting, contrarian = _build_signals(coin, bars, idx, primary_dir3, bars[idx].date)

        # Phase B：異質多源訊號擴充（在既有 OHLCV signals 後追加）
        if extra_signals:
            date_str = bars[idx].date
            # FNG：market-wide，所有幣別共用同一值
            fng_sup, fng_con = _build_fng_signal(
                coin, date_str, primary_dir3, date_str,
                fng_index=fng_index,
            )
            supporting.extend(fng_sup)
            contrarian.extend(fng_con)
            # Blockchain.com：BTC only（_build_blockchain_signals 內部守門）
            bc_sup, bc_con = _build_blockchain_signals(
                coin, date_str, primary_dir3, date_str,
                bc_index=bc_index,
            )
            supporting.extend(bc_sup)
            contrarian.extend(bc_con)

        confidence = (sum(sc.trust for sc in supporting) / len(supporting)) if supporting else 0.0
        strength = _evidence_strength(supporting, contrarian, confidence)

        fut = bars[idx + FORWARD_DAYS]
        fut_ret = _pct(bars[idx].close, fut.close)
        actual_dir = "up" if fut_ret > 0 else ("down" if fut_ret < 0 else primary_dir3)
        wrong = actual_dir != primary_dir3
        families = {"price"}
        if extra_signals and (fng_sup or fng_con):
            families.add("sentiment")
        if extra_signals and (bc_sup or bc_con):
            families.add("onchain")
        samples.append(Sample(
            coin=coin,
            date=bars[idx].date,
            evidence_strength=strength,
            wrong=wrong,
            source_families=frozenset(families),
        ))
    return samples


def _time_split(n_dates: int) -> tuple[int, int]:
    """回傳 (calib_start_idx, test_start_idx)（依日期索引，5 幣共用同一切點）。"""
    train_end = int(n_dates * TRAIN_FRAC)
    calib_end = int(n_dates * (TRAIN_FRAC + CALIB_FRAC))
    return train_end, calib_end


def _chronological_partitions(
    all_samples: dict[str, list[Sample]],
) -> tuple[list[Sample], list[Sample], str, str]:
    """Split by global unique dates, never by one coin's bar indexes.

    Every row on a date is assigned to the same partition even when coin
    calendars differ.  The first 70% remains an unused training interval,
    the next 15% is calibration, and the final interval is held out.
    """
    dates = sorted({sample.date for samples in all_samples.values() for sample in samples})
    if len(dates) < 7:
        raise ValueError("at least 7 global unique dates are required")
    calib_start_idx, held_start_idx = _time_split(len(dates))
    if not 0 < calib_start_idx < held_start_idx < len(dates):
        raise ValueError("dataset is too small for chronological train/calibration/held-out split")
    calib_start = dates[calib_start_idx]
    held_start = dates[held_start_idx]
    held_start_date = _dt.strptime(held_start, "%Y-%m-%d")
    calibration = [
        sample
        for samples in all_samples.values()
        for sample in samples
        if calib_start <= sample.date < held_start
        and _dt.strptime(sample.date, "%Y-%m-%d") + _td(days=FORWARD_DAYS)
        < held_start_date
    ]
    held_out = [
        sample
        for samples in all_samples.values()
        for sample in samples
        if sample.date >= held_start
    ]
    if not calibration or not held_out:
        raise ValueError("chronological calibration and held-out partitions must be non-empty")
    return calibration, held_out, calib_start, held_start


def compute_tau(wrong_strengths: list[float], alpha: float = ALPHA) -> float:
    """標準 split conformal 有限樣本分位數：第 ceil((n+1)(1-alpha)) 大順序統計量。

    保證是對 **嚴格不等式** `strength > tau` 成立的。
    n=0 或名次超出樣本數 → math.inf（一律 abstain）。
    """
    n = len(wrong_strengths)
    if n == 0:
        return math.inf
    ordered = sorted(wrong_strengths)
    k = math.ceil((n + 1) * (1 - alpha))
    if k > n:
        return math.inf
    return ordered[k - 1]


def _heterogeneous_ready(
    calibration: list[Sample],
    held_out: list[Sample],
    fng_index: dict[str, dict],
    blockchain_index: dict[str, dict[str, float]],
) -> tuple[bool, set[str], set[str]]:
    """Require loaded inputs and observed family support in both partitions."""
    calibration_families = set().union(
        *(sample.source_families for sample in calibration)
    )
    held_out_families = set().union(
        *(sample.source_families for sample in held_out)
    )
    ready = (
        bool(fng_index)
        and bool(blockchain_index)
        and len(calibration_families) >= 2
        and len(held_out_families) >= 2
    )
    return ready, calibration_families, held_out_families


def main() -> None:
    # ——— OHLCV-only baseline（既有行為，逐字不變）———
    all_samples_ohlcv: dict[str, list[Sample]] = {c: _samples_for_coin(c) for c in COINS}
    bars_ref = load_ohlcv(COINS[0], DATA_DIR)
    calib_ohlcv, test_ohlcv, calib_date_cut, test_date_cut = (
        _chronological_partitions(all_samples_ohlcv)
    )
    wrong_strengths_ohlcv = [s.evidence_strength for s in calib_ohlcv if s.wrong]
    tau_ohlcv = compute_tau(wrong_strengths_ohlcv)

    n_test_ohlcv = len(test_ohlcv)
    n_pass_ohlcv = sum(1 for s in test_ohlcv if s.evidence_strength > tau_ohlcv)
    n_cw_ohlcv = sum(1 for s in test_ohlcv if s.wrong and s.evidence_strength > tau_ohlcv)
    jwr_ohlcv = (n_cw_ohlcv / n_test_ohlcv) if n_test_ohlcv else 0.0
    cwr_ohlcv = (n_cw_ohlcv / n_pass_ohlcv) if n_pass_ohlcv else 0.0
    abr_ohlcv = 1.0 - (n_pass_ohlcv / n_test_ohlcv) if n_test_ohlcv else 0.0

    # ——— 異質多源擴充（OHLCV + FNG + Blockchain）———
    fng_idx = _get_fng_index()
    bc_idx = _get_bc_index()
    all_samples_expanded: dict[str, list[Sample]] = {
        c: _samples_for_coin(c, extra_signals=True, fng_index=fng_idx, bc_index=bc_idx)
        for c in COINS
    }

    calib_exp, test_exp, exp_calib_cut, exp_test_cut = (
        _chronological_partitions(all_samples_expanded)
    )
    if (exp_calib_cut, exp_test_cut) != (calib_date_cut, test_date_cut):
        raise RuntimeError("baseline and expanded samples produced inconsistent date boundaries")
    wrong_strengths_exp = [s.evidence_strength for s in calib_exp if s.wrong]
    tau_exp = compute_tau(wrong_strengths_exp)

    n_test_exp = len(test_exp)
    n_pass_exp = sum(1 for s in test_exp if s.evidence_strength > tau_exp)
    n_cw_exp = sum(1 for s in test_exp if s.wrong and s.evidence_strength > tau_exp)
    jwr_exp = (n_cw_exp / n_test_exp) if n_test_exp else 0.0
    cwr_exp = (n_cw_exp / n_pass_exp) if n_pass_exp else 0.0
    abr_exp = 1.0 - (n_pass_exp / n_test_exp) if n_test_exp else 0.0

    # ——— 比較報告 ———
    def _f4(v: float) -> str: return f"{v:.4f}"
    def _s(n: int) -> str: return str(n)

    print("=== W4 Conformal Backtest — 異質多源 Conformal Backtest #197 ===")
    print(f"date range: {bars_ref[0].date} ~ {bars_ref[-1].date} | coins: {', '.join(COINS)}")
    print(f"split cutoffs: calib>={calib_date_cut}, test>={test_date_cut}")
    print(f"alpha={ALPHA}, forward_days={FORWARD_DAYS}")
    print(f"FNG index: {len(fng_idx)} dates loaded")
    print(f"Blockchain index: {len(bc_idx)} dates loaded")
    print()
    print(f"{'指標':<45} {'OHLCV-only':>14} {'OHLCV+FNG+BC':>14} {'Threshold':>12} {'達標':>5}")
    print(f"{'─' * 45} {'─' * 14} {'─' * 14} {'─' * 12} {'─' * 5}")
    print(f"{'τ':<45} {_f4(tau_ohlcv):>14} {_f4(tau_exp):>14} {'':>12} {'':>5}")
    print(f"{'calib samples':<45} {_s(len(calib_ohlcv)):>14} {_s(len(calib_exp)):>14} {'':>12} {'':>5}")
    print(f"{'calib wrong':<45} {_s(len(wrong_strengths_ohlcv)):>14} {_s(len(wrong_strengths_exp)):>14} {'':>12} {'':>5}")
    print(f"{'test samples':<45} {_s(n_test_ohlcv):>14} {_s(n_test_exp):>14} {'':>12} {'':>5}")
    print()

    # P1: joint coverage ≤ 0.10
    p1_exp = "PASS" if jwr_exp <= ALPHA else "FAIL"
    print(f"{'P1 joint coverage (≤0.10)':<45} {_f4(jwr_ohlcv):>14} {_f4(jwr_exp):>14} {'≤ 0.10':>12} {p1_exp:>5}")

    # P2: abstain rate ≤ 0.60
    p2_exp = "PASS" if abr_exp <= 0.60 else "FAIL"
    print(f"{'P2 abstain rate (≤0.60)':<45} {_f4(abr_ohlcv):>14} {_f4(abr_exp):>14} {'≤ 0.60':>12} {p2_exp:>5}")

    # P3: conditional wrong ≤ 0.55
    p3_exp = "PASS" if cwr_exp <= 0.55 else "FAIL"
    print(f"{'P3 conditional wrong (≤0.55)':<45} {_f4(cwr_ohlcv):>14} {_f4(cwr_exp):>14} {'≤ 0.55':>12} {p3_exp:>5}")

    # P4: held-out pass ≥ 100
    p4_exp = "PASS" if n_pass_exp >= 100 else "FAIL"
    print(f"{'P4 held-out pass (≥100)':<45} {_s(n_pass_ohlcv):>14} {_s(n_pass_exp):>14} {'≥ 100':>12} {p4_exp:>5}")

    heterogeneous_ready, calib_families, test_families = _heterogeneous_ready(
        calib_exp, test_exp, fng_idx, bc_idx
    )
    p5_exp = "PASS" if heterogeneous_ready else "FAIL"
    print(
        f"{'P5 heterogeneous families in both partitions':<45} "
        f"{','.join(sorted(calib_families)):>14} "
        f"{','.join(sorted(test_families)):>14} {'≥ 2 each':>12} {p5_exp:>5}"
    )

    print()
    all_pass = (
        p1_exp == "PASS" and p2_exp == "PASS" and p3_exp == "PASS"
        and p4_exp == "PASS" and p5_exp == "PASS"
    )
    if all_pass:
        print(">>> ALL P1-P4 PASS — Promotion eligible (Phase D: Wire Production) <<<")
        print(f"    conformal._CONFORMAL_TAU = {tau_exp:.4f}  # 無條件進位到 4 位")
    else:
        failed = []
        if p1_exp == "FAIL":
            failed.append("P1")
        if p2_exp == "FAIL":
            failed.append("P2")
        if p3_exp == "FAIL":
            failed.append("P3")
        if p4_exp == "FAIL":
            failed.append("P4")
        if p5_exp == "FAIL":
            failed.append("P5")
        print(f">>> FAILED: {', '.join(failed)} — Phase E (Honest State) <<<")
        print("    不偽造、不強上。conformal.py 維持現狀，記錄 FAIL 原因。")

    print()
    print("(對照：舊簡化門檻 calibrated<0.35 約對應 evidence_strength≈0.30 附近，非精確反函數)")


if __name__ == "__main__":
    main()
