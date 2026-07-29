#!/usr/bin/env python3
"""Block installation when unresolved rollback-failure evidence exists."""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path

SCHEMA = "trustforge.release-install-rollback-failed/v3"


def _digest(value: object, *, allow_absent: bool = False) -> bool:
    if allow_absent and value == "absent":
        return True
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def inspect(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or info.st_size > 64 * 1024
        ):
            raise SystemExit("release evidence BLOCK: unsafe rollback evidence")
        raw = os.read(fd, 64 * 1024 + 1)
        if len(raw) != info.st_size:
            raise SystemExit("release evidence BLOCK: rollback evidence changed")
    finally:
        os.close(fd)
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit("release evidence BLOCK: invalid rollback evidence") from exc
    if (
        not isinstance(payload, dict)
        or json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        != raw
    ):
        raise SystemExit("release evidence BLOCK: noncanonical rollback evidence")
    if payload.get("schema") != SCHEMA:
        raise SystemExit(
            "release evidence BLOCK: legacy rollback evidence requires audit"
        )
    if not _digest(payload.get("target_allowlist_sha256")) or not _digest(
        payload.get("prior_allowlist_sha256"), allow_absent=True
    ):
        raise SystemExit(
            "release evidence BLOCK: rollback allowlist restore digest is invalid"
        )
    raise SystemExit("release evidence BLOCK: unresolved rollback failure")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    inspect(args.evidence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
