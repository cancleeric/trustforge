#!/usr/bin/env python3
"""Verify the root-pinned intended release receipt before router installation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from trustforge.release_router import ReleaseRoutingError, RoutingPolicy
from trustforge.signed_event_ledger import SignedEventLedger

SCHEMA = "trustforge.release-install-evidence/v2"
SIGNING_DOMAIN = b"trustforge.release-install-evidence.v2\x00"
FIELDS = (
    "unit_sha256",
    "runtime_sha256",
    "keys_sha256",
    "control_bootstrap_sha256",
    "control_events_sha256",
    "control_head_sha256",
    "outcome_bootstrap_sha256",
    "a_artifact_sha256",
    "b_artifact_sha256",
    "endpoint_manifests_sha256",
    "router_archive_sha256",
    "router_tree_manifest_sha256",
    "runtime_lock_sha256",
    "canary_allowlist_sha256",
)
IDENTITY_FIELDS = ("control_ledger_id", "control_ledger_head")


def _regular(path: Path, *, mode: int | None = None) -> int:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) & 0o022
        or (mode is not None and stat.S_IMODE(info.st_mode) != mode)
    ):
        os.close(fd)
        raise SystemExit(f"unsafe release evidence input: {path}")
    return fd


def _digest(path: Path) -> str:
    fd = _regular(path)
    digest = hashlib.sha256()
    try:
        while chunk := os.read(fd, 1024 * 1024):
            digest.update(chunk)
    finally:
        os.close(fd)
    return digest.hexdigest()


def _json_file(path: Path) -> dict:
    fd = _regular(path)
    try:
        info = os.fstat(fd)
        if info.st_size > 1024 * 1024:
            raise SystemExit(f"release JSON input is oversized: {path}")
        raw = os.read(fd, 1024 * 1024 + 1)
    finally:
        os.close(fd)
    value = json.loads(raw)
    if (
        not isinstance(value, dict)
        or json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        != raw
    ):
        raise SystemExit(f"release JSON input is noncanonical: {path}")
    return value


def _key_mapping(payload: dict, role: str) -> dict[str, bytes]:
    value = payload.get(role)
    if not isinstance(value, dict) or not value:
        raise SystemExit(f"runtime {role} key mapping is invalid")
    try:
        decoded = {key: bytes.fromhex(item) for key, item in value.items()}
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"runtime {role} key mapping is invalid") from exc
    if any(not key or len(item) != 32 for key, item in decoded.items()):
        raise SystemExit(f"runtime {role} key mapping is invalid")
    return decoded


def _control_ledger(
    args: argparse.Namespace,
    keys: dict,
    *,
    coordination_lock_path: Path | None = None,
) -> SignedEventLedger:
    control_keys = _key_mapping(keys, "control_event_public")
    directory = args.control_bootstrap.parent
    if (
        args.control_events != directory / "events.jsonl"
        or args.control_head != directory / "head.json"
    ):
        raise SystemExit("control ledger file paths are not canonical")
    root = directory.parent
    directory_info, root_info = os.lstat(directory), os.lstat(root)
    bootstrap_info = os.lstat(args.control_bootstrap)
    coordination = (
        {}
        if coordination_lock_path is None
        else {
            "coordination_lock_path": coordination_lock_path,
            "coordination_lock_mode": 0o660,
            "coordination_lock_owner_uid": 0,
            "coordination_lock_group": "trustforge-release",
        }
    )
    return SignedEventLedger(
        directory=directory,
        verification_keys=control_keys,
        event_permissions={
            "release-control": frozenset(
                {
                    "deployment_initialized",
                    "operator_stop",
                    "activation_prepared",
                    "activation_completed",
                    "activation_failed",
                }
            )
        },
        domain_keys={"release-control": frozenset(control_keys)},
        ledger_role="release-control",
        coordination_root=root,
        root_owner_uid=root_info.st_uid,
        root_mode=stat.S_IMODE(root_info.st_mode),
        directory_owner_uid=directory_info.st_uid,
        directory_mode=stat.S_IMODE(directory_info.st_mode),
        file_mode=stat.S_IMODE(bootstrap_info.st_mode),
        **coordination,
    )


def _verified_control_history(args: argparse.Namespace, keys: dict) -> list[dict]:
    records = _control_ledger(args, keys).read()
    if not records or records[0]["event"].get("kind") != "deployment_initialized":
        raise SystemExit("authenticated control ledger is not initialized")
    return records


def _verify_outcome_projection(args: argparse.Namespace, keys: dict) -> None:
    outcome_keys = _key_mapping(keys, "router_outcome_public")
    directory = args.outcome_bootstrap.parent
    root = directory.parent
    directory_info, root_info = os.lstat(directory), os.lstat(root)
    bootstrap_info = os.lstat(args.outcome_bootstrap)
    SignedEventLedger(
        directory=directory,
        verification_keys=outcome_keys,
        event_permissions={
            "release-router-outcome": frozenset(
                {
                    "candidate_reservation",
                    "candidate_result",
                    "candidate_cost_reconciliation",
                    "router_emergency_stop",
                }
            )
        },
        domain_keys={"release-router-outcome": frozenset(outcome_keys)},
        ledger_role="release-router-outcomes",
        coordination_root=root,
        root_owner_uid=root_info.st_uid,
        root_mode=stat.S_IMODE(root_info.st_mode),
        directory_owner_uid=directory_info.st_uid,
        directory_mode=stat.S_IMODE(directory_info.st_mode),
        file_mode=stat.S_IMODE(bootstrap_info.st_mode),
    ).read()


def _verify_endpoint_semantics(args: argparse.Namespace, payload: dict) -> None:
    a_digest = "sha256:" + _digest(args.a_artifact)
    b_digest = "sha256:" + _digest(args.b_artifact)
    bundle = _json_file(args.endpoint_manifests)
    if (
        set(bundle) != {"schema", "a", "b", "public_keys"}
        or bundle.get("schema") != "trustforge.endpoint-manifest-bundle/v1"
    ):
        raise SystemExit("endpoint manifest bundle schema is invalid")
    keys = bundle["public_keys"]
    if not isinstance(keys, dict) or not keys:
        raise SystemExit("endpoint manifest bundle keyring is invalid")
    key_ids: list[str] = []
    for role, expected_digest in (("a", a_digest), ("b", b_digest)):
        manifest = bundle[role]
        if not isinstance(manifest, dict) or set(manifest) != {
            "schema",
            "artifact_digest",
            "origin",
            "key_id",
            "signature",
        }:
            raise SystemExit("endpoint manifest is incomplete")
        key_id = manifest["key_id"]
        try:
            unsigned = {
                key: value for key, value in manifest.items() if key != "signature"
            }
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(keys[key_id])).verify(
                bytes.fromhex(manifest["signature"]),
                b"trustforge.endpoint-manifest.v1\x00"
                + json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode(),
            )
        except (KeyError, TypeError, ValueError, InvalidSignature) as exc:
            raise SystemExit("endpoint manifest signature is invalid") from exc
        if (
            manifest["schema"] != "trustforge.endpoint-manifest/v1"
            or manifest["artifact_digest"] != expected_digest
        ):
            raise SystemExit("endpoint manifest artifact identity is unrelated")
        key_ids.append(key_id)
    runtime_keys = _json_file(args.keys)
    if set(runtime_keys) != {
        "control_event_public",
        "router_outcome_public",
        "router_outcome_private",
        "routing",
        "endpoint_manifest_public",
        "authorization_public",
        "completion_public",
        "canary_cost_budget_public",
    }:
        raise SystemExit("runtime key roles are incomplete or over-privileged")
    endpoint_runtime = _key_mapping(runtime_keys, "endpoint_manifest_public")
    _key_mapping(runtime_keys, "canary_cost_budget_public")
    try:
        endpoint_bundle = {key: bytes.fromhex(value) for key, value in keys.items()}
    except (TypeError, ValueError) as exc:
        raise SystemExit("endpoint manifest bundle keyring is invalid") from exc
    if endpoint_runtime != endpoint_bundle or set(key_ids) != set(endpoint_bundle):
        raise SystemExit("runtime endpoint manifest keys do not exactly match bundle")
    records = _verified_control_history(args, runtime_keys)
    initialized = records[0]["event"]
    ledger_id, ledger_head = records[0]["ledger_id"], records[-1]["event_hash"]
    if (
        payload["control_ledger_id"] != ledger_id
        or payload["control_ledger_head"] != ledger_head
    ):
        raise SystemExit("release evidence control ledger identity mismatch")
    policy = initialized.get("policy")
    if (
        not isinstance(policy, dict)
        or initialized.get("active", {}).get("release_digest") != a_digest
        or initialized.get("candidate", {}).get("release_digest") != b_digest
    ):
        raise SystemExit("deployment initialization A/B identity mismatch")
    try:
        validated_policy = RoutingPolicy(**policy)
    except (TypeError, ReleaseRoutingError) as exc:
        raise SystemExit("deployment routing policy is invalid") from exc
    for role in ("a", "b"):
        endpoint = initialized["active" if role == "a" else "candidate"]
        if bundle[role]["origin"] != endpoint.get("base_url") or bundle[role][
            "key_id"
        ] != endpoint.get("manifest_key_id"):
            raise SystemExit("deployment initialization endpoint identity mismatch")
    routing_keys = _key_mapping(runtime_keys, "routing")
    routing_key_id = validated_policy.routing_key_id
    if routing_key_id not in routing_keys:
        raise SystemExit("deployment routing signer is unavailable")
    outcome_public = _key_mapping(runtime_keys, "router_outcome_public")
    outcome_private = _key_mapping(runtime_keys, "router_outcome_private")
    if len(outcome_private) != 1:
        raise SystemExit("runtime outcome signer cardinality is invalid")
    outcome_key_id, outcome_seed = next(iter(outcome_private.items()))
    derived = Ed25519PrivateKey.from_private_bytes(outcome_seed).public_key()
    if (
        outcome_key_id not in outcome_public
        or derived.public_bytes_raw() != outcome_public[outcome_key_id]
    ):
        raise SystemExit("runtime outcome signer identity mismatch")
    outcome_bootstrap = _json_file(args.outcome_bootstrap)
    if (
        outcome_bootstrap.get("key_id") not in outcome_public
        or outcome_bootstrap.get("key_id") == outcome_key_id
        or outcome_bootstrap.get("signer_domain") != "release-router-outcome"
    ):
        raise SystemExit("outcome bootstrap signer identity mismatch")
    _verify_outcome_projection(args, runtime_keys)
    runtime = _json_file(args.runtime)
    expected_runtime = {
        "schema": "trustforge.release-router-runtime/v1",
        "a_artifact_digest": a_digest,
        "b_artifact_digest": b_digest,
        "endpoint_manifest_key_ids": sorted(set(key_ids)),
        "control_ledger_id": ledger_id,
        "deployment_initialized_event_hash": records[0]["event_hash"],
        "routing_policy": policy,
        "outcome_signing_key_id": outcome_key_id,
    }
    if runtime != expected_runtime:
        raise SystemExit("runtime A/B identity does not match signed artifacts")
    runtime_lock = _json_file(args.runtime_lock)
    tree_digest = _digest(args.router_tree_manifest)
    tree = _json_file(args.router_tree_manifest)
    entries = tree.get("entries")
    if not isinstance(entries, list):
        raise SystemExit("router runtime lock requires canonical tree entries")
    python_entries = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("path") == ".venv/bin/python"
    ]
    distributions = runtime_lock.get("distributions")
    if (
        set(runtime_lock) != {"schema", "tree_manifest_sha256", "distributions"}
        or runtime_lock.get("schema") != "trustforge.router-runtime-lock/v2"
        or runtime_lock.get("tree_manifest_sha256") != tree_digest
        or len(python_entries) != 1
        or not isinstance(distributions, dict)
        or not distributions
        or any(
            not isinstance(name, str)
            or not name
            or not isinstance(claim, dict)
            or set(claim)
            != {"version", "dist_info", "metadata_sha256", "record_sha256"}
            or any(not isinstance(value, str) or not value for value in claim.values())
            for name, claim in distributions.items()
        )
    ):
        raise SystemExit("router runtime lock attestation is invalid")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--public-keyring", type=Path, required=True)
    for field in FIELDS:
        parser.add_argument(
            "--" + field.removesuffix("_sha256").replace("_", "-"),
            type=Path,
            required=True,
        )
    args = parser.parse_args()
    fd = _regular(args.evidence, mode=0o600)
    try:
        info = os.fstat(fd)
        if info.st_size > 4096:
            raise SystemExit("release evidence receipt is oversized")
        raw = os.read(fd, 4097)
        if len(raw) != info.st_size:
            raise SystemExit("release evidence changed during read")
    finally:
        os.close(fd)
    payload = json.loads(raw)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "key_id", "signature", *FIELDS, *IDENTITY_FIELDS}
        or payload.get("schema") != SCHEMA
        or json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        != raw
    ):
        raise SystemExit("release evidence receipt is noncanonical or incomplete")
    key_fd = _regular(args.public_keyring, mode=0o400)
    try:
        key_info = os.fstat(key_fd)
        if key_info.st_size > 4096:
            raise SystemExit("release evidence keyring is oversized")
        key_raw = os.read(key_fd, 4097)
        if len(key_raw) != key_info.st_size:
            raise SystemExit("release evidence keyring changed during read")
        keyring = json.loads(key_raw)
    finally:
        os.close(key_fd)
    key_id = payload["key_id"]
    if (
        not isinstance(keyring, dict)
        or not keyring
        or json.dumps(keyring, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        != key_raw
    ):
        raise SystemExit("release evidence keyring is invalid")
    try:
        encoded_key = keyring[key_id]
        unsigned = {key: value for key, value in payload.items() if key != "signature"}
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(encoded_key)).verify(
            bytes.fromhex(payload["signature"]),
            SIGNING_DOMAIN
            + json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode(),
        )
    except (KeyError, TypeError, ValueError, InvalidSignature) as exc:
        raise SystemExit("release evidence signature is invalid") from exc
    for field in FIELDS:
        path = getattr(args, field.removesuffix("_sha256"))
        expected = payload[field]
        if (
            not isinstance(expected, str)
            or len(expected) != 64
            or _digest(path) != expected
        ):
            raise SystemExit(f"intended release digest mismatch: {field}")
    if any(
        not isinstance(payload[field], str) or not payload[field]
        for field in IDENTITY_FIELDS
    ):
        raise SystemExit("release evidence ledger identity is invalid")
    _verify_endpoint_semantics(args, payload)
    print(payload["runtime_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
