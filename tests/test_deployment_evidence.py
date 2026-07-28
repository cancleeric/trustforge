from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from trustforge.agent.shadow_contracts import (
    CONTRACT_VERSION,
    ShadowReleaseIdentity,
    canonical_json,
    load_policy,
    policy_digest,
)
from trustforge.deployment_evidence import (
    EvidenceError,
    GateReceipt,
    REQUIRED_GATES,
    snapshot_artifact,
    verify_gate_receipts,
)

NOW = datetime(2026, 7, 28, 0, 0, tzinfo=timezone.utc)
KEY = b"e" * 32


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _identity():
    return ShadowReleaseIdentity(
        active_release="release:a@1",
        candidate_release="release:b@2",
        active_artifact_digest=_digest(b"A"),
        candidate_artifact_digest=_digest(b"B"),
        policy_digest=policy_digest(load_policy()),
        contract_version=CONTRACT_VERSION,
    )


def _protected_json(path, payload):
    path.write_text(json.dumps(payload, sort_keys=True))
    os.chmod(path, 0o600)
    return path


def test_a_and_b_artifacts_are_fd_snapshotted_and_digest_bound(tmp_path):
    a, b = tmp_path / "a.zip", tmp_path / "b.zip"
    a.write_bytes(b"A")
    b.write_bytes(b"B")
    assert snapshot_artifact(a, _digest(b"A")).digest == _digest(b"A")
    assert snapshot_artifact(b, _digest(b"B")).digest == _digest(b"B")
    with pytest.raises(EvidenceError, match="digest"):
        snapshot_artifact(b, _digest(b"A"))


def _gate(gate, identity, nonce):
    unsigned = {
        "gate": gate,
        "active_artifact_digest": identity.active_artifact_digest,
        "candidate_artifact_digest": identity.candidate_artifact_digest,
        "command_digest": _digest(f"command-{gate}".encode()),
        "output_digest": _digest(f"output-{gate}".encode()),
        "result": "pass",
        "provider_calls": 0,
        "cost_usd": 0.0,
        "executed_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "key_id": "gate-1",
        "nonce": nonce,
        "receipt_version": "trustforge.executable-gate-receipt/v1",
    }
    signature = hmac.new(
        KEY,
        b"trustforge.gate-receipt.v1\x00" + canonical_json(unsigned),
        hashlib.sha256,
    ).hexdigest()
    return GateReceipt(**unsigned, signature=signature)


def test_all_executable_gates_are_signed_identity_and_cost_bound(tmp_path):
    identity = _identity()
    paths = {}
    for index, gate in enumerate(sorted(REQUIRED_GATES)):
        receipt = _gate(gate, identity, f"nonce-{index}")
        paths[gate] = _protected_json(tmp_path / f"{gate}.json", asdict(receipt))
    verified = verify_gate_receipts(
        paths,
        active_artifact_digest=identity.active_artifact_digest,
        candidate_artifact_digest=identity.candidate_artifact_digest,
        keyring={"gate-1": KEY},
        now=NOW,
    )
    assert {item.gate for item in verified} == REQUIRED_GATES
    forged = json.loads(paths["health"].read_text())
    forged["result"] = "pass"
    forged["cost_usd"] = 1.0
    _protected_json(paths["health"], forged)
    with pytest.raises(EvidenceError):
        verify_gate_receipts(
            paths,
            active_artifact_digest=identity.active_artifact_digest,
            candidate_artifact_digest=identity.candidate_artifact_digest,
            keyring={"gate-1": KEY},
            now=NOW,
        )
