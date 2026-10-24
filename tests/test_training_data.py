from __future__ import annotations

import os
from pathlib import Path

import pytest

from trustforge import training_data
from trustforge.training_data import TrainingDataUnavailable, scan_training_data


def _directory(tmp_path: Path) -> Path:
    directory = tmp_path / "training"
    directory.mkdir()
    return directory


@pytest.mark.parametrize(
    "payload",
    [
        b'{"direction":\n',
        b'["not", "an", "object"]\n',
        b'{"direction":"bullish"}\xff\n',
    ],
)
def test_scan_rejects_malformed_non_object_and_invalid_utf8(
    tmp_path: Path, payload: bytes
) -> None:
    directory = _directory(tmp_path)
    (directory / "btc.jsonl").write_bytes(payload)

    with pytest.raises(TrainingDataUnavailable):
        scan_training_data(directory)


def test_scan_rejects_symlink_and_named_non_regular_file(tmp_path: Path) -> None:
    directory = _directory(tmp_path)
    target = tmp_path / "outside.jsonl"
    target.write_text('{"direction":"bullish"}\n', encoding="utf-8")
    (directory / "btc.jsonl").symlink_to(target)

    with pytest.raises(TrainingDataUnavailable):
        scan_training_data(directory)

    (directory / "btc.jsonl").unlink()
    (directory / "btc.jsonl").mkdir()
    with pytest.raises(TrainingDataUnavailable):
        scan_training_data(directory)


def test_scan_rejects_symlink_root(tmp_path: Path) -> None:
    real = _directory(tmp_path)
    alias = tmp_path / "training-alias"
    alias.symlink_to(real, target_is_directory=True)

    with pytest.raises(TrainingDataUnavailable):
        scan_training_data(alias)


@pytest.mark.parametrize(
    ("constant", "value", "contents"),
    [
        ("MAX_TRAINING_FILES", 0, b""),
        ("MAX_TRAINING_BYTES", 2, b"{}\n"),
        ("MAX_TRAINING_LINE_BYTES", 2, b"{}\n"),
        ("MAX_TRAINING_RECORDS", 0, b"{}\n"),
    ],
)
def test_scan_fails_closed_when_a_bound_is_exceeded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    constant: str,
    value: int,
    contents: bytes,
) -> None:
    directory = _directory(tmp_path)
    (directory / "btc.jsonl").write_bytes(contents)
    monkeypatch.setattr(training_data, constant, value)

    with pytest.raises(TrainingDataUnavailable):
        scan_training_data(directory)


def test_scan_bounds_all_directory_entries_not_only_jsonl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    directory = _directory(tmp_path)
    (directory / "ignored.txt").write_text("ignored", encoding="utf-8")
    monkeypatch.setattr(training_data, "MAX_TRAINING_DIRECTORY_ENTRIES", 0)

    with pytest.raises(TrainingDataUnavailable, match="directory entry limit"):
        scan_training_data(directory)


def test_scan_detects_file_swap_between_enumeration_and_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    directory = _directory(tmp_path)
    path = directory / "btc.jsonl"
    path.write_text('{"direction":"bullish"}\n', encoding="utf-8")
    real_open = os.open

    def swapped_open(target: str | bytes | os.PathLike[str] | os.PathLike[bytes], flags: int) -> int:
        path.unlink()
        path.write_text('{"direction":"bearish","swapped":true}\n', encoding="utf-8")
        return real_open(target, flags)

    monkeypatch.setattr(os, "open", swapped_open)
    with pytest.raises(TrainingDataUnavailable, match="changed during scan"):
        scan_training_data(directory)


def test_scan_detects_same_inode_same_size_write_with_restored_mtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    directory = _directory(tmp_path)
    path = directory / "btc.jsonl"
    path.write_bytes(b'{"direction":"bullish"}\n')
    initial = path.stat()
    real_loads = training_data.json.loads

    def mutate_after_read(line: str) -> object:
        with path.open("r+b") as writer:
            writer.write(b'{"direction":"bearish"}\n')
        os.utime(path, ns=(initial.st_atime_ns, initial.st_mtime_ns))
        assert path.stat().st_ino == initial.st_ino
        assert path.stat().st_size == initial.st_size
        assert path.stat().st_mtime_ns == initial.st_mtime_ns
        return real_loads(line)

    monkeypatch.setattr(training_data.json, "loads", mutate_after_read)
    with pytest.raises(TrainingDataUnavailable, match="changed during scan"):
        scan_training_data(directory)


