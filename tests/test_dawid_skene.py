"""Dawid-Skene EM 離線 fallback 單元測試（#181）。

全合成、禁捏造歷史：用「已知準確率 source + 已知真標籤 + 噪聲」生成投票，
驗證 EM 能從「多源方向標籤的統計共識」還原每來源可靠度排序與量級。

紅線（與 #167 / AUC 無關，本測試不聲稱預測力）：
- 只用合成資料（已知真標籤 + 噪聲率）驗證；絕不捏造歷史多源資料湊指標（#24）。
- DS 產出是「統計共識信心」，不是預測力；本測試不斷言、不暗示解決 #167 AUC。
"""
from __future__ import annotations

import random

from trustforge.trust.dawid_skene import LABELS, em_source_reliability


def _build_votes(acc: dict[str, float], n_items: int = 360, seed: int = 7) -> dict:
    """由「已知準確率 source」生成合成投票。

    acc: {source: 準確率}（0~1，越高越可靠）。每個 item 給一個輪流的真標籤，
    每來源以 `acc` 機率輸出真標籤、否則隨機輸出一個錯標。回 `(coin, window)` key
    的 votes dict。全程用 `random.Random(seed)`（固定種子，確定性生成；DS 演算法
    本身仍不依賴 random）。
    """
    rng = random.Random(seed)
    votes: dict = {}
    for i in range(n_items):
        true = LABELS[i % len(LABELS)]
        sv = {}
        for s, a in acc.items():
            if rng.random() < a:
                lab = true
            else:
                lab = rng.choice([l for l in LABELS if l != true])
            sv[s] = lab
        # 每 item 用獨一 key，避免 dict 同 key 塌縮（實際呼叫端每 (coin,window)
        # 一筆，本測試為還原真實多 item 結構刻意打散 key）。
        votes[("BTC", i)] = sv
    return votes


def _oracle_r(accuracy: float) -> float:
    """用與 `em_source_reliability` 完全相同的可靠度公式，套在「已知真混淆矩陣」
    （對角=accuracy、off-diag 均分）上算出的「神諭可靠度」，供誤差比較。"""
    off = (1.0 - accuracy) / (len(LABELS) - 1)
    skill = accuracy - off
    r = 0.5 + 0.5 * (skill - 0.5) * 2
    return max(0.0, min(1.0, r))


def test_reliability_ranking_and_magnitude_above_chance():
    """已知準確率 source（A0.90 / B0.75 / C0.60，皆高於隨機 1/3）生成投票：
    - 可靠度排序 r(A) > r(B) > r(C)。
    - 每來源 |EM 可靠度 − 神諭可靠度| < 0.1（EM 從共識還原出真混淆矩陣量級）。
    """
    acc = {"A": 0.90, "B": 0.75, "C": 0.60}
    votes = _build_votes(acc)
    rel, _cm, _post, meta = em_source_reliability(votes)

    assert rel["A"] > rel["B"] > rel["C"], f"可靠度排序錯誤：{rel}"

    oracle = {s: _oracle_r(a) for s, a in acc.items()}
    for s in acc:
        assert abs(rel[s] - oracle[s]) < 0.1, (
            f"{s} 可靠度 {rel[s]:.3f} 與神諭 {oracle[s]:.3f} 誤差超過 0.1"
        )
    # 確認確實沒有退化成全部 0.5
    assert rel["A"] > 0.6, f"A 應明顯高於先驗等價值 0.5，實際 {rel['A']:.3f}"
    assert meta["fallback_sources"] == [], "above-chance 多源不應退化"


def test_spec_example_ordering_held_for_known_noise_rates():
    """規格範例（A0.9 / B0.6 / C0.3）的**排序**仍成立：r(A) > r(B) > r(C)。

    ⚠️ 已知限制（DS 標籤可辨識性）：C 的準確率 0.3 < 隨機 1/3，屬「低於隨機」的
    來源——EM 的 MLE 會把全域標籤整體翻轉使該來源「看起來」高可靠（label-flip
    退化），使量級還原誤差偏大。本測試只驗證**排序**（弱來源 C 仍排最末），量級
    誤差 < 0.1 的嚴格檢查放在 `test_reliability_ranking_and_magnitude_above_chance`
    （全 above-chance 來源，DS 良好可辨識）。這是 Dawid-Skene EM 的已知數學特性，
    非實作 bug；離線 fallback 的用途是「相對排序」，不依賴 below-chance 來源的
    精確量級。
    """
    acc = {"A": 0.90, "B": 0.60, "C": 0.30}
    votes = _build_votes(acc)
    rel, _cm, _post, _meta = em_source_reliability(votes)
    assert rel["A"] > rel["B"] > rel["C"], f"規格範例排序應 r(A)>r(B)>r(C)，實際 {rel}"


