"""Small dir-fd based filesystem primitives for security-sensitive artifacts."""
from __future__ import annotations

import contextlib
import errno
import os
import secrets
import stat
from pathlib import Path
from typing import Iterator


class SafePathError(OSError):
    pass


def _directory_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


@contextlib.contextmanager
def pinned_directory(path: Path, *, create: bool = False) -> Iterator[int]:
    """Pin every directory component and yield the final directory descriptor."""
    absolute = Path(os.path.abspath(path))
    descriptor = os.open(absolute.anchor, _directory_flags())
    try:
        for component in absolute.parts[1:]:
            if component in {"", ".", ".."}:
                raise SafePathError("unsafe path component")
            if create:
                try:
                    os.mkdir(component, dir_fd=descriptor)
                except FileExistsError:
                    pass
                # This must also run after FileExistsError: an earlier failed
                # attempt may have created the entry without durably syncing it.
                os.fsync(descriptor)
            try:
                child = os.open(component, _directory_flags(), dir_fd=descriptor)
            except OSError as exc:
                raise SafePathError("path contains a symlink or non-directory component") from exc
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def read_regular_file(path: Path, *, maximum_bytes: int | None = None) -> tuple[bytes, os.stat_result]:
    """Read a regular file relative to a pinned parent directory."""
    with pinned_directory(path.parent) as parent_fd:
        return read_regular_file_at(parent_fd, path.name, maximum_bytes=maximum_bytes)


def read_regular_file_at(
    parent_fd: int, name: str, *, maximum_bytes: int | None = None
) -> tuple[bytes, os.stat_result]:
    """Read a regular file relative to an already pinned directory."""
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=parent_fd)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise SafePathError("file is not regular")
        if maximum_bytes is not None and info.st_size > maximum_bytes:
            raise SafePathError("file exceeds size limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if maximum_bytes is not None and total > maximum_bytes:
                raise SafePathError("file exceeds size limit")
            chunks.append(chunk)
        return b"".join(chunks), info
    finally:
        os.close(descriptor)


def _temporary_name(name: str) -> str:
    return f".{name}.{secrets.token_hex(12)}.tmp"


def write_atomic(path: Path, encoded: bytes, *, immutable: bool) -> None:
    """Write through one pinned parent fd, fsyncing file and directory."""
    if not path.name or path.name in {".", ".."}:
        raise SafePathError("unsafe filename")
    with pinned_directory(path.parent, create=True) as parent_fd:
        write_atomic_at(parent_fd, path.name, encoded, immutable=immutable)


def write_atomic_at(parent_fd: int, name: str, encoded: bytes, *, immutable: bool) -> None:
    """Write relative to one caller-owned pinned directory descriptor."""
    if not name or name in {".", ".."} or Path(name).name != name:
        raise SafePathError("unsafe filename")
    temporary = _temporary_name(name)
    backup: str | None = None
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600, dir_fd=parent_fd,
    )
    try:
        try:
            view = memoryview(encoded)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError(errno.EIO, "short write")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except BaseException:
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except OSError:
            pass
        raise
    try:
        if immutable:
            os.link(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.unlink(temporary, dir_fd=parent_fd)
        else:
            try:
                existing = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                existing = None
            if existing is not None:
                if not stat.S_ISREG(existing.st_mode):
                    raise SafePathError("existing destination is not a regular file")
                backup = _temporary_name(f"{name}.backup")
                os.link(name, backup, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        try:
            os.fsync(parent_fd)
        except BaseException as publication_error:
            if immutable:
                os.unlink(name, dir_fd=parent_fd)
            elif backup is not None:
                os.replace(backup, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                backup = None
            else:
                os.unlink(name, dir_fd=parent_fd)
            try:
                os.fsync(parent_fd)
            except BaseException as rollback_error:
                raise OSError("publication and rollback directory fsync failed") from rollback_error
            raise publication_error
        if backup is not None:
            os.unlink(backup, dir_fd=parent_fd)
            backup = None
    except BaseException:
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except OSError:
            pass
        if backup is not None:
            try:
                os.unlink(backup, dir_fd=parent_fd)
            except OSError:
                pass
        raise
