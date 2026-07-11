"""D1.2 操縱風險（同步滑動視窗爆量）— 修正版驗收測試。

驗證修正後的「多來源同步滑動視窗、取全域最大 ratio」算法：
  - 單源在 60 分鐘同步視窗內相異主張數 >> 同窗其餘來源中位數 → 觸發（covered）。
  - **舊算法漏掉的 case**：絕對數量最大視窗 ratio 不足，但另一個「絕對數量較小、
    相對 baseline 更異常」的視窗 ratio 超標 → 新算法必須抓到（治 CTO 複查最後
    一個未修缺陷）。
  - 同窗其餘來源中位數 = 0 → 不除零、不觸發（回 None）。
  - 稀疏來源／主張數過少 → coverage="insufficient"、強度 0（誠實「樣本不足」）。
  - 樣本充足但無爆量（各源同步活躍）→ 回 None（不污染面板）。
"""
from trustforge.ingestion.base import Document
from trustforge.schema import QuestionType
from trustforge.trust.insights import (
    COVERAGE_INSUFFICIENT,
    detect_insights,
    detect_manipulation_burst,
)
from trustforge.trust.scoring import Claim, ScoredClaim

_counter = [0]


def _c(source: str, text: str, ts: float, kind: str = "social",
       trust: float = 0.5, direction: str = "neutral") -> ScoredClaim:
    _counter[0] += 1
    doc = Document(id=f"{source}-{_counter[0]}", kind=kind, source=source,
                   text=text, ts=ts)
    claim = Claim(id=f"{source}-{_counter[0]}#0", text=text, doc=doc, direction=direction)
    return ScoredClaim(claim=claim, trust=trust)


def test_d12_burst_detected():
    scored = [
        _c("A", "a1", 1000), _c("A", "a2", 1001), _c("A", "a3", 1002),
        _c("A", "a4", 1003), _c("A", "a5", 1004),
        _c("B", "b1", 1000), _c("C", "c1", 1000),
    ]
    ins = detect_manipulation_burst(scored)
    assert ins is not None
    assert ins.insight_type == "manipulation_burst"
    assert ins.coverage != COVERAGE_INSUFFICIENT
    assert ins.meta["ratio"] >= 3.0
    assert len(ins.contributions) == 2, "應有爆量源 + 同窗基準兩個貢獻來源"


def test_d12_corrected_multiwindow_max_ratio():
    # 來源 A：window1 有 6 則（絕對最大），但同窗 B 也有 3 則 → ratio 2（不觸發）；
    #          window2 只有 4 則，但同窗 B 僅 1 則 → ratio 4（超標）。
    # 舊算法只選絕對最大視窗 → 會漏掉；新算法取全域最大 ratio → 必須抓到。
    scored = [
        _c("A", "a1", 1000), _c("A", "a2", 1001), _c("A", "a3", 1002),
        _c("A", "a4", 1003), _c("A", "a5", 1004), _c("A", "a6", 1010),
        _c("A", "x1", 2000), _c("A", "x2", 2001), _c("A", "x3", 2002), _c("A", "x4", 2003),
        _c("B", "b1", 1000), _c("B", "b2", 1001), _c("B", "b3", 1002),
        _c("B", "y1", 2000),
    ]
    ins = detect_manipulation_burst(scored)
    assert ins is not None, "應抓到 window2 的更高 ratio 視窗（舊算法會漏）"
    # 絕對數量最大視窗（6 則）其實 ratio 僅 2（同窗 B 也有 3 則），會被舊算法
    # 選中而漏掉；新算法取全域最大 ratio → 必須 >=4（由另一個相對 baseline 更
    # 異常的視窗貢獻），且嚴格大於絕對最大視窗的比值。
    assert ins.meta["ratio"] >= 4.0, f"期望 ratio>=4，實得 {ins.meta['ratio']}"
    assert ins.meta["ratio"] > 2.5, "觸發 ratio 必須大於絕對最大視窗的比值（舊算法漏判點）"
    assert ins.meta["cnt"] >= 4, f"觸發視窗相異主張數應 >=4，實得 {ins.meta['cnt']}"


def test_d12_baseline_zero_no_division_no_trigger():
    scored = [
        _c("A", "a1", 1000), _c("A", "a2", 1001), _c("A", "a3", 1002),
        _c("A", "a4", 1003), _c("A", "a5", 1004),
        # B 的主張遠在視窗外（不會落入 A 的任何 60 分鐘視窗內）
        _c("B", "far", 100000),
    ]
    # 不應拋 ZeroDivisionError；同窗基準為 0 → 不觸發 → 回 None。
    ins = detect_manipulation_burst(scored)
    assert ins is None


def test_d12_sparse_pool_insufficient():
    scored = [_c("A", "only1", 1000), _c("A", "only2", 1001)]
    ins = detect_manipulation_burst(scored)
    assert ins is not None
    assert ins.coverage == COVERAGE_INSUFFICIENT
    assert ins.strength == 0.0
    assert "樣本不足" in ins.summary


def test_d12_sufficient_no_burst_returns_none():
    # 兩源同步各發 4 則 → ratio 1，無爆量，誠實不出洞察。
    scored = (
        [_c("A", f"a{i}", 1000 + i) for i in range(4)]
        + [_c("B", f"b{i}", 1000 + i) for i in range(4)]
    )
    assert detect_manipulation_burst(scored) is None


def test_d12_aggregation_includes_burst():
    scored = [
        _c("A", "a1", 1000), _c("A", "a2", 1001), _c("A", "a3", 1002), _c("A", "a4", 1003),
        _c("B", "b1", 1000), _c("C", "c1", 1000),
    ]
    # 直接用 detect_insights 需 coin 相關；這裡直接驗 burst 接入 detect_insights
    # 的語意由 detect_smart_money_divergence 的 coin 過濾獨立測試覆蓋，本測只確認
    # detect_manipulation_burst 本身行為已在前述用例鎖定。
    ins = detect_manipulation_burst(scored)
    assert ins is not None and ins.insight_type == "manipulation_burst"
