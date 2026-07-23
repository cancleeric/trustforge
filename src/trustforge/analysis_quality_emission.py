"""Narrow, backend-agnostic emission boundary for analysis-quality events."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from .analysis_quality_event import build_analysis_quality_event
from .learning_event_contract import LearningEvent, LearningEventError

AppendStatus = Literal["created", "idempotent", "conflict", "error"]


@runtime_checkable
class AnalysisQualityAppendSink(Protocol):
    """Minimal append-only sink contract; no backend is selected here."""

    def append(self, event: LearningEvent) -> AppendStatus: ...


@dataclass(frozen=True)
class EmissionResult:
    status: Literal["created", "idempotent"]
    identity: str


class AnalysisQualityEmissionError(LearningEventError):
    """Raised when a sink does not durably accept the canonical event."""


class AnalysisQualityConflictError(AnalysisQualityEmissionError):
    """Raised when an existing identity has different canonical bytes."""


def emit_analysis_quality_event(
    snapshot: dict[str, object],
    *,
    trusted_tenant_id: str,
    trusted_pit: dict[str, object],
    trusted_provenance: dict[str, object],
    sink: AnalysisQualityAppendSink,
) -> EmissionResult:
    """Build then append once; never convert storage failure into success."""

    event = build_analysis_quality_event(
        snapshot,
        trusted_tenant_id=trusted_tenant_id,
        trusted_pit=trusted_pit,
        trusted_provenance=trusted_provenance,
    )
    status = sink.append(event)
    if status in {"created", "idempotent"}:
        return EmissionResult(status=status, identity=event.identity)
    if status == "conflict":
        raise AnalysisQualityConflictError(
            "analysis-quality identity already exists with different canonical content"
        )
    if status == "error":
        raise AnalysisQualityEmissionError("analysis-quality append sink reported an error")
    raise AnalysisQualityEmissionError("analysis-quality append sink returned an invalid status")
