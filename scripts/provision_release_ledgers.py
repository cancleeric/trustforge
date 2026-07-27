#!/usr/bin/env python3
"""Root-only, identity-separated bootstrap for release ledgers."""

from __future__ import annotations

import argparse
import json
import os
import pwd
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


def _seed_file(path: Path) -> int:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o400
        or info.st_size != 32
    ):
        os.close(descriptor)
        raise SystemExit(f"unsafe bootstrap seed metadata: {path}")
    return descriptor


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
    key_id = "control-1" if args.role == "control" else "router-outcome-1"
    owner = pwd.getpwnam(args.owner).pw_uid
    SignedEventLedger(
        directory=Path(args.root) / args.directory,
        verification_keys={key_id: public},
        event_permissions={domain: kinds},
        domain_keys={domain: frozenset({key_id})},
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
    identity: str, role: str, directory: str, root: Path, seed: Path
) -> dict[str, str]:
    descriptor = _seed_file(seed)
    try:
        result = subprocess.run(
            [
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
            ],
            check=True,
            pass_fds=(descriptor,),
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    provision = subparsers.add_parser("provision")
    provision.add_argument("--root", type=Path, required=True)
    provision.add_argument("--control-key", type=Path, required=True)
    provision.add_argument("--outcome-bootstrap-key", type=Path, required=True)
    hidden = subparsers.add_parser("_bootstrap-one")
    hidden.add_argument("--role", choices=("control", "outcome"), required=True)
    hidden.add_argument("--owner", required=True)
    hidden.add_argument("--directory", required=True)
    hidden.add_argument("--root", type=Path, required=True)
    hidden.add_argument("--seed-fd", type=int, required=True)
    args = parser.parse_args()
    if args.command == "_bootstrap-one":
        return _bootstrap_one(args)
    if os.geteuid() != 0:
        raise SystemExit("ledger provisioning requires root")
    # Resolve only after systemd-sysusers has created both identities.
    pwd.getpwnam("trustforge-operator")
    pwd.getpwnam("trustforge-router")
    control_public = _run_as(
        "trustforge-operator", "control", "control", args.root, args.control_key
    )
    outcome_public = _run_as(
        "trustforge-router",
        "outcome",
        "router-outcomes",
        args.root,
        args.outcome_bootstrap_key,
    )
    args.outcome_bootstrap_key.unlink()
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
