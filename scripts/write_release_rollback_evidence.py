#!/usr/bin/env python3
"""Durably record an installer rollback that could not be verified."""

from __future__ import annotations

import argparse
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = "trustforge.release-install-rollback-failed/v3"


def _write_all(fd: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(fd, remaining)
        if written <= 0:
            raise OSError("short rollback evidence write")
        remaining = remaining[written:]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--original-status", type=int, required=True)
    parser.add_argument("--service-stop-code", type=int, required=True)
    parser.add_argument("--artifact-restore-code", type=int, required=True)
    parser.add_argument("--daemon-reload-code", type=int, required=True)
    parser.add_argument("--service-health-code", type=int, required=True)
    parser.add_argument("--target-release", required=True)
    parser.add_argument("--target-evidence-sha256", required=True)
    parser.add_argument("--target-archive-sha256", required=True)
    parser.add_argument("--prior-release", required=True)
    parser.add_argument("--target-unit-sha256", required=True)
    parser.add_argument("--prior-unit-sha256", required=True)
    parser.add_argument("--target-allowlist-sha256", required=True)
    parser.add_argument("--prior-allowlist-sha256", required=True)
    parser.add_argument("--target-pid", type=int, required=True)
    parser.add_argument("--restored-pid", type=int, required=True)
    args = parser.parse_args()
    for label, digest in (
        ("release evidence", args.target_evidence_sha256),
        ("router archive", args.target_archive_sha256),
        ("target allowlist", args.target_allowlist_sha256),
    ):
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise SystemExit(f"{label} SHA-256 is invalid")
    if args.prior_allowlist_sha256 != "absent" and (
        len(args.prior_allowlist_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in args.prior_allowlist_sha256
        )
    ):
        raise SystemExit("prior allowlist SHA-256 is invalid")
    parent_fd = os.open(
        args.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:
        parent = os.fstat(parent_fd)
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.geteuid()
            or stat.S_IMODE(parent.st_mode) & 0o022
        ):
            raise SystemExit("unsafe rollback evidence directory")
        payload = {
            "original_status": args.original_status,
            "prior_release": args.prior_release,
            "prior_unit_sha256": args.prior_unit_sha256,
            "restored_pid": args.restored_pid,
            "schema": SCHEMA,
            "steps": [
                {
                    "attempted": True,
                    "error_code": code,
                    "name": name,
                    "success": code == 0,
                }
                for name, code in (
                    ("service-stop", args.service_stop_code),
                    ("artifact-restore", args.artifact_restore_code),
                    ("daemon-reload", args.daemon_reload_code),
                    ("service-health", args.service_health_code),
                )
            ],
            "target_pid": args.target_pid,
            "target_release": args.target_release,
            "target_evidence_sha256": args.target_evidence_sha256,
            "target_archive_sha256": args.target_archive_sha256,
            "target_allowlist_sha256": args.target_allowlist_sha256,
            "prior_allowlist_sha256": args.prior_allowlist_sha256,
            "target_unit_sha256": args.target_unit_sha256,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        encoded = (
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        )
        temporary = f".rollback-failed-{os.getpid()}.tmp"
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=parent_fd,
        )
        try:
            os.fchown(fd, os.geteuid(), os.getegid())
            os.fchmod(fd, 0o600)
            _write_all(fd, encoded)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(
            temporary,
            "release-install-rollback-failed.json",
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
