from __future__ import annotations

import json
import runpy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from trustforge.agent.shadow_contracts import canonical_json
from trustforge.deployment_control import (
    DeploymentAuthorization,
    DeploymentControlError,
)
from trustforge.evidence_action_intent import (
    CEO_SIGNING_DOMAIN,
    EVIDENCE_ACTION,
    OPERATOR_SIGNING_DOMAIN,
    EvidenceActionContractError,
    EvidenceActionScopeV4,
    build_unsigned_evidence_action_v4,
    describe_evidence_action_intent,
    evidence_action_signing_bytes,
    load_evidence_action_envelope_v4,
)

NOW = datetime(2026, 7, 30, 6, tzinfo=timezone.utc)
CEO_SEED = b"c" * 32
OPERATOR_SEED = b"o" * 32


def _digest(char: str) -> str:
    return "sha256:" + char * 64


def _public(seed: bytes) -> bytes:
    return (
        Ed25519PrivateKey.from_private_bytes(seed)
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
    )


def _scope(**changes) -> EvidenceActionScopeV4:
    value = {
        "target": "trustforge-production",
        "candidate": "release:candidate@1",
        "active_release_digest": _digest("a"),
        "candidate_release_digest": _digest("b"),
        "release_manifest_digest": _digest("c"),
        "promotion_pass_event_hash": "d" * 64,
        "git_sha": "e" * 40,
        "dataset_digest": _digest("f"),
        "policy_digest": _digest("1"),
        "ramp_digest": _digest("2"),
        "pit_cutoff": "2026-07-30T05:00:00Z",
        "evidence_bundle_digest": _digest("3"),
        "routing_key_id": "routing-2026-07",
        "control_ledger_id": "4" * 32,
        "expected_control_head": "5" * 64,
        "expected_sequence": 42,
        "transcript_v2_digest": _digest("6"),
        "provenance_digest": _digest("7"),
        "evidence_key_id": "release-evidence-1",
    }
    value.update(changes)
    return EvidenceActionScopeV4(**value)


def _envelope(role: str, *, scope=None, seed=None, **changes) -> dict:
    actual_scope = scope or _scope()
    identity = {
        "ceo": ("ceo@example.test", "ceo-v4-1", "ceo-intent-1", CEO_SEED),
        "operator": (
            "operator@example.test",
            "operator-v4-1",
            "operator-intent-1",
            OPERATOR_SEED,
        ),
    }[role]
    unsigned = build_unsigned_evidence_action_v4(
        role=role,  # type: ignore[arg-type]
        scope=actual_scope,
        actor=changes.pop("actor", identity[0]),
        issued_at=changes.pop("issued_at", (NOW - timedelta(minutes=1)).isoformat()),
        expires_at=changes.pop("expires_at", (NOW + timedelta(minutes=10)).isoformat()),
        nonce=changes.pop("nonce", identity[2]),
        key_id=changes.pop("key_id", identity[1]),
    )
    unsigned.update(changes)
    signing_seed = seed or identity[3]
    domain = CEO_SIGNING_DOMAIN if role == "ceo" else OPERATOR_SIGNING_DOMAIN
    signature = (
        Ed25519PrivateKey.from_private_bytes(signing_seed)
        .sign(domain + canonical_json(unsigned))
        .hex()
    )
    return {**unsigned, "signature": signature}


def _description(**changes):
    arguments = {
        "ceo_payload": _envelope("ceo"),
        "operator_payload": _envelope("operator"),
        "observed_at": NOW,
    }
    arguments.update(changes)
    return describe_evidence_action_intent(**arguments)


def test_exact_v4_pair_describes_inert_transaction_intent():
    result = _description()
    assert result.intent_digest.startswith("sha256:")
    assert result.scope == _scope()
    assert result.ceo_envelope.role == "ceo"
    assert result.operator_envelope.role == "operator"
    assert "authorized" not in type(result).__name__.lower()
    assert "eligible" not in type(result).__name__.lower()


def test_canonical_producer_and_role_domains_are_deterministic_and_distinct():
    ceo = _envelope("ceo")
    operator = _envelope("operator")
    ceo_unsigned = {key: value for key, value in ceo.items() if key != "signature"}
    operator_unsigned = {
        key: value for key, value in operator.items() if key != "signature"
    }
    assert evidence_action_signing_bytes(ceo_unsigned, role="ceo") == (
        CEO_SIGNING_DOMAIN + canonical_json(ceo_unsigned)
    )
    assert evidence_action_signing_bytes(operator_unsigned, role="operator") == (
        OPERATOR_SIGNING_DOMAIN + canonical_json(operator_unsigned)
    )
    assert CEO_SIGNING_DOMAIN != OPERATOR_SIGNING_DOMAIN
    with pytest.raises(EvidenceActionContractError):
        evidence_action_signing_bytes(ceo_unsigned, role="operator")


