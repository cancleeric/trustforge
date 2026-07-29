"""Issue #879: limited canary controller + stop monitor (framework only).

The intrinsic promotion gate (G, #875) currently emits ``BLOCK`` for the real
dataset.  While G=BLOCK the promote path is permanently refused: this module
provides the *framework* that would govern a limited canary plus the live stop
monitor, but it never auto-promotes.  Promotion can only occur through
:class:`PromotionAuthorizationGate`, which requires (1) a CEO signature,
(2) ``g_receipt.decision == PASS``, and (3) a clean monitor — an impossible
conjunction while G=BLOCK.

Components
----------
* :class:`CanaryScope` — allowlist + non-production target assertion.
* :class:`CanaryStopMonitor` — 5-signal live breach detector that reuses G's
  :class:`IntrinsicPromotionPolicy` thresholds and :class:`IntrinsicPromotionReason`
  codes (it does not reimplement the gate).
* :class:`PromotionAuthorizationGate` — the 3-door gate; always refuses while
  G=BLOCK.
* :class:`PostPromotionMonitor` — skeleton; only constructible at
  ``phase == "promoted"`` (unreachable while G=BLOCK).
* :class:`CanaryController` — ``start_canary`` / ``observe`` / ``route_back``;
  **no** ``promote()`` automatic method.

Hard constraints honored
------------------------
* :data:`trustforge.deployment_evidence.REQUIRED_GATES` is **not** extended.
  The controller's disposition artifact is controller-emitted evidence, not a
  release-evidence gate.
* Reuses G's policy / reason (imported, not rewritten).
* Reuses J's (#877) rollback signing machinery via *injected* signers
  (:class:`CanaryTransitionSigners`); no copy of J's helpers lives here.
* No automatic promotion code path.
* Flag-off byte parity: the production runtime must not import this module
  unless ``TRUSTFORGE_CANARY_CONTROL_ENABLED`` is set; only the hermetic drill
  script and tests exercise it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from trustforge.asset_intrinsic_candidate import CandidateShadow
from trustforge.asset_intrinsic_promotion import (
    IntrinsicPromotionDecision,
    IntrinsicPromotionPolicy,
    IntrinsicPromotionReason,
    IntrinsicPromotionReceipt,
    receipt_id as _g_receipt_id,
)
from trustforge.deployment_control import (
    ActivationCompletionReceipt,
    DeploymentAuthorization,
    DeploymentControlLedger,
)

CANARY_CONTROL_FLAG = "TRUSTFORGE_CANARY_CONTROL_ENABLED"
CANARY_DISPOSITION_SCHEMA = "trustforge.canary-disposition/v1"
CANARY_ROUTE_BACK_SCHEMA = "trustforge.canary-route-back-receipt/v1"
CANARY_ROUTE_BACK_DOMAIN = b"trustforge.canary.route-back.v1\x00"
PROMOTION_REQUEST_DOMAIN = b"trustforge.canary.promotion-request.v1\x00"
PROMOTION_REQUEST_VERSION = "trustforge.canary-promotion-request/v1"

SIGNAL_SCORE_SPREAD = "score_spread"
SIGNAL_FLIP = "flip"
SIGNAL_COVERAGE = "coverage"
SIGNAL_MISSINGNESS = "missingness"
SIGNAL_SOURCE_CONCENTRATION = "source_concentration"

CANARY_STOP_SIGNALS = frozenset(
    {
        SIGNAL_SCORE_SPREAD,
        SIGNAL_FLIP,
        SIGNAL_COVERAGE,
        SIGNAL_MISSINGNESS,
        SIGNAL_SOURCE_CONCENTRATION,
    }
)

_CANARY_SIGNAL_TO_G_REASON: Mapping[str, IntrinsicPromotionReason] = {
    SIGNAL_SCORE_SPREAD: IntrinsicPromotionReason.DELTA_EXCEEDS_NON_INFERIORITY_MARGIN,
    SIGNAL_FLIP: IntrinsicPromotionReason.DIRECTION_OR_DECISION_FLIP,
    SIGNAL_COVERAGE: IntrinsicPromotionReason.COVERAGE_DISPARITY,
    SIGNAL_MISSINGNESS: IntrinsicPromotionReason.MISSINGNESS_RATE_EXCEEDED,
    SIGNAL_SOURCE_CONCENTRATION: IntrinsicPromotionReason.SINGLE_SOURCE_DEPENDENCY,
}

_PRODUCTION_TARGET_TOKENS = ("production", "trustforge-production")


class CanaryControlError(RuntimeError):
    """A canary scope, monitor, gate, or controller invariant was violated."""


def map_ac5_signal_to_g_reason(signal: str) -> IntrinsicPromotionReason:
    """Map one AC5 live-breach signal to its G reason code.

    The mapping is closed: every canary stop signal projects onto exactly one
    :class:`IntrinsicPromotionReason` so a live stop is provably consistent with
    the intrinsic gate's verdict vocabulary.  Unknown signals fail closed.
    """
    if signal not in _CANARY_SIGNAL_TO_G_REASON:
        raise CanaryControlError(f"unknown canary stop signal: {signal!r}")
    return _CANARY_SIGNAL_TO_G_REASON[signal]


# ---------------------------------------------------------------------------
# D1.a: CanaryScope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CanaryScope:
    """The closed world a limited canary may operate in.

    ``allowlist`` is the exhaustive set of subjects (asset ids) permitted to
    enter the canary; anything outside falls back to the active release (A).
    ``target`` must be a non-production sandbox target — a production target is
    rejected at construction so the controller can never drive a real host.
    """

    allowlist: frozenset[str]
    target: str

    def __post_init__(self) -> None:
        if not isinstance(self.allowlist, frozenset) or not self.allowlist:
            raise CanaryControlError("canary allowlist must be a non-empty frozenset")
        for subject in self.allowlist:
            if not isinstance(subject, str) or not subject:
                raise CanaryControlError("canary allowlist subject must be non-empty str")
        if not isinstance(self.target, str) or not self.target:
            raise CanaryControlError("canary target must be a non-empty string")
        lowered = self.target.strip().lower()
        for token in _PRODUCTION_TARGET_TOKENS:
            if lowered == token:
                raise CanaryControlError(
                    "canary target must not be a production target"
                )
        if "canary" not in lowered and "sandbox" not in lowered:
            raise CanaryControlError(
                "canary target must carry a canary/sandbox marker"
            )

    def eligible(self, subject: str) -> bool:
        return subject in self.allowlist


# ---------------------------------------------------------------------------
# D1.b: CanaryStopMonitor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CanaryObservation:
    """One live observation projected from a CandidateShadow window element.

    Carries the shadow diff plus the three measured signals that
    :class:`CandidateShadow` does not expose (coverage disparity, missingness
    rate, dominant source-family share).  All five AC5 signals are derivable.
    """

    subject: str
    shadow: CandidateShadow
    coverage_disparity: int
    missingness_rate: float
    source_concentration: float

    def __post_init__(self) -> None:
        if not isinstance(self.subject, str) or not self.subject:
            raise CanaryControlError("observation subject must be a non-empty str")
        if isinstance(self.coverage_disparity, bool) or not isinstance(
            self.coverage_disparity, int
        ) or self.coverage_disparity < 0:
            raise CanaryControlError("coverage_disparity must be a non-negative int")
        for name in ("missingness_rate", "source_concentration"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CanaryControlError(f"{name} must be numeric")
            if not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
                raise CanaryControlError(f"{name} must be finite and within [0, 1]")


@dataclass(frozen=True)
class CanaryStopReason:
    """A live breach projected onto G's reason vocabulary + bound to G's receipt."""

    signal: str
    g_reason: IntrinsicPromotionReason
    measured_value: float
    threshold: float
    g_receipt_id: str
    subject: str


class CanaryStopMonitor:
    """Consume a CandidateShadow window; detect the 5 AC5 live-breach signals.

    Thresholds are reused verbatim from G's :class:`IntrinsicPromotionPolicy`
    (non-inferiority margin, decision-flip, coverage disparity, missingness,
    single-source).  The monitor does **not** reimplement the gate: it projects
    each live observation onto the same thresholds and, on the first breach,
    records a :class:`CanaryStopReason` bound to the G gate receipt id so a live
    stop is provably consistent with the intrinsic verdict.
    """

    def __init__(
        self,
        policy: IntrinsicPromotionPolicy,
        *,
        g_receipt_id: str,
        g_decision: IntrinsicPromotionDecision,
    ) -> None:
        if not isinstance(policy, IntrinsicPromotionPolicy):
            raise CanaryControlError("policy must be IntrinsicPromotionPolicy")
        if not isinstance(g_receipt_id, str) or not g_receipt_id.startswith("sha256:"):
            raise CanaryControlError("g_receipt_id must be a sha256 digest")
        if not isinstance(g_decision, IntrinsicPromotionDecision):
            raise CanaryControlError("g_decision must be IntrinsicPromotionDecision")
        self._policy = policy
        self._g_receipt_id = g_receipt_id
        self._g_decision = g_decision
        self._observations: list[CanaryObservation] = []
        self._stop_reason: CanaryStopReason | None = None

    @property
    def g_receipt_id(self) -> str:
        return self._g_receipt_id

    @property
    def g_decision(self) -> IntrinsicPromotionDecision:
        return self._g_decision

    @property
    def stop_reason(self) -> CanaryStopReason | None:
        return self._stop_reason

    @property
    def clean(self) -> bool:
        return self._stop_reason is None

    @property
    def observation_count(self) -> int:
        return len(self._observations)

    def observe(self, observation: CanaryObservation) -> CanaryStopReason | None:
        if self._stop_reason is not None:
            return self._stop_reason
        if not isinstance(observation, CanaryObservation):
            raise CanaryControlError("observation must be CanaryObservation")
        self._observations.append(observation)
        self._stop_reason = self._detect_breach(observation)
        return self._stop_reason

    def _detect_breach(self, obs: CanaryObservation) -> CanaryStopReason | None:
        checks = self._signal_checks(obs)
        for signal, measured, threshold in checks:
            if measured > threshold:
                return CanaryStopReason(
                    signal=signal,
                    g_reason=map_ac5_signal_to_g_reason(signal),
                    measured_value=round(float(measured), 8),
                    threshold=round(float(threshold), 8),
                    g_receipt_id=self._g_receipt_id,
                    subject=obs.subject,
                )
        return None

    def _signal_checks(
        self, obs: CanaryObservation
    ) -> tuple[tuple[str, float, float], ...]:
        p = self._policy
        return (
            (
                SIGNAL_SCORE_SPREAD,
                abs(float(obs.shadow.calibrated_delta)),
                float(p.max_abs_delta),
            ),
            (
                SIGNAL_FLIP,
                1.0 if obs.shadow.decision_state_changed else 0.0,
                0.0,
            ),
            (
                SIGNAL_COVERAGE,
                float(obs.coverage_disparity),
                float(p.max_coverage_disparity),
            ),
            (
                SIGNAL_MISSINGNESS,
                float(obs.missingness_rate),
                float(p.max_missingness_rate),
            ),
            (
                SIGNAL_SOURCE_CONCENTRATION,
                float(obs.source_concentration),
                float(p.max_single_source_family_share),
            ),
        )


# ---------------------------------------------------------------------------
# D1.c: PromotionAuthorizationGate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromotionRequest:
    """A CEO-signed request to promote one canary subject."""

    subject: str
    g_receipt_id: str
    requested_at: str
    actor: str
    nonce: str
    key_id: str
    signature: str
    receipt_version: str = PROMOTION_REQUEST_VERSION

    def unsigned(self) -> dict[str, Any]:
        value = {
            "subject": self.subject,
            "g_receipt_id": self.g_receipt_id,
            "requested_at": self.requested_at,
            "actor": self.actor,
            "nonce": self.nonce,
            "key_id": self.key_id,
            "receipt_version": self.receipt_version,
        }
        return value


REFUSAL_MISSING_CEO_SIGNATURE = "missing_or_invalid_ceo_signature"
REFUSAL_PROMOTION_BLOCKED_BY_INTRINSIC_GATE = "promotion_blocked_by_intrinsic_gate"
REFUSAL_ACTIVE_STOP_REASON = "active_stop_reason_present"


@dataclass(frozen=True)
class PromotionDecision:
    """The gate's verdict on a promotion request."""

    authorized: bool
    refusal: str | None
    g_receipt_id: str
    g_decision: str
    stop_reason: CanaryStopReason | None