def test_scan_detects_directory_membership_change_while_reading_last_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    directory = _directory(tmp_path)
    (directory / "btc.jsonl").write_bytes(b"{}\n")
    real_loads = training_data.json.loads

    def add_file_after_read(line: str) -> object:
        (directory / "eth.jsonl").write_bytes(b"{}\n")
        return real_loads(line)

    monkeypatch.setattr(training_data.json, "loads", add_file_after_read)
    with pytest.raises(TrainingDataUnavailable, match="directory changed during scan"):
        scan_training_data(directory)


def test_scan_detects_new_file_after_empty_enumeration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    directory = _directory(tmp_path)
    real_scandir = os.scandir

    class ChangingEnumeration:
        def __enter__(self):
            self.iterator = real_scandir(directory)
            return self.iterator

        def __exit__(self, *args):
            self.iterator.close()
            (directory / "btc.jsonl").write_bytes(b"{}\n")

    monkeypatch.setattr(os, "scandir", lambda _: ChangingEnumeration())
    with pytest.raises(TrainingDataUnavailable, match="directory changed during scan"):
        scan_training_data(directory)


def test_scan_detects_directory_replacement_after_last_file_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    directory = _directory(tmp_path)
    (directory / "btc.jsonl").write_bytes(b"{}\n")
    real_loads = training_data.json.loads

    def replace_directory(line: str) -> object:
        directory.rename(tmp_path / "old-training")
        directory.mkdir()
        (directory / "btc.jsonl").write_bytes(b"{}\n")
        return real_loads(line)

    monkeypatch.setattr(training_data.json, "loads", replace_directory)
    with pytest.raises(TrainingDataUnavailable, match="changed during scan"):
        scan_training_data(directory)


def test_scan_rechecks_earlier_file_after_later_file_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    directory = _directory(tmp_path)
    earlier = directory / "btc.jsonl"
    earlier.write_bytes(b"{}\n")
    (directory / "eth.jsonl").write_bytes(b'{"last":true}\n')
    real_loads = training_data.json.loads

    def mutate_earlier(line: str) -> object:
        if "last" in line:
            earlier.write_bytes(b'{"changed":true}\n')
        return real_loads(line)

    monkeypatch.setattr(training_data.json, "loads", mutate_earlier)
    with pytest.raises(TrainingDataUnavailable, match="file changed during scan"):
        scan_training_data(directory)


@pytest.mark.parametrize("failure_point", ["fstat", "fdopen"])
def test_scan_closes_descriptor_when_setup_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure_point: str
) -> None:
    directory = _directory(tmp_path)
    (directory / "btc.jsonl").write_bytes(b"{}\n")
    real_open = os.open
    real_fstat = os.fstat
    opened = []

    def track_open(*args, **kwargs):
        descriptor = real_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def fail(*args, **kwargs):
        raise OSError("injected setup failure")

    monkeypatch.setattr(os, "open", track_open)
    monkeypatch.setattr(os, failure_point, fail)
    with pytest.raises(TrainingDataUnavailable):
        scan_training_data(directory)
    assert len(opened) == 1
    with pytest.raises(OSError):
        real_fstat(opened[0])


def test_scan_preserves_empty_and_normal_multifile_results(tmp_path: Path) -> None:
    directory = _directory(tmp_path)
    assert scan_training_data(directory) == training_data.TrainingDataScan(0, 0, {})
    (directory / "btc.jsonl").write_bytes(b'{"direction":"bullish"}\n{}\n')
    (directory / "eth.jsonl").write_bytes(b'\n{"direction":"not-known"}\n')
    assert scan_training_data(directory) == training_data.TrainingDataScan(
        3, 2,
        {"BTC": {"total": 2, "has_direction": 1},
         "ETH": {"total": 1, "has_direction": 1}},
    )
