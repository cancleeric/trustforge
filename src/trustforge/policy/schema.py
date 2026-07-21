"""Typed policy schemas for each outer-skill family.

Each family has a frozen dataclass with validated defaults.  These are the
**only** knobs outer skills may tune — anything outside these fields is
rejected by the guard layer.

Design notes:
    - frozen=True ensures policies are immutable after compilation
    - Default values represent safe baselines; approved artifacts override them
    - FAMILY_SCHEMA maps family name → dataclass type (used by compiler/loader)
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourcePolicy:
    """Controls for the ingestion/source layer."""
    timeout_sec: int = 30
    max_concurrent: int = 5
    retry_limit: int = 2
    fallback_order: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AnalysisPolicy:
    """Controls for claim extraction and analysis orchestration."""
    claim_extraction_budget: int = 40
    contrarian_search_enabled: bool = True
    max_llm_calls: int = 8


@dataclass(frozen=True)
class ReportPolicy:
    """Controls for report generation and delivery."""
    language: str = "zh-TW"
    max_sections: int = 6
    include_contrarian: bool = True


@dataclass(frozen=True)
class EvaluationPolicy:
    """Controls for quality evaluation and replay gate."""
    min_pass_score: float = 0.6
    replay_sample_size: int = 5


@dataclass(frozen=True)
class ImprovementPolicy:
    """Controls for the improvement diagnostics layer."""
    proposal_limit: int = 3
    auto_stage: bool = False  # stage only, never auto-approve


FAMILY_SCHEMA: dict[str, type] = {
    "source": SourcePolicy,
    "analysis": AnalysisPolicy,
    "report": ReportPolicy,
    "evaluation": EvaluationPolicy,
    "improvement": ImprovementPolicy,
}
