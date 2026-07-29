#!/usr/bin/env python3
"""Offline-only bootstrap of the verified release-gate audit ledger."""

from __future__ import annotations

import argparse
import grp
import json
import os
import pwd
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from trustforge.secure_keyring import (
    SecureKeyringError,
    decode_private_keyring,
    read_private_keyring,
)
from trustforge.signed_event_ledger import SignedEventLedger
from trustforge.verified_receipt_release_gate import (
    AUDIT_INTENT_KIND,
    AUDIT_OUTCOME_KIND,
)

DOMAIN = "verified-receipt-release-gate"
MAX_PIPE_CREDENTIAL_BYTES = 32_768


def _read_pipe_credential(descriptor: int):
    raw = bytearray()
    try:
        while True:
            remaining = MAX_PIPE_CREDENTIAL_BYTES + 1 - len(raw)
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > MAX_PIPE_CREDENTIAL_BYTES:
                raise SecureKeyringError(
                    "pipe credential exceeds maximum size"
                )
        if not raw:
            raise SecureKeyringError("pipe credential is empty")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise SecureKeyringError("pipe credential is invalid JSON") from exc
        return decode_private_keyring(value)
    finally:
        os.close(descriptor)
        for index in range(len(raw)):
            raw[index] = 0


def _remove_created(path: Path, identity: tuple[int, int]) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or (info.st_dev, info.st_ino) != identity
    ):
        raise RuntimeError("refusing to remove replaced audit ledger")
    shutil.rmtree(path)


def _bootstrap_worker(args) -> int:
    identity = _identity(args)
    if not args.test_owner_current_user:
        release_gid = grp.getgrnam(identity["group"]).gr_gid
        os.setgroups([release_gid])
        os.setgid(release_gid)
        os.setuid(identity["owner_uid"])
    key_id, private, public = _read_pipe_credential(args.bootstrap_fd)
    if args.test_worker_hang:
        time.sleep(5)
    if args.test_worker_failure:
        raise RuntimeError("injected bootstrap worker failure")
    SignedEventLedger(
        directory=args.ledger_root / "verified-release-gate-audit",
        verification_keys=public,
        event_permissions={
            DOMAIN: frozenset({AUDIT_INTENT_KIND, AUDIT_OUTCOME_KIND})
        },
        domain_keys={DOMAIN: frozenset(public)},
        signing_key_id=key_id,
        signing_private_key=private,
        signing_domain=DOMAIN,
        ledger_role="verified-receipt-release-gate",
        bootstrap=True,
        coordination_root=args.ledger_root,
        coordination_lock_path=args.ledger_root / "coordination.lock",
        coordination_lock_mode=identity["lock_mode"],
        coordination_lock_owner_uid=identity["root_uid"],
        coordination_lock_group=identity["group"],
        root_owner_uid=identity["root_uid"],
        root_group=identity["group"],
        root_mode=0o750,
        directory_owner_uid=identity["owner_uid"],
        directory_group=identity["group"],
        directory_mode=identity["directory_mode"],
        file_mode=identity["file_mode"],
    )
    return 0