def test_exact_parser_rejects_missing_extra_and_wrong_type_fields():
    valid = _envelope("ceo")
    missing = dict(valid)
    missing.pop("action")
    for payload in (
        missing,
        {**valid, "verdict": "PASS"},
        {**valid, "expected_sequence": True},
        {**valid, "actor": 7},
        {**valid, "signature": 7},
    ):
        with pytest.raises((EvidenceActionContractError, TypeError)):
            load_evidence_action_envelope_v4(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "trustforge.release-evidence-action/v3"),
        ("version", "trustforge.deployment-authorization/v3"),
        ("action", "start"),
        ("action", "start-canary"),
        ("role", "operator"),
        ("signature", "00"),
    ],
)
def test_wrong_schema_version_action_role_and_signature_block(field, value):
    with pytest.raises(EvidenceActionContractError):
        _description(ceo_payload={**_envelope("ceo"), field: value})


@pytest.mark.parametrize("field", ["actor", "key_id", "nonce"])
def test_pair_identity_strings_must_differ(field):
    ceo = _envelope("ceo")
    operator = _envelope("operator", **{field: ceo[field]})
    with pytest.raises(EvidenceActionContractError):
        _description(operator_payload=operator)


@pytest.mark.parametrize(
    "role,changes",
    [
        ("ceo", {"issued_at": (NOW + timedelta(minutes=1)).isoformat()}),
        ("ceo", {"expires_at": NOW.isoformat()}),
        ("operator", {"expires_at": (NOW + timedelta(minutes=16)).isoformat()}),
        ("operator", {"issued_at": "not-a-time"}),
    ],
)
def test_stale_expired_future_and_overlong_windows_block(role, changes):
    with pytest.raises(EvidenceActionContractError):
        _description(**{f"{role}_payload": _envelope(role, **changes)})


@pytest.mark.parametrize(
    "field,value",
    [
        ("active_release_digest", "sha256:bad"),
        ("promotion_pass_event_hash", "bad"),
        ("git_sha", "not-git"),
        ("control_ledger_id", "bad"),
        ("expected_control_head", "bad"),
        ("expected_sequence", 0),
        ("pit_cutoff", "naive-time"),
    ],
)
def test_invalid_scope_formats_block(field, value):
    with pytest.raises(EvidenceActionContractError):
        _envelope("ceo", scope=_scope(**{field: value}))


def test_mismatched_pair_scope_head_and_sequence_block():
    for changed in (
        _scope(dataset_digest=_digest("9")),
        _scope(expected_control_head="9" * 64),
        _scope(expected_sequence=43),
    ):
        with pytest.raises(EvidenceActionContractError):
            _description(operator_payload=_envelope("operator", scope=changed))


def test_intent_digest_changes_for_every_scope_field_and_signature():
    baseline = _description()
    baseline_ceo = asdict(baseline.ceo_envelope)
    baseline_operator = asdict(baseline.operator_envelope)
    for name in baseline.scope.__dataclass_fields__:
        value = getattr(baseline.scope, name)
        if name == "expected_sequence":
            replacement = value + 1
        elif name in {"promotion_pass_event_hash", "expected_control_head"}:
            replacement = "9" * 64
        elif name == "control_ledger_id":
            replacement = "9" * 32
        elif name == "git_sha":
            replacement = "9" * 40
        elif name == "pit_cutoff":
            replacement = "2026-07-30T04:59:59Z"
        elif isinstance(value, str) and value.startswith("sha256:"):
            replacement = _digest("9")
        else:
            replacement = value + "-changed"
        changed_scope = _scope(**{name: replacement})
        changed = _description(
            ceo_payload=_envelope("ceo", scope=changed_scope),
            operator_payload=_envelope("operator", scope=changed_scope),
        )
        assert changed.intent_digest != baseline.intent_digest, name
    for role, original in (
        ("ceo", baseline_ceo),
        ("operator", baseline_operator),
    ):
        changed_payload = {**original, "signature": "9" * 128}
        changed = _description(**{f"{role}_payload": changed_payload})
        assert changed.intent_digest != baseline.intent_digest


@pytest.mark.parametrize("role", ["ceo", "operator"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("actor", "changed-actor@example.test"),
        ("issued_at", (NOW - timedelta(seconds=30)).isoformat()),
        ("expires_at", (NOW + timedelta(minutes=9)).isoformat()),
        ("nonce", "changed-intent-nonce"),
        ("key_id", "changed-v4-key"),
    ],
)
def test_intent_digest_changes_for_each_valid_identity_and_time_field(
    role, field, value
):
    baseline = _description()
    changed_payload = _envelope(role, **{field: value})
    changed = _description(**{f"{role}_payload": changed_payload})
    assert changed.intent_digest != baseline.intent_digest


