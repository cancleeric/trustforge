#!/usr/bin/env python3
"""Install a verified router source tree into a content-addressed directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tarfile
from pathlib import Path, PurePosixPath

SCHEMA = "trustforge.router-tree-manifest/v1"
MAX_FILES = 10_000
MAX_MEMBERS = 12_000
MAX_DIRECTORIES = 2_000
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_PATH_BYTES = 1024
MAX_PATH_DEPTH = 32
FILE_MODES = {0o444, 0o555}
REQUIRED_FILES = {
    PurePosixPath(".venv/bin/python"): 0o555,
    PurePosixPath("scripts/release_router_service.py"): 0o444,
}
REQUIRED_DIRECTORIES = {
    PurePosixPath(".venv"),
    PurePosixPath(".venv/bin"),
    PurePosixPath("scripts"),
}


def _safe_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name
        or len(name.encode("utf-8")) > MAX_PATH_BYTES
        or len(path.parts) > MAX_PATH_DEPTH
        or path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or any(not part for part in path.parts)
    ):
        raise SystemExit(f"unsafe router archive path: {name}")
    return path


def _digest_fd(fd: int) -> str:
    digest = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while chunk := os.read(fd, 1024 * 1024):
        digest.update(chunk)
    os.lseek(fd, 0, os.SEEK_SET)
    return digest.hexdigest()


def _digest_regular_fd(parent_fd: int, name: str) -> tuple[str, os.stat_result]:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SystemExit("installed router tree contains an unsafe file")
        return _digest_fd(fd), info
    finally:
        os.close(fd)


def _verify_tree(
    root: Path, expected: dict[PurePosixPath, tuple[int, str]], expected_uid: int
) -> None:
    root_info = os.lstat(root)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    pinned_root_info = os.fstat(root_fd)
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or stat.S_ISLNK(root_info.st_mode)
        or (root_info.st_dev, root_info.st_ino)
        != (pinned_root_info.st_dev, pinned_root_info.st_ino)
        or pinned_root_info.st_uid != expected_uid
        or stat.S_IMODE(pinned_root_info.st_mode) != 0o555
    ):
        os.close(root_fd)
        raise SystemExit("installed router root has unsafe metadata")
    observed_files: set[PurePosixPath] = set()
    observed_dirs: set[PurePosixPath] = set()
    try:
        for directory, directories, files, directory_fd in os.fwalk(
            ".", topdown=True, follow_symlinks=False, dir_fd=root_fd
        ):
            base = PurePosixPath() if directory == "." else PurePosixPath(directory)
            for name in directories:
                relative = base / name
                info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if (
                    not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != expected_uid
                    or stat.S_IMODE(info.st_mode) != 0o555
                ):
                    raise SystemExit("installed router directory has unsafe metadata")
                observed_dirs.add(relative)
            for name in files:
                relative = base / name
                if relative not in expected:
                    raise SystemExit("installed router tree contains an unlisted file")
                digest, info = _digest_regular_fd(directory_fd, name)
                mode, wanted_digest = expected[relative]
                if (
                    info.st_uid != expected_uid
                    or stat.S_IMODE(info.st_mode) != mode
                    or digest != wanted_digest
                ):
                    raise SystemExit("installed router tree does not match manifest")
                observed_files.add(relative)
    finally:
        os.close(root_fd)
    if observed_files != set(expected) or observed_dirs != REQUIRED_DIRECTORIES:
        raise SystemExit("installed router tree is incomplete or has extra directories")


def _parse_manifest(raw: bytes) -> dict[PurePosixPath, tuple[int, str]]:
    manifest = json.loads(raw)
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema", "entries"}
        or manifest["schema"] != SCHEMA
        or not isinstance(manifest["entries"], list)
        or not manifest["entries"]
        or len(manifest["entries"]) > MAX_FILES
        or json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        != raw
    ):
        raise SystemExit("router tree manifest is invalid")
    expected: dict[PurePosixPath, tuple[int, str]] = {}
    for entry in manifest["entries"]:
        if not isinstance(entry, dict) or set(entry) != {
            "path",
            "type",
            "mode",
            "sha256",
        }:
            raise SystemExit("router tree manifest entry is invalid")
        if entry["type"] != "file" or not isinstance(entry["mode"], str):
            raise SystemExit("router tree manifest entry type or mode is invalid")
        try:
            mode = int(entry["mode"], 8)
        except ValueError as error:
            raise SystemExit("router tree manifest mode is invalid") from error
        digest = entry["sha256"]
        path = _safe_name(entry["path"]) if isinstance(entry["path"], str) else None
        if (
            path is None
            or mode not in FILE_MODES
            or entry["mode"] != f"{mode:04o}"
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or path in expected
        ):
            raise SystemExit("router tree manifest entry is invalid")
        expected[path] = (mode, digest)
    if any(expected.get(path, (None,))[0] != mode for path, mode in REQUIRED_FILES.items()):
        raise SystemExit("router tree omits isolated runtime or entrypoint")
    return expected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--tree-manifest", type=Path, required=True)
    parser.add_argument("--releases-root", type=Path, required=True)
    args = parser.parse_args()
    expected_uid = os.geteuid()
    archive_fd = os.open(args.archive, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        archive_info = os.fstat(archive_fd)
        archive_path_info = os.lstat(args.archive)
        manifest_info = os.lstat(args.tree_manifest)
        if (
            not stat.S_ISREG(archive_info.st_mode)
            or archive_info.st_nlink != 1
            or archive_info.st_uid != expected_uid
            or stat.S_IMODE(archive_info.st_mode) & 0o022
            or (archive_info.st_dev, archive_info.st_ino)
            != (archive_path_info.st_dev, archive_path_info.st_ino)
            or not stat.S_ISREG(manifest_info.st_mode)
            or manifest_info.st_nlink != 1
            or manifest_info.st_uid != expected_uid
            or stat.S_IMODE(manifest_info.st_mode) & 0o022
        ):
            raise SystemExit("router release inputs are unsafe")
        if archive_info.st_size > MAX_TOTAL_BYTES or manifest_info.st_size > 1024 * 1024:
            raise SystemExit("router release input exceeds size bound")
        expected = _parse_manifest(args.tree_manifest.read_bytes())
        release_digest = _digest_fd(archive_fd)
        args.releases_root.mkdir(mode=0o755, parents=True, exist_ok=True)
        releases_info = os.lstat(args.releases_root)
        if (
            not stat.S_ISDIR(releases_info.st_mode)
            or stat.S_ISLNK(releases_info.st_mode)
            or releases_info.st_uid != expected_uid
            or stat.S_IMODE(releases_info.st_mode) != 0o755
        ):
            raise SystemExit("releases root must be owner-controlled 0755")
        stage = args.releases_root / f".{release_digest}.{os.getpid()}.staging"
        target = args.releases_root / release_digest
        if target.exists():
            _verify_tree(target, expected, expected_uid)
            print(target)
            return 0
        stage.mkdir(mode=0o700)
        try:
            observed: set[PurePosixPath] = set()
            observed_dirs: set[PurePosixPath] = set()
            total_bytes = 0
            member_count = 0
            os.lseek(archive_fd, 0, os.SEEK_SET)
            with os.fdopen(os.dup(archive_fd), "rb") as archive_stream:
                with tarfile.open(fileobj=archive_stream, mode="r|*") as archive:
                    for member in archive:
                        member_count += 1
                        if member_count > MAX_MEMBERS:
                            raise SystemExit("router archive has too many members")
                        name = _safe_name(member.name.rstrip("/") if member.isdir() else member.name)
                        if member.isdir():
                            if (
                                name not in REQUIRED_DIRECTORIES
                                or name in observed_dirs
                                or len(observed_dirs) >= MAX_DIRECTORIES
                            ):
                                raise SystemExit("archive contains an unexpected directory")
                            observed_dirs.add(name)
                            continue
                        if (
                            not member.isfile()
                            or member.size < 0
                            or member.size > MAX_FILE_BYTES
                            or name not in expected
                            or name in observed
                        ):
                            raise SystemExit("archive contains unlisted or unsafe entry")
                        total_bytes += member.size
                        if total_bytes > MAX_TOTAL_BYTES or len(observed) >= MAX_FILES:
                            raise SystemExit("expanded router archive exceeds bound")
                        observed.add(name)
                        destination = stage.joinpath(*name.parts)
                        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                        source = archive.extractfile(member)
                        if source is None:
                            raise SystemExit("archive member cannot be read")
                        mode, wanted_digest = expected[name]
                        fd = os.open(
                            destination,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o400,
                        )
                        digest = hashlib.sha256()
                        extracted = 0
                        try:
                            while chunk := source.read(1024 * 1024):
                                extracted += len(chunk)
                                if extracted > member.size or extracted > MAX_FILE_BYTES:
                                    raise SystemExit("archive member exceeded declared size")
                                digest.update(chunk)
                                view = memoryview(chunk)
                                while view:
                                    written = os.write(fd, view)
                                    if written <= 0:
                                        raise OSError("short router artifact write")
                                    view = view[written:]
                            os.fchmod(fd, mode)
                            os.fsync(fd)
                        finally:
                            os.close(fd)
                        if digest.hexdigest() != wanted_digest:
                            raise SystemExit(f"router tree digest mismatch: {name}")
                        if extracted != member.size:
                            raise SystemExit("archive member was truncated")
            final_archive_info = os.fstat(archive_fd)
            if (
                (final_archive_info.st_dev, final_archive_info.st_ino)
                != (archive_info.st_dev, archive_info.st_ino)
                or final_archive_info.st_size != archive_info.st_size
                or _digest_fd(archive_fd) != release_digest
            ):
                raise SystemExit("router archive changed during extraction")
            if observed != set(expected) or observed_dirs != REQUIRED_DIRECTORIES:
                raise SystemExit("archive does not exactly match tree manifest")
            for directory, directories, _ in os.walk(stage, topdown=False):
                for child in directories:
                    os.chmod(Path(directory) / child, 0o555)
                if Path(directory) != stage:
                    os.chmod(directory, 0o555)
            os.replace(stage, target)
            os.chmod(target, 0o555)
            parent_fd = os.open(args.releases_root, os.O_RDONLY | os.O_DIRECTORY)
            os.fsync(parent_fd)
            os.close(parent_fd)
            _verify_tree(target, expected, expected_uid)
        except BaseException:
            if stage.exists():
                for directory, directories, _ in os.walk(stage):
                    os.chmod(directory, 0o700)
                    for child in directories:
                        os.chmod(Path(directory) / child, 0o700)
                shutil.rmtree(stage)
            raise
        print(target)
        return 0
    finally:
        os.close(archive_fd)


if __name__ == "__main__":
    raise SystemExit(main())
