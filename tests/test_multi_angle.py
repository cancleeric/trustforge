"""Tests for multi_angle.py — 五角度綜合分析確定性演算法。

涵蓋案例：
- 全 normal 同方向 → consensus 正確
- 方向背離 → direction_divergence conflict
- 單角度 abstain → partial_abstain
- 全角度 abstain → full_abstain
- 證據高度重疊 → evidence_independence 警示
- 信心差距 → confidence_gap conflict
- 混合情境 → 多種衝突並存
- angle_result_from_payload 反序列化（含容錯）
- agreement_matrix 正確性
"""
from __future__ import annotations

import json

import pytest

from trustforge.multi_angle import (
    AngleResult,
    _build_agreement_matrix,
    _is_opposing,
    _weighted_confidence,
    angle_result_from_payload,
    synthesize_angles,
)
from trustforge.schema import QuestionType


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_angle(
    angle: str = "risk",
    direction: str = "偏多",
    calibrated_confidence: float = 0.65,
    decision_state: str = "normal",
    evidence_sources: set[str] | None = None,
    evidence_count: int = 5,
    market_judgment: str = "測試判斷",
    qtype: QuestionType = QuestionType.MULTI_SOURCE,
) -> AngleResult:
    return AngleResult(
        angle=angle,
        qtype=qtype,
        direction=direction,
        calibrated_confidence=calibrated_confidence,
        decision_state=decision_state,
        key_basis=["basis1", "basis2"],
        evidence_sources=evidence_sources or {f"{angle}-src-1", f"{angle}-src-2"},
        evidence_count=evidence_count,
        market_judgment=market_judgment,
        snapshot_id="snap-test-001",
        job_id=f"job-{angle}",
    )


def _five_angles(**overrides) -> list[AngleResult]:
    """建立五角度預設 fixture（全 normal、全偏多）。"""
    modes = ["risk", "sentiment", "fundamentals", "news", "catalyst"]
    qtypes = [
        QuestionType.MULTI_SOURCE, QuestionType.MULTI_SOURCE,
        QuestionType.HYPOTHESIS, QuestionType.MULTI_SOURCE,
        QuestionType.HYPOTHESIS,
    ]
    angles = []
    for mode, qt in zip(modes, qtypes):
        kwargs = {"angle": mode, "qtype": qt}
        kwargs.update(overrides)
        angles.append(_make_angle(**kwargs))
    return angles


# ---------------------------------------------------------------------------
# _is_opposing
# ---------------------------------------------------------------------------

class TestIsOpposing:
    def test_bullish_vs_bearish(self):
        assert _is_opposing("偏多", "偏空") is True
        assert _is_opposing("偏空", "偏多") is True

    def test_same_direction(self):
        assert _is_opposing("偏多", "偏多") is False
        assert _is_opposing("偏空", "偏空") is False

    def test_neutral_not_opposing(self):
        assert _is_opposing("偏多", "中性") is False
        assert _is_opposing("中性", "偏空") is False
        assert _is_opposing("中性", "中性") is False

    def test_unknown_not_opposing(self):
        assert _is_opposing("不明", "偏多") is False
        assert _is_opposing("偏空", "不明") is False


# ---------------------------------------------------------------------------
# _weighted_confidence
# ---------------------------------------------------------------------------

class TestWeightedConfidence:
    def test_empty(self):
        assert _weighted_confidence([]) == 0.0

    def test_single(self):
        a = _make_angle(calibrated_confidence=0.7)
        assert _weighted_confidence([a]) == 0.7

    def test_multiple(self):
        angles = [
            _make_angle(angle="risk", calibrated_confidence=0.6),
            _make_angle(angle="news", calibrated_confidence=0.8),
        ]
        result = _weighted_confidence(angles)
        assert result == pytest.approx(0.7, abs=0.001)

    def test_duplicate_sources_receive_independence_penalty(self):
        duplicated = _make_angle(
            angle="risk", calibrated_confidence=1.0, evidence_sources={"shared"}
        )
        peer = _make_angle(
            angle="news", calibrated_confidence=0.2, evidence_sources={"shared"}
        )
        independent = _make_angle(
            angle="catalyst", calibrated_confidence=0.2, evidence_sources={"unique"}
        )
        # The duplicated high-confidence angle must not dominate merely by
        # repeating the same source family.
        assert _weighted_confidence([duplicated, peer, independent]) < 0.5


# ---------------------------------------------------------------------------
# angle_result_from_payload
# ---------------------------------------------------------------------------

