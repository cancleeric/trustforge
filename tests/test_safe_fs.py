import os
import stat

import pytest

from trustforge.safe_fs import read_regular_file, write_atomic
from trustforge.safe_fs import pinned_directory


def _require_dirfd_support():
    if os.open not in os.supports_dir_fd or os.rename not in os.supports_dir_fd:
        pytest.skip("platform does not support required dir_fd operations")


def test_write_uses_pinned_parent_when_pathname_is_swapped(tmp_path, monkeypatch):
    _require_dirfd_support()
    output = tmp_path / "output"
    moved = tmp_path / "pinned-output"
    attacker = tmp_path / "attacker"
    output.mkdir()
    attacker.mkdir()
    real_open = os.open
    swapped = False

    def swap_before_temp_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if not swapped and dir_fd is not None and isinstance(path, str) and path.endswith(".tmp"):
            os.rename(output, moved)
            os.symlink(attacker, output, target_is_directory=True)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr("trustforge.safe_fs.os.open", swap_before_temp_open)
    write_atomic(output / "current.json", b'{"safe":true}', immutable=False)
    assert swapped
    assert (moved / "current.json").read_bytes() == b'{"safe":true}'
    assert list(attacker.iterdir()) == []


def test_read_uses_pinned_parent_when_pathname_is_swapped(tmp_path, monkeypatch):
    _require_dirfd_support()
    output = tmp_path / "output"
    moved = tmp_path / "pinned-output"
    attacker = tmp_path / "attacker"
    output.mkdir()
    attacker.mkdir()
    (output / "payload").write_bytes(b"trusted")
    (attacker / "payload").write_bytes(b"attacker")
    real_open = os.open
    swapped = False

    def swap_before_final_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if not swapped and dir_fd is not None and path == "payload":
            os.rename(output, moved)
            os.symlink(attacker, output, target_is_directory=True)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr("trustforge.safe_fs.os.open", swap_before_final_open)
    encoded, _ = read_regular_file(output / "payload")
    assert swapped and encoded == b"trusted"


def test_immutable_directory_fsync_failure_removes_uncommitted_target(tmp_path, monkeypatch):
    real_fsync = os.fsync

    def fail_directory(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("directory fsync failed")
        return real_fsync(descriptor)

    monkeypatch.setattr("trustforge.safe_fs.os.fsync", fail_directory)
    target = tmp_path / "immutable.json"
    with pytest.raises(OSError, match="directory fsync"):
        write_atomic(target, b"content", immutable=True)
    assert not target.exists()


def test_current_directory_fsync_failure_restores_previous_target(tmp_path, monkeypatch):
    target = tmp_path / "current.json"
    target.write_bytes(b"old")
    real_fsync = os.fsync

    def fail_directory(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("directory fsync failed")
        return real_fsync(descriptor)

    monkeypatch.setattr("trustforge.safe_fs.os.fsync", fail_directory)
    with pytest.raises(OSError, match="directory fsync"):
        write_atomic(target, b"new", immutable=False)
    assert target.read_bytes() == b"old"


def test_rollback_is_directory_fsynced_after_first_fsync_failure(tmp_path, monkeypatch):
    target = tmp_path / "current.json"
    target.write_bytes(b"old")
    real_fsync = os.fsync
    directory_calls = 0

    def fail_first_directory(descriptor):
        nonlocal directory_calls
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_calls += 1
            if directory_calls == 1:
                raise OSError("publication fsync failed")
        return real_fsync(descriptor)

    monkeypatch.setattr("trustforge.safe_fs.os.fsync", fail_first_directory)
    with pytest.raises(OSError, match="publication fsync"):
        write_atomic(target, b"new", immutable=False)
    assert directory_calls == 2
    assert target.read_bytes() == b"old"


def test_persistent_directory_fsync_failure_is_explicit(tmp_path, monkeypatch):
    target = tmp_path / "current.json"
    target.write_bytes(b"old")

    real_fsync = os.fsync

    def fail_directory(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("persistent fsync failure")
        return real_fsync(descriptor)

    monkeypatch.setattr("trustforge.safe_fs.os.fsync", fail_directory)
    with pytest.raises(OSError, match="publication and rollback directory fsync failed"):
        write_atomic(target, b"new", immutable=False)
    assert target.read_bytes() == b"old"


def test_nested_directory_creation_fsyncs_each_new_parent_entry(tmp_path, monkeypatch):
    real_fsync = os.fsync
    directory_fsyncs = 0

    def count_directory_fsync(descriptor):
        nonlocal directory_fsyncs
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsyncs += 1
        return real_fsync(descriptor)

    monkeypatch.setattr("trustforge.safe_fs.os.fsync", count_directory_fsync)
    nested = tmp_path / "one" / "two" / "three"
    with pinned_directory(nested, create=True) as descriptor:
        assert stat.S_ISDIR(os.fstat(descriptor).st_mode)
    assert nested.is_dir()
    assert directory_fsyncs == 3


def test_nested_directory_creation_fsync_failure_propagates(tmp_path, monkeypatch):
    real_fsync = os.fsync
    directory_fsyncs = 0

    def fail_second_new_entry(descriptor):
        nonlocal directory_fsyncs
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsyncs += 1
            if directory_fsyncs == 2:
                raise OSError("new directory entry fsync failed")
        return real_fsync(descriptor)

    monkeypatch.setattr("trustforge.safe_fs.os.fsync", fail_second_new_entry)
    nested = tmp_path / "one" / "two" / "three"
    with pytest.raises(OSError, match="new directory entry fsync failed"):
        with pinned_directory(nested, create=True):
            pass
    assert directory_fsyncs == 2
    # A just-created entry may remain after an fsync error; it was never
    # reported as a successfully pinned durable path.
    assert not nested.exists()
