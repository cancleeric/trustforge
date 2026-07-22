"""Golden baselines for Trust Kernel and analyze APIs (#403)."""
from __future__ import annotations

import json

from trustforge import web
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import Document
from trustforge.schema import BasisItem, Evidence, QuestionType, Report
from trustforge.trust.kernel import KernelInput, run_kernel
from trustforge.trust.scoring import Claim


PIT = 1_700_000_000
GENERATED_AT = "2026-07-22T00:00:00Z"


def _doc(doc_id: str, kind: str, source: str, text: str) -> Document:
    return Document(
        id=doc_id,
        kind=kind,
        source=source,
        text=text,
        ts=PIT,
        meta={"coin": "BTC"},
    )


def _report(coin: str, qtype: str, question: str) -> Report:
    return Report(
        coin=coin,
        question_type=qtype,
        question=question,
        market_judgment=f"{coin} deterministic baseline",
        facts=[f"{coin} fact"],
        inferences=[f"{coin} inference"],
        key_basis=[BasisItem(f"{coin} basis claim", "fixture explanation", [1])],
        confidence=0.7,
        limits=["fixture limit"],
        could_flip=["fixture flip"],
        contrarian=[],
        generated_at=GENERATED_AT,
        calibrated_confidence=0.65,
        decision_state="normal",
    )


def _evidence(source: str, kind: str, trust: float) -> Evidence:
    return Evidence(
        source=source,
        fetched_at=GENERATED_AT,
        content_reference=f"{source} ref",
        related_claim=f"{source} claim",
        kind=kind,
        trust=trust,
        trust_components={"source": trust},
    )


def test_kernel_golden_vector_contradiction_abstention():
    claims = [
        Claim(
            "c1",
            "BTC ETF inflows expanded",
            _doc("d1", "regulatory", "sec", "BTC ETF inflows expanded"),
            direction="bullish",
        ),
        Claim(
            "c2",
            "BTC whale transfers increased",
            _doc("d2", "onchain", "glassnode", "BTC whale transfers increased"),
            direction="bullish",
        ),
        Claim(
            "c3",
            "BTC social hype looks manipulated",
            _doc("d3", "social", "x", "BTC social hype looks manipulated"),
            direction="bearish",
        ),
    ]

    out = run_kernel(
        KernelInput(claims=claims, pit_epoch=PIT, coin="BTC", query="BTC outlook")
    )

    assert out.__dict__ == {
        "trust_score": 0.6125,
        "confidence": 0.272959,
        "abstain": True,
        "direction": "偏多",
        "reason_codes": ["low_confidence", "contrarian_evidence_present"],
        "supporting_count": 2,
        "independent_sources": 2,
    }


def test_kernel_golden_vector_sparse_empty_input():
    out = run_kernel(
        KernelInput(claims=[], pit_epoch=PIT, coin="BTC", query="BTC outlook")
    )

    assert out.__dict__ == {
        "trust_score": 0.0,
        "confidence": 0.25,
        "abstain": True,
        "direction": "不明",
        "reason_codes": [
            "low_confidence",
            "no_supporting_claims",
            "calibration_boosted",
        ],
        "supporting_count": 0,
        "independent_sources": 0,
    }


def test_api_analyze_single_golden_fixture(monkeypatch):
    def fake_run(coin, query, qtype, **_kwargs):
        return (
            _report(coin, qtype.value, query),
            [_evidence("fixture-news", "news", 0.8)],
            ExecutionLog(),
        )

    monkeypatch.setattr(web, "run", fake_run)

    code, body = web._handle_api_analyze(
        {"coin": ["BTC"], "type": ["multi_source"], "q": ["golden single"]},
        client_ip="10.40.3.1",
    )

    assert code == 200
    parsed = json.loads(body)
    assert parsed["ok"] is True
    data = parsed["data"]
    assert data["report"]["coin"] == "BTC"
    assert data["report"]["market_judgment"] == "BTC deterministic baseline"
    assert data["report"]["decision_state"] == "normal"
    assert data["evidence"][0]["source"] == "fixture-news"
    assert data["evidence"][0]["kind"] == "news"
    assert data["trust_radar"]["news"]["trust"] == 0.8
    assert data["execution"]["agent"] == "hermes"


def test_api_analyze_comparison_golden_fixture(monkeypatch):
    def fake_run_comparison(coin_a, coin_b, query, **_kwargs):
        return (
            _report(coin_a, "comparison", query),
            [_evidence("fixture-a", "news", 0.8)],
            _report(coin_b, "comparison", query),
            [_evidence("fixture-b", "price", 0.9)],
            ExecutionLog(),
        )

    monkeypatch.setattr(web, "run_comparison", fake_run_comparison)

    code, body = web._handle_api_analyze(
        {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["golden comparison"]},
        client_ip="10.40.3.2",
    )

    assert code == 200
    parsed = json.loads(body)
    assert parsed["ok"] is True
    data = parsed["data"]
    assert data["report_a"]["coin"] == "BTC"
    assert data["report_b"]["coin"] == "ETH"
    assert data["evidence_a"][0]["source"] == "fixture-a"
    assert data["evidence_b"][0]["source"] == "fixture-b"
    assert data["trust_radar_a"]["news"]["trust"] == 0.8
    assert data["trust_radar_b"]["price"]["trust"] == 0.9
    assert data["execution"]["agent"] == "hermes"