class PromotionAuthorizationGate:
    """Three-door promotion gate; always refuses while G=BLOCK.

    Doors (checked in order):

    1. CEO signature over the :class:`PromotionRequest` verifies against a known
       CEO public key.
    2. The bound G receipt decision is ``PASS``.
    3. The live stop monitor is clean (no active stop reason).

    Because G currently emits ``BLOCK`` for the real dataset, door 2 can never
    pass, so the gate always refuses with
    :data:`REFUSAL_PROMOTION_BLOCKED_BY_INTRINSIC_GATE` (when a valid CEO
    signature is presented).  There is no bypass and no automatic promotion.
    """

    def __init__(self, ceo_public_keys: Mapping[str, bytes]) -> None:
        if not isinstance(ceo_public_keys, Mapping):
            raise CanaryControlError("ceo_public_keys must be a mapping")
        for key_id, public in ceo_public_keys.items():
            if not isinstance(key_id, str) or not key_id:
                raise CanaryControlError("ceo key_id must be a non-empty str")
            if not isinstance(public, (bytes, bytearray)) or len(public) != 32:
                raise CanaryControlError("ceo public key must be 32 raw bytes")
        self._ceo_keys = {k: bytes(v) for k, v in ceo_public_keys.items()}

    def authorize(
        self,
        request: PromotionRequest,
        *,
        g_receipt: IntrinsicPromotionReceipt,
        monitor: CanaryStopMonitor,
    ) -> PromotionDecision:
        base = PromotionDecision(
            authorized=False,
            refusal=None,
            g_receipt_id=monitor.g_receipt_id,
            g_decision=g_receipt.decision.value,
            stop_reason=monitor.stop_reason,
        )
        if not self._verify_signature(request):
            return PromotionDecision(
                **{
                    **base.__dict__,
                    "refusal": REFUSAL_MISSING_CEO_SIGNATURE,
                }
            )
        if g_receipt.decision is not IntrinsicPromotionDecision.PASS:
            return PromotionDecision(
                **{
                    **base.__dict__,
                    "refusal": REFUSAL_PROMOTION_BLOCKED_BY_INTRINSIC_GATE,
                }
            )
        if monitor.stop_reason is not None:
            return PromotionDecision(
                **{
                    **base.__dict__,
                    "refusal": REFUSAL_ACTIVE_STOP_REASON,
                }
            )
        return PromotionDecision(
            authorized=True,
            refusal=None,
            g_receipt_id=monitor.g_receipt_id,
            g_decision=g_receipt.decision.value,
            stop_reason=None,
        )

    def _verify_signature(self, request: PromotionRequest) -> bool:
        if request.receipt_version != PROMOTION_REQUEST_VERSION:
            return False
        if not request.actor or not request.nonce or not request.subject:
            return False
        public = self._ceo_keys.get(request.key_id)
        if public is None:
            return False
        try:
            Ed25519PublicKey.from_public_bytes(public).verify(
                bytes.fromhex(request.signature),
                PROMOTION_REQUEST_DOMAIN + _canonical_json(request.unsigned()),
            )
            return True
        except (InvalidSignature, ValueError):
            return False


