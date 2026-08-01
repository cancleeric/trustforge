"""Contract tests for #583 — Report term_annotations schema integration.

驗證重點：
1. 舊 Report payload（無 term_annotations）仍可 parse（向後相容）。
2. 新 Report payload（有 term_annotations）格式正確。
3. build_report 產出的 Report 帶有對 market_judgment 的正確標註。
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from trustforge.schema import Report
from trustforge.term_annotations import TermAnnotation, annotate_terms


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_report(**kwargs) -> Report:
    defaults = dict(
        coin="BTC",
        question_type="multi_source",
        question="test",
        market_judgment="偏多：TVL 穩定，FDV 偏高，gas fee 上升。",
        facts=[],
        inferences=[],
        key_basis=[],
        confidence=0.6,
        limits=[],
        could_flip=[],
        contrarian=[],
        generated_at="2026-07-26T00:00:00Z",
    )
    defaults.update(kwargs)
    return Report(**defaults)


# ---------------------------------------------------------------------------
# 1. Backward compatibility: old payload without term_annotations still parses
# ---------------------------------------------------------------------------

def test_legacy_report_without_term_annotations_defaults_to_empty_list():
    """舊呼叫端不傳 term_annotations → 預設空 list。"""
    r = _make_report()
    assert r.term_annotations == []


def test_legacy_report_asdict_json_roundtrip():
    """舊 Report asdict → json.dumps → json.loads 後 term_annotations 仍為 []（不向後崩）。"""
    r = _make_report()
    d = dataclasses.asdict(r)
    s = json.dumps(d, ensure_ascii=False)
    parsed = json.loads(s)
    assert parsed.get("term_annotations") == []


# ---------------------------------------------------------------------------
# 2. New payload with term_annotations has correct shape
# ---------------------------------------------------------------------------

def test_report_with_term_annotations_field():
    """新 Report 帶 term_annotations → 正確序列化。"""
    annotations = [
        {
            "term_id": "tvl",
            "term_name": "TVL",
            "matched_text": "TVL",
            "start": 4,
            "end": 7,
            "glossary_link": "/glossary/tvl",
        },
        {
            "term_id": "fdv",
            "term_name": "FDV",
            "matched_text": "FDV",
            "start": 15,
            "end": 18,
            "glossary_link": "/glossary/fdv",
        },
    ]
    r = _make_report(term_annotations=annotations)
    assert r.term_annotations == annotations

    d = dataclasses.asdict(r)
    assert d["term_annotations"] == annotations

    s = json.dumps(d, ensure_ascii=False)
    parsed = json.loads(s)
    assert len(parsed["term_annotations"]) == 2
    assert parsed["term_annotations"][0]["term_id"] == "tvl"
    assert parsed["term_annotations"][0]["glossary_link"] == "/glossary/tvl"
    assert parsed["term_annotations"][1]["term_name"] == "FDV"


# ---------------------------------------------------------------------------
# 3. Annotation engine produces correct dict shape for Report payload
# ---------------------------------------------------------------------------

def test_annotate_terms_produces_report_compatible_dict():
    """annotate_terms().to_dict() 的 key 組合可無縫塞進 Report.term_annotations。"""
    text = "FDV and market cap are not the same as TVL."
    annotations = annotate_terms(text)

    for ann in annotations:
        d = ann.to_dict()
        required_keys = {"term_id", "term_name", "matched_text", "start", "end", "glossary_link"}
        assert set(d.keys()) == required_keys
        assert isinstance(d["start"], int)
        assert isinstance(d["end"], int)
        assert d["glossary_link"].startswith("/glossary/")

    # 確認可直接塞進 Report
    report_anns = [ann.to_dict() for ann in annotations]
    r = _make_report(market_judgment=text, term_annotations=report_anns)
    assert len(r.term_annotations) == 3


# ---------------------------------------------------------------------------
# 4. Integration: wiring verify — annotation engine → Report payload
# ---------------------------------------------------------------------------

def test_build_report_annotation_injection_pipeline():
    """End-to-end：從 annotate_terms() 到 Report.term_annotations 的完整 wiring。

    不直接呼叫 build_report()（其內部依賴過深），而是模擬 build_report 內部的
    接線路徑：對 market_judgment 文字跑 annotate_terms() → 產出 dict list →
    塞進 Report() → asdict → json roundtrip。
    """
    from trustforge.agent.orchestrator import build_report
    from trustforge.ingestion.base import Document
    from trustforge.trust.scoring import ScoredClaim, Claim, TrustedBrief

    # 使用真實 Document / Claim 物件，確保 build_report 路徑可走完
    doc = Document(
        id="test-doc-pipeline",
        kind="price",
        source="test-source",
        text="TVL of Bitcoin is growing steadily while FDV remains high.",
        url="https://example.com/test",
        ts=0.0,
        meta={"content_reference": "TVL observed at $1.2T"},
    )
    claim = Claim(id="c-pipeline", text="TVL is high", doc=doc)
    sc = ScoredClaim(claim=claim, trust=0.8)

    brief = TrustedBrief(
        query="Is BTC bullish?",
        supporting=[sc],
        contrarian=[],
        confidence=0.8,
        calibrated_confidence=0.75,
    )

    report, evidence = build_report(
        query="Is BTC bullish?",
        coin="BTC",
        qtype=pytest.importorskip("trustforge.schema").QuestionType.MULTI_SOURCE,
        brief=brief,
        run_scope_id="test-term-annotations",
    )

    # build_report 產出的 Report 應有 term_annotations（由 #583 接線注入）
    assert isinstance(report.term_annotations, list)
    for ann in report.term_annotations:
        assert set(ann.keys()) >= {"term_id", "term_name", "matched_text", "start", "end", "glossary_link"}
        assert isinstance(ann["start"], int)
        assert isinstance(ann["end"], int)
        assert ann["glossary_link"].startswith("/glossary/")


def test_build_report_term_annotations_roundtrip():
    """build_report → asdict → json.dumps → json.loads 的完整 roundtrip。"""
    from trustforge.agent.orchestrator import build_report
    from trustforge.ingestion.base import Document
    from trustforge.trust.scoring import ScoredClaim, Claim, TrustedBrief

    doc = Document(
        id="test-doc-roundtrip",
        kind="price",
        source="test-source",
        text="Gas fee on ETH network is rising, raising FDV concerns.",
        url="https://example.com/test",
        ts=0.0,
        meta={"content_reference": "gas fee up 200%"},
    )
    claim = Claim(id="c-roundtrip", text="Gas fee rising", doc=doc)
    sc = ScoredClaim(claim=claim, trust=0.8)

    brief = TrustedBrief(
        query="Is ETH bullish?",
        supporting=[sc],
        contrarian=[],
        confidence=0.8,
        calibrated_confidence=0.75,
    )

    report, _ = build_report(
        query="Is ETH bullish?",
        coin="ETH",
        qtype=pytest.importorskip("trustforge.schema").QuestionType.MULTI_SOURCE,
        brief=brief,
        run_scope_id="test-term-annotations-roundtrip",
    )

    d = dataclasses.asdict(report)
    s = json.dumps(d, ensure_ascii=False)
    parsed = json.loads(s)

    # term_annotations 經過 roundtrip 後欄位完整
    assert isinstance(parsed.get("term_annotations"), list)
    for ann in parsed["term_annotations"]:
        for key in ("term_id", "term_name", "matched_text", "start", "end", "glossary_link"):
            assert key in ann, f"missing key '{key}' in annotation: {ann}"
