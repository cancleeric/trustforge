#!/usr/bin/env python3
"""Build immutable intrinsic promotion evidence from the canonical shadow DB."""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path

from trustforge.agent.shadow_contracts import (
    ShadowContractError,
    ShadowReleaseIdentity,
    canonical_json,
    load_policy,
)
from trustforge.agent.shadow_evidence_store import ShadowEvidenceStore
from trustforge.asset_intrinsic_promotion_dataset import (
    build_promotion_evidence_dataset,
)


def _canonical_identity(path: Path) -> ShadowReleaseIdentity:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o022
            or info.st_nlink != 1
            or info.st_size > 16_384
        ):
            raise SystemExit("release identity file is unsafe")
        raw = os.read(descriptor, 16_385)
        if len(raw) != info.st_size:
            raise SystemExit("release identity changed during read")
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit("release identity is invalid JSON") from exc
    if not isinstance(value, dict) or canonical_json(value) + b"\n" != raw:
        raise SystemExit("release identity is not canonical")
    try:
        return ShadowReleaseIdentity(**value)
    except (ShadowContractError, TypeError, ValueError) as exc:
        raise SystemExit("release identity contract is invalid") from exc


def _write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = path.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or stat.S_IMODE(parent.st_mode) & 0o077
    ):
        raise SystemExit("dataset output directory is unsafe")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    complete = False
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short dataset write")
            view = view[written:]
        os.fsync(descriptor)
        complete = True
    finally:
        os.close(descriptor)
        if not complete:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shadow-db", type=Path, required=True)
    parser.add_argument("--release-identity", type=Path, required=True)
    parser.add_argument("--pit-cutoff", required=True)
    parser.add_argument("--stale-after-days", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=10_000)
    args = parser.parse_args()
    identity = _canonical_identity(args.release_identity)
    policy = load_policy()
    store = ShadowEvidenceStore(
        args.shadow_db,
        read_only=True,
        max_query_rows=args.limit,
        max_rows=max(args.limit, 10_000),
    )
    try:
        dataset = build_promotion_evidence_dataset(
            store,
            identity,
            policy,
            pit_cutoff=args.pit_cutoff,
            stale_after_days=args.stale_after_days,
            limit=args.limit,
        )
    finally:
        store.close()
    _write_new(args.output, canonical_json(dataset) + b"\n")
    print(dataset["dataset_digest"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