# ---------------------------------------------------------------------------
# D1.d: PostPromotionMonitor (skeleton; unreachable while G=BLOCK)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PostPromotionMonitor:
    """Skeleton post-promotion observer.

    Only constructible when the controller phase is ``promoted``.  Because the
    gate always refuses while G=BLOCK, this object is **unreachable** in any real
    flow; it exists so the promote framework is complete and so a synthetic PASS
    fixture can exercise the post-promotion shape in tests.
    """

    phase: str
    g_receipt_id: str
    subject: str

    def __post_init__(self) -> None:
        if self.phase != "promoted":
            raise CanaryControlError(
                "PostPromotionMonitor requires phase == 'promoted'"
            )
        if not self.g_receipt_id.startswith("sha256:"):
            raise CanaryControlError("g_receipt_id must be a sha256 digest")
        if not isinstance(self.subject, str) or not self.subject:
            raise CanaryControlError("subject must be a non-empty str")


# ---------------------------------------------------------------------------
# D1.e: CanaryTransitionSigners (injected J machinery)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CanaryTransitionSigners:
    """Injected signing capability that reuses J's (#877) rollback machinery.

    ``authorization_signer`` / ``completion_signer`` are J's
    ``_authorization`` / ``_completion`` helpers (or equivalents); they are
    passed in by the drill script / tests so this module never copies J's code
    and never imports from ``scripts/``.
    """

    authorization_signer: Callable[..., DeploymentAuthorization]
    completion_signer: Callable[..., ActivationCompletionReceipt]
    auth_private: Any
    auth_key_id: str
    complete_private: Any
    complete_key_id: str

    def __post_init__(self) -> None:
        if not callable(self.authorization_signer):
            raise CanaryControlError("authorization_signer must be callable")
        if not callable(self.completion_signer):
            raise CanaryControlError("completion_signer must be callable")
        if not isinstance(self.auth_key_id, str) or not self.auth_key_id:
            raise CanaryControlError("auth_key_id must be a non-empty str")
        if not isinstance(self.complete_key_id, str) or not self.complete_key_id:
            raise CanaryControlError("complete_key_id must be a non-empty str")


