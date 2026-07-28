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
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024


def _safe_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or any(not part for part in path.parts)
    ):
        raise SystemExit(f"unsafe router archive path: {name}")
    return path


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_tree(root: Path, expected: dict[PurePosixPath, str]) -> None:
    observed: set[PurePosixPath] = set()
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in (*directories, *files):
            path = Path(directory) / name
            if path.is_symlink():
                raise SystemExit("installed router tree contains a symlink")
        for name in files:
            path = Path(directory) / name
            relative = PurePosixPath(path.relative_to(root).as_posix())
            if relative not in expected or _digest(path) != expected[relative]:
                raise SystemExit("installed router tree does not match manifest")
            observed.add(relative)
    if observed != set(expected):
        raise SystemExit("installed router tree is incomplete")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--tree-manifest", type=Path, required=True)
    parser.add_argument("--releases-root", type=Path, required=True)
    args = parser.parse_args()
    expected_uid = os.geteuid()
    archive_info = os.lstat(args.archive)
    manifest_info = os.lstat(args.tree_manifest)
    if any(
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != expected_uid
        or stat.S_IMODE(info.st_mode) & 0o022
        for info in (archive_info, manifest_info)
    ):
        raise SystemExit("router release inputs are unsafe")
    if archive_info.st_size > MAX_TOTAL_BYTES or manifest_info.st_size > 1024 * 1024:
        raise SystemExit("router release input exceeds size bound")
    raw = args.tree_manifest.read_bytes()
    manifest = json.loads(raw)
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema", "files"}
        or manifest["schema"] != SCHEMA
        or not isinstance(manifest["files"], dict)
        or not manifest["files"]
        or len(manifest["files"]) > MAX_FILES
        or json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        != raw
    ):
        raise SystemExit("router tree manifest is invalid")
    expected = {_safe_name(name): digest for name, digest in manifest["files"].items()}
    required = {
        PurePosixPath(".venv/bin/python"),
        PurePosixPath("scripts/release_router_service.py"),
    }
    if not required.issubset(expected):
        raise SystemExit("router tree omits isolated runtime or entrypoint")
    if any(
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        for digest in expected.values()
    ):
        raise SystemExit("router tree manifest digest is invalid")
    release_digest = _digest(args.archive)
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
        _verify_tree(target, expected)
        print(target)
        return 0
    stage.mkdir(mode=0o700)
    try:
        observed: set[PurePosixPath] = set()
        total_bytes = 0
        with tarfile.open(args.archive, "r:*") as archive:
            for member in archive.getmembers():
                name = _safe_name(member.name)
                if member.isdir():
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
                destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise SystemExit("archive member cannot be read")
                fd = os.open(
                    destination,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o444,
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
                    os.fchmod(fd, 0o444)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                if digest.hexdigest() != expected[name]:
                    raise SystemExit(f"router tree digest mismatch: {name}")
                if extracted != member.size:
                    raise SystemExit("archive member was truncated")
        if observed != set(expected):
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
        _verify_tree(target, expected)
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


if __name__ == "__main__":
    raise SystemExit(main())
