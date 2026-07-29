"""Tests for the limited canary controller + stop monitor (#879).

These exercise the canary framework end-to-end against a hermetic loopback
release pair backed by a temporary signed-event ledger, reusing J's (#877)
rollback-drill machinery.  Because G=BLOCK on the real dataset, every test
asserts the promote path is permanently refused and the canary remains shadow.

None of these tests may touch a real production target, key, ledger, or host.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from run_rollback_drill import (  # noqa: E402
    DRILL_EVIDENCE_SEED,
    _authorization,
    _build_artifacts,
    _completion,
    _InMemoryActivationLockBackend,
    _make_ledgers,
    _policy,
    _sha256_raw,
    _start_release_server,
)

from trustforge.activation_lock import _set_backend_for_tests  # noqa: E402
from trustforge.asset_intrinsic_candidate import CandidateShadow  # noqa: E402
from trustforge.asset_intrinsic_promotion import (  # noqa: E402
    POLICY_VERSION,
    RECEIPT_DOMAIN_VERSION,
    IntrinsicPromotionDecision,
    IntrinsicPromotionPolicy,
    IntrinsicPromotionReceipt,
    load_intrinsic_promotion_policy,
    receipt_id as _g_receipt_id,
)
from trustforge.canary_control import (  # noqa: E402
    CANARY_CONTROL_FLAG,
    CANARY_ROUTE_BACK_DOMAIN,
    CanaryControlError,
    CanaryController,
    CanaryObservation,
    CanaryScope,
    CanaryStopMonitor,
    CanaryTransitionSigners,
    PostPromotionMonitor,
    PROMOTION_REQUEST_DOMAIN,
    PROMOTION_REQUEST_VERSION,
    PromotionAuthorizationGate,
    PromotionRequest,
    RouteBackReceipt,
    REFUSAL_ACTIVE_STOP_REASON,
    REFUSAL_MISSING_CEO_SIGNATURE,
    REFUSAL_PROMOTION_BLOCKED_BY_INTRINSIC_GATE,
    REFUSAL_RECEIPT_ID_MISMATCH,
    SIGNAL_COVERAGE,
    SIGNAL_FLIP,
    SIGNAL_MISSINGNESS,
    SIGNAL_SCORE_SPREAD,
    SIGNAL_SOURCE_CONCENTRATION,
    build_disposition,
    build_route_back_receipt,
    load_g_receipt,
    map_ac5_signal_to_g_reason,
    verify_route_back_receipt,
)
from trustforge.deployment_control import DeploymentControlLedger  # noqa: E402
from trustforge.release_router import ReleaseEndpoint  # noqa: E402

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402

CANARY_TARGET = "canary-sandbox-879"
G_RECEIPT_PATH = ROOT / "data" / "intrinsic_promotion" / "receipt-current.json"


# ---------------------------------------------------------------------------
# Hermetic environment fixture (reuses J's machinery)
# ---------------------------------------------------------------------------


class _CanaryEnv:
    """Bundle of hermetic canary controller + signed identities."""

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        self.evidence_bundle_digest = _sha256_raw(DRILL_EVIDENCE_SEED)

        self.g_receipt, self.g_receipt_id = load_g_receipt(G_RECEIPT_PATH)
        self.policy = load_intrinsic_promotion_policy()

        self.manifest_private = Ed25519PrivateKey.generate()
        self.manifest_key_id = "canary-manifest-1"
        self.auth_private = Ed25519PrivateKey.generate()
        self.auth_key_id = "canary-auth-1"
        self.complete_private = Ed25519PrivateKey.generate()
        self.complete_key_id = "canary-complete-1"
        self.ceo_private = Ed25519PrivateKey.generate()
        self.ceo_key_id = "canary-ceo-1"
        self.gate_key = b"k" * 32
        self.gate_key_id = "canary-route-back-1"

        a_path, a_digest, b_path, b_digest = _build_artifacts(work_dir)
        self.a_digest = a_digest
        self.b_digest = b_digest
        self.a_server, self.a_handler = _start_release_server(
            b"ACTIVE", a_digest, self.manifest_private, self.manifest_key_id
        )
        self.b_server, self.b_handler = _start_release_server(
            b"CANDIDATE", b_digest, self.manifest_private, self.manifest_key_id
        )
        self.lock_backend = _InMemoryActivationLockBackend()
        self._installed = False

    def install(self) -> None:
        _set_backend_for_tests(self.lock_backend)
        self._installed = True

    def teardown(self) -> None:
        if self._installed:
            _set_backend_for_tests(None)
        self.a_server.shutdown()
        self.b_server.shutdown()

    def build_ledger(self) -> DeploymentControlLedger:
        control_ledger, outcome_ledger = _make_ledgers(self.work_dir)
        a_endpoint = ReleaseEndpoint(
            self.a_digest, self.a_handler.origin, self.manifest_key_id
        )
        b_endpoint = ReleaseEndpoint(
            self.b_digest, self.b_handler.origin, self.manifest_key_id
        )
        confirmation = f"PRODUCTION:{CANARY_TARGET}:{self.a_digest}:{self.b_digest}"
        control = DeploymentControlLedger(
            control_ledger,
            outcome_ledger=outcome_ledger,
            authorization_keys={
                self.auth_key_id: self.auth_private.public_key()
                .public_bytes(Encoding.Raw, PublicFormat.Raw)
            },
            completion_keys={
                self.complete_key_id: self.complete_private.public_key()
                .public_bytes(Encoding.Raw, PublicFormat.Raw)
            },
            target=CANARY_TARGET,
            target_confirmation=confirmation,
            active=a_endpoint,
            candidate=b_endpoint,
            policy=_policy(),
            evidence_bundle_digest=self.evidence_bundle_digest,
            stop_after_errors=2,
            require_distributed_lock=False,
            clock=lambda: self.now,
        )
        control.initialize()
        return control

    def build_controller(
        self, *, monitor: CanaryStopMonitor | None = None
    ) -> CanaryController:
        control = self.build_ledger()
        scope = CanaryScope(
            allowlist=frozenset({"asset:btc", "asset:eth"}),
            target=CANARY_TARGET,
        )
        mon = monitor or CanaryStopMonitor(
            self.policy,
            g_receipt_id=self.g_receipt_id,
            g_decision=self.g_receipt.decision,
        )
        signers = CanaryTransitionSigners(
            authorization_signer=_authorization,
            completion_signer=_completion,
            auth_private=self.auth_private,
            auth_key_id=self.auth_key_id,
            complete_private=self.complete_private,
            complete_key_id=self.complete_key_id,
        )
        gate = PromotionAuthorizationGate(
            {
                self.ceo_key_id: self.ceo_private.public_key()
                .public_bytes(Encoding.Raw, PublicFormat.Raw)
            }
        )
        return CanaryController(control, scope, mon, signers=signers, gate=gate)

    def sign_promotion_request(
        self, *, subject: str, g_receipt_id: str | None = None
    ) -> PromotionRequest:
        unsigned = {
            "subject": subject,
            "g_receipt_id": g_receipt_id or self.g_receipt_id,
            "requested_at": self.now.isoformat(),
            "actor": "ceo",
            "nonce": "canary-promote-1",
            "key_id": self.ceo_key_id,
            "receipt_version": PROMOTION_REQUEST_VERSION,
        }
        signature = self.ceo_private.sign(
            PROMOTION_REQUEST_DOMAIN
            + json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
        ).hex()
        return PromotionRequest(**unsigned, signature=signature)


@pytest.fixture
def env(tmp_path: Path) -> _CanaryEnv:
    env_ = _CanaryEnv(tmp_path)
    env_.install()
    yield env_
    env_.teardown()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shadow(
    *,
    calibrated_delta: float = 0.0,
    decision_state_changed: bool = False,
) -> CandidateShadow:
    return CandidateShadow(
        baseline_raw=0.5,
        candidate_raw=0.5,
        total_delta=calibrated_delta,
        baseline_calibrated=0.4,
        candidate_calibrated=0.4 + calibrated_delta,
        calibrated_delta=calibrated_delta,
        baseline_decision_state="normal",
        candidate_decision_state=("flipped" if decision_state_changed else "normal"),
        decision_state_changed=decision_state_changed,
        facts_hash="sha256:" + "0" * 64,
    )


def _observation(
    *,
    subject: str = "asset:btc",
    calibrated_delta: float = 0.0,
    decision_state_changed: bool = False,
    coverage_disparity: int = 0,
    missingness_rate: float = 0.0,
    source_concentration: float = 0.0,
) -> CanaryObservation:
    return CanaryObservation(
        subject=subject,
        shadow=_shadow(
            calibrated_delta=calibrated_delta,
            decision_state_changed=decision_state_changed,
        ),
        coverage_disparity=coverage_disparity,
        missingness_rate=missingness_rate,
        source_concentration=source_concentration,
    )


def _clean_observation(subject: str = "asset:btc") -> CanaryObservation:
    return _observation(subject=subject)


def _synthetic_pass_receipt(g_receipt_id_source: IntrinsicPromotionReceipt):
    """Build a minimal valid PASS receipt for the unreachable promote path."""
    pass_policy = IntrinsicPromotionPolicy(
        version=POLICY_VERSION,
        min_observations=200,
        min_assets=5,
        min_days=30,
        min_known=3,
        min_families=2,
        max_abs_delta=0.08,
        brier_degradation_limit=0.01,
        ece_degradation_limit=0.01,
        labels_mature=True,
        min_eligible_fraction=0.6,
        max_decision_flips=0,
        max_coverage_disparity=2,
        max_missingness_rate=0.5,
        sensitivity_bound=0.08,
        max_single_source_family_share=0.6,
        corrupt_rate_max=0.05,
    )
    return IntrinsicPromotionReceipt(
        receipt_domain_version=RECEIPT_DOMAIN_VERSION,
        policy_digest="sha256:" + "a" * 64,
        observation_root_digest="sha256:" + "b" * 64,
        benchmark_manifest_digest="sha256:" + "c" * 64,
        evaluated_at="2026-07-29T00:00:00Z",
        policy=asdict(pass_policy),
        decision=IntrinsicPromotionDecision.PASS,
        reasons=(),
        calibration_claim="verified_no_regression",
        counts={
            "observation_count": 300,
            "valid_count": 300,
            "corrupt_count": 0,
            "asset_count": 6,
            "day_span": 45,
            "eligible_count": 250,
            "eligible_fraction": 0.83,
            "corrupt_rate": 0.0,
            "known_count_min": 3,
            "known_count_max": 5,
            "source_family_count_min": 2,
            "distinct_family_count": 4,
            "decision_flips": 0,
            "missingness_rate": 0.1,
        },
    )


# ===========================================================================
# 1. start rejects a production target
# ===========================================================================


class TestStartRejectsProductionTarget:
    def test_scope_rejects_production_target_name(self):
        with pytest.raises(CanaryControlError, match="must not be a production target"):
            CanaryScope(allowlist=frozenset({"asset:btc"}), target="production")

    def test_scope_rejects_trustforge_production(self):
        with pytest.raises(CanaryControlError, match="must not be a production target"):
            CanaryScope(
                allowlist=frozenset({"asset:btc"}), target="trustforge-production"
            )

    def test_scope_requires_canary_or_sandbox_marker(self):
        with pytest.raises(CanaryControlError, match="canary/sandbox marker"):
            CanaryScope(allowlist=frozenset({"asset:btc"}), target="release-target")

    def test_scope_accepts_canary_sandbox(self):
        scope = CanaryScope(
            allowlist=frozenset({"asset:btc"}), target=CANARY_TARGET
        )
        assert scope.eligible("asset:btc")
        assert "production" not in scope.target


# ===========================================================================
# 2. allowlist-outside subject falls back to active
# ===========================================================================


class TestAllowlistOutsideSubjectFallsBackToA:
    def test_outside_subject_does_not_start_canary(self, env: _CanaryEnv):
        controller = env.build_controller()
        result = controller.start_canary("asset:sol", now=env.now)
        assert result.started is False
        assert result.fallback == "active"
        assert result.phase == "disabled"
        assert controller.phase == "disabled"

    def test_inside_subject_starts_canary(self, env: _CanaryEnv):
        controller = env.build_controller()
        result = controller.start_canary("asset:btc", now=env.now)
        assert result.started is True
        assert result.fallback == "canary"
        assert result.phase == "canary"


# ===========================================================================
# 3. live breach (each of the 5 signals) trips a stop, never promotes
# ===========================================================================


class TestLiveBreachFiveSignals:
    @pytest.mark.parametrize(
        "signal,observation_kwargs,expected_g_reason",
        [
            (
                SIGNAL_SCORE_SPREAD,
                dict(calibrated_delta=0.5),
                "delta_exceeds_non_inferiority_margin",
            ),
            (
                SIGNAL_FLIP,
                dict(decision_state_changed=True),
                "direction_or_decision_flip",
            ),
            (
                SIGNAL_COVERAGE,
                dict(coverage_disparity=3),
                "coverage_disparity",
            ),
            (
                SIGNAL_MISSINGNESS,
                dict(missingness_rate=0.6),
                "missingness_rate_exceeded",
            ),
            (
                SIGNAL_SOURCE_CONCENTRATION,
                dict(source_concentration=0.7),
                "single_source_dependency",
            ),
        ],
    )
    def test_each_signal_trips_stop(
        self, env: _CanaryEnv, signal, observation_kwargs, expected_g_reason
    ):
        monitor = CanaryStopMonitor(
            env.policy,
            g_receipt_id=env.g_receipt_id,
            g_decision=env.g_receipt.decision,
        )
        reason = monitor.observe(_observation(**observation_kwargs))
        assert reason is not None
        assert reason.signal == signal
        assert reason.g_reason is map_ac5_signal_to_g_reason(signal)
        assert reason.g_reason.value == expected_g_reason
        assert reason.g_receipt_id == env.g_receipt_id
        assert monitor.clean is False

    def test_clean_observation_does_not_trip_stop(self, env: _CanaryEnv):
        monitor = CanaryStopMonitor(
            env.policy,
            g_receipt_id=env.g_receipt_id,
            g_decision=env.g_receipt.decision,
        )
        reason = monitor.observe(_clean_observation())
        assert reason is None
        assert monitor.clean is True

    def test_first_breach_is_sticky(self, env: _CanaryEnv):
        monitor = CanaryStopMonitor(
            env.policy,
            g_receipt_id=env.g_receipt_id,
            g_decision=env.g_receipt.decision,
        )
        first = monitor.observe(_observation(calibrated_delta=0.5))
        second = monitor.observe(_observation(coverage_disparity=99))
        assert second is first
        assert second.signal == SIGNAL_SCORE_SPREAD

    def test_stop_monitor_binds_g_decision_block(self, env: _CanaryEnv):
        monitor = CanaryStopMonitor(
            env.policy,
            g_receipt_id=env.g_receipt_id,
            g_decision=env.g_receipt.decision,
        )
        assert monitor.g_decision is IntrinsicPromotionDecision.BLOCK


# ===========================================================================
# 4. G=BLOCK -> promotion_blocked_by_intrinsic_gate + route-back + remain_shadow
# ===========================================================================


class TestGBlockRouteBackAndRemainShadow:
    def test_full_lifecycle_remains_shadow(self, env: _CanaryEnv):
        controller = env.build_controller()
        start = controller.start_canary("asset:btc", now=env.now)
        assert start.phase == "canary"

        stop_reason = controller.observe(
            _observation(calibrated_delta=0.5)
        )
        assert stop_reason is not None
        assert stop_reason.signal == SIGNAL_SCORE_SPREAD

        route = controller.route_back(now=env.now)
        assert route.final_phase == "disabled"
        assert route.stop_reason is stop_reason

    def test_request_promote_blocked_by_intrinsic_gate(self, env: _CanaryEnv):
        controller = env.build_controller()
        request = env.sign_promotion_request(subject="asset:btc")
        decision = controller.request_promote(
            request, g_receipt=env.g_receipt
        )
        assert decision.authorized is False
        assert decision.refusal == REFUSAL_PROMOTION_BLOCKED_BY_INTRINSIC_GATE
        assert decision.g_decision == "block"

    def test_g_receipt_is_block_with_real_reasons(self, env: _CanaryEnv):
        assert env.g_receipt.decision is IntrinsicPromotionDecision.BLOCK
        assert env.g_receipt.reasons
        assert env.g_receipt_id.startswith("sha256:")

    def test_disposition_is_remain_shadow(self, env: _CanaryEnv):
        controller = env.build_controller()
        controller.start_canary("asset:btc", now=env.now)
        stop_reason = controller.observe(_observation(calibrated_delta=0.5))
        route = controller.route_back(now=env.now)
        request = env.sign_promotion_request(subject="asset:btc")
        decision = controller.request_promote(request, g_receipt=env.g_receipt)
        disposition = build_disposition(
            decision=decision,
            stop_reason=stop_reason,
            final_phase=route.final_phase,
            executed_at=env.now.isoformat(),
        )
        assert disposition.disposition == "remain_shadow"
        assert disposition.promote_path_exercised is False
        assert disposition.g_decision == "block"
        assert disposition.stop_triggered is True
        assert disposition.stop_signal == SIGNAL_SCORE_SPREAD
        assert disposition.final_phase == "disabled"


# ===========================================================================
# 5. request_promote: three refusals
# ===========================================================================


class TestPromotionThreeRefusals:
    def test_refusal_missing_ceo_signature(self, env: _CanaryEnv):
        controller = env.build_controller()
        unsigned = {
            "subject": "asset:btc",
            "g_receipt_id": env.g_receipt_id,
            "requested_at": env.now.isoformat(),
            "actor": "ceo",
            "nonce": "canary-promote-1",
            "key_id": "canary-ceo-1",
            "receipt_version": PROMOTION_REQUEST_VERSION,
        }
        request = PromotionRequest(**unsigned, signature="00" * 32)
        decision = controller.request_promote(request, g_receipt=env.g_receipt)
        assert decision.authorized is False
        assert decision.refusal == REFUSAL_MISSING_CEO_SIGNATURE

    def test_refusal_promotion_blocked_by_intrinsic_gate(self, env: _CanaryEnv):
        controller = env.build_controller()
        request = env.sign_promotion_request(subject="asset:btc")
        decision = controller.request_promote(request, g_receipt=env.g_receipt)
        assert decision.authorized is False
        assert decision.refusal == REFUSAL_PROMOTION_BLOCKED_BY_INTRINSIC_GATE

    def test_refusal_active_stop_reason_with_synthetic_pass(self, env: _CanaryEnv):
        pass_receipt = _synthetic_pass_receipt(env.g_receipt)
        monitor = CanaryStopMonitor(
            env.policy,
            g_receipt_id=_g_receipt_id(pass_receipt),
            g_decision=env.g_receipt.decision,
        )
        monitor.observe(_observation(calibrated_delta=0.5))
        assert monitor.stop_reason is not None
        control = env.build_ledger()
        scope = CanaryScope(
            allowlist=frozenset({"asset:btc"}), target=CANARY_TARGET
        )
        signers = CanaryTransitionSigners(
            authorization_signer=_authorization,
            completion_signer=_completion,
            auth_private=env.auth_private,
            auth_key_id=env.auth_key_id,
            complete_private=env.complete_private,
            complete_key_id=env.complete_key_id,
        )
        gate = PromotionAuthorizationGate(
            {
                env.ceo_key_id: env.ceo_private.public_key()
                .public_bytes(Encoding.Raw, PublicFormat.Raw)
            }
        )
        controller = CanaryController(control, scope, monitor, signers=signers, gate=gate)
        request = env.sign_promotion_request(subject="asset:btc")
        decision = controller.request_promote(request, g_receipt=pass_receipt)
        assert decision.authorized is False
        assert decision.refusal == REFUSAL_ACTIVE_STOP_REASON

    def test_refusal_receipt_id_mismatch_rejects_forged_pass(self, env: _CanaryEnv):
        """harper WARN fix: a forged PASS receipt whose receipt_id does not match
        the monitor's bound G receipt must be refused — the three-door invariant
        is enforced in code, not by caller discipline."""
        monitor = CanaryStopMonitor(
            env.policy,
            g_receipt_id=env.g_receipt_id,
            g_decision=env.g_receipt.decision,
        )
        control = env.build_ledger()
        scope = CanaryScope(
            allowlist=frozenset({"asset:btc"}), target=CANARY_TARGET
        )
        signers = CanaryTransitionSigners(
            authorization_signer=_authorization,
            completion_signer=_completion,
            auth_private=env.auth_private,
            auth_key_id=env.auth_key_id,
            complete_private=env.complete_private,
            complete_key_id=env.complete_key_id,
        )
        gate = PromotionAuthorizationGate(
            {
                env.ceo_key_id: env.ceo_private.public_key()
                .public_bytes(Encoding.Raw, PublicFormat.Raw)
            }
        )
        controller = CanaryController(control, scope, monitor, signers=signers, gate=gate)
        request = env.sign_promotion_request(subject="asset:btc")
        forged_pass = _synthetic_pass_receipt(env.g_receipt)
        assert _g_receipt_id(forged_pass) != env.g_receipt_id
        decision = controller.request_promote(request, g_receipt=forged_pass)
        assert decision.authorized is False
        assert decision.refusal == REFUSAL_RECEIPT_ID_MISMATCH


# ===========================================================================
# 6. synthetic PASS fixture -> PostPromotionMonitor constructible (unreachable)
# ===========================================================================


class TestPostPromotionMonitorSyntheticPass:
    def test_constructible_only_at_promoted_phase(self):
        monitor = PostPromotionMonitor(
            phase="promoted",
            g_receipt_id="sha256:" + "a" * 64,
            subject="asset:btc",
        )
        assert monitor.phase == "promoted"

    def test_rejects_non_promoted_phase(self):
        with pytest.raises(CanaryControlError, match="phase == 'promoted'"):
            PostPromotionMonitor(
                phase="canary",
                g_receipt_id="sha256:" + "a" * 64,
                subject="asset:btc",
            )

    def test_real_flow_never_reaches_promoted(self, env: _CanaryEnv):
        controller = env.build_controller()
        controller.start_canary("asset:btc", now=env.now)
        controller.observe(_observation(calibrated_delta=0.5))
        controller.route_back(now=env.now)
        assert controller.phase == "disabled"
        with pytest.raises(CanaryControlError, match="phase == 'promoted'"):
            PostPromotionMonitor(
                phase=controller.phase,
                g_receipt_id=env.g_receipt_id,
                subject="asset:btc",
            )


# ===========================================================================
# 7. route-back receipt HMAC + output_digest binding
# ===========================================================================


class TestRouteBackReceiptHmacAndOutputDigest:
    def test_receipt_hmac_verifies(self, env: _CanaryEnv):
        controller = env.build_controller()
        controller.start_canary("asset:btc", now=env.now)
        stop_reason = controller.observe(_observation(calibrated_delta=0.5))
        route = controller.route_back(now=env.now)

        disposition = build_disposition(
            decision=controller.request_promote(
                env.sign_promotion_request(subject="asset:btc"),
                g_receipt=env.g_receipt,
            ),
            stop_reason=stop_reason,
            final_phase=route.final_phase,
            executed_at=env.now.isoformat(),
        )
        disp_bytes = (
            json.dumps(disposition.to_dict(), sort_keys=True).encode() + b"\n"
        )
        control_head = controller.ledger._records()[-1]["event_hash"]
        receipt = build_route_back_receipt(
            disposition_bytes=disp_bytes,
            g_receipt_id=env.g_receipt_id,
            final_phase=route.final_phase,
            control_head=control_head,
            executed_at=env.now.isoformat(),
            key=env.gate_key,
            key_id=env.gate_key_id,
            nonce="route-back-nonce-1",
        )
        expected = hmac.new(
            env.gate_key,
            CANARY_ROUTE_BACK_DOMAIN
            + json.dumps(
                receipt.unsigned(), sort_keys=True, separators=(",", ":")
            ).encode(),
            hashlib.sha256,
        ).hexdigest()
        assert hmac.compare_digest(receipt.signature, expected)
        assert verify_route_back_receipt(
            receipt,
            keyring={env.gate_key_id: env.gate_key},
            disposition_bytes=disp_bytes,
        )

    def test_output_digest_binds_exact_bytes(self, env: _CanaryEnv):
        disposition = build_disposition(
            decision=type(
                "D",
                (),
                {
                    "authorized": False,
                    "refusal": REFUSAL_PROMOTION_BLOCKED_BY_INTRINSIC_GATE,
                    "g_receipt_id": env.g_receipt_id,
                    "g_decision": "block",
                    "stop_reason": None,
                },
            )(),
            stop_reason=None,
            final_phase="disabled",
            executed_at=env.now.isoformat(),
        )
        disp_bytes = (
            json.dumps(disposition.to_dict(), sort_keys=True).encode() + b"\n"
        )
        receipt = build_route_back_receipt(
            disposition_bytes=disp_bytes,
            g_receipt_id=env.g_receipt_id,
            final_phase="disabled",
            control_head="sha256:" + "d" * 64,
            executed_at=env.now.isoformat(),
            key=env.gate_key,
            key_id=env.gate_key_id,
            nonce="route-back-nonce-2",
        )
        expected_output = "sha256:" + hashlib.sha256(disp_bytes).hexdigest()
        assert receipt.output_digest == expected_output
        tampered = disp_bytes.replace(b"disabled", b"promoted")
        assert not verify_route_back_receipt(
            receipt,
            keyring={env.gate_key_id: env.gate_key},
            disposition_bytes=tampered,
        )

    def test_receipt_rejects_wrong_key(self, env: _CanaryEnv):
        disp_bytes = b'{"disposition":"remain_shadow"}\n'
        receipt = build_route_back_receipt(
            disposition_bytes=disp_bytes,
            g_receipt_id=env.g_receipt_id,
            final_phase="disabled",
            control_head="sha256:" + "d" * 64,
            executed_at=env.now.isoformat(),
            key=env.gate_key,
            key_id=env.gate_key_id,
            nonce="route-back-nonce-3",
        )
        assert not verify_route_back_receipt(
            receipt,
            keyring={"unknown-key": b"x" * 32},
            disposition_bytes=disp_bytes,
        )

    def test_route_back_terminal_ledger_history_is_signed(self, env: _CanaryEnv):
        controller = env.build_controller()
        controller.start_canary("asset:btc", now=env.now)
        controller.observe(_observation(calibrated_delta=0.5))
        route = controller.route_back(now=env.now)
        assert route.control_event_count == 6
        records = controller.ledger._records()
        kinds = [r["event"]["kind"] for r in records[1:]]
        assert kinds == [
            "activation_prepared",
            "activation_completed",
            "operator_stop",
            "activation_prepared",
            "activation_completed",
        ]
        last_rollback = [
            r for r in records if r["event"]["kind"] == "activation_completed"
            and r["event"]["action"] == "rollback-a"
        ][-1]
        assert last_rollback["event"]["pointer_active_digest"] == env.a_digest


# ===========================================================================
# 8. flag-off byte parity
# ===========================================================================


class TestFlagOffByteParity:
    def test_canary_control_flag_constant_exists(self):
        assert CANARY_CONTROL_FLAG == "TRUSTFORGE_CANARY_CONTROL_ENABLED"

    def test_no_runtime_module_imports_canary_control(self):
        import ast

        src = ROOT / "src" / "trustforge"
        offenders: list[str] = []
        for path in src.rglob("*.py"):
            if path.name == "canary_control.py":
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and (
                    node.module == "trustforge.canary_control"
                    or node.module.startswith("trustforge.canary_control.")
                ):
                    offenders.append(str(path.relative_to(ROOT)))
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in (
                            "trustforge.canary_control",
                        ) or alias.name.startswith("trustforge.canary_control."):
                            offenders.append(str(path.relative_to(ROOT)))
        assert offenders == [], (
            f"runtime modules import canary_control at module level (breaks "
            f"flag-off byte parity): {offenders}"
        )

    def test_controller_run_refuses_when_flag_off(self, tmp_path, monkeypatch):
        import run_canary_controller

        monkeypatch.delenv(CANARY_CONTROL_FLAG, raising=False)
        with pytest.raises(CanaryControlError, match="must be set to run"):
            run_canary_controller.run_canary(tmp_path)


# ===========================================================================
# AC5 -> G reason mapping completeness
# ===========================================================================


def test_ac5_signal_mapping_is_closed_and_consistent():
    from trustforge.asset_intrinsic_promotion import BLOCK_REASONS

    signals = {
        SIGNAL_SCORE_SPREAD,
        SIGNAL_FLIP,
        SIGNAL_COVERAGE,
        SIGNAL_MISSINGNESS,
        SIGNAL_SOURCE_CONCENTRATION,
    }
    for signal in signals:
        reason = map_ac5_signal_to_g_reason(signal)
        assert reason in BLOCK_REASONS, (
            f"signal {signal} maps to {reason} which is not a hard-block reason"
        )
    with pytest.raises(CanaryControlError, match="unknown canary stop signal"):
        map_ac5_signal_to_g_reason("not-a-real-signal")
