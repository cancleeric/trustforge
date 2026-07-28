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
import base64
import csv
import fcntl
import re
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
RUNTIME_LOCK_SCHEMA = "trustforge.router-runtime-lock/v2"
DIST_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PUBLISHED_SCHEMA = "trustforge.router-published-release/v1"


def _normalized_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


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
    root: Path,
    expected: dict[PurePosixPath, tuple[int, str]],
    expected_dirs: dict[PurePosixPath, int],
    expected_uid: int,
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
    if observed_files != set(expected) or observed_dirs != set(expected_dirs):
        raise SystemExit("installed router tree is incomplete or has extra directories")


def _parse_manifest(
    raw: bytes,
) -> tuple[dict[PurePosixPath, tuple[int, str]], dict[PurePosixPath, int]]:
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
    expected_dirs: dict[PurePosixPath, int] = {}
    for entry in manifest["entries"]:
        if not isinstance(entry, dict) or set(entry) not in (
            {"path", "type", "mode", "sha256"},
            {"path", "type", "mode"},
        ):
            raise SystemExit("router tree manifest entry is invalid")
        if entry["type"] not in {"file", "directory"} or not isinstance(
            entry["mode"], str
        ):
            raise SystemExit("router tree manifest entry type or mode is invalid")
        try:
            mode = int(entry["mode"], 8)
        except ValueError as error:
            raise SystemExit("router tree manifest mode is invalid") from error
        path = _safe_name(entry["path"]) if isinstance(entry["path"], str) else None
        if entry["type"] == "directory":
            if (
                set(entry) != {"path", "type", "mode"}
                or path is None
                or mode != 0o555
                or entry["mode"] != "0555"
                or path in expected_dirs
            ):
                raise SystemExit("router tree directory entry is invalid")
            expected_dirs[path] = mode
            continue
        digest = entry.get("sha256")
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
    derived_dirs = {
        parent
        for path in expected
        for parent in path.parents
        if parent != PurePosixPath(".")
    }
    if set(expected_dirs) != derived_dirs or len(expected_dirs) > MAX_DIRECTORIES:
        raise SystemExit("router tree manifest directory set is not exact")
    if any(
        expected.get(path, (None,))[0] != mode for path, mode in REQUIRED_FILES.items()
    ):
        raise SystemExit("router tree omits isolated runtime or entrypoint")
    return expected, expected_dirs


def _read_pinned_json(path: Path, limit: int) -> tuple[dict, str]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        path_info = os.lstat(path)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o022
            or info.st_size > limit
            or (info.st_dev, info.st_ino) != (path_info.st_dev, path_info.st_ino)
        ):
            raise SystemExit("router release JSON input is unsafe")
        raw = os.read(fd, limit + 1)
        if len(raw) != info.st_size:
            raise SystemExit("router release JSON changed during read")
        digest = hashlib.sha256(raw).hexdigest()
    finally:
        os.close(fd)
    value = json.loads(raw)
    if json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n" != raw:
        raise SystemExit("router release JSON is noncanonical")
    return value, digest


def _record_digest(value: str) -> str:
    if not value.startswith("sha256="):
        raise SystemExit("runtime RECORD uses an unsupported digest")
    try:
        return base64.urlsafe_b64decode(value[7:] + "==").hex()
    except ValueError as error:
        raise SystemExit("runtime RECORD digest is invalid") from error


