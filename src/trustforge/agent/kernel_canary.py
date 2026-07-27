"""Kernel Deploy Canary (P0-7 / Issue #733).

Controls limited-ratio kernel activation for production safety.
Canary is always bounded: auto-stop on error threshold, manual promotion
required before full cutover.

Architecture:
  - `KERNEL_CANARY_RATIO` env var (float 0-1, default 0) controls ratio
  - Ratio 0 = no kernel activation (legacy only)
  - Ratio 0.05 = 5% of requests use kernel
  - Ratio 1.0 = full kernel (post-manual-promotion)
  - Deterministic request bucket: SHA-256(coin + query + timestamp) mod 10000

Canary stop conditions:
  - Exceeding MAX_CANARY_ERRORS consecutive kernel failures
  - Kernel output parsing failure rate exceeding MAX_FAILURE_RATE
  - Manual operator stop threshold

Manual promotion steps:
  1. Set KERNEL_CANARY_RATIO=0.05, deploy
  2. Monitor canary for > CANARY_MIN_DURATION_MIN minutes
  3. Verify parity metrics within PROMOTION_PARITY_RATE_MIN
  4. Manual operator promotes by setting KERNEL_CANARY_RATIO=1.0

Rollback:
  - Set KERNEL_CANARY_RATIO=0, deploy
  - Previous approved A artifact must be verifiable per P0-4 drill

References:
  - P0-4 rollback drill: docs/drills/DRILL-730-2026-07-27.md
  - #728 artifact identity: src/trustforge/release_manifest.py
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os

# ---------------------------------------------------------------------------
# Canary config — read at call time with validation, crash safely
# ---------------------------------------------------------------------------

def _canary_ratio() -> float:
    raw = os.getenv("KERNEL_CANARY_RATIO", "0")
    try:
        val = float(raw)
    except (ValueError, TypeError):
        logging.error("KERNEL_CANARY_RATIO=%r is not a number; using 0", raw)
        return 0.0
    if not math.isfinite(val):
        logging.error("KERNEL_CANARY_RATIO=%r is not finite; using 0", raw)
        return 0.0
    if val < 0.0 or val > 1.0:
        logging.error(
            "KERNEL_CANARY_RATIO=%.3f is out of range [0,1]; clamping to [0,1]",
            val,
        )
        val = max(0.0, min(1.0, val))
    return val

def _canary_min_duration() -> int:
    raw = os.getenv("CANARY_MIN_DURATION_MIN", "30")
    try:
        return int(raw)
    except (ValueError, TypeError):
        logging.error("CANARY_MIN_DURATION_MIN=%r is not an integer; using 30", raw)
        return 30

def _max_canary_errors() -> int:
    raw = os.getenv("MAX_CANARY_ERRORS", "5")
    try:
        return int(raw)
    except (ValueError, TypeError):
        logging.error("MAX_CANARY_ERRORS=%r is not an integer; using 5", raw)
        return 5

def _max_failure_rate() -> float:
    raw = os.getenv("MAX_FAILURE_RATE", "0.01")
    try:
        return float(raw)
    except (ValueError, TypeError):
        logging.error("MAX_FAILURE_RATE=%r is not a number; using 0.01", raw)
        return 0.01


def canary_active() -> bool:
    """True if canary is deployed (0 < ratio < 1)."""
    r = _canary_ratio()
    return 0.0 < r < 1.0


def full_kernel_active() -> bool:
    """True if kernel is fully promoted (ratio == 1.0)."""
    return _canary_ratio() >= 1.0


def should_use_kernel(*, coin: str, query: str, ts: str) -> bool:
    """Deterministic canary bucket: true if this request falls in the canary ratio.

    Uses SHA-256 hash of the request identity for deterministic, repeatable
    bucketing across process restarts.  Same request always gets same path
    (eliminates flicker for debugging).

    Uses JSON serialization (sorted keys) to avoid delimiter collisions.
    """
    ratio = _canary_ratio()
    if ratio >= 1.0:
        return True
    if ratio <= 0.0:
        return False
    payload = json.dumps({"coin": coin, "query": query, "ts": ts}, sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256(payload.encode()).hexdigest()
    bucket = int(digest[:8], 16) % 10000
    return bucket < int(ratio * 10000)


# ---------------------------------------------------------------------------
# Canary state machine
# ---------------------------------------------------------------------------

class CanaryState:
    """In-process canary counter — resets on restart (fail-closed)."""

    def __init__(self) -> None:
        self.total_canary_requests: int = 0
        self.total_canary_successes: int = 0
        self.consecutive_errors: int = 0
        self.active: bool = False

    @property
    def error_rate(self) -> float:
        if self.total_canary_requests == 0:
            return 0.0
        return 1.0 - (self.total_canary_successes / self.total_canary_requests)

    @property
    def should_stop(self) -> bool:
        """True if canary has exceeded error thresholds.  Pure property — no side effects."""
        if self.consecutive_errors >= _max_canary_errors():
            return True
        if (
            self.total_canary_requests >= 100
            and self.error_rate > _max_failure_rate()
        ):
            return True
        return False

    def check_and_log_stop(self) -> bool:
        """Check if canary should stop, with CRITICAL logging on first detection.

        Call this from the canary orchestration loop; the property is pure
        and safe for repeated polling (e.g., health endpoints).
        """
        if not self.should_stop:
            return False
        logging.critical(
            "Kernel canary stop triggered: consecutive_errors=%d, error_rate=%.2f, total_requests=%d",
            self.consecutive_errors, self.error_rate, self.total_canary_requests,
        )
        return True

    def record_success(self) -> None:
        self.total_canary_requests += 1
        self.total_canary_successes += 1
        self.consecutive_errors = 0

    def record_error(self) -> None:
        self.total_canary_requests += 1
        self.consecutive_errors += 1

    def diagnostics(self) -> dict:
        return {
            "canary_active": canary_active() or full_kernel_active(),
            "canary_ratio": _canary_ratio(),
            "total_canary_requests": self.total_canary_requests,
            "total_canary_successes": self.total_canary_successes,
            "consecutive_errors": self.consecutive_errors,
            "error_rate": round(self.error_rate, 4),
            "should_stop": self.should_stop,
        }


# Process-local singleton
_CANARY_STATE: CanaryState | None = None


def get_canary_state() -> CanaryState:
    global _CANARY_STATE
    if _CANARY_STATE is None:
        _CANARY_STATE = CanaryState()
    return _CANARY_STATE
