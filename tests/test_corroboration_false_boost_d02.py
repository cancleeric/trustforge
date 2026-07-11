"""D0.2（#15 + #4）：Corroboration 虛抬雙修。

- #4 否定詞語意偵測：同一方向詞一方 asserted、一方 negated（「X 上漲」vs
  「X 不會上漲」）→ 即使有 token 重疊、被否定方判成 neutral，也不計為獨立佐證，
  trust 不被虛抬。
- #15 token-overlap：重疊門檻（相對 target >=0.4）持續擋掉主題無關／低重疊的主張；
  跨幣單一共享 token 巧合（<0.4）不計佐證。真正的語意歧義（同 token 不同主題）
  仍由 stance_fn（W1.5 語意分類器）把關，符合 #15 原設計。
"""
from __future__ import annotations

from trustforge.ingestion.base import Document
from trustforge.trust.scoring import Claim, _corroboration


def _doc(id_: str, source: str, text: str) -> Document:
    return Document(id=id_, kind="news", source=source, text=text, ts=1_000_000.0)


def _claim(id_: str, source: str, text: str) -> Claim:
    return Claim(id=id_, text=text, doc=_doc(id_, source, text))


# ─── #4 否定詞語意偵測（有 token 重疊，排除來自否定閘）──────────────

def test_negation_opposition_excludes_corroboration():
    """「BTC 上漲 突破 阻力」（asserted 上漲）vs「BTC 不會上漲 突破 阻力」
    （negated 上漲，共享 突破/阻力 → overlap>=0.4）→ 語意對立，否定閘排除，
    corr == 0（trust 不被虛抬）。"""
    tgt = _claim("t", "coindesk", "BTC 上漲 突破 阻力")
    cand = _claim("c", "reuters", "BTC 不會上漲 突破 阻力")
    corr = _corroboration(tgt, [tgt, cand], stance_fn=None)
    assert corr == 0.0, f"否定對立不應計為佐證，corr 應=0，實得 {corr}"


def test_negation_opposition_both_directions():
    """反向：「BTC 不會下跌 維持 強勢」(negated 下跌) vs「BTC 下跌 維持 強勢」
    （asserted 下跌，共享 維持/強勢）→ 排除。"""
    tgt = _claim("t", "coindesk", "BTC 不會下跌 維持 強勢")
    cand = _claim("c", "reuters", "BTC 下跌 維持 強勢")
    corr = _corroboration(tgt, [tgt, cand], stance_fn=None)
    assert corr == 0.0, f"否定對立不應計為佐證，corr 應=0，實得 {corr}"


def test_negated_same_direction_still_corroborates():
    """「BTC 不會上漲 維持 弱勢 盤整」(negated 上漲) vs「BTC 下跌 弱勢 盤整
    跌破 支撐」(asserted 下跌，共享 弱勢/盤整)：兩者都偏空，無「同詞
    asserted/negated 對立」，且方向相容 → 仍計為佐證（>0）。否定閘不誤殺
    真正同向主張。"""
    tgt = _claim("t", "coindesk", "BTC 不會上漲 維持 弱勢 盤整")
    cand = _claim("c", "reuters", "BTC 下跌 弱勢 盤整 跌破 支撐")
    corr = _corroboration(tgt, [tgt, cand], stance_fn=None)
    assert corr > 0.0, f"真正同向（不會上漲≈偏空 vs 下跌）應計佐證，實得 {corr}"


# ─── #15 token-overlap（低重疊 / 跨幣單詞巧合不計佐證）──────────────

def test_low_overlap_unrelated_not_corroborated():
    """#15：主題無關、共享 token <0.4 → 不計佐證。"""
    tgt = _claim("t", "coindesk", "黃金 突破 阻力 測試 前高")
    cand = _claim("c", "reuters", "白銀 突破 下跌")
    corr = _corroboration(tgt, [tgt, cand], stance_fn=None)
    assert corr == 0.0, f"低重疊不相關主張不應計佐證，實得 {corr}"


def test_single_token_coincidence_across_coins_not_corroborated():
    """#15：跨幣的單一共享 token 巧合（都提到「突破」，但各自僅 2–3 實質 token、
    重疊 <0.4）→ 不計為佐證。"""
    tgt = _claim("t", "coindesk", "黃金 突破 阻力")
    cand = _claim("c", "reuters", "白銀 突破 下跌")
    corr = _corroboration(tgt, [tgt, cand], stance_fn=None)
    assert corr == 0.0, f"跨幣單一 token 巧合不應計佐證，實得 {corr}"


def test_same_topic_high_overlap_still_corroborates():
    """回歸鎖：同主題高相似主張（ETF 審批相關）仍計佐證。"""
    tgt = _claim("t", "coindesk", "清算 瀑布 觸發 ETF 審批 加速")
    c1 = _claim("c1", "reuters", "清算 瀑布 影響 ETF 申請 結果")
    c2 = _claim("c2", "bloomberg", "清算 瀑布 導致 ETF 審批 延後")
    corr = _corroboration(tgt, [tgt, c1, c2], stance_fn=None)
    assert corr > 0.0, f"同主題高相似主張應計佐證，實得 {corr}"


def test_bidirectional_gate_regression_two_independent_sources():
    """回歸鎖：兩真正不同來源、同主題 → corr == 1 - 0.5**2 == 0.75。"""
    tgt = _claim("ga", "glassnode", "清算 瀑布 觸發 ETF 審批 加速")
    c1 = _claim("gb", "coindesk", "清算 瀑布 影響 ETF 申請 結果")
    c2 = _claim("gc", "reuters", "清算 瀑布 導致 ETF 審批 延後")
    corr = _corroboration(tgt, [tgt, c1, c2], stance_fn=None)
    assert corr == 1.0 - 0.5 ** 2, f"兩獨立來源 corr 應=0.75，實得 {corr}"
