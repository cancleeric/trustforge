#!/usr/bin/env python3
"""Audited metadata migration for fully verified signed release ledgers."""

from __future__ import annotations

import argparse
import grp
import json
import os
import pwd
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trustforge.signed_event_ledger import SignedEventLedger  # noqa: E402

CONTROL_KINDS = frozenset(
    {
        "deployment_initialized",
        "operator_stop",
        "activation_prepared",
        "activation_completed",
        "activation_failed",
    }
)
OUTCOME_KINDS = frozenset(
    {"candidate_reservation", "candidate_result", "router_emergency_stop"}
)


def _keys(path: Path) -> dict[str, bytes]:
    info = os.lstat(path)
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o400
    ):
        raise SystemExit(f"unsafe verification key file: {path}")
    value = json.loads(path.read_text())
    decoded = {name: bytes.fromhex(encoded) for name, encoded in value.items()}
    if not decoded or any(len(key) != 32 for key in decoded.values()):
        raise SystemExit("verification keys are invalid")
    return decoded


def _verified_projection(
    root: Path, directory: str, keys: dict[str, bytes], domain: str, kinds
) -> SignedEventLedger:
    root_info = os.lstat(root)
    directory_info = os.lstat(root / directory)
    file_info = os.lstat(root / directory / "bootstrap.json")
    projection = SignedEventLedger(
        directory=root / directory,
        verification_keys=keys,
        event_permissions={domain: kinds},
        domain_keys={domain: frozenset(keys)},
        ledger_role=(
            "release-router-outcomes"
            if directory == "router-outcomes"
            else "release-control"
        ),
        coordination_root=root,
        root_owner_uid=root_info.st_uid,
        root_group=grp.getgrgid(root_info.st_gid).gr_name,
        root_mode=stat.S_IMODE(root_info.st_mode),
        directory_owner_uid=directory_info.st_uid,
        directory_group=grp.getgrgid(directory_info.st_gid).gr_name,
        directory_mode=stat.S_IMODE(directory_info.st_mode),
        file_mode=stat.S_IMODE(file_info.st_mode),
    )
    projection.read()  # Full schema, hash-chain, signature and signed-head validation.
    return projection


def _copy_ledger(source: Path, target: Path, owner: int, group: int) -> None:
    target.mkdir(mode=0o750)
    os.chown(target, owner, group)
    os.chmod(target, 0o750)
    for entry in source.iterdir():
        info = os.lstat(entry)
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise SystemExit(f"unsafe ledger migration entry: {entry}")
        destination = target / entry.name
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o640,
        )
        try:
            os.fchmod(descriptor, 0o640)
            os.fchown(descriptor, owner, group)
            with entry.open("rb") as source_stream:
                while chunk := source_stream.read(1024 * 1024):
                    remaining = memoryview(chunk)
                    while remaining:
                        written = os.write(descriptor, remaining)
                        if written <= 0:
                            raise OSError("short migration write")
                        remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--control-public", type=Path, required=True)
    parser.add_argument("--outcome-public", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("ledger migration requires root")
    control_keys = _keys(args.control_public)
    outcome_keys = _keys(args.outcome_public)
    # Nothing below mutates until both independent ledgers fully authenticate.
    _verified_projection(
        args.source_root, "control", control_keys, "release-control", CONTROL_KINDS
    )
    _verified_projection(
        args.source_root,
        "router-outcomes",
        outcome_keys,
        "release-router-outcome",
        OUTCOME_KINDS,
    )
    release_gid = grp.getgrnam("trustforge-release").gr_gid
    operator_uid = pwd.getpwnam("trustforge-operator").pw_uid
    router_uid = pwd.getpwnam("trustforge-router").pw_uid
    stage = args.target_root.with_name(args.target_root.name + ".staging")
    backup = args.target_root.with_name(args.target_root.name + ".rollback")
    if stage.exists() or backup.exists():
        raise SystemExit("migration staging or rollback path already exists")
    stage.mkdir(mode=0o750)
    os.chown(stage, 0, release_gid)
    os.chmod(stage, 0o750)
    _copy_ledger(
        args.source_root / "control",
        stage / "control",
        operator_uid,
        release_gid,
    )
    _copy_ledger(
        args.source_root / "router-outcomes",
        stage / "router-outcomes",
        router_uid,
        release_gid,
    )
    _verified_projection(
        stage, "control", control_keys, "release-control", CONTROL_KINDS
    )
    _verified_projection(
        stage,
        "router-outcomes",
        outcome_keys,
        "release-router-outcome",
        OUTCOME_KINDS,
    )
    parent_fd = os.open(stage.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        if args.target_root.exists():
            os.rename(args.target_root, backup)
        try:
            os.rename(stage, args.target_root)
            os.fsync(parent_fd)
        except BaseException:
            if backup.exists() and not args.target_root.exists():
                os.rename(backup, args.target_root)
                os.fsync(parent_fd)
            raise
    finally:
        os.close(parent_fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
