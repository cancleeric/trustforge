"""把 TrustedBrief 交給 Bedrock 生成市場分析，並強制引用 claim id（帶溯源）。"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..bedrock import BedrockClient
from ..trust.scoring import TrustedBrief

SYSTEM = (
    "你是加密市場分析助理。你只能根據提供的「已信任加權主張」作答，"
    "每個結論都必須以 [claim_id] 標註其依據。明確指出信心程度與反方證據，"
    "不提供投資建議，只提供可查證的分析。"
)


@dataclass
class Analysis:
    query: str
    narrative: str
    confidence: float
    provenance: list[dict] = field(default_factory=list)
    contrarian: list[str] = field(default_factory=list)


def _format_brief(brief: TrustedBrief) -> str:
    lines = [f"Query: {brief.query}", "", "支撐主張（已信任加權）："]
    for sc in brief.supporting:
        lines.append(f"  [{sc.claim.id}] (trust={sc.trust:.2f}, {sc.claim.doc.kind}) {sc.claim.text}")
    if brief.contrarian:
        lines.append("\n反方 / 低信任主張：")
        for sc in brief.contrarian:
            lines.append(f"  [{sc.claim.id}] (trust={sc.trust:.2f}) {sc.claim.text}")
    return "\n".join(lines)


def analyze(brief: TrustedBrief, client: BedrockClient | None = None) -> Analysis:
    client = client or BedrockClient(offline=True)
    narrative = client.complete(system=SYSTEM, prompt=_format_brief(brief))
    return Analysis(
        query=brief.query,
        narrative=narrative,
        confidence=brief.confidence,
        provenance=brief.provenance(),
        contrarian=[sc.claim.text for sc in brief.contrarian],
    )