class TestAngleResultFromPayload:
    def test_full_payload(self):
        payload = json.dumps({
            "report": {
                "question_type": "hypothesis",
                "direction": "偏空",
                "calibrated_confidence": 0.72,
                "decision_state": "normal",
                "market_judgment": "BTC 偏空判斷",
                "key_basis": [
                    {"claim": "鏈上大額流出", "explanation": "...", "evidence_idx": [0]},
                    {"claim": "RSI 過高", "explanation": "...", "evidence_idx": [1]},
                    {"claim": "新聞負面", "explanation": "...", "evidence_idx": [2]},
                    {"claim": "第四條不會取", "explanation": "...", "evidence_idx": [3]},
                ],
            },
            "evidence": [
                {"source": "coingecko", "trust": 0.9},
                {"source": "reddit", "trust": 0.4},
                {"source": "coingecko", "trust": 0.85},
            ],
            "snapshot_id": "snap-btc-abc123",
        })
        result = angle_result_from_payload("fundamentals", payload, job_id="job-123")

        assert result.angle == "fundamentals"
        assert result.qtype == QuestionType.HYPOTHESIS
        assert result.direction == "偏空"
        assert result.calibrated_confidence == 0.72
        assert result.decision_state == "normal"
        assert result.key_basis == ["鏈上大額流出", "RSI 過高", "新聞負面"]  # 只取前 3
        assert result.evidence_sources == {"coingecko", "reddit"}
        assert result.evidence_count == 3
        assert result.market_judgment == "BTC 偏空判斷"
        assert result.snapshot_id == "snap-btc-abc123"
        assert result.job_id == "job-123"

    def test_missing_fields_fallback(self):
        """缺欄位不 crash，用安全預設值。"""
        payload = json.dumps({"report": {}, "evidence": []})
        result = angle_result_from_payload("risk", payload)

        assert result.direction == "不明"
        assert result.calibrated_confidence == 0.0
        assert result.decision_state == "normal"
        assert result.key_basis == []
        assert result.evidence_sources == set()
        assert result.evidence_count == 0
        assert result.qtype == QuestionType.MULTI_SOURCE

    def test_invalid_qtype_fallback(self):
        payload = json.dumps({"report": {"question_type": "invalid_type"}})
        result = angle_result_from_payload("news", payload)
        assert result.qtype == QuestionType.MULTI_SOURCE

    def test_dict_input(self):
        """也接受已 parse 的 dict。"""
        data = {"report": {"direction": "偏多", "calibrated_confidence": 0.55}, "evidence": [{"source": "x"}]}
        result = angle_result_from_payload("catalyst", data)
        assert result.direction == "偏多"
        assert result.evidence_sources == {"x"}

    @pytest.mark.parametrize("bad", [None, "bad", float("nan"), float("inf"), -1, 2])
    def test_invalid_confidence_is_safe_and_bounded(self, bad):
        result = angle_result_from_payload(
            "risk", {"report": {"calibrated_confidence": bad}, "evidence": []}
        )
        assert 0.0 <= result.calibrated_confidence <= 1.0


# ---------------------------------------------------------------------------
# synthesize_angles — 全 normal 同方向
# ---------------------------------------------------------------------------

class TestSynthesizeAllNormalSameDirection:
    def test_all_bullish(self):
        angles = _five_angles(direction="偏多", calibrated_confidence=0.65)
        report = synthesize_angles(angles, "BTC", "snap-001")

        assert report.coin == "BTC"
        assert report.snapshot_id == "snap-001"
        assert report.consensus == "偏多"
        assert report.consensus_confidence == pytest.approx(0.65, abs=0.001)
        assert report.conflicts == []
        assert len(report.angles) == 5
        assert report.generated_at != ""

    def test_all_bearish(self):
        angles = _five_angles(direction="偏空", calibrated_confidence=0.7)
        report = synthesize_angles(angles, "ETH", "snap-002")

        assert report.consensus == "偏空"
        assert report.conflicts == []

    def test_all_neutral(self):
        angles = _five_angles(direction="中性", calibrated_confidence=0.5)
        report = synthesize_angles(angles, "SOL", "snap-003")

        assert report.consensus == "中性"


# ---------------------------------------------------------------------------
# synthesize_angles — 方向背離
# ---------------------------------------------------------------------------