def _identity(args) -> dict:
    test = args.test_owner_current_user
    return {
        "root_uid": os.geteuid() if test else 0,
        "group": (
            grp.getgrgid(os.getegid()).gr_name
            if test
            else "trustforge-release"
        ),
        "owner_uid": (
            os.geteuid()
            if test
            else pwd.getpwnam("trustforge-operator").pw_uid
        ),
        "lock_mode": 0o600 if test else 0o660,
        "directory_mode": 0o700 if test else 0o750,
        "file_mode": 0o600 if test else 0o640,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger-root", type=Path, required=True)
    parser.add_argument("--bootstrap-keyring", type=Path)
    parser.add_argument(
        "--bootstrap-fd", type=int, default=-1, help=argparse.SUPPRESS
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--bootstrap-worker", action="store_true", help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--test-worker-failure", action="store_true", help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--test-worker-hang", action="store_true", help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--worker-timeout",
        type=float,
        default=15.0,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--test-owner-current-user", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.bootstrap_worker:
        if args.bootstrap_fd < 0:
            raise SystemExit("bootstrap worker credential fd is required")
        return _bootstrap_worker(args)
    target = args.ledger_root / "verified-release-gate-audit"
    if not args.apply:
        print(f"provision {target} using existing shared coordination.lock")
        return 0
    if args.bootstrap_keyring is None:
        raise SystemExit("bootstrap keyring is required")
    if os.geteuid() != 0 and not args.test_owner_current_user:
        raise SystemExit("audit ledger provisioning requires root")
    if args.test_owner_current_user and Path(tempfile.gettempdir()).resolve() not in (
        args.ledger_root.resolve(),
        *args.ledger_root.resolve().parents,
    ):
        raise SystemExit("test ownership mode requires temporary root")
    root_info = args.ledger_root.lstat()
    lock = args.ledger_root / "coordination.lock"
    lock_info = lock.lstat()
    identity_values = _identity(args)
    root_uid = identity_values["root_uid"]
    group = identity_values["group"]
    owner_uid = identity_values["owner_uid"]
    root_mode = 0o750
    lock_mode = identity_values["lock_mode"]
    directory_mode = identity_values["directory_mode"]
    file_mode = identity_values["file_mode"]
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or stat.S_ISLNK(root_info.st_mode)
        or root_info.st_uid != root_uid
        or stat.S_IMODE(root_info.st_mode) != root_mode
        or not stat.S_ISREG(lock_info.st_mode)
        or stat.S_ISLNK(lock_info.st_mode)
        or lock_info.st_uid != root_uid
        or stat.S_IMODE(lock_info.st_mode) != lock_mode
    ):
        raise SystemExit("shared ledger root or coordination lock is unsafe")
    if target.exists() or target.is_symlink():
        raise SystemExit("audit ledger already exists")
    target.mkdir(mode=directory_mode)
    info = target.lstat()
    identity = (info.st_dev, info.st_ino)
    try:
        release_gid = grp.getgrnam(group).gr_gid
        target_fd = os.open(
            target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        try:
            target_info = os.fstat(target_fd)
            if (target_info.st_dev, target_info.st_ino) != identity:
                raise RuntimeError("audit ledger was replaced before ownership")
            os.fchmod(target_fd, directory_mode)
            os.fchown(target_fd, owner_uid, release_gid)
        finally:
            os.close(target_fd)
        key_id, private, public = read_private_keyring(args.bootstrap_keyring)
        payload = bytearray(
            json.dumps(
                {
                    "key_id": key_id,
                    "private_key": private.hex(),
                    "verification_keys": {
                        item: value.hex() for item, value in public.items()
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        try:
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--ledger-root",
                str(args.ledger_root),
                "--apply",
                "--bootstrap-worker",
                "--bootstrap-fd",
                "0",
            ]
            if args.test_owner_current_user:
                command.append("--test-owner-current-user")
            if args.test_worker_failure:
                command.append("--test-worker-failure")
            if args.test_worker_hang:
                command.append("--test-worker-hang")
            subprocess.run(
                command,
                input=bytes(payload),
                check=True,
                close_fds=True,
                timeout=args.worker_timeout,
            )
        finally:
            for index in range(len(payload)):
                payload[index] = 0
        after = target.lstat()
        if (
            (after.st_dev, after.st_ino) != identity
            or after.st_uid != owner_uid
            or after.st_gid != release_gid
            or stat.S_IMODE(after.st_mode) != directory_mode
        ):
            raise RuntimeError("audit ledger ownership changed after bootstrap")
        for child in target.iterdir():
            child_info = child.lstat()
            if (
                not stat.S_ISREG(child_info.st_mode)
                or child_info.st_nlink != 1
                or child_info.st_uid != owner_uid
                or child_info.st_gid != release_gid
                or stat.S_IMODE(child_info.st_mode) != file_mode
            ):
                raise RuntimeError("audit ledger file metadata is unsafe")
    except BaseException:
        _remove_created(target, identity)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
