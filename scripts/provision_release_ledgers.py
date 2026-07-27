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


def _seed_file(path: Path) -> tuple[int, int, int, int]:
    parent_fd = os.open(path.parent, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY)
    descriptor = os.open(
        path.name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=parent_fd
    )
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
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


def _recover_provision(root: Path, stage: Path, backup: Path, journal: Path) -> None:
    if not journal.exists():
        return
    payload = json.loads(journal.read_text())
    if set(payload) != {"state", "stage", "backup"}:
        raise SystemExit("invalid provisioning transaction journal")
    if payload["state"] != "committed" and backup.exists():
        if root.exists():
            shutil.rmtree(root)
        os.replace(backup, root)
    if stage.exists():
        shutil.rmtree(stage)
    if backup.exists() and root.exists():
        shutil.rmtree(backup)
    journal.unlink()
    parent_fd = os.open(root.parent, os.O_RDONLY | os.O_DIRECTORY)
    os.fsync(parent_fd)
    os.close(parent_fd)


def _write_provision_journal(
    journal: Path, state: str, stage: Path, backup: Path
) -> None:
    temporary = journal.with_name(journal.name + f".{os.getpid()}.tmp")
    data = (
        json.dumps(
            {"state": state, "stage": stage.name, "backup": backup.name},
            sort_keys=True,
        ).encode()
        + b"\n"
    )
    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
    )
    try:
        os.write(fd, data)
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
    # Resolve only after systemd-sysusers has created both identities.
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
    parent = args.root.parent
    parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(
        parent / ".trustforge-provision.lock", os.O_RDWR | os.O_CREAT, 0o600
    )
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    stage = parent / f".{args.root.name}.provision-{os.getpid()}"
    backup = parent / f".{args.root.name}.preprovisioned"
    journal = parent / JOURNAL
    try:
        if journal.exists():
            _recover_provision(args.root, stage, backup, journal)
        if backup.exists():
            raise SystemExit("stale provisioning backup without journal")
        stage.mkdir(mode=0o750)
        os.chown(stage, 0, __import__("grp").getgrnam("trustforge-release").gr_gid)
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
        _write_provision_journal(journal, "staged", stage, backup)
        if args.root.exists():
            os.replace(args.root, backup)
            _write_provision_journal(journal, "old-backed-up", stage, backup)
        try:
            os.replace(stage, args.root)
            dirfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            os.fsync(dirfd)
            os.close(dirfd)
            _write_provision_journal(journal, "committed", stage, backup)
        except BaseException:
            if backup.exists():
                if args.root.exists():
                    shutil.rmtree(args.root)
                os.replace(backup, args.root)
                rollback_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
                os.fsync(rollback_fd)
                os.close(rollback_fd)
            raise
        journal.unlink()
        if backup.exists():
            shutil.rmtree(backup)
        _consume_seed(
            args.control_key,
            control_seed[1],
            control_seed[2],
            control_seed[3],
        )
        _consume_seed(
            args.outcome_bootstrap_key,
            outcome_seed[1],
            outcome_seed[2],
            outcome_seed[3],
        )
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    finally:
        os.close(control_seed[0])
        os.close(control_seed[1])
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
