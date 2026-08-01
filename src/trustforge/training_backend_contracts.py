"""Platform contracts shared by training callers and connector adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TrainingBackend(Protocol):
    backend_id: str

    def trigger_training(
        self, coin: str, rows: list[dict[str, Any]], *, config: dict[str, Any] | None = None
    ) -> str: ...

    def poll_result(
        self, job_id: str, *, max_wait: float = 300.0, interval: float = 5.0
    ) -> dict[str, Any]: ...

    def download_artifact(self, job_id: str, local_path: Path) -> Path: ...


class TrainingBackendConfigError(RuntimeError):
    """Training backend configuration is unsupported or incomplete."""
