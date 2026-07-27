"""Shadow parity comparison and observation diagnostics (Issue #732).

Core dual-track pipeline always runs both legacy and kernel scoring in parallel.
Legacy continues to be the source of truth for `build_report`.  The kernel
runs in shadow and is never consumed by the active report path.  Promotion is
an explicit operator/release concern; observation code cannot activate or
revoke a release.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from trustforge_core import KernelOutput

# ---------------------------------------------------------------------------
# Threshold constants — tuned per CPO plan (Issue #732, 5 decisions)
# ---------------------------------------------------------------------------

# Minimum shadow runs accumulated before observation readiness.
SHADOW_WINDOW = 30

# Maximum allowed absolute delta on confidence (kernel vs legacy).
PARITY_CONFIDENCE_DELTA_MAX = 0.05

# Maximum allowed absolute delta on trust_score (kernel vs legacy).
PARITY_TRUST_DELTA_MAX = 0.05

# Minimum Jaccard overlap ratio between supporting claim ID sets.
PARITY_SUPPORTING_JACCARD_MIN = 0.70

# Minimum fraction of window runs that must pass parity to be eligible for
# operator review.
OBSERVATION_PARITY_RATE_MIN = 0.90

# Scenario coverage gating: need at least this many distinct coins and
# question types in the accumulated observation window.
MIN_COIN_COVERAGE = 3
MIN_QTYPE_COVERAGE = 2

# Consecutive blocking failure gating: if the last N runs all fail parity,
# block readiness even if the overall rate is acceptable (indicates a
# regression pattern, not noise).
BLOCKING_STREAK_MAX = 3


# ---------------------------------------------------------------------------
# Parity result record
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ShadowParityResult:
    """Immutable record of one kernel-vs-legacy comparison."""

    coin: str
    qtype_value: str
    legacy_confidence: float
    kernel_confidence: float
    legacy_supporting_ids: frozenset[str]
    kernel_supporting_ids: frozenset[str]
    legacy_direction: str | None
    kernel_direction: str | None
    legacy_abstain: bool
    kernel_abstain: bool
    delta_confidence: float
    delta_trust: float
    supporting_jaccard: float
    direction_match: bool
    decision_state_match: bool
    parity_passed: bool
    blocking_reasons: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Process-local observation diagnostics
# ---------------------------------------------------------------------------

@dataclass
class ShadowAccumulator:
    """Rolling-window accumulator for shadow-run parity statistics.

    Process-local singleton (same pattern as ``budget_guard``).  Not persisted
    across process restarts — a restart resets accumulated shadow history and
    defers observation readiness until the window refills.  It is diagnostics
    only and cannot authorize any release operation.
    """

    _window_size: int = field(default=SHADOW_WINDOW, repr=False)
    _runs: list[ShadowParityResult] = field(default_factory=list, repr=False)
    # Prometheus-style counters (monotonically increasing across resets)
    _total_runs: int = field(default=0, repr=False)
    _total_passed: int = field(default=0, repr=False)

    # ------------------------------------------------------------------
    # public read API
    # ------------------------------------------------------------------
    @property
    def total_runs(self) -> int:
        return self._total_runs

    @property
    def window_runs(self) -> int:
        return len(self._runs)

    @property
    def window_passed(self) -> int:
        return sum(1 for r in self._runs if r.parity_passed)

    @property
    def parity_rate(self) -> float:
        """Fraction of window runs that passed parity (0.0–1.0)."""
        if not self._runs:
            return 0.0
        return self.window_passed / len(self._runs)

    @property
    def observation_eligible(self) -> bool:
        """Non-authoritative indication that the diagnostic window is complete."""
        if len(self._runs) < self._window_size:
            return False
        if self.parity_rate < OBSERVATION_PARITY_RATE_MIN:
            return False
        coins = {r.coin for r in self._runs}
        if len(coins) < MIN_COIN_COVERAGE:
            return False
        qtypes = {r.qtype_value for r in self._runs}
        if len(qtypes) < MIN_QTYPE_COVERAGE:
            return False
        # Blocking streak gate
        streak = 0
        for r in reversed(self._runs):
            if not r.parity_passed:
                streak += 1
            else:
                break
        if streak >= BLOCKING_STREAK_MAX:
            return False
        return True

    # ------------------------------------------------------------------
    # mutation API
    # ------------------------------------------------------------------
    def record(self, result: ShadowParityResult) -> None:
        """Append one result and maintain the diagnostic rolling window."""
        self._runs.append(result)
        if len(self._runs) > self._window_size:
            self._runs.pop(0)
        self._total_runs += 1
        if result.parity_passed:
            self._total_passed += 1

    def reset(self) -> None:
        """Clear process-local diagnostics; this has no release effect."""
        self._runs.clear()

    # ------------------------------------------------------------------
    # diagnostics
    # ------------------------------------------------------------------
    def diagnostics(self) -> dict[str, Any]:
        """Snapshot for logging / observability."""
        return {
            "total_runs": self._total_runs,
            "window_runs": len(self._runs),
            "window_passed": self.window_passed,
            "parity_rate": round(self.parity_rate, 3),
            "observation_eligible": self.observation_eligible,
            "coins_seen": sorted({r.coin for r in self._runs}),
            "qtypes_seen": sorted({r.qtype_value for r in self._runs}),
        }


# Process-local singleton (same pattern as budget_guard._UNLEDGERED_SPEND)
_SHADOW_ACC: ShadowAccumulator | None = None


def _get_shadow_acc() -> ShadowAccumulator:
    global _SHADOW_ACC
    if _SHADOW_ACC is None:
        _SHADOW_ACC = ShadowAccumulator()
    return _SHADOW_ACC


# ---------------------------------------------------------------------------
# Comparison logic
# ---------------------------------------------------------------------------

def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def compare_outputs(
    kernel: KernelOutput,
    *,
    legacy_confidence: float,
    legacy_trust_raw: float,
    legacy_scored: list,
    coin: str,
    qtype_value: str,
) -> ShadowParityResult:
    """Compare one kernel output against the legacy pipeline result.

    Parameters
    ----------
    kernel:
        The ``KernelOutput`` from ``run_kernel()``.
    legacy_confidence:
        ``brief.calibrated_confidence`` (calibrated) from legacy.
    legacy_trust_raw:
        ``brief.confidence`` (raw aggregate trust) from legacy.
    legacy_scored:
        The ``list[ScoredClaim]`` from legacy ``score()``.
    coin:
        Coin symbol (for scenario coverage tracking).
    qtype_value:
        ``QuestionType.value`` (for scenario coverage tracking).

    Returns
    -------
    ShadowParityResult
        Immutable comparison record, including ``parity_passed`` flag.
    """
    # Build legacy brief-equivalent from scored claims (best-effort)

    legacy_supporting_ids = frozenset(
        sc.claim.id for sc in legacy_scored
        if sc.trust >= 0.5
    )
    kernel_supporting_ids = frozenset(
        item.claim.id for item in kernel.supporting
    )

    # Derive legacy abstain & direction from legacy scored claims
    legacy_abstain = legacy_confidence < 0.35  # same threshold as orchestrator
    legacy_direction = _derive_direction(legacy_scored)

    # CRITICAL C1: NaN/Inf finite-value guards
    blocking: list[str] = []
    if not math.isfinite(kernel.confidence):
        blocking.append(f"kernel.confidence is non-finite ({kernel.confidence})")
    if not math.isfinite(kernel.trust_score):
        blocking.append(f"kernel.trust_score is non-finite ({kernel.trust_score})")
    if not math.isfinite(legacy_confidence):
        blocking.append(f"legacy_confidence is non-finite ({legacy_confidence})")
    if not math.isfinite(legacy_trust_raw):
        blocking.append(f"legacy_trust_raw is non-finite ({legacy_trust_raw})")

    supporting_jaccard = _jaccard(legacy_supporting_ids, kernel_supporting_ids)
    direction_match = kernel.direction == legacy_direction or (
        kernel.direction in ("bullish", "bearish", "neutral")
        and legacy_direction in ("偏多", "偏空", "中性", None)
        and _directions_equivalent(kernel.direction, legacy_direction)
    )
    decision_state_match = (
        kernel.abstain == legacy_abstain
        and kernel.decision_state == ("abstain" if legacy_abstain else "normal")
    ) or (kernel.abstain and legacy_abstain)

    # If any non-finite values detected, fail parity immediately.
    if blocking:
        return ShadowParityResult(
            coin=coin, qtype_value=qtype_value,
            legacy_confidence=legacy_confidence, kernel_confidence=kernel.confidence,
            legacy_supporting_ids=legacy_supporting_ids, kernel_supporting_ids=kernel_supporting_ids,
            legacy_direction=legacy_direction, kernel_direction=kernel.direction,
            legacy_abstain=legacy_abstain, kernel_abstain=kernel.abstain,
            delta_confidence=float('nan'), delta_trust=float('nan'),
            supporting_jaccard=supporting_jaccard,
            direction_match=direction_match, decision_state_match=decision_state_match,
            parity_passed=False, blocking_reasons=tuple(blocking),
        )

    delta_confidence = abs(kernel.confidence - legacy_confidence)
    delta_trust = abs(kernel.trust_score - legacy_trust_raw)

    # Determine blocking reasons
    blocking = []
    if delta_confidence > PARITY_CONFIDENCE_DELTA_MAX:
        blocking.append(
            f"confidence_delta={delta_confidence:.3f}>{PARITY_CONFIDENCE_DELTA_MAX}"
        )
    if delta_trust > PARITY_TRUST_DELTA_MAX:
        blocking.append(
            f"trust_delta={delta_trust:.3f}>{PARITY_TRUST_DELTA_MAX}"
        )
    if supporting_jaccard < PARITY_SUPPORTING_JACCARD_MIN:
        blocking.append(
            f"supporting_jaccard={supporting_jaccard:.3f}<{PARITY_SUPPORTING_JACCARD_MIN}"
        )
    if not direction_match:
        blocking.append(
            f"direction_mismatch(kernel={kernel.direction},legacy={legacy_direction})"
        )
    if not decision_state_match:
        blocking.append(
            f"decision_mismatch(kernel_abstain={kernel.abstain},legacy_abstain={legacy_abstain})"
        )

    parity_passed = len(blocking) == 0

    return ShadowParityResult(
        coin=coin,
        qtype_value=qtype_value,
        legacy_confidence=legacy_confidence,
        kernel_confidence=kernel.confidence,
        legacy_supporting_ids=legacy_supporting_ids,
        kernel_supporting_ids=kernel_supporting_ids,
        legacy_direction=legacy_direction,
        kernel_direction=kernel.direction,
        legacy_abstain=legacy_abstain,
        kernel_abstain=kernel.abstain,
        delta_confidence=delta_confidence,
        delta_trust=delta_trust,
        supporting_jaccard=supporting_jaccard,
        direction_match=direction_match,
        decision_state_match=decision_state_match,
        parity_passed=parity_passed,
        blocking_reasons=tuple(blocking),
    )


def _derive_direction(scored: list) -> str | None:
    """Reconstruct direction from legacy ScoredClaim list (same heuristic as
    ``build_report`` / ``_direction``).
    """
    if not scored:
        return None
    # Use claim.direction from trust>=0.5 claims, same as _dominant
    weights: dict[str, float] = {}
    total = 0.0
    for sc in scored:
        if sc.trust < 0.5:
            continue
        d = sc.claim.direction
        if not d:
            continue
        weights[d] = weights.get(d, 0.0) + sc.trust
        total += sc.trust
    if not total:
        return None
    best_dir = max(weights, key=lambda k: weights[k])
    return best_dir if weights[best_dir] >= 0.3 * total else "neutral"


_DIRECTION_MAP: dict[tuple[str | None, str | None], bool] = {
    ("bullish", "偏多"): True,
    ("bearish", "偏空"): True,
    ("neutral", "中性"): True,
    ("neutral", None): True,
    ("neutral", "不明"): True,
}


def _directions_equivalent(kernel_dir: str | None, legacy_dir: str | None) -> bool:
    if kernel_dir == legacy_dir:
        return True
    return _DIRECTION_MAP.get((kernel_dir, legacy_dir), False)


# ---------------------------------------------------------------------------
# Promotion API used by orchestrator
# ---------------------------------------------------------------------------

def record_shadow_run(
    kernel: KernelOutput,
    *,
    legacy_confidence: float,
    legacy_trust_raw: float,
    legacy_scored: list,
    coin: str,
    qtype_value: str,
) -> dict[str, Any]:
    """One-shot helper: compare + accumulate + return diagnostics.

    This is the entry point called by ``run_agent_pipeline`` after every
    ``run_kernel()`` invocation.
    """
    result = compare_outputs(
        kernel=kernel,
        legacy_confidence=legacy_confidence,
        legacy_trust_raw=legacy_trust_raw,
        legacy_scored=legacy_scored,
        coin=coin,
        qtype_value=qtype_value,
    )
    return record_shadow_result(result)


def record_shadow_result(result: ShadowParityResult) -> dict[str, Any]:
    """Record one already-bounded comparison result."""
    acc = _get_shadow_acc()
    acc.record(result)
    diag = acc.diagnostics()
    diag["last_parity_passed"] = result.parity_passed
    diag["last_blocking_reasons"] = result.blocking_reasons
    return diag


def shadow_diagnostics() -> dict[str, Any]:
    return _get_shadow_acc().diagnostics()


def reset_shadow_accumulator() -> None:
    """Manual reset (e.g. after hot-patch or rollback)."""
    _get_shadow_acc().reset()