def _verify_runtime_lock(root: Path, lock: dict, tree_digest: str) -> None:
    if (
        not isinstance(lock, dict)
        or set(lock) != {"schema", "tree_manifest_sha256", "distributions"}
        or lock.get("schema") != RUNTIME_LOCK_SCHEMA
        or lock.get("tree_manifest_sha256") != tree_digest
        or not isinstance(lock.get("distributions"), dict)
        or not lock["distributions"]
    ):
        raise SystemExit("router runtime lock is invalid")
    site_roots = list(root.glob(".venv/lib/python*/site-packages"))
    if len(site_roots) != 1:
        raise SystemExit("router runtime must contain exactly one site-packages")
    site = site_roots[0]
    covered: set[Path] = set()
    for name, claim in lock["distributions"].items():
        if (
            not isinstance(name, str)
            or not DIST_NAME.fullmatch(name)
            or not isinstance(claim, dict)
        ):
            raise SystemExit("runtime distribution claim is invalid")
        if set(claim) != {"version", "dist_info", "metadata_sha256", "record_sha256"}:
            raise SystemExit("runtime distribution claim is invalid")
        dist_info = claim["dist_info"]
        if (
            not isinstance(dist_info, str)
            or "/" in dist_info
            or not dist_info.endswith(".dist-info")
        ):
            raise SystemExit("runtime dist-info path is invalid")
        metadata, record = site / dist_info / "METADATA", site / dist_info / "RECORD"
        if (
            hashlib.sha256(metadata.read_bytes()).hexdigest()
            != claim["metadata_sha256"]
            or hashlib.sha256(record.read_bytes()).hexdigest() != claim["record_sha256"]
        ):
            raise SystemExit("runtime distribution metadata mismatch")
        fields = {}
        for line in metadata.read_text(encoding="utf-8").splitlines():
            if ": " in line:
                key, value = line.split(": ", 1)
                fields.setdefault(key, value)
        if (
            _normalized_distribution_name(fields.get("Name", ""))
            != _normalized_distribution_name(name)
            or fields.get("Version") != claim["version"]
        ):
            raise SystemExit("runtime distribution identity mismatch")
        for row in csv.reader(record.read_text(encoding="utf-8").splitlines()):
            if len(row) != 3:
                raise SystemExit("runtime RECORD is invalid")
            relative = PurePosixPath(row[0])
            if relative.is_absolute() or ".." in relative.parts:
                raise SystemExit("runtime RECORD path is unsafe")
            path = site.joinpath(*relative.parts)
            if not path.is_file():
                raise SystemExit("runtime RECORD file is missing")
            if row[1] and hashlib.sha256(
                path.read_bytes()
            ).hexdigest() != _record_digest(row[1]):
                raise SystemExit("runtime RECORD hash mismatch")
            if row[2] and path.stat().st_size != int(row[2]):
                raise SystemExit("runtime RECORD size mismatch")
            covered.add(path.relative_to(site))
    actual = {path.relative_to(site) for path in site.rglob("*") if path.is_file()}
    if actual != covered:
        raise SystemExit(
            "site-packages contains files outside runtime RECORD provenance"
        )


def _marker_path(root: Path, digest: str) -> Path:
    return root / f".published-{digest}.json"


def _verify_published_marker(path: Path, digest: str, expected_uid: int) -> None:
    payload, _ = _read_pinned_json(path, 1024)
    info = os.lstat(path)
    expected = {"schema": PUBLISHED_SCHEMA, "digest": digest, "target": digest}
    if (
        payload != expected
        or info.st_uid != expected_uid
        or stat.S_IMODE(info.st_mode) != 0o444
    ):
        raise SystemExit("router published marker is invalid")


def _publish_marker(root: Path, root_fd: int, digest: str, expected_uid: int) -> None:
    marker = _marker_path(root, digest)
    if os.path.lexists(marker):
        _verify_published_marker(marker, digest, expected_uid)
        return
    payload = {"schema": PUBLISHED_SCHEMA, "digest": digest, "target": digest}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    temporary = root / f".published-{digest}.{os.getpid()}.tmp"
    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o400,
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short router published marker write")
            view = view[written:]
        os.fchmod(fd, 0o444)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(temporary, marker)
        os.fsync(root_fd)
    finally:
        if temporary.exists():
            temporary.unlink()
    _verify_published_marker(marker, digest, expected_uid)


def _remove_marker(path: Path, root_fd: int, expected_uid: int) -> None:
    info = os.lstat(path)
    if info.st_uid != expected_uid or stat.S_ISDIR(info.st_mode):
        raise SystemExit("router published marker has unsafe metadata")
    os.unlink(path)
    os.fsync(root_fd)


