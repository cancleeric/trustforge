"""KernelOutput → app projection contract tests (#731).

Usage
-----
Generate / overwrite fixtures:
    env GENERATE_PROJECTION_FIXTURES=1 python -m pytest tests/test_kernel_projection_contract.py

Run contract tests:
    python -m pytest tests/test_kernel_projection_contract.py
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from trustforge.agent.kernel_projection import (
    KernelJudgment,
    project,
    _kernel_scored_to_evidence,
)
from trustforge.schema import BasisItem, Evidence, iso_utc
from trustforge_core import (
    KERNEL_CONTRACT_VERSION,
    KernelClaim,
    KernelDocument,
    KernelInput,
    KernelOutput,
    KernelReputationTrace,
    KernelScoredClaim,
    UnsupportedKernelContractVersion,
    run_kernel,
)


FIXTURE_DIR = Path(__file__).with_suffix("").parent / "fixtures" / "projection"
COMMIT = subprocess.check_output(
    ["git", "rev-parse", "HEAD"], text=True, cwd=Path(__file__).resolve().parents[1]
).strip()


# ---------------------------------------------------------------------------
# Fixture constructors
# ---------------------------------------------------------------------------

def _doc(doc_id: str, kind: str, source: str, text: str, ts: float) -> KernelDocument:
    return KernelDocument(doc_id, kind, source, text, ts)


def _claim(claim_id: str, text: str, doc: KernelDocument, *, ctype: str = "fact", direction: str = "neutral") -> KernelClaim:
    return KernelClaim(claim_id, text, doc, ctype, direction)


def _kernel_output_to_dict(ko: KernelOutput) -> dict[str, Any]:
    """Serialize KernelOutput to dict for fixture storage."""
    def _document_to_dict(doc: KernelDocument) -> dict[str, Any]:
        return {
            "id": doc.id,
            "kind": doc.kind,
            "source": doc.source,
            "text": doc.text,
            "timestamp": doc.timestamp,
            "url": doc.url,
            "metadata": [list(item) for item in doc.metadata],
        }

    def _claim_to_dict(claim: KernelClaim) -> dict[str, Any]:
        return {
            "id": claim.id,
            "text": claim.text,
            "claim_type": claim.claim_type,
            "direction": claim.direction,
            "document": _document_to_dict(claim.document),
        }

    def _trace_to_dict(trace: KernelReputationTrace | None) -> dict[str, Any] | None:
        if trace is None:
            return None
        return {
            "source": trace.source,
            "prior": trace.prior,
            "final": trace.final,
            "agree_n": trace.agree_n,
            "contradict_n": trace.contradict_n,
            "iterations_run": trace.iterations_run,
            "mode": trace.mode,
        }

    return {
        "trust_score": ko.trust_score,
        "confidence": ko.confidence,
        "abstain": ko.abstain,
        "direction": ko.direction,
        "reason_codes": list(ko.reason_codes),
        "supporting_count": ko.supporting_count,
        "independent_sources": ko.independent_sources,
        "contract_version": ko.contract_version,
        "query": ko.query,
        "decision_state": ko.decision_state,
        "scored_claims": [
            {
                "claim": _claim_to_dict(sc.claim),
                "trust": sc.trust,
                "components": [list(c) for c in sc.components],
                "reputation_trace": _trace_to_dict(sc.reputation_trace),
                "manip_flags": list(sc.manip_flags),
                "info_flags": list(sc.info_flags),
            }
            for sc in ko.scored_claims
        ],
        "supporting": [sc.claim.id for sc in ko.supporting],
        "contrarian": [sc.claim.id for sc in ko.contrarian],
    }


def _dict_to_kernel_output(d: dict[str, Any]) -> KernelOutput:
    """Reconstruct KernelOutput from fixture dict."""
    scored_claims: list[KernelScoredClaim] = []
    for sc in d["scored_claims"]:
        doc_d = sc["claim"]["document"]
        doc = KernelDocument(
            id=doc_d["id"],
            kind=doc_d["kind"],
            source=doc_d["source"],
            text=doc_d["text"],
            timestamp=doc_d["timestamp"],
            url=doc_d.get("url", ""),
            metadata=tuple(tuple(item) for item in doc_d.get("metadata", [])),
        )
        claim = KernelClaim(
            id=sc["claim"]["id"],
            text=sc["claim"]["text"],
            document=doc,
            claim_type=sc["claim"].get("claim_type", "inference"),
            direction=sc["claim"].get("direction", "neutral"),
        )
        rep_trace = None
        if sc.get("reputation_trace"):
            rt = sc["reputation_trace"]
            rep_trace = KernelReputationTrace(
                source=rt["source"],
                prior=rt["prior"],
                final=rt["final"],
                agree_n=rt["agree_n"],
                contradict_n=rt["contradict_n"],
                iterations_run=rt["iterations_run"],
                mode=rt.get("mode", "entailment"),
            )
        scored_claims.append(KernelScoredClaim(
            claim=claim,
            trust=sc["trust"],
            components=tuple(tuple(c) for c in sc.get("components", [])),
            reputation_trace=rep_trace,
            manip_flags=tuple(sc.get("manip_flags", [])),
            info_flags=tuple(sc.get("info_flags", [])),
        ))

    claim_by_id = {sc.claim.id: sc for sc in scored_claims}
    supporting = tuple(claim_by_id[cid] for cid in d.get("supporting", []))
    contrarian = tuple(claim_by_id[cid] for cid in d.get("contrarian", []))

    return KernelOutput(
        trust_score=d["trust_score"],
        confidence=d["confidence"],
        abstain=d["abstain"],
        direction=d["direction"],
        reason_codes=tuple(d["reason_codes"]),
        supporting_count=d["supporting_count"],
        independent_sources=d["independent_sources"],
        contract_version=d.get("contract_version", KERNEL_CONTRACT_VERSION),
        query=d.get("query", ""),
        scored_claims=tuple(scored_claims),
        supporting=supporting,
        contrarian=contrarian,
        decision_state=d.get("decision_state", "normal"),
    )


def _judgment_to_dict(j: KernelJudgment) -> dict[str, Any]:
    """Serialize KernelJudgment to dict for fixture storage."""
    def _evidence_to_dict(ev: Evidence) -> dict[str, Any]:
        return {
            "source": ev.source,
            "fetched_at": ev.fetched_at,
            "content_reference": ev.content_reference,
            "related_claim": ev.related_claim,
            "source_url": ev.source_url,
            "kind": ev.kind,
            "trust": ev.trust,
            "trust_components": ev.trust_components,
            "flags": list(ev.flags),
            "info_flags": list(ev.info_flags),
            "author": ev.author,
            "reputation_mode": ev.reputation_mode,
            "data_lineage": ev.data_lineage,
        }

    def _basis_to_dict(b: BasisItem) -> dict[str, Any]:
        return {
            "claim": b.claim,
            "explanation": b.explanation,
            "evidence_idx": list(b.evidence_idx),
        }

    return {
        "coin": j.coin,
        "query": j.query,
        "direction": j.direction,
        "confidence": j.confidence,
        "raw_confidence": j.raw_confidence,
        "abstain": j.abstain,
        "decision_state": j.decision_state,
        "reason_codes": list(j.reason_codes),
        "supporting_count": j.supporting_count,
        "independent_sources": j.independent_sources,
        "kernel_contract_version": j.kernel_contract_version,
        "evidence": [_evidence_to_dict(ev) for ev in j.evidence],
        "supporting_evidence": [_evidence_to_dict(ev) for ev in j.supporting_evidence],
        "contrarian_texts": list(j.contrarian_texts),
        "key_basis": [_basis_to_dict(b) for b in j.key_basis],
    }


# ---------------------------------------------------------------------------
# Fixture generation from parity cases
# ---------------------------------------------------------------------------

PARITY_DIR = Path(__file__).with_suffix("").parent / "fixtures" / "parity"

SCENARIOS: list[tuple[str, str, list[KernelClaim], float, str]] = [
    (
        "support",
        "Multiple high-trust supporting claims from diverse sources",
        [
            _claim("c1", "BTC ETF inflows expanded", _doc("d1", "news", "Reuters", "BTC ETF inflows expanded", 900.0), direction="bullish"),
            _claim("c2", "BTC exchange reserves fell", _doc("d2", "onchain", "Glassnode", "BTC exchange reserves fell", 910.0), direction="bullish"),
            _claim("c3", "BTC price broke resistance", _doc("d3", "price", "CoinGecko", "BTC price broke resistance", 920.0), direction="bullish"),
            _claim("c4", "Institutional accumulation continues", _doc("d4", "regulatory", "SEC", "Institutional accumulation continues", 930.0), direction="bullish"),
        ],
        1000.0,
        "BTC",
    ),
    (
        "abstain",
        "Low-confidence / insufficient sources -> abstain",
        [
            _claim("c1", "BTC social post says pump", _doc("d1", "social", "twitter_user", "BTC pump shill", 900.0), direction="bullish"),
        ],
        1000.0,
        "BTC",
    ),
    (
        "empty_output",
        "Edge case: empty output with zero claims",
        [],
        1000.0,
        "BTC",
    ),
    (
        "single_source",
        "Edge case: single high-trust source",
        [
            _claim("c1", "BTC ETF approved", _doc("d1", "news", "Reuters", "BTC ETF approved", 900.0), direction="bullish"),
        ],
        1000.0,
        "BTC",
    ),
    (
        "reason_codes",
        "Edge case: reason_codes populated",
        [
            _claim("c1", "BTC old news", _doc("d1", "news", "Reuters", "BTC old news", 100.0), direction="bullish"),
            _claim("c2", "BTC fresh news", _doc("d2", "news", "Reuters", "BTC fresh news", 999.0), direction="bullish"),
        ],
        1000.0,
        "BTC",
    ),
]


def _generate_all_fixtures() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    for case_id, description, claims, now, coin in SCENARIOS:
        if case_id == "empty_output":
            # empty claims -> we construct an empty KernelOutput directly
            ko = KernelOutput(
                trust_score=0.0,
                confidence=0.0,
                abstain=True,
                direction="不明",
                reason_codes=("low_calibrated_confidence", "insufficient_independent_sources"),
                supporting_count=0,
                independent_sources=0,
                query="BTC outlook",
                scored_claims=(),
                supporting=(),
                contrarian=(),
                decision_state="abstain",
            )
        else:
            ki = KernelInput(
                claims=tuple(claims),
                pit_epoch=now,
                coin=coin,
                query="BTC outlook",
            )
            ko = run_kernel(ki)

        projection = project(ko, coin=coin)

        fixture = {
            "case_id": case_id,
            "description": description,
            "commit": COMMIT,
            "input": _kernel_output_to_dict(ko),
            "expected_projection": _judgment_to_dict(projection),
        }
        path = FIXTURE_DIR / f"{case_id}.json"
        path.write_text(json.dumps(fixture, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
        print(f"Generated {path}")

    # invalid_contract: manually construct a KernelOutput with bad version
    sc_invalid = KernelScoredClaim(
        claim=_claim("c1", "BTC news", _doc("d1", "news", "Reuters", "BTC news", 900.0)),
        trust=0.5,
    )
    ko_invalid = KernelOutput(
        trust_score=0.5,
        confidence=0.5,
        abstain=False,
        direction="偏多",
        reason_codes=(),
        supporting_count=1,
        independent_sources=1,
        query="BTC outlook",
        scored_claims=(sc_invalid,),
        supporting=(sc_invalid,),
        contrarian=(),
    )
    object.__setattr__(ko_invalid, "contract_version", "999.0.0")
    fixture_invalid = {
        "case_id": "invalid_contract",
        "description": "Unknown contract version should raise UnsupportedKernelContractVersion",
        "commit": COMMIT,
        "input": _kernel_output_to_dict(ko_invalid),
        "expected_projection": None,
    }
    path_invalid = FIXTURE_DIR / "invalid_contract.json"
    path_invalid.write_text(json.dumps(fixture_invalid, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    print(f"Generated {path_invalid}")


if __name__ == "__main__" or os.environ.get("GENERATE_PROJECTION_FIXTURES"):
    _generate_all_fixtures()


# ---------------------------------------------------------------------------
# API Contract Tests
# ---------------------------------------------------------------------------

class TestAPIContract:
    """Phase 4: API contract tests."""

    def test_accept_valid_kernel_output(self):
        claim = _claim("c1", "BTC news", _doc("d1", "news", "Reuters", "BTC news", 900.0))
        ki = KernelInput((claim,), 1000.0, "BTC", "BTC outlook")
        ko = run_kernel(ki)
        result = project(ko, coin="BTC")
        assert isinstance(result, KernelJudgment)
        assert result.kernel_contract_version == KERNEL_CONTRACT_VERSION

    def test_reject_unknown_contract_version(self):
        sc = KernelScoredClaim(
            claim=_claim("c1", "BTC news", _doc("d1", "news", "Reuters", "BTC news", 900.0)),
            trust=0.5,
        )
        ko = KernelOutput(
            trust_score=0.5,
            confidence=0.5,
            abstain=False,
            direction="偏多",
            reason_codes=(),
            supporting_count=1,
            independent_sources=1,
            query="BTC outlook",
            scored_claims=(sc,),
            supporting=(sc,),
            contrarian=(),
        )
        object.__setattr__(ko, "contract_version", "999.0.0")
        with pytest.raises(UnsupportedKernelContractVersion):
            project(ko, coin="BTC")

    def test_reject_non_kernel_output(self):
        with pytest.raises((TypeError, AttributeError)):
            project("not a kernel output", coin="BTC")  # type: ignore[call-overload]

    def test_deterministic(self):
        claim = _claim("c1", "BTC news", _doc("d1", "news", "Reuters", "BTC news", 900.0))
        ki = KernelInput((claim,), 1000.0, "BTC", "BTC outlook")
        ko = run_kernel(ki)
        first = project(ko, coin="BTC")
        second = project(ko, coin="BTC")
        assert first == second

    def test_no_side_effects(self):
        claim = _claim("c1", "BTC news", _doc("d1", "news", "Reuters", "BTC news", 900.0))
        ki = KernelInput((claim,), 1000.0, "BTC", "BTC outlook")
        ko = run_kernel(ki)
        original = dataclasses.asdict(ko)
        project(ko, coin="BTC")
        after = dataclasses.asdict(ko)
        assert original == after

    def test_coin_kwarg_required(self):
        claim = _claim("c1", "BTC news", _doc("d1", "news", "Reuters", "BTC news", 900.0))
        ki = KernelInput((claim,), 1000.0, "BTC", "BTC outlook")
        ko = run_kernel(ki)
        with pytest.raises(TypeError):
            project(ko)  # type: ignore[call-arg]


class TestReportContract:
    """Phase 4: Report field pass-through exactness."""

    def test_direction_confidence_abstain_decision_state_exact_pass_through(self):
        claims = [
            _claim("c1", "BTC ETF inflows expanded", _doc("d1", "news", "Reuters", "BTC ETF inflows expanded", 900.0), direction="bullish"),
            _claim("c2", "BTC exchange reserves fell", _doc("d2", "onchain", "Glassnode", "BTC exchange reserves fell", 910.0), direction="bullish"),
            _claim("c3", "BTC social posts promise guaranteed profit", _doc("d3", "social", "anon", "guaranteed profit", 920.0), direction="bearish"),
        ]
        ki = KernelInput(tuple(claims), 1000.0, "BTC", "BTC outlook")
        ko = run_kernel(ki)
        result = project(ko, coin="BTC")
        assert result.direction == ko.direction
        assert result.confidence == ko.confidence
        assert result.raw_confidence == ko.trust_score
        assert result.abstain == ko.abstain
        assert result.decision_state == ko.decision_state
        assert result.reason_codes == ko.reason_codes
        assert result.supporting_count == ko.supporting_count
        assert result.independent_sources == ko.independent_sources
        assert result.query == ko.query
        assert result.kernel_contract_version == ko.contract_version

    def test_empty_output_projectable(self):
        ko = KernelOutput(
            trust_score=0.0,
            confidence=0.0,
            abstain=True,
            direction="不明",
            reason_codes=("low_calibrated_confidence", "insufficient_independent_sources"),
            supporting_count=0,
            independent_sources=0,
            query="BTC outlook",
            scored_claims=(),
            supporting=(),
            contrarian=(),
            decision_state="abstain",
        )
        result = project(ko, coin="BTC")
        assert result.coin == "BTC"
        assert result.evidence == ()
        assert result.supporting_evidence == ()
        assert result.contrarian_texts == ()
        assert result.key_basis == ()


class TestEvidenceContract:
    """Phase 4: Evidence field exactness."""

    def test_trust_components_flags_info_flags_reputation_exact(self):
        claim = _claim("c1", "BTC pump shill", _doc("d1", "social", "twitter_user", "BTC pump shill", 900.0), direction="bullish")
        ki = KernelInput((claim,), 1000.0, "BTC", "BTC outlook")
        ko = run_kernel(ki)
        result = project(ko, coin="BTC")
        assert len(result.evidence) == len(ko.scored_claims)
        for ev, sc in zip(result.evidence, ko.scored_claims):
            assert ev.trust == round(sc.trust, 3)
            assert ev.trust_components == {k: round(v, 3) for k, v in sc.components}
            assert ev.flags == list(sc.manip_flags)
            assert ev.info_flags == list(sc.info_flags)
            if sc.reputation_trace is not None:
                assert ev.reputation_mode == sc.reputation_trace.mode
                assert "reputation_prior" in ev.trust_components
                assert "reputation_final" in ev.trust_components
            else:
                assert ev.reputation_mode is None

    def test_supporting_evidence_matches_supporting(self):
        claims = [
            _claim("c1", "BTC ETF inflows expanded", _doc("d1", "news", "Reuters", "BTC ETF inflows expanded", 900.0), direction="bullish"),
            _claim("c2", "BTC exchange reserves fell", _doc("d2", "onchain", "Glassnode", "BTC exchange reserves fell", 910.0), direction="bullish"),
            _claim("c3", "BTC social posts promise guaranteed profit", _doc("d3", "social", "anon", "guaranteed profit", 920.0), direction="bearish"),
        ]
        ki = KernelInput(tuple(claims), 1000.0, "BTC", "BTC outlook")
        ko = run_kernel(ki)
        result = project(ko, coin="BTC")
        assert len(result.supporting_evidence) == len(ko.supporting)
        assert len(result.contrarian_texts) == len(ko.contrarian)
        supporting_claim_ids = {sc.claim.id for sc in ko.supporting}
        for ev in result.supporting_evidence:
            assert ev.related_claim == "BTC 市場判斷"
        for txt, sc in zip(result.contrarian_texts, ko.contrarian):
            assert txt == sc.claim.text

    def test_evidence_no_deduplication(self):
        """Evidence is not deduplicated (semantic difference from build_report)."""
        claims = [
            _claim("c1", "BTC ETF approved", _doc("d1", "news", "Reuters", "BTC ETF approved", 900.0), direction="bullish"),
            _claim("c2", "BTC ETF approved", _doc("d2", "news", "Reuters", "BTC ETF approved", 910.0), direction="bullish"),
        ]
        ki = KernelInput(tuple(claims), 1000.0, "BTC", "BTC outlook")
        ko = run_kernel(ki)
        result = project(ko, coin="BTC")
        assert len(result.evidence) == len(ko.scored_claims)
        # In build_report, these would be deduplicated by (source, content_reference, related)
        # In projection, they remain separate.
        sources = [ev.source for ev in result.evidence]
        assert sources.count("Reuters") == 2

    def test_key_basis_maps_to_supporting(self):
        claims = [
            _claim("c1", "BTC ETF inflows expanded", _doc("d1", "news", "Reuters", "BTC ETF inflows expanded", 900.0), direction="bullish"),
            _claim("c2", "BTC exchange reserves fell", _doc("d2", "onchain", "Glassnode", "BTC exchange reserves fell", 910.0), direction="bullish"),
        ]
        ki = KernelInput(tuple(claims), 1000.0, "BTC", "BTC outlook")
        ko = run_kernel(ki)
        result = project(ko, coin="BTC")
        assert len(result.key_basis) == len(result.supporting_evidence)
        for basis, ev in zip(result.key_basis, result.supporting_evidence):
            assert isinstance(basis, BasisItem)
            assert basis.claim in [e.content_reference for e in result.evidence]
            assert basis.evidence_idx


class TestGoldenVectors:
    """Phase 4: Golden vector exact match."""

    @pytest.mark.parametrize("case_id, description, claims, now, coin", SCENARIOS)
    def test_golden_vector_exact_match(self, case_id, description, claims, now, coin):
        fixture_path = FIXTURE_DIR / f"{case_id}.json"
        if not fixture_path.is_file():
            pytest.skip(f"fixture not generated yet: {fixture_path}")
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        ko = _dict_to_kernel_output(fixture["input"])
        projection = project(ko, coin=coin)
        expected = fixture["expected_projection"]
        assert projection.coin == expected["coin"]
        assert projection.query == expected["query"]
        assert projection.direction == expected["direction"]
        assert projection.confidence == pytest.approx(expected["confidence"])
        assert projection.raw_confidence == pytest.approx(expected["raw_confidence"])
        assert projection.abstain == expected["abstain"]
        assert projection.decision_state == expected["decision_state"]
        assert list(projection.reason_codes) == expected["reason_codes"]
        assert projection.supporting_count == expected["supporting_count"]
        assert projection.independent_sources == expected["independent_sources"]
        assert projection.kernel_contract_version == expected["kernel_contract_version"]
        assert len(projection.evidence) == len(expected["evidence"])
        assert len(projection.supporting_evidence) == len(expected["supporting_evidence"])
        assert len(projection.contrarian_texts) == len(expected["contrarian_texts"])
        assert len(projection.key_basis) == len(expected["key_basis"])


class TestSnapshotRoundtrip:
    """Phase 4: Snapshot roundtrip + replay identical."""

    def test_roundtrip_via_dataclass_asdict(self):
        claims = [
            _claim("c1", "BTC ETF inflows expanded", _doc("d1", "news", "Reuters", "BTC ETF inflows expanded", 900.0), direction="bullish"),
            _claim("c2", "BTC exchange reserves fell", _doc("d2", "onchain", "Glassnode", "BTC exchange reserves fell", 910.0), direction="bullish"),
        ]
        ki = KernelInput(tuple(claims), 1000.0, "BTC", "BTC outlook")
        ko = run_kernel(ki)
        original = project(ko, coin="BTC")
        frozen = json.dumps(dataclasses.asdict(original), sort_keys=True)
        thawed_dict = json.loads(frozen)
        # Replay: reconstruct from dict
        replayed = KernelJudgment(
            coin=thawed_dict["coin"],
            query=thawed_dict["query"],
            direction=thawed_dict["direction"],
            confidence=thawed_dict["confidence"],
            raw_confidence=thawed_dict["raw_confidence"],
            abstain=thawed_dict["abstain"],
            decision_state=thawed_dict["decision_state"],
            reason_codes=tuple(thawed_dict["reason_codes"]),
            supporting_count=thawed_dict["supporting_count"],
            independent_sources=thawed_dict["independent_sources"],
            kernel_contract_version=thawed_dict["kernel_contract_version"],
            evidence=tuple(
                Evidence(
                    source=e["source"],
                    fetched_at=e["fetched_at"],
                    content_reference=e["content_reference"],
                    related_claim=e["related_claim"],
                    source_url=e["source_url"],
                    kind=e["kind"],
                    trust=e["trust"],
                    trust_components=e["trust_components"],
                    flags=e["flags"],
                    info_flags=e["info_flags"],
                    author=e.get("author"),
                    reputation_mode=e.get("reputation_mode"),
                    data_lineage=e.get("data_lineage"),
                    direction=e.get("direction", ""),
                )
                for e in thawed_dict["evidence"]
            ),
            supporting_evidence=tuple(
                Evidence(
                    source=e["source"],
                    fetched_at=e["fetched_at"],
                    content_reference=e["content_reference"],
                    related_claim=e["related_claim"],
                    source_url=e["source_url"],
                    kind=e["kind"],
                    trust=e["trust"],
                    trust_components=e["trust_components"],
                    flags=e["flags"],
                    info_flags=e["info_flags"],
                    author=e.get("author"),
                    reputation_mode=e.get("reputation_mode"),
                    data_lineage=e.get("data_lineage"),
                    direction=e.get("direction", ""),
                )
                for e in thawed_dict["supporting_evidence"]
            ),
            contrarian_texts=tuple(thawed_dict["contrarian_texts"]),
            key_basis=tuple(
                BasisItem(
                    claim=b["claim"],
                    explanation=b["explanation"],
                    evidence_idx=b["evidence_idx"],
                )
                for b in thawed_dict["key_basis"]
            ),
        )
        assert original == replayed


class TestDifferences:
    """Phase 5: All differences have disposition."""

    def test_no_narrative_llm_text(self):
        """No narrative (LLM text) — intentional."""
        claims = [
            _claim("c1", "BTC news", _doc("d1", "news", "Reuters", "BTC news", 900.0)),
        ]
        ki = KernelInput(tuple(claims), 1000.0, "BTC", "BTC outlook")
        ko = run_kernel(ki)
        result = project(ko, coin="BTC")
        # KernelJudgment has no narrative/market_judgment field
        assert not hasattr(result, "market_judgment")
        assert not hasattr(result, "narrative")

    def test_no_facts_limits_could_flip(self):
        """No facts / market_judgment — intentional.
        No limits/could_flip — intentional.
        """
        claims = [
            _claim("c1", "BTC news", _doc("d1", "news", "Reuters", "BTC news", 900.0)),
        ]
        ki = KernelInput(tuple(claims), 1000.0, "BTC", "BTC outlook")
        ko = run_kernel(ki)
        result = project(ko, coin="BTC")
        assert not hasattr(result, "facts")
        assert not hasattr(result, "limits")
        assert not hasattr(result, "could_flip")

    def test_no_insight_hypothesis_ledger(self):
        """No insight/hypothesis_ledger — intentional."""
        claims = [
            _claim("c1", "BTC news", _doc("d1", "news", "Reuters", "BTC news", 900.0)),
        ]
        ki = KernelInput(tuple(claims), 1000.0, "BTC", "BTC outlook")
        ko = run_kernel(ki)
        result = project(ko, coin="BTC")
        assert not hasattr(result, "insights")
        assert not hasattr(result, "hypothesis_ledger")

    def test_evidence_not_deduplicated(self):
        """Evidence 不經去重 — semantic difference from build_report."""
        claims = [
            _claim("c1", "BTC ETF approved", _doc("d1", "news", "Reuters", "BTC ETF approved", 900.0), direction="bullish"),
            _claim("c2", "BTC ETF approved", _doc("d2", "news", "Reuters", "BTC ETF approved", 910.0), direction="bullish"),
        ]
        ki = KernelInput(tuple(claims), 1000.0, "BTC", "BTC outlook")
        ko = run_kernel(ki)
        result = project(ko, coin="BTC")
        # Projection keeps both; build_report would deduplicate by key
        assert len(result.evidence) == 2
