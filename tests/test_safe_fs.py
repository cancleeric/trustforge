import os
import stat
from pathlib import Path

import pytest

from trustforge.safe_fs import (
    SafePathError,
    pinned_directory,
    read_regular_file,
    write_atomic,
    write_immutable_cross_directory_at,
)


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
    containing = tmp_path.stat()

    def fail_first_directory(descriptor):
        nonlocal directory_calls
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) == (containing.st_dev, containing.st_ino):
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
    containing = tmp_path.stat()
    directory_calls = 0

    def fail_directory(descriptor):
        nonlocal directory_calls
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) == (containing.st_dev, containing.st_ino):
            directory_calls += 1
            if directory_calls >= 1:
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
    assert directory_fsyncs >= 3


def test_pinned_directory_allows_platform_tmp_symlink_root():
    linked_tmp = Path("/tmp")
    if not linked_tmp.is_symlink():
        pytest.skip("/tmp is not a platform symlink on this host")

    nested = linked_tmp / "trustforge-safe-fs-platform-tmp"
    with pinned_directory(nested, create=True) as descriptor:
        assert stat.S_ISDIR(os.fstat(descriptor).st_mode)
    assert nested.resolve().is_dir()


def test_pinned_directory_rejects_user_controlled_intermediate_symlink(tmp_path):
    trusted = tmp_path / "trusted"
    attacker = tmp_path / "attacker"
    trusted.mkdir()
    attacker.mkdir()
    os.symlink(attacker, trusted / "link", target_is_directory=True)

    with pytest.raises(SafePathError):
        with pinned_directory(trusted / "link" / "created", create=True):
            pass

    assert not (attacker / "created").exists()


def test_nested_directory_creation_fsync_failure_propagates(tmp_path, monkeypatch):
    real_fsync = os.fsync
    one = tmp_path / "one"
    one.mkdir()
    containing = one.stat()
    containing_fsyncs = 0

    def fail_new_nested_entry(descriptor):
        nonlocal containing_fsyncs
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) == (containing.st_dev, containing.st_ino):
            containing_fsyncs += 1
            raise OSError("new directory entry fsync failed")
        return real_fsync(descriptor)

    monkeypatch.setattr("trustforge.safe_fs.os.fsync", fail_new_nested_entry)
    nested = one / "two" / "three"
    with pytest.raises(OSError, match="new directory entry fsync failed"):
        with pinned_directory(nested, create=True):
            pass
    assert containing_fsyncs == 1
    # A just-created entry may remain after an fsync error; it was never
    # reported as a successfully pinned durable path.
    assert (one / "two").is_dir()
    assert not nested.exists()


def test_retry_fsyncs_entry_left_by_transient_failure_before_descending(tmp_path, monkeypatch):
    real_fsync = os.fsync
    containing = tmp_path.stat()
    failed = False
    containing_fsyncs = 0

    def fail_once_on_containing_directory(descriptor):
        nonlocal failed, containing_fsyncs
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) == (containing.st_dev, containing.st_ino):
            containing_fsyncs += 1
            if not failed:
                failed = True
                raise OSError("transient containing-directory fsync failure")
        return real_fsync(descriptor)

    monkeypatch.setattr("trustforge.safe_fs.os.fsync", fail_once_on_containing_directory)
    nested = tmp_path / "left-behind" / "child"
    with pytest.raises(OSError, match="transient containing-directory"):
        with pinned_directory(nested, create=True):
            pass
    assert (tmp_path / "left-behind").is_dir()
    with pinned_directory(nested, create=True) as descriptor:
        assert stat.S_ISDIR(os.fstat(descriptor).st_mode)
    assert containing_fsyncs == 2


