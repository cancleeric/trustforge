"""KernelOutput → app projection contract (#731).

Zero IO, zero provider import, zero BedrockClient.  The projection is a
deterministic, side-effect-free map that rejects unknown contract versions
fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass

from trustforge.schema import BasisItem, Evidence, iso_utc
from trustforge_core import (
    KERNEL_CONTRACT_VERSION,
    KernelOutput,
    KernelScoredClaim,
    UnsupportedKernelContractVersion,
    require_supported_contract_version,
)


@dataclass(frozen=True, slots=True)
class KernelJudgment:
    """App-domain projection of one deterministic kernel output.

    No LLM text, no narrative, no facts/market_judgment, no limits/could_flip,
    no insight/hypothesis_ledger.  Evidence is not deduplicated.
    """

    coin: str
    query: str
    direction: str
    confidence: float
    raw_confidence: float
    abstain: bool
    decision_state: str
    reason_codes: tuple[str, ...]
    supporting_count: int
    independent_sources: int
    kernel_contract_version: str
    evidence: tuple[Evidence, ...]
    supporting_evidence: tuple[Evidence, ...]
    contrarian_texts: tuple[str, ...]
    key_basis: tuple[BasisItem, ...]


def _kernel_scored_to_evidence(
    sc: KernelScoredClaim, *, related: str
) -> Evidence:
    doc = sc.claim.document
    trust_components = {k: round(v, 3) for k, v in sc.components}
    rep_mode = None
    if sc.reputation_trace is not None:
        trace = sc.reputation_trace
        trust_components["reputation_prior"] = round(trace.prior, 3)
        trust_components["reputation_final"] = round(trace.final, 3)
        trust_components["reputation_agree_n"] = trace.agree_n
        trust_components["reputation_contradict_n"] = trace.contradict_n
        trust_components["reputation_iterations_run"] = trace.iterations_run
        rep_mode = trace.mode
    return Evidence(
        source=doc.source,
        fetched_at=iso_utc(doc.timestamp),
        content_reference=sc.claim.text[:120],
        related_claim=related,
        source_url=doc.url,
        kind=doc.kind,
        trust=round(sc.trust, 3),
        trust_components=trust_components,
        flags=list(sc.manip_flags),
        info_flags=list(sc.info_flags),
        reputation_mode=rep_mode,
    )


def project(kernel_output: KernelOutput, *, coin: str) -> KernelJudgment:
    """Project a validated KernelOutput into the app domain.

    Raises:
        UnsupportedKernelContractVersion: if *kernel_output* carries an
            unknown contract version.
    """
    require_supported_contract_version(kernel_output.contract_version)

    evidence: list[Evidence] = []
    supporting_evidence: list[Evidence] = []
    contrarian_texts: list[str] = []
    key_basis: list[BasisItem] = []

    judgment_tag = f"{coin} \u5e02\u5834\u5224\u65b7"

    for sc in kernel_output.scored_claims:
        ev = _kernel_scored_to_evidence(sc, related=judgment_tag)
        evidence.append(ev)

    supporting_ids = {id(sc) for sc in kernel_output.supporting}
    for idx, sc in enumerate(kernel_output.scored_claims):
        ev = evidence[idx]
        if id(sc) in supporting_ids:
            supporting_evidence.append(ev)
            key_basis.append(
                BasisItem(
                    claim=sc.claim.text,
                    explanation=f"{sc.claim.document.source} "
                    f"({sc.claim.document.kind}) trust={sc.trust:.3f}",
                    evidence_idx=[len(supporting_evidence) - 1],
                )
            )
        else:
            contrarian_texts.append(sc.claim.text)

    return KernelJudgment(
        coin=coin,
        query=kernel_output.query,
        direction=kernel_output.direction,
        confidence=kernel_output.confidence,
        raw_confidence=kernel_output.trust_score,
        abstain=kernel_output.abstain,
        decision_state=kernel_output.decision_state,
        reason_codes=kernel_output.reason_codes,
        supporting_count=kernel_output.supporting_count,
        independent_sources=kernel_output.independent_sources,
        kernel_contract_version=kernel_output.contract_version,
        evidence=tuple(evidence),
        supporting_evidence=tuple(supporting_evidence),
        contrarian_texts=tuple(contrarian_texts),
        key_basis=tuple(key_basis),
    )