# ---------------------------------------------------------------------------
# D1.f: CanaryController
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CanaryStartResult:
    """Outcome of a canary-start attempt for one subject."""

    subject: str
    started: bool
    fallback: str
    phase: str


@dataclass(frozen=True)
class RouteBackResult:
    """Outcome of a route-back (operator stop + rollback-a)."""

    final_phase: str
    stop_reason: CanaryStopReason | None
    control_event_count: int


class CanaryController:
    """Drive the deployment-control ledger through a limited canary lifecycle.

    Holds a :class:`DeploymentControlLedger` (K) and governs
    ``disabled -> canary -> stopped -> disabled``.  There is **no**
    ``promote()`` automatic method: promotion is only reachable through
    :meth:`request_promote`, which delegates to :class:`PromotionAuthorizationGate`
    and returns a (always-refusing, while G=BLOCK) :class:`PromotionDecision`.
    """

    def __init__(
        self,
        ledger: DeploymentControlLedger,
        scope: CanaryScope,
        monitor: CanaryStopMonitor,
        *,
        signers: CanaryTransitionSigners,
        gate: PromotionAuthorizationGate | None = None,
    ) -> None:
        if not isinstance(ledger, DeploymentControlLedger):
            raise CanaryControlError("ledger must be a DeploymentControlLedger")
        if not isinstance(scope, CanaryScope):
            raise CanaryControlError("scope must be a CanaryScope")
        if not isinstance(monitor, CanaryStopMonitor):
            raise CanaryControlError("monitor must be a CanaryStopMonitor")
        if not isinstance(signers, CanaryTransitionSigners):
            raise CanaryControlError("signers must be CanaryTransitionSigners")
        self._ledger = ledger
        self._scope = scope
        self._monitor = monitor
        self._signers = signers
        self._gate = gate or PromotionAuthorizationGate({})

    @property
    def ledger(self) -> DeploymentControlLedger:
        return self._ledger

    @property
    def scope(self) -> CanaryScope:
        return self._scope

    @property
    def monitor(self) -> CanaryStopMonitor:
        return self._monitor

    @property
    def stop_reason(self) -> CanaryStopReason | None:
        return self._monitor.stop_reason

    @property
    def phase(self) -> str:
        return self._ledger.routing_snapshot().phase

    def start_canary(self, subject: str, *, now: datetime) -> CanaryStartResult:
        if not isinstance(subject, str) or not subject:
            raise CanaryControlError("subject must be a non-empty str")
        current = self.phase
        if not self._scope.eligible(subject):
            return CanaryStartResult(
                subject=subject,
                started=False,
                fallback="active",
                phase=current,
            )
        if current != "disabled":
            raise CanaryControlError(
                f"canary start requires disabled phase, got {current!r}"
            )
        auth = self._signers.authorization_signer(
            self._ledger,
            "start",
            f"canary-start:{subject}",
            now,
            self._signers.auth_private,
            self._signers.auth_key_id,
        )
        prepared = self._ledger.prepare("start", auth, now=now)
        completion = self._signers.completion_signer(
            self._ledger,
            prepared,
            "start",
            f"canary-start-complete:{subject}",
            now,
            self._signers.complete_private,
            self._signers.complete_key_id,
        )
        self._ledger.complete(completion, now=now)
        phase = self._ledger.routing_snapshot().phase
        if phase != "canary":
            raise CanaryControlError(f"canary start did not reach canary: {phase!r}")
        return CanaryStartResult(
            subject=subject, started=True, fallback="canary", phase=phase
        )

    def observe(self, observation: CanaryObservation) -> CanaryStopReason | None:
        return self._monitor.observe(observation)

    def route_back(self, *, now: datetime) -> RouteBackResult:
        current = self.phase
        if current != "canary":
            raise CanaryControlError(
                f"route_back requires canary phase, got {current!r}"
            )
        stop_auth = self._signers.authorization_signer(
            self._ledger,
            "stop",
            "canary-stop",
            now,
            self._signers.auth_private,
            self._signers.auth_key_id,
        )
        self._ledger.prepare("stop", stop_auth, now=now)
        if self._ledger.routing_snapshot().phase != "stopped":
            raise CanaryControlError("operator stop did not reach stopped")
        rollback_auth = self._signers.authorization_signer(
            self._ledger,
            "rollback-a",
            "canary-rollback",
            now,
            self._signers.auth_private,
            self._signers.auth_key_id,
        )
        prepared = self._ledger.prepare("rollback-a", rollback_auth, now=now)
        completion = self._signers.completion_signer(
            self._ledger,
            prepared,
            "rollback-a",
            "canary-rollback-complete",
            now,
            self._signers.complete_private,
            self._signers.complete_key_id,
        )
        self._ledger.complete(completion, now=now)
        final_phase = self._ledger.routing_snapshot().phase
        if final_phase != "disabled":
            raise CanaryControlError(
                f"rollback-a did not restore disabled: {final_phase!r}"
            )
        return RouteBackResult(
            final_phase=final_phase,
            stop_reason=self._monitor.stop_reason,
            control_event_count=len(self._ledger._records()),
        )

    def request_promote(
        self,
        request: PromotionRequest,
        *,
        g_receipt: IntrinsicPromotionReceipt,
    ) -> PromotionDecision:
        return self._gate.authorize(
            request, g_receipt=g_receipt, monitor=self._monitor
        )