class TestSynthesizeDirectionDivergence:
    def test_risk_bearish_sentiment_bullish(self):
        angles = _five_angles(direction="偏多")
        angles[0] = _make_angle(angle="risk", direction="偏空")  # risk 偏空
        angles[1] = _make_angle(angle="sentiment", direction="偏多")  # sentiment 偏多

        report = synthesize_angles(angles, "BTC", "snap-div")

        # 4:1 的嚴格多數不可被單一反向角度覆寫成「分歧」。
        assert report.consensus == "偏多"
        divergences = [c for c in report.conflicts if c.conflict_type == "direction_divergence"]
        assert len(divergences) >= 1
        # risk vs sentiment 應該出現
        pairs = {(c.angle_a, c.angle_b) for c in divergences}
        assert ("risk", "sentiment") in pairs or ("sentiment", "risk") in pairs

    def test_divergence_summary_contains_labels(self):
        angles = [
            _make_angle(angle="risk", direction="偏空"),
            _make_angle(angle="sentiment", direction="偏多"),
            _make_angle(angle="news", direction="中性"),
            _make_angle(angle="fundamentals", direction="中性", qtype=QuestionType.HYPOTHESIS),
            _make_angle(angle="catalyst", direction="中性", qtype=QuestionType.HYPOTHESIS),
        ]
        report = synthesize_angles(angles, "BTC", "snap-sum")
        div = [c for c in report.conflicts if c.conflict_type == "direction_divergence"][0]
        assert "風險評估" in div.summary or "市場情緒" in div.summary


# ---------------------------------------------------------------------------
# synthesize_angles — abstain
# ---------------------------------------------------------------------------

class TestSynthesizeAbstain:
    def test_single_abstain_partial(self):
        angles = _five_angles(direction="偏多")
        angles[2] = _make_angle(angle="fundamentals", decision_state="abstain",
                                direction="不明", calibrated_confidence=0.0,
                                qtype=QuestionType.HYPOTHESIS)

        report = synthesize_angles(angles, "BTC", "snap-pa")

        assert report.consensus == "偏多"
        # confidence 只算 active 角度
        assert report.consensus_confidence == pytest.approx(0.65, abs=0.001)
        assert any("棄權" in lim for lim in report.limits)

    def test_all_abstain(self):
        angles = _five_angles(decision_state="abstain", direction="不明",
                              calibrated_confidence=0.0)

        report = synthesize_angles(angles, "ETH", "snap-fa")

        assert report.consensus == "full_abstain"
        assert report.consensus_confidence == 0.0
        assert report.conflicts == []
        assert "棄權" in report.synthesis_summary

    def test_abstain_not_promoted_to_normal(self):
        """abstain 不投票，但也不可抹掉其餘 active 角度的真多數。"""
        angles = _five_angles(direction="偏多")
        angles[4] = _make_angle(angle="catalyst", decision_state="abstain",
                                direction="不明", calibrated_confidence=0.0,
                                qtype=QuestionType.HYPOTHESIS)

        report = synthesize_angles(angles, "BTC", "snap-no-promo")
        assert report.consensus == "偏多"


# ---------------------------------------------------------------------------
# synthesize_angles — 證據重疊
# ---------------------------------------------------------------------------

class TestSynthesizeEvidenceOverlap:
    def test_all_same_source_low_independence(self):
        # 所有角度都用同一個 source
        common_sources = {"single-source"}
        angles = _five_angles(evidence_sources=common_sources)

        report = synthesize_angles(angles, "BTC", "snap-overlap")

        # 所有角度共用 1 個 source，交集=聯集，overlap=1.0，independence=0.0
        assert report.evidence_independence == pytest.approx(0.0, abs=0.01)
        assert any("獨立性" in lim for lim in report.limits)
        assert any(c.conflict_type == "evidence_overlap" for c in report.conflicts)

    def test_all_different_sources_high_independence(self):
        angles = []
        modes = ["risk", "sentiment", "fundamentals", "news", "catalyst"]
        for i, mode in enumerate(modes):
            angles.append(_make_angle(
                angle=mode,
                evidence_sources={f"unique-src-{i}-a", f"unique-src-{i}-b"},
            ))

        report = synthesize_angles(angles, "BTC", "snap-indep")

        # 沒有任何共用來源，intersection 為空，independence = 1.0
        assert report.evidence_independence == pytest.approx(1.0, abs=0.01)
        assert not any("獨立性" in lim for lim in report.limits)

    def test_chained_pairwise_overlap_is_not_false_independence(self):
        modes = ["risk", "sentiment", "fundamentals", "news", "catalyst"]
        sources = [
            {"a", "b"}, {"b", "c"}, {"c", "d"}, {"d", "e"}, {"e", "a"},
        ]
        angles = [
            _make_angle(angle=mode, evidence_sources=source)
            for mode, source in zip(modes, sources)
        ]
        report = synthesize_angles(angles, "BTC", "snap-chain")
        assert 0.0 < report.evidence_independence < 1.0


# ---------------------------------------------------------------------------
# synthesize_angles — 信心差距
# ---------------------------------------------------------------------------