def _remove_unpublished_target(target: Path, expected_uid: int) -> None:
    info = os.lstat(target)
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != expected_uid
        or stat.S_IMODE(info.st_mode) not in {0o700, 0o555}
    ):
        raise SystemExit("unpublished router target has unsafe metadata")
    os.chmod(target, 0o700)
    for directory, directories, _ in os.walk(target):
        os.chmod(directory, 0o700)
        for child in directories:
            os.chmod(Path(directory) / child, 0o700)
    shutil.rmtree(target)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--tree-manifest", type=Path, required=True)
    parser.add_argument("--runtime-lock", type=Path, required=True)
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
        if (
            archive_info.st_size > MAX_TOTAL_BYTES
            or manifest_info.st_size > 1024 * 1024
        ):
            raise SystemExit("router release input exceeds size bound")
        manifest, tree_digest = _read_pinned_json(args.tree_manifest, 1024 * 1024)
        expected, expected_dirs = _parse_manifest(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        )
        runtime_lock, _ = _read_pinned_json(args.runtime_lock, 1024 * 1024)
        release_digest = _digest_fd(archive_fd)
        args.releases_root.parent.mkdir(parents=True, exist_ok=True)
        created_releases_root = False
        try:
            os.mkdir(args.releases_root, mode=0o755)
            created_releases_root = True
        except FileExistsError:
            pass
        try:
            parent_fd = os.open(
                args.releases_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
        except OSError as exc:
            raise SystemExit(
                "releases root must be owner-controlled 0755"
            ) from exc
        releases_info = os.fstat(parent_fd)
        releases_path_info = os.lstat(args.releases_root)
        if (
            not stat.S_ISDIR(releases_info.st_mode)
            or not stat.S_ISDIR(releases_path_info.st_mode)
            or stat.S_ISLNK(releases_path_info.st_mode)
            or releases_info.st_uid != expected_uid
            or releases_path_info.st_uid != expected_uid
            or (releases_info.st_dev, releases_info.st_ino)
            != (releases_path_info.st_dev, releases_path_info.st_ino)
        ):
            os.close(parent_fd)
            raise SystemExit("releases root must be owner-controlled 0755")
        if created_releases_root:
            os.fchmod(parent_fd, 0o755)
            os.fsync(parent_fd)
            releases_info = os.fstat(parent_fd)
        if stat.S_IMODE(releases_info.st_mode) != 0o755:
            os.close(parent_fd)
            raise SystemExit("releases root must be owner-controlled 0755")
        fcntl.flock(parent_fd, fcntl.LOCK_EX)
        stage = args.releases_root / f".{release_digest}.{os.getpid()}.staging"
        target = args.releases_root / release_digest
        try:
            marker = _marker_path(args.releases_root, release_digest)
            if os.path.lexists(marker):
                try:
                    _verify_published_marker(marker, release_digest, expected_uid)
                except (OSError, SystemExit):
                    if os.path.lexists(target):
                        raise
                    _remove_marker(marker, parent_fd, expected_uid)
                else:
                    if not os.path.lexists(target):
                        _remove_marker(marker, parent_fd, expected_uid)
                    else:
                        _verify_tree(target, expected, expected_dirs, expected_uid)
                        _verify_runtime_lock(target, runtime_lock, tree_digest)
                        print(target)
                        return 0
            if os.path.lexists(target):
                try:
                    _verify_tree(target, expected, expected_dirs, expected_uid)
                    _verify_runtime_lock(target, runtime_lock, tree_digest)
                except (OSError, SystemExit):
                    _remove_unpublished_target(target, expected_uid)
                else:
                    _publish_marker(
                        args.releases_root, parent_fd, release_digest, expected_uid
                    )
                    print(target)
                    return 0
            stage.mkdir(mode=0o700)
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
                        name = _safe_name(
                            member.name.rstrip("/") if member.isdir() else member.name
                        )
                        if member.isdir():
                            if (
                                name not in expected_dirs
                                or name in observed_dirs
                                or len(observed_dirs) >= MAX_DIRECTORIES
                            ):
                                raise SystemExit(
                                    "archive contains an unexpected directory"
                                )
                            observed_dirs.add(name)
                            continue
                        if (
                            not member.isfile()
                            or member.size < 0
                            or member.size > MAX_FILE_BYTES
                            or name not in expected
                            or name in observed
                        ):
                            raise SystemExit(
                                "archive contains unlisted or unsafe entry"
                            )
                        total_bytes += member.size
                        if total_bytes > MAX_TOTAL_BYTES or len(observed) >= MAX_FILES:
                            raise SystemExit("expanded router archive exceeds bound")
                        observed.add(name)
                        destination = stage.joinpath(*name.parts)
                        destination.parent.mkdir(
                            mode=0o700, parents=True, exist_ok=True
                        )
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
                                if (
                                    extracted > member.size
                                    or extracted > MAX_FILE_BYTES
                                ):
                                    raise SystemExit(
                                        "archive member exceeded declared size"
                                    )
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
            if observed != set(expected) or observed_dirs != set(expected_dirs):
                raise SystemExit("archive does not exactly match tree manifest")
            for directory, directories, _ in os.walk(stage, topdown=False):
                for child in directories:
                    os.chmod(Path(directory) / child, 0o555)
                if Path(directory) != stage:
                    os.chmod(directory, 0o555)
            os.chmod(stage, 0o555)
            _verify_tree(stage, expected, expected_dirs, expected_uid)
            _verify_runtime_lock(stage, runtime_lock, tree_digest)
            os.chmod(stage, 0o700)
            os.replace(stage, target)
            os.fsync(parent_fd)
            os.chmod(target, 0o555)
            _verify_tree(target, expected, expected_dirs, expected_uid)
            _verify_runtime_lock(target, runtime_lock, tree_digest)
            _publish_marker(args.releases_root, parent_fd, release_digest, expected_uid)
        except BaseException:
            if stage.exists():
                os.chmod(stage, 0o700)
                for directory, directories, _ in os.walk(stage):
                    os.chmod(directory, 0o700)
                    for child in directories:
                        os.chmod(Path(directory) / child, 0o700)
                shutil.rmtree(stage)
            raise
        finally:
            os.close(parent_fd)
        print(target)
        return 0
    finally:
        os.close(archive_fd)


if __name__ == "__main__":
    raise SystemExit(main())