def test_cross_directory_immutable_publish_commits_staging_before_destination(tmp_path, monkeypatch):
    staging = tmp_path / "staging"
    destination = tmp_path / "events"
    staging.mkdir()
    destination.mkdir()
    staging_info = staging.stat()
    destination_info = destination.stat()
    real_fsync = os.fsync
    directory_order = []

    def record_fsync(descriptor):
        info = os.fstat(descriptor)
        identity = (info.st_dev, info.st_ino)
        if identity == (staging_info.st_dev, staging_info.st_ino):
            directory_order.append("staging")
        elif identity == (destination_info.st_dev, destination_info.st_ino):
            directory_order.append("destination")
        return real_fsync(descriptor)

    monkeypatch.setattr("trustforge.safe_fs.os.fsync", record_fsync)
    with pinned_directory(staging) as staging_fd, pinned_directory(destination) as destination_fd:
        write_immutable_cross_directory_at(staging_fd, destination_fd, "event.json", b"event")

    assert directory_order[-2:] == ["staging", "destination"]
    assert (destination / "event.json").read_bytes() == b"event"
    assert list(staging.iterdir()) == []


def test_cross_directory_destination_fsync_failure_rolls_back_both_directories(
    tmp_path, monkeypatch
):
    staging = tmp_path / "staging"
    destination = tmp_path / "events"
    staging.mkdir()
    destination.mkdir()
    destination_info = destination.stat()
    real_fsync = os.fsync
    destination_calls = 0

    def fail_first_destination_fsync(descriptor):
        nonlocal destination_calls
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) == (destination_info.st_dev, destination_info.st_ino):
            destination_calls += 1
            if destination_calls == 1:
                raise OSError("destination commit fsync failed")
        return real_fsync(descriptor)

    monkeypatch.setattr("trustforge.safe_fs.os.fsync", fail_first_destination_fsync)
    with pinned_directory(staging) as staging_fd, pinned_directory(destination) as destination_fd:
        with pytest.raises(OSError, match="destination commit fsync failed"):
            write_immutable_cross_directory_at(
                staging_fd, destination_fd, "event.json", b"event"
            )

    assert destination_calls == 2
    assert not (destination / "event.json").exists()
    assert list(staging.iterdir()) == []


def test_cross_directory_persistent_staging_fsync_reports_rollback_failure(
    tmp_path, monkeypatch
):
    staging = tmp_path / "staging"
    destination = tmp_path / "events"
    staging.mkdir()
    destination.mkdir()
    staging_info = staging.stat()
    real_fsync = os.fsync

    def fail_staging_fsync(descriptor):
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) == (staging_info.st_dev, staging_info.st_ino):
            raise OSError("persistent staging fsync failure")
        return real_fsync(descriptor)

    monkeypatch.setattr("trustforge.safe_fs.os.fsync", fail_staging_fsync)
    with pinned_directory(staging) as staging_fd, pinned_directory(destination) as destination_fd:
        with pytest.raises(
            OSError, match="publication failed and rollback was not durable"
        ):
            write_immutable_cross_directory_at(
                staging_fd, destination_fd, "event.json", b"event"
            )

    assert not (destination / "event.json").exists()


def test_cross_directory_persistent_destination_fsync_reports_rollback_failure(
    tmp_path, monkeypatch
):
    staging = tmp_path / "staging"
    destination = tmp_path / "events"
    staging.mkdir()
    destination.mkdir()
    destination_info = destination.stat()
    real_fsync = os.fsync

    def fail_destination_fsync(descriptor):
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) == (destination_info.st_dev, destination_info.st_ino):
            raise OSError("persistent destination fsync failure")
        return real_fsync(descriptor)

    monkeypatch.setattr("trustforge.safe_fs.os.fsync", fail_destination_fsync)
    with pinned_directory(staging) as staging_fd, pinned_directory(destination) as destination_fd:
        with pytest.raises(
            OSError, match="publication failed and rollback was not durable"
        ):
            write_immutable_cross_directory_at(
                staging_fd, destination_fd, "event.json", b"event"
            )

    assert not (destination / "event.json").exists()