class TestSynthesizeConfidenceGap:
    def test_large_gap_creates_conflict(self):
        angles = _five_angles(calibrated_confidence=0.7)
        # news 信心特別低
        angles[3] = _make_angle(angle="news", calibrated_confidence=0.3,
                                direction="偏多")

        report = synthesize_angles(angles, "BTC", "snap-gap")

        gap_conflicts = [c for c in report.conflicts if c.conflict_type == "confidence_gap"]
        assert len(gap_conflicts) >= 1
        # 至少有一對涉及 news
        news_involved = any(c.angle_a == "news" or c.angle_b == "news" for c in gap_conflicts)
        assert news_involved

    def test_small_gap_no_conflict(self):
        # 所有角度信心接近
        angles = _five_angles(calibrated_confidence=0.6)
        angles[0] = _make_angle(angle="risk", calibrated_confidence=0.65)

        report = synthesize_angles(angles, "BTC", "snap-no-gap")

        gap_conflicts = [c for c in report.conflicts if c.conflict_type == "confidence_gap"]
        assert gap_conflicts == []

    def test_gap_only_between_normal_states(self):
        """low_confidence 的角度不觸發 confidence_gap。"""
        angles = _five_angles(calibrated_confidence=0.7)
        angles[2] = _make_angle(angle="fundamentals", calibrated_confidence=0.2,
                                decision_state="low_confidence", direction="偏多",
                                qtype=QuestionType.HYPOTHESIS)

        report = synthesize_angles(angles, "BTC", "snap-lc-gap")

        # fundamentals 是 low_confidence，不應觸發 confidence_gap
        gap_conflicts = [c for c in report.conflicts if c.conflict_type == "confidence_gap"]
        fund_involved = any(
            c.angle_a == "fundamentals" or c.angle_b == "fundamentals"
            for c in gap_conflicts
        )
        assert not fund_involved


# ---------------------------------------------------------------------------
# synthesize_angles — 混合情境
# ---------------------------------------------------------------------------

class TestSynthesizeMixed:
    def test_divergence_plus_abstain(self):
        """3 偏多、1 偏空、1 abstain → active 角度真多數偏多。"""
        angles = [
            _make_angle(angle="risk", direction="偏空"),
            _make_angle(angle="sentiment", direction="偏多"),
            _make_angle(angle="news", direction="偏多"),
            _make_angle(angle="fundamentals", direction="偏多", qtype=QuestionType.HYPOTHESIS),
            _make_angle(angle="catalyst", decision_state="abstain", direction="不明",
                        calibrated_confidence=0.0, qtype=QuestionType.HYPOTHESIS),
        ]
        report = synthesize_angles(angles, "BTC", "snap-mixed")

        assert report.consensus == "偏多"
        assert any(c.conflict_type == "direction_divergence" for c in report.conflicts)


# ---------------------------------------------------------------------------
# agreement_matrix
# ---------------------------------------------------------------------------

class TestAgreementMatrix:
    def test_all_agree(self):
        angles = _five_angles(direction="偏多")
        matrix = _build_agreement_matrix(angles)

        for a in angles:
            for b in angles:
                if a.angle == b.angle:
                    assert matrix[a.angle][b.angle] == "self"
                else:
                    assert matrix[a.angle][b.angle] == "agree"

    def test_disagree(self):
        angles = [
            _make_angle(angle="risk", direction="偏空"),
            _make_angle(angle="sentiment", direction="偏多"),
        ]
        matrix = _build_agreement_matrix(angles)

        assert matrix["risk"]["sentiment"] == "disagree"
        assert matrix["sentiment"]["risk"] == "disagree"

    def test_one_abstain(self):
        angles = [
            _make_angle(angle="risk", direction="偏多"),
            _make_angle(angle="news", decision_state="abstain", direction="不明"),
        ]
        matrix = _build_agreement_matrix(angles)

        assert matrix["risk"]["news"] == "one_abstain"
        assert matrix["news"]["risk"] == "one_abstain"

    def test_both_abstain(self):
        angles = [
            _make_angle(angle="risk", decision_state="abstain", direction="不明"),
            _make_angle(angle="news", decision_state="abstain", direction="不明"),
        ]
        matrix = _build_agreement_matrix(angles)

        assert matrix["risk"]["news"] == "both_abstain"


