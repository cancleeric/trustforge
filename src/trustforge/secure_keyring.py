"""Descriptor-bound JSON and Ed25519 keyring loading for release workers."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


class SecureKeyringError(RuntimeError):
    pass


def read_protected_json(
    path: Path,
    *,
    private: bool,
    max_bytes: int = 32_768,
) -> Any:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise SecureKeyringError("protected input cannot be opened") from exc
    try:
        before = os.fstat(descriptor)
        forbidden = 0o077 if private else 0o022
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(before.st_mode) & forbidden
            or before.st_size > max_bytes
        ):
            raise SecureKeyringError("protected input metadata is unsafe")
        raw = os.read(descriptor, max_bytes + 1)
        after = os.fstat(descriptor)
        def identity(info):
            return (
                info.st_dev,
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )
        if (
            len(raw) > max_bytes
            or len(raw) != before.st_size
            or identity(before) != identity(after)
        ):
            raise SecureKeyringError("protected input changed during read")
    finally:
        os.close(descriptor)
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise SecureKeyringError("protected input is invalid JSON") from exc


def _public_mapping(value: Any) -> dict[str, bytes]:
    if not isinstance(value, dict) or not value:
        raise SecureKeyringError("verification key mapping is empty")
    keys: dict[str, bytes] = {}
    try:
        for key_id, encoded in value.items():
            if not isinstance(key_id, str) or not key_id:
                raise SecureKeyringError("verification key id is invalid")
            raw = bytes.fromhex(encoded)
            if len(raw) != 32:
                raise SecureKeyringError("verification key length is invalid")
            keys[key_id] = raw
    except (AttributeError, TypeError, ValueError) as exc:
        raise SecureKeyringError("verification key encoding is invalid") from exc
    return keys


def read_public_keyring(path: Path) -> dict[str, bytes]:
    value = read_protected_json(path, private=False)
    if not isinstance(value, dict) or set(value) != {"verification_keys"}:
        raise SecureKeyringError("public keyring contract is invalid")
    return _public_mapping(value["verification_keys"])


def read_private_keyring(
    path: Path,
) -> tuple[str, bytes, dict[str, bytes]]:
    return decode_private_keyring(read_protected_json(path, private=True))


def decode_private_keyring(
    value: Any,
) -> tuple[str, bytes, dict[str, bytes]]:
    if not isinstance(value, dict) or set(value) != {
        "key_id",
        "private_key",
        "verification_keys",
    }:
        raise SecureKeyringError("private keyring contract is invalid")
    public = _public_mapping(value["verification_keys"])
    try:
        private = bytes.fromhex(value["private_key"])
        if len(private) != 32:
            raise ValueError
        derived = (
            Ed25519PrivateKey.from_private_bytes(private)
            .public_key()
            .public_bytes(Encoding.Raw, PublicFormat.Raw)
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise SecureKeyringError("private signing key is invalid") from exc
    key_id = value["key_id"]
    if (
        not isinstance(key_id, str)
        or not key_id
        or key_id not in public
        or public[key_id] != derived
    ):
        raise SecureKeyringError("private and verification keys do not match")
    return key_id, private, public
