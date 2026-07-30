"""Canonical training-data location shared by API and producers."""
from __future__ import annotations

import os
from pathlib import Path


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