# ---------------------------------------------------------------------------
# to_dict 序列化
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_angle_result_to_dict(self):
        a = _make_angle()
        d = a.to_dict()
        assert d["angle"] == "risk"
        assert d["qtype"] == "multi_source"
        assert isinstance(d["evidence_sources"], list)  # set 轉成 sorted list

    def test_multi_angle_report_to_dict(self):
        angles = _five_angles()
        report = synthesize_angles(angles, "BTC", "snap-ser")
        d = report.to_dict()

        assert d["coin"] == "BTC"
        assert d["snapshot_id"] == "snap-ser"
        assert len(d["angles"]) == 5
        assert isinstance(d["conflicts"], list)
        assert isinstance(d["agreement_matrix"], dict)
        # JSON serializable
        json.dumps(d, ensure_ascii=False)


# ---------------------------------------------------------------------------
# synthesis_summary 文字
# ---------------------------------------------------------------------------

class TestSynthesisSummary:
    def test_contains_angle_count(self):
        angles = _five_angles()
        report = synthesize_angles(angles, "BTC", "snap-txt")
        assert "5/5" in report.synthesis_summary

    def test_contains_confidence(self):
        angles = _five_angles(calibrated_confidence=0.72)
        report = synthesize_angles(angles, "BTC", "snap-txt2")
        assert "0.72" in report.synthesis_summary

    def test_full_abstain_summary(self):
        angles = _five_angles(decision_state="abstain", direction="不明",
                              calibrated_confidence=0.0)
        report = synthesize_angles(angles, "BTC", "snap-fa-txt")
        assert "棄權" in report.synthesis_summary


# ---------------------------------------------------------------------------
# narrate_synthesis (#811)
# ---------------------------------------------------------------------------

class TestNarrateSynthesis:
    def test_disabled_by_default(self, monkeypatch):
        """Without env var, returns synthesis_summary directly."""
        from trustforge.multi_angle import narrate_synthesis
        monkeypatch.delenv("TRUSTFORGE_MULTI_ANGLE_NARRATION", raising=False)

        angles = _five_angles()
        report = synthesize_angles(angles, "BTC", "snap-narr")

        result = narrate_synthesis(report, client=None)
        assert result == report.synthesis_summary

    def test_offline_client_returns_summary(self, monkeypatch):
        """Even with env=1, offline client returns synthesis_summary."""
        from trustforge.multi_angle import narrate_synthesis
        monkeypatch.setenv("TRUSTFORGE_MULTI_ANGLE_NARRATION", "1")

        angles = _five_angles()
        report = synthesize_angles(angles, "BTC", "snap-narr2")

        class FakeClient:
            offline = True

        result = narrate_synthesis(report, client=FakeClient())
        assert result == report.synthesis_summary

    def test_online_client_uses_llm(self, monkeypatch):
        """With env=1 and online client, calls client.complete and uses result."""
        from trustforge.multi_angle import narrate_synthesis
        monkeypatch.setenv("TRUSTFORGE_MULTI_ANGLE_NARRATION", "1")

        angles = _five_angles()
        report = synthesize_angles(angles, "BTC", "snap-narr3")

        class FakeResult:
            text = "這是 LLM 產生的摘要文字，描述五角度偏多的共識。"

        class FakeClient:
            offline = False
            def complete(self, system, prompt):
                assert "不可自行發明" in system or "不可添加" in prompt
                return FakeResult()

        result = narrate_synthesis(report, client=FakeClient())
        assert result == "這是 LLM 產生的摘要文字，描述五角度偏多的共識。"

    def test_llm_failure_degrades(self, monkeypatch):
        """LLM raises → gracefully falls back to synthesis_summary."""
        from trustforge.multi_angle import narrate_synthesis
        monkeypatch.setenv("TRUSTFORGE_MULTI_ANGLE_NARRATION", "1")

        angles = _five_angles()
        report = synthesize_angles(angles, "BTC", "snap-narr4")

        class FakeClient:
            offline = False
            def complete(self, system, prompt):
                raise RuntimeError("Bedrock timeout")

        result = narrate_synthesis(report, client=FakeClient())
        assert result == report.synthesis_summary

    def test_structural_fields_unchanged(self, monkeypatch):
        """narrate_synthesis never modifies report's structural fields."""
        from trustforge.multi_angle import narrate_synthesis
        monkeypatch.setenv("TRUSTFORGE_MULTI_ANGLE_NARRATION", "1")

        angles = _five_angles(direction="偏空")
        report = synthesize_angles(angles, "ETH", "snap-narr5")
        original_consensus = report.consensus
        original_conflicts = list(report.conflicts)

        class FakeResult:
            text = "LLM 說偏多但不應影響結構"

        class FakeClient:
            offline = False
            def complete(self, system, prompt):
                return FakeResult()

        narrate_synthesis(report, client=FakeClient())
        # Structural fields unchanged
        assert report.consensus == original_consensus
        assert report.conflicts == original_conflicts