# ---------------------------------------------------------------------------
# Disposition artifact (controller-emitted; NOT a release-evidence gate)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CanaryDisposition:
    """The commit-bound disposition of one canary controller run.

    This is a controller-emitted artifact written to
    ``data/canary_control/canary_disposition.json``.  It is deliberately **not**
    a :data:`~trustforge.deployment_evidence.REQUIRED_GATES` entry: the 9-gate
    release-evidence contract is frozen and untouched.
    """

    schema: str
    disposition: str
    promote_path_exercised: bool
    g_receipt_id: str
    g_decision: str
    stop_triggered: bool
    stop_signal: str | None
    stop_g_reason: str | None
    final_phase: str
    executed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "disposition": self.disposition,
            "promote_path_exercised": self.promote_path_exercised,
            "g_receipt_id": self.g_receipt_id,
            "g_decision": self.g_decision,
            "stop_triggered": self.stop_triggered,
            "stop_signal": self.stop_signal,
            "stop_g_reason": self.stop_g_reason,
            "final_phase": self.final_phase,
            "executed_at": self.executed_at,
        }


def build_disposition(
    *,
    decision: PromotionDecision,
    stop_reason: CanaryStopReason | None,
    final_phase: str,
    executed_at: str,
) -> CanaryDisposition:
    promote_path_exercised = decision.authorized
    return CanaryDisposition(
        schema=CANARY_DISPOSITION_SCHEMA,
        disposition="promoted" if promote_path_exercised else "remain_shadow",
        promote_path_exercised=promote_path_exercised,
        g_receipt_id=decision.g_receipt_id,
        g_decision=decision.g_decision,
        stop_triggered=stop_reason is not None,
        stop_signal=stop_reason.signal if stop_reason else None,
        stop_g_reason=(
            stop_reason.g_reason.value if stop_reason else None
        ),
        final_phase=final_phase,
        executed_at=executed_at,
    )


