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
        path.write_text('{"direction":"bearish"}\n', encoding="utf-8")
        return real_open(target, flags)

    monkeypatch.setattr(os, "open", swapped_open)
    with pytest.raises(TrainingDataUnavailable, match="changed during scan"):
        scan_training_data(directory)
