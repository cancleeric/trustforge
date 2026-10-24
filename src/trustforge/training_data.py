"""Canonical, bounded training-data inspection shared by API and release gates."""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_TRAINING_FILES = 512
MAX_TRAINING_DIRECTORY_ENTRIES = 1024
MAX_TRAINING_BYTES = 64 * 1024 * 1024
MAX_TRAINING_LINE_BYTES = 1024 * 1024
MAX_TRAINING_RECORDS = 100_000


class TrainingDataUnavailable(RuntimeError):
    """The configured corpus cannot be inspected safely and completely."""


@dataclass(frozen=True)
class TrainingDataScan:
    total_records: int
    has_direction: int
    per_coin: dict[str, dict[str, int]]


def resolve_training_data_dir(*, default: Path | None = None) -> Path:
    """Return the configured absolute directory or the repository default."""
    configured = os.getenv("TRUSTFORGE_TRAINING_DATA_DIR", "").strip()
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            raise ValueError("TRUSTFORGE_TRAINING_DATA_DIR must be absolute")
        return path
    if default is not None:
        return default
    return Path(__file__).resolve().parents[2] / "data" / "training"


def _scan_identity(value: os.stat_result) -> tuple[int, ...]:
    """Metadata fingerprint for detecting observed changes, not a filesystem snapshot."""
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_size,
        value.st_mtime_ns, value.st_ctime_ns,
    )


def scan_training_data(training_dir: Path) -> TrainingDataScan:
    """Strictly scan a bounded JSONL corpus, or fail without partial results."""
    root = Path(training_dir)
    try:
        if not root.is_absolute():
            raise TrainingDataUnavailable("training data path must be absolute")
        root_lstat = root.lstat()
        if stat.S_ISLNK(root_lstat.st_mode) or not stat.S_ISDIR(root_lstat.st_mode):
            raise TrainingDataUnavailable("training data directory is unsafe")
        canonical_root = root.resolve(strict=True)
        if _scan_identity(canonical_root.lstat()) != _scan_identity(root_lstat):
            raise TrainingDataUnavailable("training data directory changed during scan")
        candidates = []
        directory_entries = 0
        with os.scandir(canonical_root) as iterator:
            for entry in iterator:
                directory_entries += 1
                if directory_entries > MAX_TRAINING_DIRECTORY_ENTRIES:
                    raise TrainingDataUnavailable(
                        "training data directory entry limit exceeded"
                    )
                if entry.name.endswith(".jsonl"):
                    candidates.append(entry)
                    if len(candidates) > MAX_TRAINING_FILES:
                        raise TrainingDataUnavailable(
                            "training data file limit exceeded"
                        )
    except (OSError, RuntimeError) as exc:
        if isinstance(exc, TrainingDataUnavailable):
            raise
        raise TrainingDataUnavailable("training data directory is unavailable") from exc

    declared_bytes = 0
    scanned_bytes = 0
    total_records = 0
    has_direction = 0
    per_coin: dict[str, dict[str, int]] = {}
    scanned_files: list[tuple[Path, os.stat_result]] = []
    for entry in sorted(candidates, key=lambda candidate: candidate.name):
        descriptor = None
        try:
            entry_stat = entry.stat(follow_symlinks=False)
            if entry.is_symlink() or not stat.S_ISREG(entry_stat.st_mode):
                raise TrainingDataUnavailable("training data file is unsafe")
            path = Path(entry.path).resolve(strict=True)
            path.relative_to(canonical_root)
            declared_bytes += entry_stat.st_size
            if declared_bytes > MAX_TRAINING_BYTES:
                raise TrainingDataUnavailable("training data byte limit exceeded")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            descriptor_stat = os.fstat(descriptor)
            reread_entry_stat = path.lstat()
            if (
                not stat.S_ISREG(descriptor_stat.st_mode)
                or _scan_identity(descriptor_stat) != _scan_identity(entry_stat)
                or _scan_identity(reread_entry_stat) != _scan_identity(entry_stat)
            ):
                raise TrainingDataUnavailable("training data file changed during scan")
        except (OSError, RuntimeError, ValueError) as exc:
            if descriptor is not None:
                os.close(descriptor)
            if isinstance(exc, TrainingDataUnavailable):
                raise
            raise TrainingDataUnavailable("training data file is unavailable") from exc

        coin = path.stem.upper()
        coin_total = 0
        coin_direction = 0
        try:
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None  # The stream now owns the descriptor.
                while raw_line := stream.readline(MAX_TRAINING_LINE_BYTES + 1):
                    scanned_bytes += len(raw_line)
                    if scanned_bytes > MAX_TRAINING_BYTES:
                        raise TrainingDataUnavailable("training data byte limit exceeded")
                    if len(raw_line) > MAX_TRAINING_LINE_BYTES:
                        raise TrainingDataUnavailable("training data line limit exceeded")
                    try:
                        line = raw_line.decode("utf-8", errors="strict").strip()
                    except UnicodeDecodeError as exc:
                        raise TrainingDataUnavailable("training data is not UTF-8") from exc
                    if not line:
                        continue
                    try:
                        record: Any = json.loads(line)
                    except (json.JSONDecodeError, ValueError) as exc:
                        raise TrainingDataUnavailable("training data contains invalid JSON") from exc
                    if not isinstance(record, dict):
                        raise TrainingDataUnavailable("training data record must be an object")
                    total_records += 1
                    coin_total += 1
                    if total_records > MAX_TRAINING_RECORDS:
                        raise TrainingDataUnavailable("training data record limit exceeded")
                    direction = record.get("direction")
                    if direction is not None and direction != "" and direction != "不明":
                        has_direction += 1
                        coin_direction += 1
                final_stat = os.fstat(stream.fileno())
                if (
                    _scan_identity(final_stat) != _scan_identity(descriptor_stat)
                    or _scan_identity(path.lstat()) != _scan_identity(descriptor_stat)
                ):
                    raise TrainingDataUnavailable(
                        "training data file changed during scan"
                    )
        except OSError as exc:
            raise TrainingDataUnavailable("training data file is unreadable") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        scanned_files.append((path, descriptor_stat))
        previous = per_coin.setdefault(coin, {"total": 0, "has_direction": 0})
        previous["total"] += coin_total
        previous["has_direction"] += coin_direction

    try:
        for path, scanned_stat in scanned_files:
            if _scan_identity(path.lstat()) != _scan_identity(scanned_stat):
                raise TrainingDataUnavailable("training data file changed during scan")
        if (
            _scan_identity(root.lstat()) != _scan_identity(root_lstat)
            or _scan_identity(canonical_root.lstat()) != _scan_identity(root_lstat)
        ):
            raise TrainingDataUnavailable("training data directory changed during scan")
    except OSError as exc:
        raise TrainingDataUnavailable("training data changed during scan") from exc

    return TrainingDataScan(
        total_records=total_records,
        has_direction=has_direction,
        per_coin=per_coin,
    )