def write_disposition(
    disposition: CanaryDisposition,
    *,
    out_path: Path,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(disposition.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out_path


# ---------------------------------------------------------------------------
# Bound route-back receipt (controller-emitted; HMAC + output_digest binding)
#
# Mirrors J's (#877) GateReceipt discipline: the route-back observable output is
# hashed into ``output_digest`` and the receipt is HMAC-signed so a verifier can
# prove the disposition bytes were not mutated.  This is deliberately **not** a
# :data:`~trustforge.deployment_evidence.REQUIRED_GATES` entry — the 9-gate
# release-evidence contract is frozen and untouched.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RouteBackReceipt:
    """HMAC-signed receipt binding one route-back to its observable output."""

    schema: str
    g_receipt_id: str
    final_phase: str
    control_head: str
    output_digest: str
    executed_at: str
    key_id: str
    nonce: str
    signature: str
    receipt_version: str = "trustforge.canary-route-back-receipt/v1"

    def unsigned(self) -> dict[str, Any]:
        value = {
            "schema": self.schema,
            "g_receipt_id": self.g_receipt_id,
            "final_phase": self.final_phase,
            "control_head": self.control_head,
            "output_digest": self.output_digest,
            "executed_at": self.executed_at,
            "key_id": self.key_id,
            "nonce": self.nonce,
            "receipt_version": self.receipt_version,
        }
        return value


def build_route_back_receipt(
    *,
    disposition_bytes: bytes,
    g_receipt_id: str,
    final_phase: str,
    control_head: str,
    executed_at: str,
    key: bytes,
    key_id: str,
    nonce: str,
) -> RouteBackReceipt:
    """Construct the HMAC-signed route-back receipt.

    ``output_digest`` is the sha256 of the exact disposition bytes, so mutating
    the disposition after binding is detectable (mirrors J's discipline).
    """
    if not isinstance(key, (bytes, bytearray)) or len(key) < 32:
        raise CanaryControlError("route-back key must be at least 32 bytes")
    if not isinstance(disposition_bytes, (bytes, bytearray)):
        raise CanaryControlError("disposition_bytes must be bytes")
    output_digest = "sha256:" + hashlib.sha256(disposition_bytes).hexdigest()
    unsigned = {
        "schema": CANARY_ROUTE_BACK_SCHEMA,
        "g_receipt_id": g_receipt_id,
        "final_phase": final_phase,
        "control_head": control_head,
        "output_digest": output_digest,
        "executed_at": executed_at,
        "key_id": key_id,
        "nonce": nonce,
        "receipt_version": "trustforge.canary-route-back-receipt/v1",
    }
    signature = hmac.new(
        bytes(key),
        CANARY_ROUTE_BACK_DOMAIN + _canonical_json(unsigned),
        hashlib.sha256,
    ).hexdigest()
    return RouteBackReceipt(**unsigned, signature=signature)


def verify_route_back_receipt(
    receipt: RouteBackReceipt,
    *,
    keyring: Mapping[str, bytes],
    disposition_bytes: bytes,
) -> bool:
    """Verify an HMAC signature and that ``output_digest`` binds the bytes."""
    key = keyring.get(receipt.key_id)
    if key is None:
        return False
    expected = hmac.new(
        bytes(key),
        CANARY_ROUTE_BACK_DOMAIN + _canonical_json(receipt.unsigned()),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(receipt.signature, expected):
        return False
    expected_output = "sha256:" + hashlib.sha256(disposition_bytes).hexdigest()
    return hmac.compare_digest(receipt.output_digest, expected_output)


# ---------------------------------------------------------------------------
# G receipt loader (parses the commit-bound G artifact)
# ---------------------------------------------------------------------------


def load_g_receipt(
    path: Path,
) -> tuple[IntrinsicPromotionReceipt, str]:
    """Load and reconstruct the G intrinsic-promotion receipt from disk.

    Returns the reconstructed :class:`IntrinsicPromotionReceipt` plus its
    content-addressed receipt id.  Reuses G's own ``receipt_id`` so the binding
    is identical to what G emitted.
    """
    raw = json.loads(path.read_bytes())
    receipt_payload = raw["receipt"]
    decision = IntrinsicPromotionDecision(receipt_payload["decision"])
    reasons = tuple(
        IntrinsicPromotionReason(r) for r in receipt_payload["reasons"]
    )
    receipt = IntrinsicPromotionReceipt(
        receipt_domain_version=receipt_payload["receipt_domain_version"],
        policy_digest=receipt_payload["policy_digest"],
        observation_root_digest=receipt_payload["observation_root_digest"],
        benchmark_manifest_digest=receipt_payload["benchmark_manifest_digest"],
        evaluated_at=receipt_payload["evaluated_at"],
        policy=dict(receipt_payload["policy"]),
        decision=decision,
        reasons=reasons,
        calibration_claim=receipt_payload["calibration_claim"],
        counts=dict(receipt_payload["counts"]),
    )
    return receipt, _g_receipt_id(receipt)


# ---------------------------------------------------------------------------
# canonical JSON (mirrors the bounded canonical form used across the codebase)
# ---------------------------------------------------------------------------


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


__all__ = [
    "CANARY_CONTROL_FLAG",
    "CANARY_DISPOSITION_SCHEMA",
    "CANARY_ROUTE_BACK_DOMAIN",
    "CANARY_ROUTE_BACK_SCHEMA",
    "CANARY_STOP_SIGNALS",
    "PROMOTION_REQUEST_DOMAIN",
    "PROMOTION_REQUEST_VERSION",
    "REFUSAL_ACTIVE_STOP_REASON",
    "REFUSAL_MISSING_CEO_SIGNATURE",
    "REFUSAL_PROMOTION_BLOCKED_BY_INTRINSIC_GATE",
    "CanaryControlError",
    "CanaryController",
    "CanaryDisposition",
    "CanaryObservation",
    "CanaryScope",
    "CanaryStartResult",
    "CanaryStopMonitor",
    "CanaryStopReason",
    "CanaryTransitionSigners",
    "PostPromotionMonitor",
    "PromotionAuthorizationGate",
    "PromotionDecision",
    "PromotionRequest",
    "RouteBackReceipt",
    "RouteBackResult",
    "SIGNAL_COVERAGE",
    "SIGNAL_FLIP",
    "SIGNAL_MISSINGNESS",
    "SIGNAL_SCORE_SPREAD",
    "SIGNAL_SOURCE_CONCENTRATION",
    "build_disposition",
    "build_route_back_receipt",
    "load_g_receipt",
    "map_ac5_signal_to_g_reason",
    "verify_route_back_receipt",
    "write_disposition",
]
