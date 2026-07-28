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
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

SCHEMA = "trustforge.release-install-evidence/v1"
SIGNING_DOMAIN = b"trustforge.release-install-evidence.v1\x00"
FIELDS = (
    "unit_sha256",
    "runtime_sha256",
    "keys_sha256",
    "control_bootstrap_sha256",
    "outcome_bootstrap_sha256",
    "a_artifact_sha256",
    "b_artifact_sha256",
    "endpoint_manifests_sha256",
)


def _regular(path: Path, *, mode: int | None = None) -> int:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
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
        or set(payload) != {"schema", "key_id", "signature", *FIELDS}
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
    print(payload["runtime_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
