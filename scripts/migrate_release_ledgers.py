#!/usr/bin/env python3
"""Audited metadata migration for fully verified signed release ledgers."""

from __future__ import annotations

import argparse
import fcntl
import grp
import json
import os
import pwd
import shutil
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
ALLOWED_FIXED_FILES = frozenset({"bootstrap.json", "events.jsonl", "head.json"})
JOURNAL_SCHEMA = "trustforge.release-ledger-migration/v1"
JOURNAL_STATES = frozenset({"staged", "old-backed-up", "published", "committed"})


def _allowed_entry(name: str) -> bool:
    return name in ALLOWED_FIXED_FILES or (
        name.startswith("epoch-stop-") and name.endswith(".json")
    )


def _fsync_tree(root: Path) -> None:
    for directory, _, files in os.walk(root):
        for name in files:
            fd = os.open(Path(directory) / name, os.O_RDONLY | os.O_NOFOLLOW)
            os.fsync(fd)
            os.close(fd)
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        os.fsync(fd)
        os.close(fd)


def _remove_direct_directory(path: Path) -> None:
    parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            "/" in path.name
            or not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_dev != os.fstat(parent_fd).st_dev
        ):
            raise SystemExit("unsafe migration recovery directory")
        shutil.rmtree(path.name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _write_journal(path: Path, state: str, stage: Path, backup: Path) -> None:
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    encoded = (
        json.dumps(
            {
                "schema": JOURNAL_SCHEMA,
                "state": state,
                "stage": stage.name,
                "backup": backup.name,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )
    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.fchown(fd, os.geteuid(), os.getegid())
        os.fchmod(fd, 0o600)
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, path)
    parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    os.fsync(parent_fd)
    os.close(parent_fd)


def _recover(journal: Path, target: Path, stage: Path, backup: Path) -> None:
    if not journal.exists():
        return
    journal_fd = os.open(journal, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(journal_fd)
        if info.st_size > 4096:
            raise SystemExit("unknown migration recovery journal")
        raw = os.read(journal_fd, 4097)
        if len(raw) != info.st_size:
            raise SystemExit("migration journal changed during read")
    finally:
        os.close(journal_fd)
    payload = json.loads(raw)
    expected_names = (target.name + ".staging", target.name + ".rollback")
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or not isinstance(payload, dict)
        or set(payload) != {"schema", "state", "stage", "backup"}
        or json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        != raw
        or payload.get("schema") != JOURNAL_SCHEMA
        or payload.get("state") not in JOURNAL_STATES
        or (payload.get("stage"), payload.get("backup")) != expected_names
    ):
        raise SystemExit("unknown migration recovery journal")
    state = payload.get("state")
    # Until a committed journal exists, the authenticated old target wins.
    if state != "committed" and backup.exists():
        if target.exists():
            _remove_direct_directory(target)
        os.replace(backup, target)
    if stage.exists():
        _remove_direct_directory(stage)
    if backup.exists() and target.exists():
        _remove_direct_directory(backup)
    journal.unlink()
    parent_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    os.fsync(parent_fd)
    os.close(parent_fd)


def _publish_swap(stage: Path, target: Path, backup: Path, journal: Path) -> None:
    _write_journal(journal, "staged", stage, backup)
    if target.exists():
        os.replace(target, backup)
        _write_journal(journal, "old-backed-up", stage, backup)
    try:
        os.replace(stage, target)
        _write_journal(journal, "published", stage, backup)
        _fsync_tree(target)
        _write_journal(journal, "committed", stage, backup)
        if backup.exists():
            _remove_direct_directory(backup)
        journal.unlink()
        parent_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        os.fsync(parent_fd)
        os.close(parent_fd)
    except BaseException:
        _recover(journal, target, stage, backup)
        raise


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
    root: Path,
    directory: str,
    keys: dict[str, bytes],
    domain: str,
    kinds,
    *,
    verify: bool = True,
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
    if verify:
        projection.read()  # Full schema, chain, signature and head validation.
    return projection


def _copy_ledger(source: Path, target: Path, owner: int, group: int) -> None:
    target.mkdir(mode=0o750)
    os.chown(target, owner, group)
    os.chmod(target, 0o750)
    for entry in source.iterdir():
        if not _allowed_entry(entry.name):
            raise SystemExit(f"unknown ledger migration entry: {entry}")
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


def _copy_public_receipt(source_root: Path, stage: Path) -> None:
    source = source_root / "provision-receipt.json"
    if not source.exists():
        return
    info = os.lstat(source)
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o644
        or info.st_size > 4096
    ):
        raise SystemExit("unsafe provisioning receipt during migration")
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    target_fd = os.open(
        stage / "provision-receipt.json",
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o644,
    )
    try:
        raw = os.read(source_fd, 4097)
        if len(raw) != info.st_size:
            raise SystemExit("provisioning receipt changed during migration")
        os.fchown(target_fd, 0, 0)
        os.fchmod(target_fd, 0o644)
        remaining = memoryview(raw)
        while remaining:
            written = os.write(target_fd, remaining)
            if written <= 0:
                raise OSError("short provisioning receipt write")
            remaining = remaining[written:]
        os.fsync(target_fd)
    finally:
        os.close(source_fd)
        os.close(target_fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--control-public", type=Path, required=True)
    parser.add_argument("--outcome-public", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("ledger migration requires root")
    args.target_root.parent.mkdir(parents=True, exist_ok=True)
    parent_info = os.lstat(args.target_root.parent)
    if parent_info.st_uid != 0 or stat.S_IMODE(parent_info.st_mode) & 0o022:
        raise SystemExit("migration parent must be root-owned and not writable")
    stage = args.target_root.with_name(args.target_root.name + ".staging")
    backup = args.target_root.with_name(args.target_root.name + ".rollback")
    journal = args.target_root.with_name(args.target_root.name + ".migration.json")
    coordination = os.open(
        args.target_root.parent / f".{args.target_root.name}.migration.lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    os.fchmod(coordination, 0o600)
    fcntl.flock(coordination, fcntl.LOCK_EX)
    # Recovery is always first while holding the stable parent lock.
    _recover(journal, args.target_root, stage, backup)
    control_keys = _keys(args.control_public)
    outcome_keys = _keys(args.outcome_public)
    release_gid = grp.getgrnam("trustforge-release").gr_gid
    operator_uid = pwd.getpwnam("trustforge-operator").pw_uid
    router_uid = pwd.getpwnam("trustforge-router").pw_uid
    event_fds: list[int] = []
    try:
        # Fixed-order exclusive locks fence appenders from verification to durable publish.
        for directory in ("control", "router-outcomes"):
            fd = os.open(
                args.source_root / directory / "events.jsonl",
                os.O_RDWR | os.O_NOFOLLOW,
            )
            fcntl.flock(fd, fcntl.LOCK_EX)
            event_fds.append(fd)
        source_heads = []
        for index, (directory, keys, domain, kinds) in enumerate(
            (
                ("control", control_keys, "release-control", CONTROL_KINDS),
                (
                    "router-outcomes",
                    outcome_keys,
                    "release-router-outcome",
                    OUTCOME_KINDS,
                ),
            )
        ):
            projection = _verified_projection(
                args.source_root, directory, keys, domain, kinds, verify=False
            )
            records = projection.read_from_exclusively_locked_fd(event_fds[index])
            source_heads.append(records[-1]["event_hash"] if records else None)
        same_root = args.target_root.exists() and os.path.samestat(
            os.stat(args.source_root), os.stat(args.target_root)
        )
        if args.target_root.exists() and not same_root:
            # Never replace an unknown target: the rollback candidate must itself
            # be a fully authenticated pair of ledgers.
            for directory, keys, domain, kinds in (
                ("control", control_keys, "release-control", CONTROL_KINDS),
                (
                    "router-outcomes",
                    outcome_keys,
                    "release-router-outcome",
                    OUTCOME_KINDS,
                ),
            ):
                _verified_projection(
                    args.target_root, directory, keys, domain, kinds
                ).read()
        stage.mkdir(mode=0o750)
        os.chown(stage, 0, release_gid)
        os.chmod(stage, 0o750)
        _copy_ledger(
            args.source_root / "control", stage / "control", operator_uid, release_gid
        )
        _copy_ledger(
            args.source_root / "router-outcomes",
            stage / "router-outcomes",
            router_uid,
            release_gid,
        )
        _copy_public_receipt(args.source_root, stage)
        staged_heads = []
        for directory, keys, domain, kinds in (
            ("control", control_keys, "release-control", CONTROL_KINDS),
            ("router-outcomes", outcome_keys, "release-router-outcome", OUTCOME_KINDS),
        ):
            records = _verified_projection(stage, directory, keys, domain, kinds).read()
            staged_heads.append(records[-1]["event_hash"] if records else None)
        if staged_heads != source_heads:
            raise SystemExit("authenticated source heads changed during migration")
        _fsync_tree(stage)
        _publish_swap(stage, args.target_root, backup, journal)
    finally:
        for fd in reversed(event_fds):
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        fcntl.flock(coordination, fcntl.LOCK_UN)
        os.close(coordination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