def test_shared_crypto_goldens_verify_without_conferring_runtime_trust():
    vectors = json.loads(
        Path("tests/fixtures/release_evidence_action_v4_golden.json").read_text()
    )
    assert vectors["schema"] == "trustforge.release-evidence-action-golden/v1"
    valid = vectors["valid"]
    assert valid["ceo"] == _envelope("ceo")
    assert valid["operator"] == _envelope("operator")
    Ed25519PrivateKey.from_private_bytes(CEO_SEED).public_key().verify(
        bytes.fromhex(valid["ceo"]["signature"]),
        evidence_action_signing_bytes(
            {key: value for key, value in valid["ceo"].items() if key != "signature"},
            role="ceo",
        ),
    )
    Ed25519PrivateKey.from_private_bytes(OPERATOR_SEED).public_key().verify(
        bytes.fromhex(valid["operator"]["signature"]),
        evidence_action_signing_bytes(
            {
                key: value
                for key, value in valid["operator"].items()
                if key != "signature"
            },
            role="operator",
        ),
    )
    assert vectors["public_keys"]["ceo-v4-1"] == _public(CEO_SEED).hex()
    assert vectors["public_keys"]["operator-v4-1"] == _public(OPERATOR_SEED).hex()
    assert {case["name"] for case in vectors["invalid"]} == {
        "legacy-v1",
        "legacy-v2",
        "legacy-v3",
        "start",
        "start-canary",
        "wrong-role-domain",
        "wrong-scope",
        "wrong-head",
        "wrong-sequence",
        "expired",
        "overlong-lifetime",
        "replay",
        "stale-current-state",
        "fork-current-state",
        "duplicate-raw-key-alias",
    }
    assert all(case["expected"] == "BLOCK" for case in vectors["invalid"])
    assert all(
        case["enforced_by"] in {"B1A", "B1B_OS_TRUST"} for case in vectors["invalid"]
    )


_GOLDEN_INVALID = json.loads(
    Path("tests/fixtures/release_evidence_action_v4_golden.json").read_text()
)["invalid"]


@pytest.mark.parametrize(
    "case",
    [item for item in _GOLDEN_INVALID if item["enforced_by"] == "B1A"],
    ids=lambda item: item["name"],
)
def test_each_b1a_invalid_golden_is_executable_and_blocks(case):
    field = case["mutation"]["field"]
    value = case["mutation"]["value"]
    if field == "signature_domain":
        operator = _envelope("operator")
        unsigned = {key: item for key, item in operator.items() if key != "signature"}
        wrong_signature = Ed25519PrivateKey.from_private_bytes(OPERATOR_SEED).sign(
            CEO_SIGNING_DOMAIN + canonical_json(unsigned)
        )
        with pytest.raises(InvalidSignature):
            Ed25519PrivateKey.from_private_bytes(OPERATOR_SEED).public_key().verify(
                wrong_signature,
                evidence_action_signing_bytes(unsigned, role="operator"),
            )
        return
    ceo = _envelope("ceo")
    operator = _envelope("operator")
    target = ceo if field in {"version", "action", "expires_at"} else operator
    target[field] = value
    with pytest.raises(EvidenceActionContractError):
        describe_evidence_action_intent(
            ceo_payload=ceo,
            operator_payload=operator,
            observed_at=NOW,
        )


def test_b1b_goldens_are_explicitly_deferred_not_locally_claimed():
    deferred = {
        item["name"]
        for item in _GOLDEN_INVALID
        if item["enforced_by"] == "B1B_OS_TRUST"
    }
    assert deferred == {
        "replay",
        "stale-current-state",
        "fork-current-state",
        "duplicate-raw-key-alias",
    }


def test_non_evidence_deployment_authorization_v3_remains_strict(tmp_path):
    helpers = runpy.run_path("tests/test_deployment_control.py")
    control = helpers["_control"](tmp_path, clock=lambda: helpers["NOW"])
    receipt = helpers["_authorization"](control, "start", "legacy-v3")
    snapshot = control.routing_snapshot()
    control._validate_authorization_receipt(
        receipt,
        action="start",
        ledger_id=snapshot.ledger_id,
        effective_at=helpers["NOW"],
        expected_control_head=control._records()[-1]["event_hash"],
        expected_sequence=len(control._records()) + 1,
    )
    unsigned = {
        **receipt.unsigned(),
        "action": EVIDENCE_ACTION,
    }
    signature = (
        Ed25519PrivateKey.from_private_bytes(helpers["AUTH_KEY"])
        .sign(b"trustforge.deployment-authorization.v3\x00" + canonical_json(unsigned))
        .hex()
    )
    evidence_action = DeploymentAuthorization(**unsigned, signature=signature)
    with pytest.raises(DeploymentControlError, match="binding"):
        control._validate_authorization_receipt(
            evidence_action,
            action="start",
            ledger_id=snapshot.ledger_id,
            effective_at=helpers["NOW"],
            expected_control_head=control._records()[-1]["event_hash"],
            expected_sequence=len(control._records()) + 1,
        )


def test_contract_has_no_publication_or_runtime_authority_api():
    import trustforge.evidence_action_intent as module

    exported = set(module.__all__)
    assert not any(
        forbidden in name.lower()
        for name in exported
        for forbidden in ("publish", "eligible", "authorized", "consume_nonce")
    )
    source = Path(module.__file__).read_text()
    assert "EvidenceTransactionStore" not in source
    assert "._publish(" not in source
