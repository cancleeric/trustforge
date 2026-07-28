#!/usr/bin/env python3
"""Root-only, identity-separated bootstrap for release ledgers."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import pwd
import shutil
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives.serialization import (  # noqa: E402
    Encoding,
    PublicFormat,
)

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


JOURNAL = ".provision-transaction.json"
JOURNAL_SCHEMA = "trustforge.release-ledger-provision/v2"
JOURNAL_STATES = frozenset(
    {
        "staged",
        "publishing",
        "old-backed-up",
        "committed",
        "control-consuming",
        "control-consumed",
        "outcome-consuming",
        "outcome-consumed",
    }
)
RECEIPT = "provision-receipt.json"


def _write_all(fd: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(fd, remaining)
        if written <= 0:
            raise OSError("short transaction write")
        remaining = remaining[written:]


def _remove_direct_directory(parent: Path, name: str, expected_uid: int = 0) -> None:
    if not name or "/" in name or name in {".", ".."}:
        raise SystemExit("unsafe transaction directory name")
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != expected_uid
            or info.st_dev != os.fstat(parent_fd).st_dev
        ):
            raise SystemExit("unsafe transaction directory metadata")
        shutil.rmtree(name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _seed_file(path: Path) -> tuple[int, int, int, int]:
    parent_fd = os.open(path.parent, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY)
    descriptor = os.open(
        path.name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=parent_fd
    )
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o400
        or info.st_size != 32
    ):
        os.close(descriptor)
        os.close(parent_fd)
        raise SystemExit(f"unsafe bootstrap seed metadata: {path}")
    return descriptor, parent_fd, info.st_dev, info.st_ino


def _consume_seed(path: Path, parent_fd: int, device: int, inode: int) -> None:
    current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
    if (current.st_dev, current.st_ino) != (device, inode):
        raise SystemExit("bootstrap seed identity changed before consumption")
    os.unlink(path.name, dir_fd=parent_fd)
    os.fsync(parent_fd)


def _read_provision_journal(journal: Path, root_name: str) -> dict[str, object]:
    fd = os.open(journal, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > 4096
        ):
            raise SystemExit("unsafe provisioning transaction journal")
        raw = os.read(fd, 4097)
        if len(raw) != info.st_size:
            raise SystemExit("provisioning transaction journal changed during read")
    finally:
        os.close(fd)
    payload = json.loads(raw)
    if (
        not isinstance(payload, dict)
        or json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        != raw
        or set(payload)
        != {
            "schema",
            "state",
            "stage",
            "backup",
            "control_public",
            "outcome_public",
        }
        or payload["schema"] != JOURNAL_SCHEMA
        or payload["state"] not in JOURNAL_STATES
    ):
        raise SystemExit("invalid provisioning transaction journal")
    for key, prefix in (("stage", f".{root_name}.provision-"),):
        value = payload[key]
        if not isinstance(value, str) or "/" in value or not value.startswith(prefix):
            raise SystemExit("unsafe provisioning journal path")
    backup = payload["backup"]
    if (
        not isinstance(backup, str)
        or "/" in backup
        or backup != f".{root_name}.preprovisioned"
    ):
        raise SystemExit("unsafe provisioning backup path")
    return payload


def _recover_provision(root: Path, journal: Path) -> dict[str, object] | None:
    if not journal.exists():
        return None
    payload = _read_provision_journal(journal, root.name)
    stage = journal.parent / str(payload["stage"])
    backup = journal.parent / str(payload["backup"])
    committed_states = {
        "committed",
        "control-consuming",
        "control-consumed",
        "outcome-consuming",
        "outcome-consumed",
    }
    if payload["state"] == "publishing" and not backup.exists() and root.exists():
        info = os.lstat(root)
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_dev != os.stat(root.parent).st_dev
        ):
            raise SystemExit("unsafe fresh provision recovery target")
        _remove_direct_directory(root.parent, root.name, os.geteuid())
    if payload["state"] not in committed_states and backup.exists():
        if root.exists():
            _remove_direct_directory(root.parent, root.name, os.geteuid())
        os.replace(backup, root)
    if stage.exists():
        _remove_direct_directory(stage.parent, stage.name, os.geteuid())
    if backup.exists() and root.exists():
        _remove_direct_directory(backup.parent, backup.name, os.geteuid())
    if payload["state"] not in committed_states:
        journal.unlink()
    parent_fd = os.open(root.parent, os.O_RDONLY | os.O_DIRECTORY)
    os.fsync(parent_fd)
    os.close(parent_fd)
    return payload


def _write_receipt(
    root: Path, control_public: dict[str, str], outcome_public: dict[str, str]
) -> None:
    receipt = root / RECEIPT
    payload = {
        "schema": "trustforge.release-ledger-provision-receipt/v1",
        "control_public": control_public,
        "outcome_public": outcome_public,
    }
    fd = os.open(
        receipt,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o644,
    )
    try:
        os.fchown(fd, 0, 0)
        os.fchmod(fd, 0o644)
        _write_all(
            fd,
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n",
        )
        os.fsync(fd)
    finally:
        os.close(fd)
    directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    os.fsync(directory_fd)
    os.close(directory_fd)


def _verify_receipt(root: Path, payload: dict[str, object]) -> None:
    receipt = root / RECEIPT
    info = os.lstat(receipt)
    raw = receipt.read_bytes()
    expected = {
        "schema": "trustforge.release-ledger-provision-receipt/v1",
        "control_public": payload["control_public"],
        "outcome_public": payload["outcome_public"],
    }
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o644
        or raw
        != json.dumps(expected, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    ):
        raise SystemExit("committed provisioning receipt is invalid")


def _verify_committed_bootstraps(
    args: argparse.Namespace, payload: dict[str, object]
) -> None:
    release_group = "trustforge-release"
    roles = (
        (
            "control",
            "release-control",
            "release-control",
            CONTROL_KINDS,
            payload["control_public"],
            "control-runtime-1",
            args.control_runtime_public,
            pwd.getpwnam("trustforge-operator").pw_uid,
        ),
        (
            "router-outcomes",
            "release-router-outcome",
            "release-router-outcomes",
            OUTCOME_KINDS,
            payload["outcome_public"],
            "router-outcome-runtime-1",
            args.outcome_runtime_public,
            pwd.getpwnam("trustforge-router").pw_uid,
        ),
    )
    for (
        directory,
        domain,
        ledger_role,
        kinds,
        bootstrap,
        runtime_id,
        runtime,
        uid,
    ) in roles:
        assert isinstance(bootstrap, dict)
        keys = {
            str(bootstrap["key_id"]): bytes.fromhex(str(bootstrap["public_key"])),
            runtime_id: bytes.fromhex(runtime),
        }
        SignedEventLedger(
            directory=args.root / directory,
            verification_keys=keys,
            event_permissions={domain: kinds},
            domain_keys={domain: frozenset(keys)},
            ledger_role=ledger_role,
            coordination_root=args.root,
            root_owner_uid=0,
            root_group=release_group,
            root_mode=0o750,
            directory_owner_uid=uid,
            directory_group=release_group,
            directory_mode=0o750,
            file_mode=0o640,
        ).read()


def _write_provision_journal(
    journal: Path,
    state: str,
    stage: Path,
    backup: Path,
    control_public: dict[str, str],
    outcome_public: dict[str, str],
) -> None:
    temporary = journal.with_name(journal.name + f".{os.getpid()}.tmp")
    data = (
        json.dumps(
            {
                "schema": JOURNAL_SCHEMA,
                "state": state,
                "stage": stage.name,
                "backup": backup.name,
                "control_public": control_public,
                "outcome_public": outcome_public,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )
    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
    )
    try:
        os.fchown(fd, 0, 0)
        os.fchmod(fd, 0o600)
        _write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, journal)
    parent_fd = os.open(journal.parent, os.O_RDONLY | os.O_DIRECTORY)
    os.fsync(parent_fd)
    os.close(parent_fd)


def _bootstrap_one(args: argparse.Namespace) -> int:
    seed = os.read(args.seed_fd, 33)
    os.close(args.seed_fd)
    if len(seed) != 32:
        raise SystemExit("bootstrap seed must be exactly 32 bytes")
    public = (
        Ed25519PrivateKey.from_private_bytes(seed)
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
    )
    kinds = CONTROL_KINDS if args.role == "control" else OUTCOME_KINDS
    domain = "release-control" if args.role == "control" else "release-router-outcome"
    key_id = (
        "control-bootstrap-1"
        if args.role == "control"
        else "router-outcome-bootstrap-1"
    )
    verification_keys = {key_id: public}
    if args.runtime_public:
        verification_keys[args.runtime_key_id] = bytes.fromhex(args.runtime_public)
    owner = pwd.getpwnam(args.owner).pw_uid
    SignedEventLedger(
        directory=Path(args.root) / args.directory,
        verification_keys=verification_keys,
        event_permissions={domain: kinds},
        domain_keys={domain: frozenset(verification_keys)},
        signing_key_id=key_id,
        signing_private_key=seed,
        signing_domain=domain,
        ledger_role=(
            "release-control" if args.role == "control" else "release-router-outcomes"
        ),
        bootstrap=True,
        coordination_root=args.root,
        root_owner_uid=0,
        root_group="trustforge-release",
        root_mode=0o750,
        directory_owner_uid=owner,
        directory_group="trustforge-release",
        directory_mode=0o750,
        file_mode=0o640,
    )
    print(json.dumps({"key_id": key_id, "public_key": public.hex()}))
    return 0


def _run_as(
    identity: str,
    role: str,
    directory: str,
    root: Path,
    descriptor: int,
    *,
    runtime_public: str | None = None,
    runtime_key_id: str | None = None,
) -> dict[str, str]:
    command = [
        "setpriv",
        f"--reuid={identity}",
        f"--regid={identity}",
        "--init-groups",
        sys.executable,
        str(Path(__file__).resolve()),
        "_bootstrap-one",
        "--role",
        role,
        "--owner",
        identity,
        "--directory",
        directory,
        "--root",
        str(root),
        "--seed-fd",
        str(descriptor),
    ]
    if runtime_public is not None:
        command.extend(
            (
                "--runtime-public",
                runtime_public,
                "--runtime-key-id",
                str(runtime_key_id),
            )
        )
    result = subprocess.run(
        command,
        check=True,
        pass_fds=(descriptor,),
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    provision = subparsers.add_parser("provision")
    provision.add_argument("--root", type=Path, required=True)
    provision.add_argument("--control-key", type=Path, required=True)
    provision.add_argument("--control-runtime-public", required=True)
    provision.add_argument("--outcome-bootstrap-key", type=Path, required=True)
    provision.add_argument(
        "--outcome-runtime-public",
        required=True,
        help="hex Ed25519 public key for persistent router signer",
    )
    hidden = subparsers.add_parser("_bootstrap-one")
    hidden.add_argument("--role", choices=("control", "outcome"), required=True)
    hidden.add_argument("--owner", required=True)
    hidden.add_argument("--directory", required=True)
    hidden.add_argument("--root", type=Path, required=True)
    hidden.add_argument("--seed-fd", type=int, required=True)
    hidden.add_argument("--runtime-public")
    hidden.add_argument("--runtime-key-id")
    args = parser.parse_args()
    if args.command == "_bootstrap-one":
        return _bootstrap_one(args)
    if os.geteuid() != 0:
        raise SystemExit("ledger provisioning requires root")
    parent = args.root.parent
    parent.mkdir(parents=True, exist_ok=True)
    parent_info = os.lstat(parent)
    if parent_info.st_uid != 0 or stat.S_IMODE(parent_info.st_mode) & 0o022:
        raise SystemExit(
            "provision parent must be root-owned and not writable by group/world"
        )
    lock_fd = os.open(
        parent / ".trustforge-provision.lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    os.fchmod(lock_fd, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    stage = parent / f".{args.root.name}.provision-{os.getpid()}"
    backup = parent / f".{args.root.name}.preprovisioned"
    journal = parent / JOURNAL
    control_seed = outcome_seed = None
    try:
        recovered = _recover_provision(args.root, journal)
        if recovered and journal.exists():
            _verify_receipt(args.root, recovered)
            _verify_committed_bootstraps(args, recovered)
            recovered_stage = parent / str(recovered["stage"])
            recovered_backup = parent / str(recovered["backup"])
            state = str(recovered["state"])
            if state in {"committed", "control-consuming"}:
                if state == "committed" and not args.control_key.exists():
                    raise SystemExit(
                        "control bootstrap seed vanished before consumption"
                    )
                if args.control_key.exists():
                    seed = _seed_file(args.control_key)
                    try:
                        _write_provision_journal(
                            journal,
                            "control-consuming",
                            recovered_stage,
                            recovered_backup,
                            recovered["control_public"],  # type: ignore[arg-type]
                            recovered["outcome_public"],  # type: ignore[arg-type]
                        )
                        _consume_seed(args.control_key, seed[1], seed[2], seed[3])
                    finally:
                        os.close(seed[0])
                        os.close(seed[1])
                _write_provision_journal(
                    journal,
                    "control-consumed",
                    recovered_stage,
                    recovered_backup,
                    recovered["control_public"],
                    recovered["outcome_public"],  # type: ignore[arg-type]
                )
                state = "control-consumed"
            if state in {"control-consumed", "outcome-consuming"}:
                if (
                    state == "control-consumed"
                    and not args.outcome_bootstrap_key.exists()
                ):
                    raise SystemExit(
                        "outcome bootstrap seed vanished before consumption"
                    )
                if args.outcome_bootstrap_key.exists():
                    seed = _seed_file(args.outcome_bootstrap_key)
                    try:
                        _write_provision_journal(
                            journal,
                            "outcome-consuming",
                            recovered_stage,
                            recovered_backup,
                            recovered["control_public"],
                            recovered["outcome_public"],  # type: ignore[arg-type]
                        )
                        _consume_seed(
                            args.outcome_bootstrap_key, seed[1], seed[2], seed[3]
                        )
                    finally:
                        os.close(seed[0])
                        os.close(seed[1])
                _write_provision_journal(
                    journal,
                    "outcome-consumed",
                    recovered_stage,
                    recovered_backup,
                    recovered["control_public"],
                    recovered["outcome_public"],  # type: ignore[arg-type]
                )
            journal.unlink()
            parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            os.fsync(parent_fd)
            os.close(parent_fd)
            print(
                json.dumps(
                    {
                        "control_bootstrap_public": recovered["control_public"],
                        "outcome_bootstrap_public": recovered["outcome_public"],
                    },
                    sort_keys=True,
                )
            )
            return 0
        # Resolve and inspect only after recovery owns the stable parent lock.
        operator = pwd.getpwnam("trustforge-operator")
        router = pwd.getpwnam("trustforge-router")
        if args.root.exists():
            entries = {entry.name: entry for entry in args.root.iterdir()}
            if set(entries) - {"control", "router-outcomes"} or any(
                entry.is_symlink() or not entry.is_dir() or any(entry.iterdir())
                for entry in entries.values()
            ):
                raise SystemExit(
                    "provision target contains state; use authenticated migration"
                )
        control_seed = _seed_file(args.control_key)
        outcome_seed = _seed_file(args.outcome_bootstrap_key)
        if backup.exists():
            raise SystemExit("stale provisioning backup without journal")
        stage.mkdir(mode=0o750)
        release_gid = __import__("grp").getgrnam("trustforge-release").gr_gid
        os.chown(stage, 0, release_gid)
        # Keep the shared staging root non-writable to both service identities.
        # Root provisions each private child with its final writer ownership
        # before dropping privileges, so neither writer can replace its sibling.
        for name, uid in (
            ("control", operator.pw_uid),
            ("router-outcomes", router.pw_uid),
        ):
            child = stage / name
            child.mkdir(mode=0o750)
            os.chown(child, uid, release_gid)
        control_public = _run_as(
            "trustforge-operator",
            "control",
            "control",
            stage,
            control_seed[0],
            runtime_public=args.control_runtime_public,
            runtime_key_id="control-runtime-1",
        )
        outcome_public = _run_as(
            "trustforge-router",
            "outcome",
            "router-outcomes",
            stage,
            outcome_seed[0],
            runtime_public=args.outcome_runtime_public,
            runtime_key_id="router-outcome-runtime-1",
        )
        # Both complete ledgers exist only in private same-filesystem staging.
        for name, uid in (
            ("control", operator.pw_uid),
            ("router-outcomes", router.pw_uid),
        ):
            info = os.lstat(stage / name / "bootstrap.json")
            if info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o640:
                raise SystemExit("staged ledger metadata verification failed")
        _write_provision_journal(
            journal, "staged", stage, backup, control_public, outcome_public
        )
        if args.root.exists():
            os.replace(args.root, backup)
            _write_provision_journal(
                journal,
                "old-backed-up",
                stage,
                backup,
                control_public,
                outcome_public,
            )
        else:
            _write_provision_journal(
                journal,
                "publishing",
                stage,
                backup,
                control_public,
                outcome_public,
            )
        try:
            os.replace(stage, args.root)
            dirfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            os.fsync(dirfd)
            os.close(dirfd)
            _write_receipt(args.root, control_public, outcome_public)
            _write_provision_journal(
                journal, "committed", stage, backup, control_public, outcome_public
            )
        except BaseException:
            if backup.exists():
                if args.root.exists():
                    _remove_direct_directory(parent, args.root.name)
                os.replace(backup, args.root)
                rollback_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
                os.fsync(rollback_fd)
                os.close(rollback_fd)
            raise
        if backup.exists():
            _remove_direct_directory(parent, backup.name)
        # Finish in this process while retaining the stable coordination lock.
        # No exec/re-lock boundary exists, so fresh provisioning cannot self-deadlock.
        _write_provision_journal(
            journal,
            "control-consuming",
            stage,
            backup,
            control_public,
            outcome_public,
        )
        _consume_seed(
            args.control_key, control_seed[1], control_seed[2], control_seed[3]
        )
        _write_provision_journal(
            journal,
            "control-consumed",
            stage,
            backup,
            control_public,
            outcome_public,
        )
        _write_provision_journal(
            journal,
            "outcome-consuming",
            stage,
            backup,
            control_public,
            outcome_public,
        )
        _consume_seed(
            args.outcome_bootstrap_key,
            outcome_seed[1],
            outcome_seed[2],
            outcome_seed[3],
        )
        _write_provision_journal(
            journal,
            "outcome-consumed",
            stage,
            backup,
            control_public,
            outcome_public,
        )
        journal.unlink()
        parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        os.fsync(parent_fd)
        os.close(parent_fd)
    except BaseException:
        if stage.exists():
            _remove_direct_directory(parent, stage.name)
        raise
    finally:
        if control_seed is not None:
            os.close(control_seed[0])
            os.close(control_seed[1])
        if outcome_seed is not None:
            os.close(outcome_seed[0])
            os.close(outcome_seed[1])
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    print(
        json.dumps(
            {
                "control_bootstrap_public": control_public,
                "outcome_bootstrap_public": outcome_public,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
