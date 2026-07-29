#!/usr/bin/env python3
"""Hermetic limited-canary controller driver (#879).

Drives the canary controller framework through one full lifecycle against a
hermetic loopback release pair backed by a temporary signed-event ledger:

    disabled -> canary -> (live breach) -> stopped -> disabled

Loads the **real** G intrinsic-promotion receipt (``receipt-current.json``,
currently ``decision=block``) so the run is bound to the honest current-state
verdict.  Because G=BLOCK, the monitor always trips a stop, ``route_back``
restores ``disabled``, and the promotion gate always refuses.  The run emits a
controller-bound disposition artifact at
``data/canary_control/canary_disposition.json`` with
``disposition=remain_shadow``.

This is a non-production drill.  It reuses J's (#877) hermetic machinery
(``_start_release_server`` / ``_make_ledgers`` / ``_authorization`` /
``_completion`` / ``_InMemoryActivationLockBackend``) verbatim — it does not
copy them.  It never flips a real flag, routes real traffic, or touches a host.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from trustforge.activation_lock import _set_backend_for_tests
from trustforge.canary_control import (
    CANARY_CONTROL_FLAG,
    PROMOTION_REQUEST_DOMAIN,
    PROMOTION_REQUEST_VERSION,
    CanaryControlError,
    CanaryController,
    CanaryObservation,
    CanaryScope,
    CanaryStopMonitor,
    CanaryTransitionSigners,
    PromotionAuthorizationGate,
    PromotionRequest,
    build_disposition,
    load_g_receipt,
    map_ac5_signal_to_g_reason,
    write_disposition,
)
from trustforge.deployment_control import DeploymentControlLedger
from trustforge.release_router import ReleaseEndpoint

REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from run_rollback_drill import (  # noqa: E402
    DRILL_EVIDENCE_SEED,
    _InMemoryActivationLockBackend,
    _authorization,
    _build_artifacts,
    _completion,
    _make_ledgers,
    _policy,
    _sha256_raw,
    _start_release_server,
)

CANARY_TARGET = "canary-sandbox-879"
G_RECEIPT_PATH = REPO_ROOT / "data" / "intrinsic_promotion" / "receipt-current.json"
DISPOSITION_PATH = (
    REPO_ROOT / "data" / "canary_control" / "canary_disposition.json"
)
CANARY_SUBJECT = "asset:btc"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _make_scope() -> CanaryScope:
    return CanaryScope(
        allowlist=frozenset({CANARY_SUBJECT, "asset:eth"}),
        target=CANARY_TARGET,
    )


def _breach_observation(subject: str) -> CanaryObservation:
    """Construct one observation that trips the score_spread signal.

    The calibrated_delta (0.5) exceeds the v1 non-inferiority margin (0.08), so
    the monitor records a live breach bound to G's BLOCK receipt.  This is a
    synthetic projection used only to exercise the stop path hermetically.
    """
    from trustforge.asset_intrinsic_candidate import CandidateShadow

    shadow = CandidateShadow(
        baseline_raw=0.5,
        candidate_raw=0.6,
        total_delta=0.5,
        baseline_calibrated=0.4,
        candidate_calibrated=0.9,
        calibrated_delta=0.5,
        baseline_decision_state="normal",
        candidate_decision_state="normal",
        decision_state_changed=False,
        facts_hash="sha256:" + "0" * 64,
    )
    return CanaryObservation(
        subject=subject,
        shadow=shadow,
        coverage_disparity=0,
        missingness_rate=0.0,
        source_concentration=0.0,
    )


def _build_promotion_request(
    *,
    subject: str,
    g_receipt_id: str,
    now: datetime,
    ceo_private: Ed25519PrivateKey,
    ceo_key_id: str,
) -> PromotionRequest:
    unsigned = {
        "subject": subject,
        "g_receipt_id": g_receipt_id,
        "requested_at": now.isoformat(),
        "actor": "ceo",
        "nonce": "canary-promote-request-1",
        "key_id": ceo_key_id,
        "receipt_version": PROMOTION_REQUEST_VERSION,
    }
    signature = ceo_private.sign(
        PROMOTION_REQUEST_DOMAIN + _canonical_json(unsigned)
    ).hex()
    return PromotionRequest(**unsigned, signature=signature)


def run_canary(
    work_dir: Path,
    *,
    g_receipt_path: Path = G_RECEIPT_PATH,
    disposition_path: Path = DISPOSITION_PATH,
) -> dict[str, Any]:
    """Run one hermetic canary lifecycle; return the disposition summary.

    Loads the real G receipt, drives the controller through
    start -> observe(breach) -> route_back, then exercises the (always
    refused) promotion gate and persists the disposition artifact.
    """
    if os.environ.get(CANARY_CONTROL_FLAG, "").strip().lower() not in {
        "1",
        "true",
        "yes",
    }:
        raise CanaryControlError(
            f"{CANARY_CONTROL_FLAG} must be set to run the canary controller"
        )

    work_dir.mkdir(parents=True, exist_ok=True)
    now = _utcnow()
    evidence_bundle_digest = _sha256_raw(DRILL_EVIDENCE_SEED)

    g_receipt, g_receipt_id = load_g_receipt(g_receipt_path)

    manifest_private = Ed25519PrivateKey.generate()
    manifest_key_id = "canary-manifest-1"
    auth_private = Ed25519PrivateKey.generate()
    auth_key_id = "canary-auth-1"
    complete_private = Ed25519PrivateKey.generate()
    complete_key_id = "canary-complete-1"
    ceo_private = Ed25519PrivateKey.generate()
    ceo_key_id = "canary-ceo-1"

    a_path, a_digest, b_path, b_digest = _build_artifacts(work_dir)
    a_server, a_handler = _start_release_server(
        b"ACTIVE", a_digest, manifest_private, manifest_key_id
    )
    b_server, b_handler = _start_release_server(
        b"CANDIDATE", b_digest, manifest_private, manifest_key_id
    )
    lock_backend = _InMemoryActivationLockBackend()

    with ExitStack() as stack:
        stack.callback(a_server.shutdown)
        stack.callback(b_server.shutdown)
        stack.callback(lambda: _set_backend_for_tests(None))
        _set_backend_for_tests(lock_backend)

        a_origin = a_handler.origin
        b_origin = b_handler.origin
        a_endpoint = ReleaseEndpoint(a_digest, a_origin, manifest_key_id)
        b_endpoint = ReleaseEndpoint(b_digest, b_origin, manifest_key_id)
        confirmation = f"PRODUCTION:{CANARY_TARGET}:{a_digest}:{b_digest}"

        control_ledger, outcome_ledger = _make_ledgers(work_dir)
        control = DeploymentControlLedger(
            control_ledger,
            outcome_ledger=outcome_ledger,
            authorization_keys={
                auth_key_id: auth_private.public_key()
                .public_bytes(Encoding.Raw, PublicFormat.Raw)
            },
            completion_keys={
                complete_key_id: complete_private.public_key()
                .public_bytes(Encoding.Raw, PublicFormat.Raw)
            },
            target=CANARY_TARGET,
            target_confirmation=confirmation,
            active=a_endpoint,
            candidate=b_endpoint,
            policy=_policy(),
            evidence_bundle_digest=evidence_bundle_digest,
            stop_after_errors=2,
            require_distributed_lock=False,
            clock=lambda: now,
        )
        control.initialize()

        policy = _load_canary_policy()
        monitor = CanaryStopMonitor(
            policy,
            g_receipt_id=g_receipt_id,
            g_decision=g_receipt.decision,
        )
        scope = _make_scope()
        signers = CanaryTransitionSigners(
            authorization_signer=_authorization,
            completion_signer=_completion,
            auth_private=auth_private,
            auth_key_id=auth_key_id,
            complete_private=complete_private,
            complete_key_id=complete_key_id,
        )
        gate = PromotionAuthorizationGate(
            {
                ceo_key_id: ceo_private.public_key()
                .public_bytes(Encoding.Raw, PublicFormat.Raw)
            }
        )
        controller = CanaryController(
            control,
            scope,
            monitor,
            signers=signers,
            gate=gate,
        )

        start_result = controller.start_canary(CANARY_SUBJECT, now=now)
        if not start_result.started:
            raise CanaryControlError(
                f"canary start fell back unexpectedly: {start_result.fallback}"
            )

        stop_reason = controller.observe(_breach_observation(CANARY_SUBJECT))
        if stop_reason is None:
            raise CanaryControlError(
                "monitor did not trip a stop on the breach observation"
            )

        route_result = controller.route_back(now=now)
        if route_result.final_phase != "disabled":
            raise CanaryControlError(
                f"route_back did not restore disabled: {route_result.final_phase}"
            )

        request = _build_promotion_request(
            subject=CANARY_SUBJECT,
            g_receipt_id=g_receipt_id,
            now=now,
            ceo_private=ceo_private,
            ceo_key_id=ceo_key_id,
        )
        decision = controller.request_promote(request, g_receipt=g_receipt)
        if decision.authorized:
            raise CanaryControlError(
                "promotion was authorized while G=BLOCK (impossible)"
            )

        disposition = build_disposition(
            decision=decision,
            stop_reason=stop_reason,
            final_phase=route_result.final_phase,
            executed_at=now.isoformat(),
        )
        if disposition.disposition != "remain_shadow":
            raise CanaryControlError(
                f"disposition must be remain_shadow, got {disposition.disposition}"
            )
        write_disposition(disposition, out_path=disposition_path)

        return {
            "disposition": disposition.disposition,
            "promote_path_exercised": disposition.promote_path_exercised,
            "g_receipt_id": disposition.g_receipt_id,
            "g_decision": disposition.g_decision,
            "stop_triggered": disposition.stop_triggered,
            "stop_signal": disposition.stop_signal,
            "stop_g_reason": disposition.stop_g_reason,
            "final_phase": disposition.final_phase,
            "refusal": decision.refusal,
            "control_event_count": route_result.control_event_count,
            "disposition_path": str(disposition_path),
        }


def _load_canary_policy():
    from trustforge.asset_intrinsic_promotion import (
        IntrinsicPromotionPolicy,
        load_intrinsic_promotion_policy,
    )

    return load_intrinsic_promotion_policy()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the hermetic limited-canary controller (#879)."
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        required=True,
        help="tmp canary workspace",
    )
    parser.add_argument(
        "--g-receipt",
        type=Path,
        default=G_RECEIPT_PATH,
        help="path to the G intrinsic-promotion receipt JSON",
    )
    parser.add_argument(
        "--disposition",
        type=Path,
        default=DISPOSITION_PATH,
        help="path to write the disposition artifact",
    )
    args = parser.parse_args()

    os.environ.setdefault(CANARY_CONTROL_FLAG, "1")
    summary = run_canary(
        args.work_dir,
        g_receipt_path=args.g_receipt,
        disposition_path=args.disposition,
    )
    print(
        f"canary disposition={summary['disposition']} "
        f"g_decision={summary['g_decision']} "
        f"stop={summary['stop_signal']}->{summary['stop_g_reason']} "
        f"final_phase={summary['final_phase']} "
        f"refusal={summary['refusal']}"
    )
    print(f"disposition={summary['disposition_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
