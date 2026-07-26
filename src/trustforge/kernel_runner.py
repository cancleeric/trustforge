"""Formal pipeline entry point: all runs go through run_kernel().

Unifies the ad-hoc pipeline paths into a single composition-root-aware
entry point. Each formal run receives a FrozenRunSpec and returns a
RunResult.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from trustforge.composition_root import AppContext


@dataclass(frozen=True)
class FrozenRunSpec:
    """Immutable specification for a single formal run."""
    coin: str
    query: str
    mode: str = "offline"
    extra: dict[str, Any] | None = None


@dataclass(frozen=True)
class RunResult:
    """Result of a single formal run."""
    coin: str
    direction: str
    trust_score: float
    evidence_count: int
    run_id: str


def run_kernel(spec: FrozenRunSpec) -> RunResult:
    """Run the TrustForge analysis kernel through the composition root.

    This is the *single* entry point for all formal pipeline runs.
    Ad-hoc callers should build a FrozenRunSpec and call this function.
    """
    ctx = AppContext.from_env()
    # Route to appropriate backend based on runtime mode
    if ctx.is_live:
        return _run_live(spec, ctx)
    return _run_offline(spec, ctx)


def _run_offline(spec: FrozenRunSpec, ctx: AppContext) -> RunResult:
    """Offline path: replay snapshot without Bedrock."""
    # Stub — actual implementation delegates to existing offline pipeline
    return RunResult(
        coin=spec.coin,
        direction="offline",
        trust_score=0.5,
        evidence_count=0,
        run_id="run-offline",
    )


def _run_live(spec: FrozenRunSpec, ctx: AppContext) -> RunResult:
    """Live path: full agent pipeline with Bedrock."""
    # Stub — actual implementation delegates to agent/orchestrator.py
    return RunResult(
        coin=spec.coin,
        direction="live",
        trust_score=0.7,
        evidence_count=1,
        run_id="run-live",
    )
