#!/usr/bin/env python3
"""Offline bootstrap for the dedicated intrinsic-promotion receipt ledger."""

from __future__ import annotations

import argparse
import grp
import json
import os
import pwd
import shutil
import stat
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from trustforge.asset_intrinsic_promotion_receipt import (
    EVENT_KIND,
    FAILURE_EVENT_KIND,
    SIGNER_DOMAIN,
)
from trustforge.signed_event_ledger import SignedEventLedger

_MAX_KEYRING_BYTES = 32_768


def _read_private_keyring(path: Path) -> tuple[str, bytes, dict[str, bytes]]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid not in {0, os.geteuid()}
            or before.st_mode & 0o077
            or before.st_size > _MAX_KEYRING_BYTES
        ):
            raise SystemExit("bootstrap keyring ownership or mode is unsafe")
        raw = os.read(descriptor, _MAX_KEYRING_BYTES + 1)
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if (
            len(raw) > _MAX_KEYRING_BYTES
            or len(raw) != before.st_size
            or identity_before != identity_after
        ):
            raise SystemExit("bootstrap keyring changed during read")
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {
            "key_id",
            "private_key",
            "verification_keys",
        }:
            raise ValueError
        key_id = value["key_id"]
        private = bytes.fromhex(value["private_key"])
        verification_keys = value["verification_keys"]
        if not isinstance(verification_keys, dict) or not verification_keys:
            raise ValueError
        public = {}
        for item, encoded in verification_keys.items():
            if not isinstance(item, str) or not item:
                raise ValueError
            decoded = bytes.fromhex(encoded)
            if len(decoded) != 32:
                raise ValueError
            public[item] = decoded
        if len(private) != 32:
            raise ValueError
        derived = (
            Ed25519PrivateKey.from_private_bytes(private)
            .public_key()
            .public_bytes(Encoding.Raw, PublicFormat.Raw)
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise SystemExit("bootstrap keyring contract is invalid") from exc
    if not isinstance(key_id, str) or key_id not in public or public[key_id] != derived:
        raise SystemExit("bootstrap signing key does not match verification key")
    return key_id, private, public


def _remove_owned_staging(path: Path, identity: tuple[int, int]) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or (info.st_dev, info.st_ino) != identity
    ):
        raise RuntimeError("refusing to remove replaced bootstrap staging directory")
    shutil.rmtree(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger-root", type=Path, required=True)
    parser.add_argument("--keyring", type=Path, required=True)
    parser.add_argument("--service-user", default="trustforge-receipt")
    parser.add_argument("--group", default="trustforge-release")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--test-owner-current-user", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print(
            json.dumps(
                {
                    "operation": "bootstrap-intrinsic-promotion-ledger",
                    "ledger_root": str(args.ledger_root),
                    "directory": str(
                        args.ledger_root / "intrinsic-promotion-receipts"
                    ),
                    "coordination_lock": str(args.ledger_root / "coordination.lock"),
                    "root_mode": "0750",
                    "directory_mode": "0750",
                    "file_mode": "0640",
                    "lock_mode": "0660",
                },
                sort_keys=True,
            )
        )
        return 0
    if os.geteuid() != 0 and not args.test_owner_current_user:
        raise SystemExit("ledger provisioning requires root")
    if args.test_owner_current_user and args.ledger_root == Path("/"):
        raise SystemExit("test ownership mode cannot target root")
    owner_uid = (
        os.geteuid()
        if args.test_owner_current_user
        else pwd.getpwnam(args.service_user).pw_uid
    )
    root_uid = os.geteuid() if args.test_owner_current_user else 0
    group = (
        grp.getgrgid(os.getegid()).gr_name
        if args.test_owner_current_user
        else args.group
    )
    key_id, private, public = _read_private_keyring(args.keyring)
    target = args.ledger_root
    if target.exists() or target.is_symlink():
        raise SystemExit("ledger root already exists")
    target.parent.resolve(strict=True)
    target.mkdir(mode=0o750, exist_ok=False)
    staging = target
    staging_info = staging.lstat()
    staging_identity = (staging_info.st_dev, staging_info.st_ino)
    os.chmod(staging, 0o750)
    group_gid = grp.getgrnam(group).gr_gid
    try:
        os.chown(staging, root_uid, group_gid)
        lock = staging / "coordination.lock"
        descriptor = os.open(
            lock,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o660,
        )
        os.close(descriptor)
        os.chmod(lock, 0o660)
        os.chown(lock, root_uid, group_gid)
        receipt_directory = staging / "intrinsic-promotion-receipts"
        receipt_directory.mkdir(mode=0o750)
        os.chmod(receipt_directory, 0o750)
        os.chown(receipt_directory, owner_uid, group_gid)
        SignedEventLedger(
            directory=receipt_directory,
            verification_keys=public,
            event_permissions={
                SIGNER_DOMAIN: frozenset({EVENT_KIND, FAILURE_EVENT_KIND})
            },
            domain_keys={SIGNER_DOMAIN: frozenset(public)},
            signing_key_id=key_id,
            signing_private_key=private,
            signing_domain=SIGNER_DOMAIN,
            ledger_role="intrinsic-promotion-receipts",
            bootstrap=True,
            coordination_root=staging,
            coordination_lock_path=lock,
            coordination_lock_mode=0o660,
            coordination_lock_owner_uid=root_uid,
            coordination_lock_group=group,
            root_owner_uid=root_uid,
            root_group=group,
            root_mode=0o750,
            directory_owner_uid=owner_uid,
            directory_group=group,
            directory_mode=0o750,
            file_mode=0o640,
        )
    except BaseException:
        _remove_owned_staging(staging, staging_identity)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
