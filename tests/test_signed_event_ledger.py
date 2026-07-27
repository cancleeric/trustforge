from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from trustforge.authenticated_ledger import AuthenticatedLedger, LedgerError
from trustforge.signed_event_ledger import SignedEventLedger

CONTROL_SEED = b"c" * 32
ROUTER_SEED = b"r" * 32
CONTROL_KINDS = frozenset({
    "deployment_initialized", "operator_stop", "activation_prepared",
    "activation_completed", "activation_failed",
})
ROUTER_KINDS = frozenset({
    "candidate_reservation", "candidate_result", "router_emergency_stop",
})


def _public(seed: bytes) -> bytes:
    return Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw
    )


def _ledger(tmp_path, *, seed=CONTROL_SEED, domain="release-control", kinds=CONTROL_KINDS):
    return SignedEventLedger(
        directory=tmp_path,
        verification_keys={
            "control-1": _public(CONTROL_SEED),
            "router-1": _public(ROUTER_SEED),
        },
        event_permissions={
            "release-control": CONTROL_KINDS,
            "release-router-outcome": ROUTER_KINDS,
        },
        domain_keys={
            "release-control": frozenset({"control-1"}),
            "release-router-outcome": frozenset({"router-1"}),
        },
        signing_key_id="control-1" if domain == "release-control" else "router-1",
        signing_private_key=seed,
        signing_domain=domain,
    )


def test_projection_uses_public_keys_only_and_cannot_append(tmp_path):
    writer = _ledger(tmp_path)
    writer.append({"kind": "deployment_initialized"})
    projection = SignedEventLedger(
        directory=tmp_path,
        verification_keys={"control-1": _public(CONTROL_SEED)},
        event_permissions={"release-control": CONTROL_KINDS},
        domain_keys={"release-control": frozenset({"control-1"})},
    )
    assert projection.read()[0]["event"]["kind"] == "deployment_initialized"
    with pytest.raises(LedgerError, match="projection-only"):
        projection.append({"kind": "operator_stop"})


@pytest.mark.parametrize(
    "forbidden", ["operator_stop", "activation_prepared", "activation_completed"]
)
def test_router_private_key_cannot_sign_control_events(tmp_path, forbidden):
    router = _ledger(
        tmp_path,
        seed=ROUTER_SEED,
        domain="release-router-outcome",
        kinds=ROUTER_KINDS,
    )
    with pytest.raises(LedgerError, match="not authorized"):
        router.append({"kind": forbidden})


def test_forged_router_signature_with_control_kind_fails_projection(tmp_path):
    router = _ledger(
        tmp_path,
        seed=ROUTER_SEED,
        domain="release-router-outcome",
        kinds=ROUTER_KINDS,
    )
    router.append({
        "kind": "candidate_reservation",
        "deployment_ledger_id": "a" * 32,
        "reservation_id": "1" * 32,
    })
    path = tmp_path / "events.jsonl"
    record = json.loads(path.read_text().strip())
    record["event"]["kind"] = "operator_stop"
    path.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(LedgerError):
        router.read()


def test_legacy_hmac_v1_ledger_fails_closed_under_ed25519_projection(tmp_path):
    legacy = AuthenticatedLedger(
        keyring={"legacy": b"h" * 32},
        active_key_id="legacy",
        test_directory_override=tmp_path,
    )
    legacy.append({"kind": "operator_stop"})
    projection = SignedEventLedger(
        directory=tmp_path,
        verification_keys={"control-1": _public(CONTROL_SEED)},
        event_permissions={"release-control": CONTROL_KINDS},
        domain_keys={"release-control": frozenset({"control-1"})},
    )
    with pytest.raises(LedgerError, match="legacy"):
        projection.read()