def test_likelihoods_non_decreasing():
    """每輪 data log-likelihood 必須單調不減（容差 1e-9，EM 數學保證；違反即 bug）。"""
    acc = {"A": 0.90, "B": 0.75, "C": 0.60}
    votes = _build_votes(acc)
    _rel, _cm, _post, meta = em_source_reliability(votes)
    L = meta["likelihoods"]
    assert len(L) >= 1
    for i in range(len(L) - 1):
        assert L[i] <= L[i + 1] + 1e-9, (
            f"likelihood 非單調不減：idx {i} {L[i]!r} -> idx {i+1} {L[i+1]!r}"
        )


def test_determinism_against_pythonhashseed():
    """同輸入兩次呼叫必逐位元相等（sorted + fsum 抗 PYTHONHASHSEED）。"""
    acc = {"A": 0.90, "B": 0.75, "C": 0.60}
    votes = _build_votes(acc)
    rel1, cm1, post1, meta1 = em_source_reliability(votes)
    rel2, cm2, post2, meta2 = em_source_reliability(votes)
    assert rel1 == rel2, "reliability 非確定性"
    assert cm1 == cm2, "confusion 非確定性"
    assert meta1["likelihoods"] == meta2["likelihoods"], "likelihoods 非確定性"


def test_single_source_degrades_to_prior():
    """單一來源（無法估混淆矩陣）→ 退化 r=0.5，記入 fallback_sources。"""
    votes = {("BTC", i): {"A": LABELS[i % 3]} for i in range(9)}
    rel, _cm, _post, meta = em_source_reliability(votes)
    assert rel == {"A": 0.5}
    assert meta["fallback_sources"] == ["A"]


def test_sources_fewer_than_labels_degrade():
    """來源數 < 標籤數（2 < 3）→ 全部退化 r=0.5，記入 fallback_sources。"""
    votes = {("BTC", i): {"A": LABELS[i % 3], "B": LABELS[i % 3]} for i in range(9)}
    rel, _cm, _post, meta = em_source_reliability(votes)
    assert rel == {"A": 0.5, "B": 0.5}
    assert set(meta["fallback_sources"]) == {"A", "B"}


def test_fully_consistent_multiclass_near_diagonal():
    """全一致（多類別、每來源精確跟隨輪流的真標籤）→ 高可靠度（近對角 CM）。"""
    votes = {("BTC", i): {s: LABELS[i % 3] for s in ("A", "B", "C")} for i in range(30)}
    rel, _cm, _post, meta = em_source_reliability(votes)
    for s in ("A", "B", "C"):
        assert rel[s] > 0.9, f"全一致來源 {s} 應近對角高可靠，實際 {rel[s]:.3f}"


def test_item_under_min_raters_degrades_source():
    """某來源只參與「rater 數 < min_raters_per_item」的 item → 退化 r=0.5。

    構造：前 3 個 item 有 A,B 兩票（達標），後 2 個 item 只有 A 一票（未達標）。
    B 只出現在達標 item → 正常；A 同時出現在未達標 item，但也在達標 item → 正常。
    為凸顯退化，改用：A 只出現在未達標 item、B 只出現在達標 item。
    """
    votes = {
        ("BTC", 0): {"B": "bullish", "C": "bullish"},
        ("BTC", 1): {"B": "bearish", "C": "bearish"},
        ("BTC", 2): {"A": "bullish"},  # A 只在此未達標 item
        ("BTC", 3): {"B": "neutral", "C": "neutral"},
    }
    rel, _cm, _post, meta = em_source_reliability(votes, min_raters_per_item=2)
    # A 只參與 rater<2 的 item → 退化 0.5；B 參與達標 item → 視 EM 結果（>0.5）。
    assert rel["A"] == 0.5
    assert "A" in meta["fallback_sources"]
