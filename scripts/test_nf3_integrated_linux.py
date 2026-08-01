#!/usr/bin/env python3
"""Native root/systemd NF3 integration evidence orchestrator.

This composes the unchanged NF2 adversarial harness, A2's 60-case durability
matrix, and the integrated NF2/A2 matrix. Missing native-host prerequisites are
BLOCKED_EXTERNAL_LINUX, never PASS.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import platform
import secrets
import shutil
import stat
import subprocess
import tarfile
import tempfile
import tomllib
from pathlib import Path

BLOCKED = 77
ACCEPTED_NF2_MERGE = "7c26416581a8437a6d00d7941357826b2650c474"
ACCEPTED_NF2_TREE = "cb56a4bef9708da3f9f1468aff11734f2f50adcd"
ACCEPTED_NF2_SOURCE_TREE_RECEIPT_SHA256 = (
    "4fe965e40c31916d8ae01ef55ee93be66af5ff214e6c0caf9997535df83f47c0"
)
REVIEWED_NR1A_NF2_TREE = "c43e08d8ce5cded900282ca4ddda681fe148594a"
REVIEWED_NR1A_SOURCE_TREE_RECEIPT_SHA256 = (
    "636361176b16b3d85ccce2db3789d69a193a984619df3a76617f34a1dac7700a"
)
ACCEPTED_LINKED_SOURCE_SHA256 = (
    "dc7541f5c4e409a2dd038795bcffab8d4dca442266d6efdae36564ef5c421abc"
)
REVIEWED_NR1A_LINKED_SOURCE_SHA256 = (
    "2c948fcca2c9194fce13e212e449739e5ecaa2b35256e7709b929b7822c85983"
)
# Foundation goldens (evidence/release) are the single source of truth for the
# derived foundation digest; inline literals elsewhere must reference these.
REVIEWED_NR1A_FOUNDATION_EVIDENCE_SHA256 = (
    "d4d080f116e5967e2dd7c8cca02e471f754484ca529b48f22c2106ed8c819568"
)
REVIEWED_NR1A_FOUNDATION_RELEASE_SHA256 = (
    "07a3a28ceb2ecfaed3f2ca334f60228bfbc8c500d67223aa9b4c0220e15e5005"
)
EXPECTED_RELEASE_RLIB_SHA256 = (
    "ef9e4d796488d40fce33188505abfcc8c610cb74ccd2592a410bfc1d3812ec38"
)
EXPECTED_EVIDENCE_RLIB_SHA256 = (
    "bada9d9e97d961c7660b55678c518e56d1b3867b36a489d18648e0b6f26aa22b"
)
EXPECTED_EVIDENCE_HELPER_SHA256 = (
    "2d0df0ecded2c8e4044cb54ec2ca64b65bada958f7d9ede43cdbd285bc65a666"
)
EXPECTED_RELEASE_PROBE_SHA256 = (
    "375e4dd5d8017a79a4c8e75b3ec0280b8339351811efe9395da02281631ff209"
)
EVIDENCE_PROFILE_RECEIPT_SHA256 = (
    "7f53b287a6944a5978b02dfcd35e50b5955be28107ac457369a70d22115f79a5"
)
RELEASE_PROFILE_RECEIPT_SHA256 = (
    "5cc871f48193094c28b5df2691c63b2f3c6649686b3573243de5daed90e6e070"
)
EXPECTED_FOUNDATION_SHA256 = REVIEWED_NR1A_FOUNDATION_EVIDENCE_SHA256
FIXED_TOOLCHAIN_RECEIPT_SHA256 = (
    "3ddca04f9011db7eba5f0a85103ce62710f6be8d20aca02850aec5774301ee26"
)
CANONICAL_SOURCE_ROOT = "/workspace/trustforge"
CANONICAL_BUILD_PARENT = Path("/run/trustforge-nf3-build-input")
CANONICAL_BUILD_SOURCE = CANONICAL_BUILD_PARENT / "source"
HANDOFF_ROOT = Path("/var/lib/trustforge-nf3-handoff")
SYSTEMD_EXEC_WRAPPER = "/usr/bin/env"
SYSTEMD_SCRIPT_WRAPPER = "/bin/bash"
SYSTEM_PYTHON = "/usr/bin/python3"
LOCAL_HANDOFF_FILESYSTEMS = frozenset(
    {"ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "f2fs"}
)
BLOCKED_RECEIPT = "0" * 64
ACCEPTED_NF1_COMMIT = "e28a675f03ee517dcd69fba0d7705ec8828d24cd"
TARGET = "x86_64-unknown-linux-musl"
NF2_HARNESS = Path("scripts/test_nf2_zero_capability_linux.py")
FORBIDDEN_RUSTDOC = "/nonexistent/trustforge-rustdoc-forbidden"
EVIDENCE_PROFILE = {
    "inherits": "dev",
    "opt-level": 2,
    "debug-assertions": True,
    "incremental": False,
    "codegen-units": 1,
    "lto": False,
    "panic": "unwind",
    "strip": "none",
}
RELEASE_PROFILE = {
    "opt-level": 3,
    "debug-assertions": False,
    "incremental": False,
    "codegen-units": 1,
    "lto": False,
    "panic": "abort",
    "strip": "symbols",
}


def block(reason: str) -> None:
    print(f"BLOCKED_EXTERNAL_LINUX: {reason}")
    raise SystemExit(BLOCKED)


def run(
    command: list[str],
    *,
    cwd: Path,
    capture: bool = False,
    environment: dict[str, str] | None = None,
    pass_fds: tuple[int, ...] = (),
) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        pass_fds=pass_fds,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    if result.returncode != 0:
        if result.returncode == BLOCKED:
            block(f"composed command blocked: {' '.join(command)}")
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}")
    return (result.stdout or "").strip()


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _file_generation(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        metadata.st_dev,
        metadata.st_ino,
    )


def _mountinfo_unescape(value: str) -> str:
    for encoded, decoded in (
        ("\\040", " "),
        ("\\011", "\t"),
        ("\\012", "\n"),
        ("\\134", "\\"),
    ):
        value = value.replace(encoded, decoded)
    return value


def handoff_mount_identity(
    mountinfo: str, handoff_root: Path = HANDOFF_ROOT
) -> dict[str, str]:
    """Select and validate the most specific local executable mount."""
    resolved = str(handoff_root)
    candidates: list[tuple[int, list[str], list[str]]] = []
    for line in mountinfo.splitlines():
        fields = line.split()
        try:
            separator = fields.index("-")
        except ValueError:
            continue
        if separator < 6 or len(fields) < separator + 4:
            continue
        mountpoint = _mountinfo_unescape(fields[4])
        if resolved == mountpoint or resolved.startswith(mountpoint.rstrip("/") + "/"):
            candidates.append((len(mountpoint), fields, fields[separator + 1 :]))
    if not candidates:
        block("NF3 handoff StateDirectory mount identity is unavailable")
    _, fields, filesystem = max(candidates, key=lambda item: item[0])
    mount_options = set(fields[5].split(","))
    super_options = set(filesystem[2].split(","))
    if "noexec" in mount_options or "noexec" in super_options:
        block("NF3 handoff StateDirectory mount is noexec")
    filesystem_type = filesystem[0]
    if filesystem_type not in LOCAL_HANDOFF_FILESYSTEMS:
        block("NF3 handoff StateDirectory is not on an accepted local filesystem")
    return {
        "mount_id": fields[0],
        "major_minor": fields[2],
        "root": _mountinfo_unescape(fields[3]),
        "mountpoint": _mountinfo_unescape(fields[4]),
        "filesystem_type": filesystem_type,
        "source": _mountinfo_unescape(filesystem[1]),
        "mount_options": fields[5],
        "super_options": filesystem[2],
    }


def _validate_handoff_directory(
    reviewed_commit: str,
) -> tuple[int, int, str, dict[str, str]]:
    """Open an empty systemd StateDirectory and create one exact generation."""
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in ("var", "lib", "trustforge-nf3-handoff"):
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            metadata = os.fstat(child)
            if (
                metadata.st_uid != 0
                or metadata.st_gid != 0
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                os.close(child)
                block(f"unsafe NF3 handoff path component: {component}")
            os.close(descriptor)
            descriptor = child
    except OSError:
        os.close(descriptor)
        block("systemd NF3 handoff StateDirectory is unavailable")
    parent_metadata = os.fstat(descriptor)
    if stat.S_IMODE(parent_metadata.st_mode) != 0o700:
        os.close(descriptor)
        block("systemd NF3 handoff StateDirectory metadata is unsafe")
    if os.listdir(descriptor):
        os.close(descriptor)
        block("systemd NF3 handoff StateDirectory contains stale or unknown state")
    mount_identity = handoff_mount_identity(Path("/proc/self/mountinfo").read_text())
    generation = f"{reviewed_commit}-{secrets.token_hex(16)}"
    os.mkdir(generation, 0o700, dir_fd=descriptor)
    generation_fd = -1
    active = -1
    try:
        generation_fd = os.open(
            generation,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=descriptor,
        )
        metadata = os.fstat(generation_fd)
        if (
            metadata.st_uid != 0
            or metadata.st_gid != 0
            or metadata.st_nlink != 2
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            block("NF3 handoff generation metadata is unsafe")
        active = os.open(
            "ACTIVE",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o400,
            dir_fd=generation_fd,
        )
        os.write(active, (reviewed_commit + "\n").encode())
        os.fsync(active)
        os.close(active)
        active = -1
        os.fsync(generation_fd)
        os.fsync(descriptor)
        return descriptor, generation_fd, generation, mount_identity
    except BaseException:
        if active >= 0:
            os.close(active)
        if generation_fd >= 0:
            entries = os.listdir(generation_fd)
            if entries == ["ACTIVE"]:
                active_metadata = os.stat(
                    "ACTIVE", dir_fd=generation_fd, follow_symlinks=False
                )
                if stat.S_ISREG(active_metadata.st_mode):
                    os.unlink("ACTIVE", dir_fd=generation_fd)
            if os.listdir(generation_fd):
                block("NF3 handoff initialization cleanup found unknown state")
            os.close(generation_fd)
        os.rmdir(generation, dir_fd=descriptor)
        os.fsync(descriptor)
        os.close(descriptor)
        raise


def stage_handoff_file(
    handoff_fd: int,
    source: Path,
    destination: str,
    *,
    expected_sha256: str,
    executable: bool = False,
) -> tuple[int, ...]:
    """Copy one verified generation into the host-visible handoff."""
    if "/" in destination or destination in ("", ".", ".."):
        raise ValueError("handoff destination must be one plain filename")
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    destination_fd = -1
    try:
        before = os.fstat(source_fd)
        generation = _file_generation(before)
        expected_mode = 0o500 if executable else 0o400
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or before.st_nlink < 1
            or before.st_size <= 0
            or before.st_size > 128 * 1024 * 1024
            or stat.S_IMODE(before.st_mode) & 0o022
        ):
            block(f"unsafe handoff source metadata: {source}")
        destination_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            expected_mode,
            dir_fd=handoff_fd,
        )
        os.fchown(destination_fd, 0, 0)
        value = hashlib.sha256()
        copied = 0
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            value.update(chunk)
            copied += len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                view = view[written:]
        if copied != before.st_size or value.hexdigest() != expected_sha256:
            block(f"handoff source content mismatch: {source}")
        if _file_generation(os.fstat(source_fd)) != generation:
            block(f"handoff source generation changed during copy: {source}")
        os.fchmod(destination_fd, expected_mode)
        os.fsync(destination_fd)
        staged = os.fstat(destination_fd)
        if (
            not stat.S_ISREG(staged.st_mode)
            or staged.st_uid != 0
            or staged.st_gid != 0
            or staged.st_nlink != 1
            or staged.st_size != copied
            or stat.S_IMODE(staged.st_mode) != expected_mode
        ):
            block(f"staged handoff metadata mismatch: {destination}")
        os.fsync(handoff_fd)
        return _file_generation(staged)
    except BaseException:
        if destination_fd >= 0:
            staged = os.fstat(destination_fd)
            named = os.stat(destination, dir_fd=handoff_fd, follow_symlinks=False)
            if _file_generation(staged) != _file_generation(named):
                block(f"partial handoff cleanup generation mismatch: {destination}")
            os.unlink(destination, dir_fd=handoff_fd)
            os.fsync(handoff_fd)
        raise
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)
        os.close(source_fd)


def cleanup_handoff_file(
    handoff_fd: int, destination: str, generation: tuple[int, ...]
) -> None:
    """Remove only the exact file generation created by this process."""
    metadata = os.stat(destination, dir_fd=handoff_fd, follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode) or _file_generation(metadata) != generation:
        block(f"handoff cleanup generation mismatch: {destination}")
    os.unlink(destination, dir_fd=handoff_fd)
    os.fsync(handoff_fd)


class HandoffCleanupRegistry:
    """LIFO ownership for non-file generation entries."""

    def __init__(self) -> None:
        self._entries: list[tuple[str, object]] = []

    def register(self, name: str, cleanup) -> None:
        if any(entry_name == name for entry_name, _ in self._entries):
            block(f"duplicate handoff cleanup registration: {name}")
        self._entries.append((name, cleanup))

    def discard(self, name: str) -> None:
        if not self._entries or self._entries[-1][0] != name:
            block(f"handoff cleanup order mismatch: {name}")
        self._entries.pop()

    def cleanup_all(self) -> None:
        while self._entries:
            _, cleanup = self._entries[-1]
            cleanup()
            self._entries.pop()


def _remove_registered_tree(root_fd: int) -> None:
    """Remove a registered subtree after rejecting unsafe inode types."""
    for name in sorted(os.listdir(root_fd)):
        metadata = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        if metadata.st_uid != 0 or metadata.st_gid != 0:
            block(f"registered handoff subtree ownership is unsafe: {name}")
        if stat.S_ISDIR(metadata.st_mode):
            if stat.S_IMODE(metadata.st_mode) & 0o022:
                block(f"registered handoff subtree directory is unsafe: {name}")
            child = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
            _remove_registered_tree(child)
            os.fsync(child)
            os.close(child)
            os.rmdir(name, dir_fd=root_fd)
        elif (
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_nlink == 1
            and metadata.st_size <= 128 * 1024 * 1024
            and not stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            os.unlink(name, dir_fd=root_fd)
        else:
            block(f"registered handoff subtree object is unsafe: {name}")
    os.fsync(root_fd)


def _partial_cases_root_kind(name: str) -> str | None:
    case_number = name.removeprefix("case-")
    if (
        name.startswith("case-")
        and len(case_number) == 3
        and case_number.isascii()
        and case_number.isdigit()
        and 1 <= int(case_number) <= 60
    ):
        return "dir"
    integrated = name.removeprefix("trustforge-nf3-integrated-")
    if name.startswith("trustforge-nf3-integrated-"):
        if integrated.endswith(".log"):
            token = integrated.removesuffix(".log")
            return (
                "file"
                if len(token) == 8 and token.isascii() and token.isalnum()
                else None
            )
        return (
            "dir"
            if len(integrated) == 8 and integrated.isascii() and integrated.isalnum()
            else None
        )
    witness = name.removeprefix("trustforge-nf3-witness-")
    if (
        name.startswith("trustforge-nf3-witness-")
        and len(witness) == 8
        and witness.isascii()
        and witness.isalnum()
    ):
        return "file"
    return None


def cleanup_partial_cases_tree(generation_fd: int) -> None:
    """Remove only known harness roots from a partially populated cases tree."""
    try:
        cases_fd = os.open(
            "cases",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=generation_fd,
        )
    except FileNotFoundError:
        return
    metadata = os.fstat(cases_fd)
    if (
        metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        block("partial NF3 cases directory metadata is unsafe")
    for name in os.listdir(cases_fd):
        kind = _partial_cases_root_kind(name)
        if kind is None:
            os.close(cases_fd)
            block(f"partial NF3 cases directory contains unknown entry: {name}")
        flags = os.O_RDONLY | os.O_NOFOLLOW
        if kind == "dir":
            flags |= os.O_DIRECTORY
        child = os.open(name, flags, dir_fd=cases_fd)
        child_metadata = os.fstat(child)
        named_metadata = os.stat(name, dir_fd=cases_fd, follow_symlinks=False)
        if _file_generation(child_metadata) != _file_generation(named_metadata):
            os.close(child)
            os.close(cases_fd)
            block(f"partial NF3 case root identity changed: {name}")
        if kind == "dir":
            if (
                not stat.S_ISDIR(child_metadata.st_mode)
                or child_metadata.st_uid != 0
                or child_metadata.st_gid != 0
                or stat.S_IMODE(child_metadata.st_mode) & 0o022
            ):
                os.close(child)
                os.close(cases_fd)
                block(f"partial NF3 case root is unsafe: {name}")
            _remove_registered_tree(child)
            named_metadata = os.stat(name, dir_fd=cases_fd, follow_symlinks=False)
            if _file_generation(os.fstat(child)) != _file_generation(named_metadata):
                os.close(child)
                os.close(cases_fd)
                block(f"partial NF3 case root changed during cleanup: {name}")
            os.close(child)
            os.rmdir(name, dir_fd=cases_fd)
        else:
            if (
                not stat.S_ISREG(child_metadata.st_mode)
                or child_metadata.st_uid != 0
                or child_metadata.st_gid != 0
                or child_metadata.st_nlink != 1
                or stat.S_IMODE(child_metadata.st_mode) != 0o600
                or child_metadata.st_size > 128 * 1024 * 1024
            ):
                os.close(child)
                os.close(cases_fd)
                block(f"partial NF3 case artifact is unsafe: {name}")
            named_metadata = os.stat(name, dir_fd=cases_fd, follow_symlinks=False)
            if _file_generation(os.fstat(child)) != _file_generation(named_metadata):
                os.close(child)
                os.close(cases_fd)
                block(f"partial NF3 case artifact changed during cleanup: {name}")
            os.unlink(name, dir_fd=cases_fd)
            os.close(child)
    os.fsync(cases_fd)
    os.close(cases_fd)
    os.rmdir("cases", dir_fd=generation_fd)
    os.fsync(generation_fd)


def cleanup_cases_tree(generation_fd: int, expected_cases: list[str]) -> None:
    """Verify and remove only the extracted, closed-world case evidence tree."""
    cases_fd = os.open(
        "cases",
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=generation_fd,
    )
    cases_metadata = os.fstat(cases_fd)
    if (
        cases_metadata.st_uid != 0
        or cases_metadata.st_gid != 0
        or stat.S_IMODE(cases_metadata.st_mode) != 0o700
    ):
        block("NF3 cases directory metadata is unsafe")
    if sorted(os.listdir(cases_fd)) != sorted(expected_cases):
        block("NF3 cases directory contains unknown or missing entries")
    expected_files = {
        "evidence.json",
        "evidence.json.sha256",
        "process.log",
        "terminal.record",
        "witness.txt",
    }
    for case_name in sorted(expected_cases):
        case_fd = os.open(
            case_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=cases_fd,
        )
        case_metadata = os.fstat(case_fd)
        if (
            case_metadata.st_uid != 0
            or case_metadata.st_gid != 0
            or case_metadata.st_nlink != 2
            or stat.S_IMODE(case_metadata.st_mode) != 0o700
            or set(os.listdir(case_fd)) != expected_files
        ):
            block(f"NF3 case directory is unsafe: {case_name}")
        for filename in sorted(expected_files):
            metadata = os.stat(filename, dir_fd=case_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != 0
                or metadata.st_gid != 0
                or metadata.st_nlink != 1
                or metadata.st_size < 0
                or metadata.st_size > 16 * 1024 * 1024
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                block(f"NF3 case evidence object is unsafe: {case_name}/{filename}")
            os.unlink(filename, dir_fd=case_fd)
        os.fsync(case_fd)
        os.close(case_fd)
        os.rmdir(case_name, dir_fd=cases_fd)
    os.fsync(cases_fd)
    os.close(cases_fd)
    os.rmdir("cases", dir_fd=generation_fd)
    os.fsync(generation_fd)


def _safe_archive_name(name: str) -> tuple[str, ...]:
    path = Path(name)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        block("NF1 archive contains an unsafe path")
    return path.parts


def _open_relative(root_fd: int, parts: tuple[str, ...], directory: bool) -> int:
    current = os.dup(root_fd)
    try:
        for index, part in enumerate(parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW
            if index < len(parts) - 1 or directory:
                flags |= os.O_DIRECTORY
            child = os.open(part, flags, dir_fd=current)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _closed_tree_names(root_fd: int, prefix: tuple[str, ...] = ()) -> set[str]:
    names: set[str] = set()
    for name in os.listdir(root_fd):
        if name in ("", ".", "..") or "/" in name:
            block("NF1 install contains an unsafe entry name")
        metadata = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        relative = "/".join((*prefix, name))
        names.add(relative)
        if stat.S_ISDIR(metadata.st_mode):
            child = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
            names.update(_closed_tree_names(child, (*prefix, name)))
            os.close(child)
        elif not stat.S_ISREG(metadata.st_mode):
            block("NF1 install contains a non-regular object")
    return names


def _accepted_nf1_source_modes(canonical_mode: int) -> frozenset[int]:
    return frozenset((canonical_mode, canonical_mode | stat.S_IWUSR))


def _case_semantic_record(case_directory_name: str, value: object) -> dict[str, object]:
    evidence_keys = {
        "actual",
        "case",
        "expected",
        "fault",
        "log_sha256",
        "terminal_head",
        "terminal_record_sha256",
        "witness_sha256",
    }
    outcome_keys = {
        "attempt",
        "definite_success",
        "retry_attempt_delta",
        "terminal_state",
    }
    if not isinstance(value, dict) or set(value) != evidence_keys:
        raise RuntimeError(f"case evidence schema mismatch: {case_directory_name}")
    case = value["case"]
    fault = value["fault"]
    if (
        type(case) is not int
        or not 1 <= case <= 60
        or case_directory_name != f"case-{case:03d}"
        or type(fault) is not str
        or not fault
    ):
        raise RuntimeError(f"case identity mismatch: {case_directory_name}")
    for label in ("actual", "expected"):
        outcome = value[label]
        if not isinstance(outcome, dict) or set(outcome) != outcome_keys:
            raise RuntimeError(f"case outcome schema mismatch: {case_directory_name}")
        if (
            any(
                type(outcome[field]) is not int
                for field in ("attempt", "definite_success", "retry_attempt_delta")
            )
            or type(outcome["terminal_state"]) is not str
        ):
            raise RuntimeError(f"case outcome type mismatch: {case_directory_name}")
    for field in (
        "log_sha256",
        "terminal_head",
        "terminal_record_sha256",
        "witness_sha256",
    ):
        if type(value[field]) is not str or not value[field]:
            raise RuntimeError(
                f"case artifact identity mismatch: {case_directory_name}"
            )
    if value["actual"] != value["expected"]:
        raise RuntimeError(f"case outcome mismatch: {case_directory_name}")
    return {
        "actual": value["actual"],
        "case": case,
        "expected": value["expected"],
        "fault": fault,
    }


def case_semantic_collection_sha256(records: list[dict[str, object]]) -> str:
    value = hashlib.sha256()
    value.update(b"trustforge.nf3.case-semantic-collection.v1\0")
    for record in records:
        payload = json.dumps(
            record, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
        value.update(len(payload).to_bytes(8, "big"))
        value.update(payload)
    return value.hexdigest()


def cleanup_partial_nf1_install(
    generation_fd: int,
    state: dict[str, str],
    expected: dict[str, tuple[str, str, int, int]],
) -> None:
    """Remove a registered partial NF1 tree only when it is an expected subset."""
    root_name = state["name"]
    try:
        root_fd = os.open(
            root_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=generation_fd,
        )
    except FileNotFoundError:
        if root_name.startswith(".nf1-install.tmp-"):
            try:
                root_fd = os.open(
                    "nf1-install",
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=generation_fd,
                )
            except FileNotFoundError:
                return
            root_name = "nf1-install"
        else:
            return
    root_metadata = os.fstat(root_fd)
    actual = _closed_tree_names(root_fd)
    if (
        root_metadata.st_uid != 0
        or root_metadata.st_gid != 0
        or stat.S_IMODE(root_metadata.st_mode) not in (0o700, 0o555)
        or not actual <= set(expected)
    ):
        os.close(root_fd)
        block("partial staged NF1 cleanup closed set is unsafe")
    for name in sorted(actual, key=lambda value: value.count("/"), reverse=True):
        kind, _, mode, size = expected[name]
        descriptor = _open_relative(root_fd, tuple(name.split("/")), kind == "dir")
        metadata = os.fstat(descriptor)
        safe = metadata.st_uid == 0 and metadata.st_gid == 0
        if kind == "dir":
            safe = (
                safe
                and stat.S_ISDIR(metadata.st_mode)
                and stat.S_IMODE(metadata.st_mode) in (0o700, 0o555)
            )
        else:
            safe = (
                safe
                and stat.S_ISREG(metadata.st_mode)
                and metadata.st_nlink == 1
                and metadata.st_size <= size
                and stat.S_IMODE(metadata.st_mode) in (0o400, mode)
            )
        if not safe:
            os.close(descriptor)
            os.close(root_fd)
            block(f"partial staged NF1 cleanup metadata mismatch: {name}")
        os.close(descriptor)
        parent = (
            _open_relative(root_fd, tuple(name.split("/")[:-1]), True)
            if "/" in name
            else os.dup(root_fd)
        )
        if kind == "file":
            os.unlink(name.rsplit("/", 1)[-1], dir_fd=parent)
        else:
            os.rmdir(name.rsplit("/", 1)[-1], dir_fd=parent)
        os.fsync(parent)
        os.close(parent)
    os.close(root_fd)
    os.rmdir(root_name, dir_fd=generation_fd)
    os.fsync(generation_fd)


def stage_nf1_install(
    generation_fd: int,
    source: Path,
    archive: Path,
    harness,
    cleanup_registry: HandoffCleanupRegistry,
) -> dict[str, tuple[str, str, int, int]]:
    """Cross-check and atomically stage the closed NF1 archive/install set."""
    if digest(archive) != harness.ACCEPTED_ARCHIVE:
        block("NF1 archive differs from reviewed NF2 receipt")
    manifest = source / "native-hermetic-provenance.json"
    try:
        manifest_metadata = manifest.stat(follow_symlinks=False)
    except OSError:
        block("NF1 accepted manifest is absent")
    if (
        not stat.S_ISREG(manifest_metadata.st_mode)
        or manifest_metadata.st_nlink != 1
        or digest(manifest) != harness.ACCEPTED_MANIFEST
    ):
        block("NF1 accepted manifest differs from reviewed NF2 receipt")
    expected: dict[str, tuple[str, str, int, int]] = {
        "native-hermetic-provenance.json": (
            "file",
            harness.ACCEPTED_MANIFEST,
            0o444,
            manifest_metadata.st_size,
        ),
        "package": ("dir", "", 0o555, 0),
    }
    with tarfile.open(archive, "r:") as stream:
        for member in stream:
            parts = _safe_archive_name(member.name)
            name = "/".join(("package", *parts))
            if name in expected or member.uid != 0 or member.gid != 0:
                block("NF1 archive metadata or closed-set identity is invalid")
            if member.isdir():
                if member.mode != 0o555:
                    block("NF1 archive directory mode is not 0555")
                expected[name] = ("dir", "", 0o555, 0)
            elif member.isfile():
                mode = (
                    0o555
                    if name == "package/bin/trustforge-native-foundation"
                    else 0o444
                )
                if (
                    member.mode != mode
                    or member.size < 0
                    or member.size > 128 * 1024 * 1024
                ):
                    block("NF1 archive file metadata is invalid")
                payload = stream.extractfile(member)
                if payload is None:
                    block("NF1 archive file payload is absent")
                value = hashlib.sha256()
                for chunk in iter(lambda: payload.read(1024 * 1024), b""):
                    value.update(chunk)
                payload_digest = value.hexdigest()
                if (
                    name == "package/bin/trustforge-native-foundation"
                    and payload_digest != harness.ACCEPTED_RUNTIME
                ):
                    block("NF1 runtime differs from reviewed NF2 receipt")
                expected[name] = ("file", payload_digest, mode, member.size)
            else:
                block("NF1 archive contains a non-regular object")

    source_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    source_root_generation = _file_generation(os.fstat(source_fd))
    source_generations: dict[str, tuple[int, ...]] = {}
    actual_names = _closed_tree_names(source_fd)
    if actual_names != set(expected):
        os.close(source_fd)
        block("NF1 accepted install differs from archive closed set")

    temporary = f".nf1-install.tmp-{secrets.token_hex(16)}"
    os.mkdir(temporary, 0o700, dir_fd=generation_fd)
    cleanup_state = {"name": temporary}
    cleanup_registry.register(
        "nf1-install",
        lambda: cleanup_partial_nf1_install(generation_fd, cleanup_state, expected),
    )
    temporary_fd = os.open(
        temporary,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=generation_fd,
    )
    try:
        for name, (kind, _, _, _) in sorted(
            expected.items(), key=lambda item: (item[0].count("/"), item[0])
        ):
            if kind == "dir":
                parent = (
                    _open_relative(temporary_fd, tuple(name.split("/")[:-1]), True)
                    if "/" in name
                    else os.dup(temporary_fd)
                )
                os.mkdir(name.rsplit("/", 1)[-1], 0o700, dir_fd=parent)
                os.close(parent)
        for name, (kind, expected_digest, mode, size) in sorted(expected.items()):
            source_object = _open_relative(
                source_fd, tuple(name.split("/")), kind == "dir"
            )
            metadata = os.fstat(source_object)
            source_generations[name] = _file_generation(metadata)
            if metadata.st_uid != 0 or metadata.st_gid != 0:
                block("NF1 accepted install ownership is unsafe")
            if kind == "dir":
                if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(
                    metadata.st_mode
                ) not in _accepted_nf1_source_modes(0o555):
                    block("NF1 accepted install directory metadata is unsafe")
                os.close(source_object)
                continue
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode)
                not in _accepted_nf1_source_modes(mode)
                or metadata.st_size != size
            ):
                block("NF1 accepted install file metadata is unsafe")
            parent = (
                _open_relative(temporary_fd, tuple(name.split("/")[:-1]), True)
                if "/" in name
                else os.dup(temporary_fd)
            )
            destination = os.open(
                name.rsplit("/", 1)[-1],
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o400,
                dir_fd=parent,
            )
            value = hashlib.sha256()
            while True:
                chunk = os.read(source_object, 1024 * 1024)
                if not chunk:
                    break
                value.update(chunk)
                view = memoryview(chunk)
                while view:
                    view = view[os.write(destination, view) :]
            if value.hexdigest() != expected_digest:
                block("NF1 accepted install content differs from archive")
            if _file_generation(os.fstat(source_object)) != source_generations[name]:
                block("NF1 accepted install generation changed during staging")
            os.fchown(destination, 0, 0)
            os.fchmod(destination, mode)
            os.fsync(destination)
            os.close(destination)
            os.fsync(parent)
            os.close(parent)
            os.close(source_object)
        for name, (kind, _, _, _) in sorted(
            expected.items(), key=lambda item: item[0].count("/"), reverse=True
        ):
            if kind == "dir":
                directory = _open_relative(temporary_fd, tuple(name.split("/")), True)
                os.fchmod(directory, 0o555)
                os.fsync(directory)
                os.close(directory)
        if _file_generation(os.fstat(source_fd)) != source_root_generation:
            block("NF1 accepted install root generation changed during staging")
        for name, generation in source_generations.items():
            descriptor = _open_relative(
                source_fd,
                tuple(name.split("/")),
                expected[name][0] == "dir",
            )
            if _file_generation(os.fstat(descriptor)) != generation:
                block("NF1 accepted install changed after staging")
            os.close(descriptor)
        os.fchmod(temporary_fd, 0o555)
        os.fsync(temporary_fd)
        os.rename(
            temporary, "nf1-install", src_dir_fd=generation_fd, dst_dir_fd=generation_fd
        )
        cleanup_state["name"] = "nf1-install"
        os.fsync(generation_fd)
    finally:
        os.close(temporary_fd)
        os.close(source_fd)
    return expected


def cleanup_nf1_install(
    generation_fd: int, expected: dict[str, tuple[str, str, int, int]]
) -> None:
    root_fd = os.open(
        "nf1-install",
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=generation_fd,
    )
    root_metadata = os.fstat(root_fd)
    if (
        root_metadata.st_uid != 0
        or root_metadata.st_gid != 0
        or stat.S_IMODE(root_metadata.st_mode) != 0o555
        or _closed_tree_names(root_fd) != set(expected)
    ):
        block("staged NF1 cleanup closed set is unsafe")
    for name, (kind, _, mode, size) in sorted(
        expected.items(), key=lambda item: item[0].count("/"), reverse=True
    ):
        descriptor = _open_relative(root_fd, tuple(name.split("/")), kind == "dir")
        metadata = os.fstat(descriptor)
        if (
            metadata.st_uid != 0
            or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) != mode
            or (kind == "file" and (metadata.st_nlink != 1 or metadata.st_size != size))
        ):
            block("staged NF1 cleanup metadata mismatch")
        os.close(descriptor)
        parent = (
            _open_relative(root_fd, tuple(name.split("/")[:-1]), True)
            if "/" in name
            else os.dup(root_fd)
        )
        if kind == "file":
            os.unlink(name.rsplit("/", 1)[-1], dir_fd=parent)
        else:
            os.rmdir(name.rsplit("/", 1)[-1], dir_fd=parent)
        os.fsync(parent)
        os.close(parent)
    os.close(root_fd)
    os.rmdir("nf1-install", dir_fd=generation_fd)
    os.fsync(generation_fd)


def finalize_handoff_generation(
    parent_fd: int,
    generation_fd: int,
    generation: str,
    artifacts: dict[str, tuple[int, ...]],
) -> None:
    """Transition ACTIVE to TERMINAL, then remove only this exact generation."""
    active = os.stat("ACTIVE", dir_fd=generation_fd, follow_symlinks=False)
    if (
        not stat.S_ISREG(active.st_mode)
        or active.st_uid != 0
        or active.st_gid != 0
        or active.st_nlink != 1
        or stat.S_IMODE(active.st_mode) != 0o400
    ):
        block("NF3 handoff ACTIVE lifecycle marker is unsafe")
    os.rename("ACTIVE", "TERMINAL", src_dir_fd=generation_fd, dst_dir_fd=generation_fd)
    os.fsync(generation_fd)
    for name, artifact_generation in reversed(artifacts.items()):
        cleanup_handoff_file(generation_fd, name, artifact_generation)
    terminal = os.stat("TERMINAL", dir_fd=generation_fd, follow_symlinks=False)
    if not stat.S_ISREG(terminal.st_mode) or terminal.st_nlink != 1:
        block("NF3 handoff TERMINAL lifecycle marker is unsafe")
    os.unlink("TERMINAL", dir_fd=generation_fd)
    os.fsync(generation_fd)
    if os.listdir(generation_fd):
        block("NF3 handoff generation has unknown entries at terminal cleanup")
    os.close(generation_fd)
    os.rmdir(generation, dir_fd=parent_fd)
    os.fsync(parent_fd)
    if os.listdir(parent_fd):
        block("NF3 handoff StateDirectory is not empty after terminal cleanup")
    os.close(parent_fd)


@contextlib.contextmanager
def handoff_session(reviewed_commit: str):
    """Own one exact handoff generation and always drive it to terminal cleanup."""
    parent_fd, generation_fd, generation, mount_identity = _validate_handoff_directory(
        reviewed_commit
    )
    artifacts: dict[str, tuple[int, ...]] = {}
    cleanup_registry = HandoffCleanupRegistry()
    try:
        yield generation_fd, generation, mount_identity, artifacts, cleanup_registry
    finally:
        cleanup_registry.cleanup_all()
        finalize_handoff_generation(
            parent_fd,
            generation_fd,
            generation,
            artifacts,
        )


def load_nf2_harness(repo: Path):
    path = repo / NF2_HARNESS
    specification = importlib.util.spec_from_file_location(
        "trustforge_nf2_linux_harness", path
    )
    if specification is None or specification.loader is None:
        block("cannot load reviewed NF2 Linux harness")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def verified_rust_toolchain(repo: Path, scratch: Path):
    """Resolve the exact NF2-reviewed Rust toolchain without ambient PATH."""
    harness = load_nf2_harness(repo)
    receipt = repo / harness.HOST_RECEIPT
    try:
        receipt_digest = digest(receipt)
        receipt_value = json.loads(receipt.read_bytes())
    except (OSError, json.JSONDecodeError):
        block("canonical Linux host receipt cannot be parsed")
    if receipt_digest != harness.HOST_RECEIPT_SHA256:
        block("canonical Linux host receipt differs from reviewed NF2 contract")
    harness.RECEIPT_TOOL_RECORDS.update(harness.validate_host_receipt(receipt_value))
    try:
        harness.VERIFIED_HOST_TOOLS["rustup"] = harness.verify_tool(
            harness.ROOT_RUSTUP,
            harness.APPROVED_TOOL_SHA256["rustup"],
            "rustup",
        )
    except OSError:
        block("reviewed rustup executable is missing or unreadable")
    rustup = harness.host_tool("rustup")
    rustup_environment = {
        "HOME": "/root",
        "RUSTUP_HOME": "/root/.rustup",
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
    }
    for label in ("cargo", "rustc"):
        resolved = subprocess.run(
            ["rustup", "which", "--toolchain", harness.PINNED_TOOLCHAIN, label],
            executable=rustup,
            env=rustup_environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=harness.verified_pass_fds(),
            check=False,
        )
        if resolved.returncode != 0:
            block(f"pinned Rust tool unavailable: {label}")
        try:
            harness.VERIFIED_HOST_TOOLS[label] = harness.verify_tool(
                Path(resolved.stdout.strip()),
                harness.APPROVED_TOOL_SHA256[label],
                label,
            )
        except OSError:
            block(f"reviewed Rust executable is missing or unreadable: {label}")
    try:
        harness.VERIFIED_HOST_TOOLS["rust-lld"] = harness.verify_tool(
            harness.ROOT_RUST_LLD,
            harness.APPROVED_TOOL_SHA256["rust-lld"],
            "rust-lld",
        )
    except OSError:
        block("reviewed rust-lld executable is missing or unreadable")
    rustc = harness.host_tool("rustc")
    cargo = harness.host_tool("cargo")
    rust_lld = harness.host_tool("rust-lld")
    home = scratch / "home"
    cargo_home = scratch / "cargo-home"
    home.mkdir(parents=True)
    cargo_home.mkdir()
    environment = {
        "HOME": str(home),
        "CARGO_HOME": str(cargo_home),
        "RUSTUP_HOME": "/root/.rustup",
        "PATH": os.pathsep.join(
            dict.fromkeys(
                (
                    str(harness.VERIFIED_HOST_TOOLS["cargo"].resolved.parent),
                    str(harness.VERIFIED_HOST_TOOLS["rustc"].resolved.parent),
                    "/usr/bin",
                    "/bin",
                )
            )
        ),
        "RUSTC": rustc,
        "RUSTDOC": FORBIDDEN_RUSTDOC,
        "TRUSTFORGE_RUST_LLD": rust_lld,
        "CARGO_NET_OFFLINE": "true",
        "LANG": "C",
        "LC_ALL": "C",
    }
    rust_version = subprocess.run(
        [rustc, "-Vv"],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pass_fds=harness.verified_pass_fds(),
        check=False,
    )
    cargo_version = subprocess.run(
        [cargo, "-V"],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pass_fds=harness.verified_pass_fds(),
        check=False,
    )
    if (
        rust_version.returncode != 0
        or cargo_version.returncode != 0
        or f"release: {harness.PINNED_RUST_RELEASE}" not in rust_version.stdout
        or f"commit-hash: {harness.PINNED_RUST_COMMIT}" not in rust_version.stdout
        or not cargo_version.stdout.startswith(f"cargo {harness.PINNED_RUST_RELEASE} ")
    ):
        block("pinned Rust 1.96.0 toolchain identity mismatch")
    try:
        target_root, target_entries = harness.load_target_receipt(repo)
        harness.verify_target_tree(target_root, target_entries)
    except OSError:
        block("reviewed Rust musl target tree is missing or unreadable")
    return harness, environment, target_root, target_entries


def cargo_environment(
    base_environment: dict[str, str], source_tree: Path, target_root: Path
) -> dict[str, str]:
    environment = base_environment.copy()
    environment["CARGO_TARGET_DIR"] = str(target_root)
    rust_lld = environment.pop("TRUSTFORGE_RUST_LLD")
    environment["RUSTFLAGS"] = (
        f"-C linker={rust_lld} "
        "-C linker-flavor=ld.lld "
        f"--remap-path-prefix={source_tree}={CANONICAL_SOURCE_ROOT}"
    )
    return environment


def frame(value: bytes) -> bytes:
    return len(value).to_bytes(4, "big") + value


def linked_source_digest(repo: Path) -> str:
    root = repo / "native/nf2-zero-capability-broker"
    workspace = repo / "native"
    # Cargo.lock is the workspace-root authoritative resolution (PR-B2 dedup);
    # src/sha256.rs moved to trustforge-native-sys. Keep in sync with
    # foundation.rs linked_nf2_source_sha256() SOURCES (12 entries).
    sources = [
        ("Cargo.lock", workspace / "Cargo.lock"),
        ("Cargo.toml", root / "Cargo.toml"),
        ("src/canonical_json.rs", root / "src/canonical_json.rs"),
        ("src/capability.rs", root / "src/capability.rs"),
        ("src/lib.rs", root / "src/lib.rs"),
        ("src/linux.rs", root / "src/linux.rs"),
        ("src/linux/live.rs", root / "src/linux/live.rs"),
        ("src/linux/process.rs", root / "src/linux/process.rs"),
        ("src/linux/sealed.rs", root / "src/linux/sealed.rs"),
        ("src/main.rs", root / "src/main.rs"),
        ("src/manifest.rs", root / "src/manifest.rs"),
        ("src/native_sys.rs", workspace / "trustforge-native-sys/src/lib.rs"),
    ]
    value = hashlib.sha256(b"trustforge.nf2.linked-source.v1\0")
    for name, path in sources:
        payload = path.read_bytes()
        value.update(frame(name.encode()))
        value.update(len(payload).to_bytes(8, "big"))
        value.update(payload)
    return value.hexdigest()


def source_tree_receipt(tree_oid: str, source_sha256: str) -> str:
    value = hashlib.sha256(b"trustforge.nf2.source-tree-receipt.v1\0")
    for name, payload in (
        ("git_subtree_oid_sha1", tree_oid),
        ("linked_source_sha256", source_sha256),
    ):
        value.update(frame(name.encode()))
        value.update(frame(payload.encode()))
    return value.hexdigest()


def verify_evidence_profile(repo: Path) -> None:
    manifest = tomllib.loads(
        (repo / "native/Cargo.toml").read_text()
    )
    profiles = manifest.get("profile", {})
    if profiles.get("evidence") != EVIDENCE_PROFILE:
        block("Cargo evidence profile differs from reviewed semantics")
    if profiles.get("release") != RELEASE_PROFILE:
        block("Cargo release profile differs from reviewed semantics")
    canonical = (
        "v1\nname=evidence\ninherits=dev\nopt_level=2\n"
        "debug_assertions=true\nincremental=false\ncodegen_units=1\n"
        "lto=false\npanic=unwind\nstrip=none\n"
        "rustflags=-C linker=rust-lld -C linker-flavor=ld.lld "
        "--remap-path-prefix=<source-tree>=/workspace/trustforge\n"
    ).encode()
    if hashlib.sha256(canonical).hexdigest() != EVIDENCE_PROFILE_RECEIPT_SHA256:
        raise RuntimeError("evidence profile receipt constant mismatch")
    release_canonical = (
        "v1\nname=release\nopt_level=3\ndebug_assertions=false\n"
        "incremental=false\ncodegen_units=1\nlto=false\npanic=abort\n"
        "strip=symbols\nrustflags=-C linker=rust-lld "
        "-C linker-flavor=ld.lld "
        "--remap-path-prefix=<source-tree>=/workspace/trustforge\n"
    ).encode()
    if hashlib.sha256(release_canonical).hexdigest() != RELEASE_PROFILE_RECEIPT_SHA256:
        raise RuntimeError("release profile receipt constant mismatch")
    toolchain_canonical = (
        "v1\n"
        "merge=7c26416581a8437a6d00d7941357826b2650c474\n"
        "target=x86_64-unknown-linux-musl\n"
        "profile=release\n"
        "locked=true\n"
        "rust_release=1.96.0\n"
        "rust_commit=ac68faa20c58cbccd01ee7208bf3b6e93a7d7f96\n"
        "target_receipt=49c92219312e619b6b49b9355425fa84c21da02fb38828819ba41ecc3b3489d1\n"
        "target_entries=738cd55ce0397d85b911f5171ef68c48b320465f58a8e2fd65e4067a2668979a\n"
        "cargo_lock=28f0970413222e7d6c65da3aa379e5ca1cbb8c30345a16d04204762ac1e30cbb\n"
        "cargo_toml=3b28816d29673cf4e4a1b6554fa42d367e796c4b1dd3850320af335cf73033d2\n"
        "source_remap=/workspace/trustforge\n"
    ).encode()
    if (
        hashlib.sha256(toolchain_canonical).hexdigest()
        != FIXED_TOOLCHAIN_RECEIPT_SHA256
    ):
        raise RuntimeError("fixed toolchain receipt constant mismatch")
    source_sha256 = linked_source_digest(repo)
    if source_sha256 != REVIEWED_NR1A_LINKED_SOURCE_SHA256:
        raise RuntimeError("linked source receipt mismatch")
    if (
        source_tree_receipt(REVIEWED_NR1A_NF2_TREE, source_sha256)
        != REVIEWED_NR1A_SOURCE_TREE_RECEIPT_SHA256
    ):
        raise RuntimeError("platform-independent source-tree receipt mismatch")


def require_host() -> None:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        block("native Linux x86_64 required")
    if os.geteuid() != 0:
        block("root required")
    if (
        Path("/run/systemd/container").exists()
        and Path("/run/systemd/container").read_text().strip()
    ):
        block("systemd container marker present")
    cgroup = Path("/proc/1/cgroup").read_text()
    if any(
        marker in cgroup.lower()
        for marker in ("docker", "containerd", "kubepods", "lxc", "podman")
    ):
        block("container cgroup marker present")
    if (
        subprocess.run(
            ["systemd-detect-virt", "--container", "--quiet"], check=False
        ).returncode
        == 0
    ):
        block("systemd reports a container")
    systemd_state = subprocess.run(
        ["systemctl", "is-system-running"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    if systemd_state not in ("running", "degraded"):
        block("running systemd required")


def exact_one(root: Path, pattern: str) -> Path:
    matches = list(root.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"expected one {pattern}, found {len(matches)}")
    return matches[0]


def copy_reviewed_build_inputs(repo: Path, destination: Path) -> Path:
    destination.mkdir(mode=0o700)
    native = destination / "native"
    native.mkdir(mode=0o700)
    for crate in ("nf2-zero-capability-broker", "nf3-one-shot-transaction"):
        shutil.copytree(repo / "native" / crate, native / crate)
    # nf2/nf3 path-depend on trustforge-native-sys, and nf3 foundation.rs
    # include_bytes!("../../Cargo.lock") resolves to the workspace-root lock;
    # the workspace-root manifest is needed for cargo to discover the workspace.
    # Copy these so the canonical view is a complete, self-resolving workspace.
    shutil.copytree(
        repo / "native" / "trustforge-native-sys", native / "trustforge-native-sys"
    )
    shutil.copy2(repo / "native" / "Cargo.toml", native / "Cargo.toml")
    shutil.copy2(repo / "native" / "Cargo.lock", native / "Cargo.lock")
    for root, directories, files in os.walk(destination):
        paths = [Path(root), *(Path(root) / name for name in directories + files)]
        for path in paths:
            metadata = path.lstat()
            if metadata.st_uid != 0 or metadata.st_gid != 0:
                block("cross-path build input is not root-owned")
            if stat.S_ISLNK(metadata.st_mode):
                block("cross-path build input contains a symlink")
    return destination


def verify_owned_tree(root: Path) -> None:
    for current, directories, files in os.walk(root):
        paths = [Path(current), *(Path(current) / name for name in directories + files)]
        for path in paths:
            metadata = path.lstat()
            if (
                metadata.st_uid != 0
                or metadata.st_gid != 0
                or stat.S_ISLNK(metadata.st_mode)
            ):
                block("canonical build view is not a root-owned symlink-free tree")


def install_canonical_build_view(source: Path) -> Path:
    try:
        parent = CANONICAL_BUILD_PARENT.lstat()
    except OSError:
        block("systemd RuntimeDirectory for canonical build input is missing")
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != 0
        or parent.st_gid != 0
        or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        block("canonical build parent must be root-owned mode 0700")
    entries = list(CANONICAL_BUILD_PARENT.iterdir())
    if any(entry.name != CANONICAL_BUILD_SOURCE.name for entry in entries):
        block("canonical build parent contains an unexpected entry")
    if os.path.lexists(CANONICAL_BUILD_SOURCE):
        metadata = CANONICAL_BUILD_SOURCE.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            block("existing canonical build source is unsafe")
        verify_owned_tree(CANONICAL_BUILD_SOURCE)
        shutil.rmtree(CANONICAL_BUILD_SOURCE)
    copy_reviewed_build_inputs(source, CANONICAL_BUILD_SOURCE)
    verify_owned_tree(CANONICAL_BUILD_SOURCE)
    return CANONICAL_BUILD_SOURCE


def verify_target_tree(harness, root: Path, entries: list[dict[str, object]]) -> None:
    try:
        harness.verify_target_tree(root, entries)
    except OSError:
        block("reviewed Rust musl target tree changed or became unreadable")


def build_graph(repo: Path, target_root: Path, toolchain) -> tuple[Path, Path, Path]:
    harness, base_environment, target_tree, target_entries = toolchain
    environment = cargo_environment(base_environment, repo, target_root)
    subprocess.run(
        [
            harness.host_tool("cargo"),
            "build",
            "--manifest-path",
            str(repo / "native/nf3-one-shot-transaction/Cargo.toml"),
            "--release",
            "--target",
            TARGET,
            "--locked",
            "--offline",
            "--frozen",
        ],
        cwd=repo,
        env=environment,
        pass_fds=harness.verified_pass_fds(),
        check=True,
    )
    verify_target_tree(harness, target_tree, target_entries)
    release = target_root / TARGET / "release"
    return (
        exact_one(release / "deps", "libtrustforge_nf2_zero_capability_broker-*.rlib"),
        release / "libtrustforge_nf3_one_shot_transaction.rlib",
        release / "nf3_profile_probe",
    )


def build_helper(repo: Path, target_root: Path, toolchain) -> tuple[Path, Path]:
    harness, base_environment, target_tree, target_entries = toolchain
    environment = cargo_environment(base_environment, repo, target_root)
    subprocess.run(
        [
            harness.host_tool("cargo"),
            "build",
            "--manifest-path",
            str(repo / "native/nf3-one-shot-transaction/Cargo.toml"),
            "--profile",
            "evidence",
            "--target",
            TARGET,
            "--locked",
            "--offline",
            "--frozen",
            "--features",
            "adversarial-test-hooks",
            "--bin",
            "nf3-test-helper",
        ],
        cwd=repo,
        env=environment,
        pass_fds=harness.verified_pass_fds(),
        check=True,
    )
    verify_target_tree(harness, target_tree, target_entries)
    profile_root = target_root / TARGET / "evidence"
    return (
        profile_root / "nf3-test-helper",
        exact_one(
            profile_root / "deps",
            "libtrustforge_nf2_zero_capability_broker-*.rlib",
        ),
    )


def verify_release_rejects_hooks(repo: Path, target_root: Path, toolchain) -> None:
    harness, base_environment, target_tree, target_entries = toolchain
    environment = cargo_environment(base_environment, repo, target_root)
    result = subprocess.run(
        [
            harness.host_tool("cargo"),
            "check",
            "--manifest-path",
            str(repo / "native/nf3-one-shot-transaction/Cargo.toml"),
            "--release",
            "--target",
            TARGET,
            "--locked",
            "--offline",
            "--frozen",
            "--features",
            "adversarial-test-hooks",
        ],
        cwd=repo,
        env=environment,
        pass_fds=harness.verified_pass_fds(),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if (
        result.returncode == 0
        or "adversarial test hooks are forbidden" not in result.stdout
    ):
        raise RuntimeError("release did not structurally reject adversarial hooks")
    verify_target_tree(harness, target_tree, target_entries)


def write_build_receipt(
    path: Path, profile: str, executable_sha256: str, rlib_sha256: str
) -> None:
    profile_receipt = {
        "evidence": EVIDENCE_PROFILE_RECEIPT_SHA256,
        "release": RELEASE_PROFILE_RECEIPT_SHA256,
    }[profile]
    payload = (
        "v1\n"
        f"profile={profile}\n"
        f"executable_sha256={executable_sha256}\n"
        f"linked_nf2_source_sha256={REVIEWED_NR1A_LINKED_SOURCE_SHA256}\n"
        f"linked_nf2_rlib_sha256={rlib_sha256}\n"
        f"profile_receipt_sha256={profile_receipt}\n"
        "toolchain_receipt_sha256="
        f"{FIXED_TOOLCHAIN_RECEIPT_SHA256}\n"
        f"source_tree_receipt_sha256={REVIEWED_NR1A_SOURCE_TREE_RECEIPT_SHA256}\n"
    ).encode()
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        os.fchown(descriptor, 0, 0)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-tree", type=Path, required=True)
    parser.add_argument("--reviewed-commit")
    parser.add_argument("--accepted-install", type=Path)
    parser.add_argument("--accepted-archive", type=Path)
    parser.add_argument("--accepted-nf1-source", type=Path)
    parser.add_argument("--accepted-nf1-commit")
    parser.add_argument("--evidence-out", type=Path)
    parser.add_argument("--verify-profile-only", action="store_true")
    parser.add_argument("--probe-remapped-builds", action="store_true")
    arguments = parser.parse_args()
    repo = arguments.source_tree.resolve(strict=True)
    verify_evidence_profile(repo)
    if arguments.verify_profile_only:
        print("PASS: Cargo evidence profile semantics and receipt")
        return 0
    required = (
        "reviewed_commit",
        "accepted_install",
        "accepted_archive",
        "accepted_nf1_source",
        "accepted_nf1_commit",
        "evidence_out",
    )
    missing = [name for name in required if getattr(arguments, name) is None]
    if missing:
        parser.error(f"missing required evidence arguments: {', '.join(missing)}")
    require_host()
    install = arguments.accepted_install.resolve(strict=True)
    archive = arguments.accepted_archive.resolve(strict=True)
    nf1_source = arguments.accepted_nf1_source.resolve(strict=True)
    if arguments.accepted_nf1_commit != ACCEPTED_NF1_COMMIT:
        block("accepted NF1 commit differs from fixed receipt")
    if (
        run(["git", "rev-parse", "HEAD"], cwd=nf1_source, capture=True)
        != ACCEPTED_NF1_COMMIT
    ):
        block("accepted NF1 source checkout mismatch")
    if run(["git", "status", "--porcelain"], cwd=nf1_source, capture=True):
        block("accepted NF1 source is dirty")
    if (
        digest(archive)
        != "808487c590a183a8df2e69cfc5257969e18ae88b15c4378da95d97add6c03c1b"
    ):
        block("accepted NF1 archive digest mismatch")
    head = run(["git", "rev-parse", "HEAD"], cwd=repo, capture=True)
    tree = run(["git", "rev-parse", "HEAD^{tree}"], cwd=repo, capture=True)
    if head != arguments.reviewed_commit:
        block("reviewed commit is not checked-out HEAD")
    if run(["git", "status", "--porcelain"], cwd=repo, capture=True):
        block("source worktree is dirty")
    nf2_tree = run(
        ["git", "rev-parse", f"{ACCEPTED_NF2_MERGE}:native/nf2-zero-capability-broker"],
        cwd=repo,
        capture=True,
    )
    if nf2_tree != ACCEPTED_NF2_TREE:
        block("accepted NF2 tree mismatch")
    reviewed_nf2_tree = run(
        ["git", "rev-parse", "HEAD:native/nf2-zero-capability-broker"],
        cwd=repo,
        capture=True,
    )
    if reviewed_nf2_tree != REVIEWED_NR1A_NF2_TREE:
        block("reviewed NR1a-A NF2 candidate tree mismatch")
    source_sha256 = linked_source_digest(repo)
    if source_sha256 != REVIEWED_NR1A_LINKED_SOURCE_SHA256:
        block("accepted NF2 canonical linked source mismatch")
    if (
        source_tree_receipt(reviewed_nf2_tree, source_sha256)
        != REVIEWED_NR1A_SOURCE_TREE_RECEIPT_SHA256
    ):
        block("accepted NF2 canonical source-tree receipt mismatch")
    changed_nf2 = set(
        run(
            [
                "git",
                "diff",
                "--name-only",
                ACCEPTED_NF2_MERGE,
                "HEAD",
                "--",
                "native/nf2-zero-capability-broker",
            ],
            cwd=repo,
            capture=True,
        ).splitlines()
    )
    expected_nf2_changes = {
        "native/nf2-zero-capability-broker/src/capability.rs",
        "native/nf2-zero-capability-broker/src/lib.rs",
        "native/nf2-zero-capability-broker/src/linux.rs",
        "native/nf2-zero-capability-broker/src/linux/live.rs",
        "native/nf2-zero-capability-broker/src/linux/process.rs",
    }
    if changed_nf2 != expected_nf2_changes:
        block("linked NF2 source differs from reviewed NR1a-A allowlist")
    if not arguments.probe_remapped_builds and any(
        value == BLOCKED_RECEIPT
        for value in (
            EXPECTED_RELEASE_RLIB_SHA256,
            EXPECTED_EVIDENCE_RLIB_SHA256,
            EXPECTED_EVIDENCE_HELPER_SHA256,
        )
    ):
        block("native cross-view build receipts are not yet reviewed")

    with tempfile.TemporaryDirectory(prefix="trustforge-nf3-b-") as raw:
        scratch = Path(raw)
        toolchain = verified_rust_toolchain(repo, scratch / "toolchain-environment")
        source_a = copy_reviewed_build_inputs(repo, scratch / "source-a")
        source_b = copy_reviewed_build_inputs(repo, scratch / "different-source-b")
        canonical_source = install_canonical_build_view(source_a)
        rlib_a, _, release_probe_a = build_graph(
            canonical_source, scratch / "release-a", toolchain
        )
        canonical_source = install_canonical_build_view(source_b)
        rlib_b, _, release_probe_b = build_graph(
            canonical_source, scratch / "release-b", toolchain
        )
        rlib_hashes = [digest(rlib_a), digest(rlib_b)]
        if len(set(rlib_hashes)) != 1:
            block("linked NF2 release rlib cross-view builds differ")
        release_probe_hash = digest(release_probe_a)
        if release_probe_hash != digest(release_probe_b):
            block("release profile probe double build differs")
        canonical_source = install_canonical_build_view(source_a)
        helper_a, evidence_rlib_a = build_helper(
            canonical_source, scratch / "helper-a", toolchain
        )
        canonical_source = install_canonical_build_view(source_b)
        helper_b, evidence_rlib_b = build_helper(
            canonical_source, scratch / "helper-b", toolchain
        )
        helper_hashes = [digest(helper_a), digest(helper_b)]
        if len(set(helper_hashes)) != 1:
            block("integrated helper cross-view builds differ")
        evidence_rlib_hashes = [digest(evidence_rlib_a), digest(evidence_rlib_b)]
        if len(set(evidence_rlib_hashes)) != 1:
            block("linked NF2 evidence rlib cross-view builds differ")
        if arguments.probe_remapped_builds:
            print(
                json.dumps(
                    {
                        "release_rlib_sha256": rlib_hashes,
                        "release_probe_sha256": [
                            release_probe_hash,
                            digest(release_probe_b),
                        ],
                        "evidence_rlib_sha256": evidence_rlib_hashes,
                        "evidence_helper_sha256": helper_hashes,
                    },
                    sort_keys=True,
                )
            )
            block("remapped native receipts await exact-commit review")
        if rlib_hashes[0] != EXPECTED_RELEASE_RLIB_SHA256:
            block("linked NF2 release rlib is not the pinned reproducible object")
        if release_probe_hash != EXPECTED_RELEASE_PROBE_SHA256:
            block("release profile probe is not the pinned native-host object")
        if helper_hashes[0] != EXPECTED_EVIDENCE_HELPER_SHA256:
            block("integrated helper is not the pinned native-host object")
        if evidence_rlib_hashes[0] != EXPECTED_EVIDENCE_RLIB_SHA256:
            block("exact NF2 rlib consumed by evidence helper differs")
        with handoff_session(head) as (
            handoff_fd,
            handoff_generation,
            handoff_mount,
            handoff_generations,
            handoff_cleanups,
        ):
            handoff_path = HANDOFF_ROOT / handoff_generation
            release_receipt = scratch / "release-receipt.v1"
            write_build_receipt(
                release_receipt,
                "release",
                release_probe_hash,
                EXPECTED_RELEASE_RLIB_SHA256,
            )
            handoff_generations["release-receipt.v1"] = stage_handoff_file(
                handoff_fd,
                release_receipt,
                "release-receipt.v1",
                expected_sha256=digest(release_receipt),
            )
            handoff_generations["release-probe"] = stage_handoff_file(
                handoff_fd,
                release_probe_a,
                "release-probe",
                expected_sha256=release_probe_hash,
                executable=True,
            )
            release_profile_line = run(
                [
                    "systemd-run",
                    "--wait",
                    "--collect",
                    "--pipe",
                    f"--unit=trustforge-nf3-release-receipt-{head[:12]}",
                    "--property=Type=oneshot",
                    "--property=User=root",
                    "--property=NoNewPrivileges=yes",
                    "--property=ProtectSystem=strict",
                    "--property=PrivateTmp=yes",
                    "--property=RuntimeDirectory=trustforge-nf3-build",
                    "--property=RuntimeDirectoryMode=0700",
                    f"--property=BindReadOnlyPaths={handoff_path / 'release-receipt.v1'}:/run/trustforge-nf3-build/receipt.v1",
                    f"--property=BindReadOnlyPaths={handoff_path / 'release-probe'}:/run/trustforge-nf3-release-probe",
                    SYSTEMD_EXEC_WRAPPER,
                    "/run/trustforge-nf3-release-probe",
                ],
                cwd=repo,
                capture=True,
            )
            expected_release_fields = {
                "profile": "release",
                "source": REVIEWED_NR1A_LINKED_SOURCE_SHA256,
                "rlib": EXPECTED_RELEASE_RLIB_SHA256,
                "profile_receipt": RELEASE_PROFILE_RECEIPT_SHA256,
                "foundation": REVIEWED_NR1A_FOUNDATION_RELEASE_SHA256,
            }
            tokens = release_profile_line.split()
            if len(tokens) != 6 or tokens[0] != "BOUND_PROFILE":
                block("release profile probe shape mismatch")
            actual_release_fields = dict(token.split("=", 1) for token in tokens[1:])
            if actual_release_fields != expected_release_fields:
                block("release profile probe identity mismatch")
            evidence_receipt = scratch / "evidence-receipt.v1"
            write_build_receipt(
                evidence_receipt,
                "evidence",
                helper_hashes[0],
                EXPECTED_EVIDENCE_RLIB_SHA256,
            )
            handoff_generations["evidence-receipt.v1"] = stage_handoff_file(
                handoff_fd,
                evidence_receipt,
                "evidence-receipt.v1",
                expected_sha256=digest(evidence_receipt),
            )
            handoff_generations["evidence-helper"] = stage_handoff_file(
                handoff_fd,
                helper_a,
                "evidence-helper",
                expected_sha256=helper_hashes[0],
                executable=True,
            )
            handoff_generations["evidence-nf2.rlib"] = stage_handoff_file(
                handoff_fd,
                evidence_rlib_a,
                "evidence-nf2.rlib",
                expected_sha256=EXPECTED_EVIDENCE_RLIB_SHA256,
            )
            integrated_script = (
                repo / "native/nf3-one-shot-transaction/tests/run_integrated_linux.sh"
            )
            handoff_generations["run-integrated-linux"] = stage_handoff_file(
                handoff_fd,
                integrated_script,
                "run-integrated-linux",
                expected_sha256=digest(integrated_script),
                executable=True,
            )
            verify_release_rejects_hooks(
                canonical_source, scratch / "release-hook-rejection", toolchain
            )

            harness, base_environment, target_tree, target_entries = toolchain
            test_environment = cargo_environment(
                base_environment, canonical_source, scratch / "tests"
            )
            run(
                [
                    harness.host_tool("cargo"),
                    "test",
                    "--manifest-path",
                    str(
                        canonical_source / "native/nf3-one-shot-transaction/Cargo.toml"
                    ),
                    "--target",
                    TARGET,
                    "--lib",
                    "--tests",
                    "--bins",
                    "--locked",
                    "--offline",
                    "--frozen",
                ],
                cwd=canonical_source,
                environment=test_environment,
                pass_fds=harness.verified_pass_fds(),
            )
            verify_target_tree(harness, target_tree, target_entries)

            run(
                [
                    SYSTEM_PYTHON,
                    str(repo / "scripts/test_nf2_zero_capability_linux.py"),
                    "--source-tree",
                    str(repo),
                    "--reviewed-commit",
                    arguments.reviewed_commit,
                    "--accepted-install",
                    str(install),
                    "--accepted-archive",
                    str(archive),
                ],
                cwd=repo,
            )
            run(
                [
                    str(
                        repo
                        / "native/nf3-one-shot-transaction/tests/run_crash_matrix.sh"
                    ),
                    str(helper_a),
                    "/root",
                ],
                cwd=repo,
            )
            unit = f"trustforge-nf3-b-{head[:12]}"
            staged_nf1_expected = stage_nf1_install(
                handoff_fd, install, archive, harness, handoff_cleanups
            )
            staged_nf1_install = handoff_path / "nf1-install"
            os.mkdir("cases", 0o700, dir_fd=handoff_fd)
            handoff_cleanups.register(
                "cases", lambda: cleanup_partial_cases_tree(handoff_fd)
            )
            os.fsync(handoff_fd)
            cases_root = handoff_path / "cases"
            service_helper = Path(f"/run/{unit}-helper")
            service_rlib = Path(f"/run/{unit}-nf2.rlib")
            properties = [
                "Type=oneshot",
                "User=root",
                "Group=root",
                "UMask=0077",
                "NoNewPrivileges=yes",
                "PrivateTmp=yes",
                "ProtectSystem=strict",
                "ProtectHome=read-only",
                "ProtectKernelTunables=yes",
                "ProtectKernelModules=yes",
                "ProtectControlGroups=yes",
                "RestrictAddressFamilies=AF_UNIX",
                "CapabilityBoundingSet=CAP_SYS_ADMIN CAP_SYS_PTRACE CAP_KILL",
                "RuntimeDirectory=trustforge-nf3-build",
                "RuntimeDirectoryMode=0700",
                f"BindReadOnlyPaths={handoff_path / 'evidence-receipt.v1'}:/run/trustforge-nf3-build/receipt.v1",
                f"BindReadOnlyPaths={staged_nf1_install}:/opt/trustforge/native-foundation/current",
                f"BindReadOnlyPaths={handoff_path / 'evidence-helper'}:{service_helper}",
                f"BindReadOnlyPaths={handoff_path / 'evidence-nf2.rlib'}:{service_rlib}",
                f"BindReadOnlyPaths={handoff_path / 'run-integrated-linux'}:/run/trustforge-nf3-run-integrated-linux",
                f"ReadWritePaths={cases_root}",
                "TimeoutStartSec=20min",
            ]
            command = ["systemd-run", "--wait", "--collect", "--pipe", f"--unit={unit}"]
            for prop in properties:
                command.extend(["--property", prop])
            command.extend(
                [
                    SYSTEMD_SCRIPT_WRAPPER,
                    "/run/trustforge-nf3-run-integrated-linux",
                    str(service_helper),
                    str(service_rlib),
                    helper_hashes[0],
                    str(cases_root),
                    str(cases_root),
                ]
            )
            run(command, cwd=repo)
            if list(cases_root.glob("trustforge-nf3-integrated-*")) or list(
                cases_root.glob("trustforge-nf3-witness-*")
            ):
                raise RuntimeError("integrated harness store cleanup incomplete")
            case_directories = sorted(cases_root.glob("case-*"))
            if len(case_directories) != 60:
                raise RuntimeError("integrated per-case evidence count is not 60")
            case_receipts: list[str] = []
            case_semantic_records: list[dict[str, object]] = []
            for expected_case, case_directory in enumerate(case_directories, start=1):
                evidence_file = case_directory / "evidence.json"
                receipt = (case_directory / "evidence.json.sha256").read_text().strip()
                if digest(evidence_file) != receipt:
                    raise RuntimeError(f"case receipt mismatch: {case_directory.name}")
                value = json.loads(evidence_file.read_bytes())
                semantic_record = _case_semantic_record(case_directory.name, value)
                if semantic_record["case"] != expected_case:
                    raise RuntimeError("case evidence ordering is not contiguous")
                case_semantic_records.append(semantic_record)
                case_receipts.append(f"{case_directory.name}={receipt}\n")
            case_artifact_collection_sha256 = hashlib.sha256(
                "".join(case_receipts).encode()
            ).hexdigest()
            case_evidence_collection_sha256 = case_semantic_collection_sha256(
                case_semantic_records
            )
            cases_destination = arguments.evidence_out.with_suffix(".cases")
            if cases_destination.exists():
                raise RuntimeError("case evidence destination already exists")
            shutil.copytree(cases_root, cases_destination)
            cleanup_cases_tree(
                handoff_fd,
                [case_directory.name for case_directory in case_directories],
            )
            handoff_cleanups.discard("cases")
            cleanup_nf1_install(handoff_fd, staged_nf1_expected)
            handoff_cleanups.discard("nf1-install")

            evidence = {
                "schema": "trustforge.nf3.integrated-evidence.v1",
                "gray_plan_amendment": (
                    "fixed /run receipt verifies actual executable before claim; "
                    "authority-neutral build evidence only"
                ),
                "commit": head,
                "tree": tree,
                "kernel": platform.release(),
                "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                "accepted_nf1_commit": arguments.accepted_nf1_commit,
                "accepted_nf1_source": str(nf1_source),
                "accepted_archive_sha256": digest(archive),
                "accepted_nf2_merge": ACCEPTED_NF2_MERGE,
                "accepted_nf2_tree": nf2_tree,
                "accepted_nf2_source_tree_receipt_sha256": (
                    ACCEPTED_NF2_SOURCE_TREE_RECEIPT_SHA256
                ),
                "accepted_nf2_linked_source_sha256": source_sha256,
                "separate_nohook_release_nf2_rlib_sha256": rlib_hashes[0],
                "release_profile_probe_sha256": release_probe_hash,
                "linked_nf2_evidence_rlib_sha256": evidence_rlib_hashes[0],
                "evidence_profile_receipt_sha256": EVIDENCE_PROFILE_RECEIPT_SHA256,
                "release_profile_receipt_sha256": RELEASE_PROFILE_RECEIPT_SHA256,
                "evidence_profile": EVIDENCE_PROFILE,
                "release_profile": RELEASE_PROFILE,
                "integrated_helper_sha256": helper_hashes[0],
                "build_receipt_provenance": {
                    "kind": "canonical-native-double-build",
                    "host_class": "non-container Linux x86_64 root/systemd",
                    "reviewed_commit": arguments.reviewed_commit,
                    "canonical_build_view": str(CANONICAL_BUILD_SOURCE),
                    "source_remap": CANONICAL_SOURCE_ROOT,
                    "cross_host_substitution": "forbidden",
                },
                "handoff": {
                    "state_directory": str(HANDOFF_ROOT),
                    "generation": handoff_generation,
                    "mount_identity": handoff_mount,
                    "lifecycle": "ACTIVE_TO_TERMINAL_CLEAN",
                },
                "evidence_build_receipt_sha256": digest(evidence_receipt),
                "release_build_receipt_sha256": digest(release_receipt),
                "foundation_sha256": EXPECTED_FOUNDATION_SHA256,
                "witness_semantics": {
                    "ATTEMPT": "durable boundary immediately before NF2 call; not proof of action",
                    "DEFINITE_SUCCESS": "NF2 returned its fixed Completed outcome",
                },
                "tool_sha256": {
                    **{
                        name: tool.expected
                        for name, tool in harness.VERIFIED_HOST_TOOLS.items()
                        if name in ("rustup", "cargo", "rustc", "rust-lld")
                    },
                    **{
                        name: digest(Path(path))
                        for name in (
                            "git",
                            "systemctl",
                            "systemd-run",
                            "systemd-detect-virt",
                            "bash",
                            "python3",
                        )
                        if (path := shutil.which(name)) is not None
                    },
                },
                "composed_matrices": {
                    "nf2_positive_adversarial": "PASS",
                    "a2_durability_cases": 60,
                    "integrated_success_before_commit": {
                        "SIGKILL": 20,
                        "EIO": 20,
                        "ENOSPC": 20,
                    },
                    "integrated_other": "positive/replay/32-concurrency/stale",
                },
                "case_evidence_directory": str(cases_destination),
                "case_artifact_collection_sha256": case_artifact_collection_sha256,
                "case_evidence_collection_sha256": case_evidence_collection_sha256,
                "non_claims": [
                    "no signer/capability/authorization/release authority",
                    "build receipt is not a trust verifier or signer",
                    "malicious root authoring a false build receipt is excluded",
                    "no malicious-root whole-volume rollback resistance",
                ],
            }
            arguments.evidence_out.parent.mkdir(parents=True, exist_ok=True)
            arguments.evidence_out.write_text(
                json.dumps(evidence, sort_keys=True, indent=2) + "\n"
            )
            os.chmod(arguments.evidence_out, stat.S_IRUSR | stat.S_IWUSR)
    print("PASS: NF3 integrated native root/systemd evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
