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


# Coins supported by the scheduled training trigger backends. Canonical home
# is this platform contract so agent-layer callers (training_trigger) can
# import it without crossing into web-owned submitter modules (#1468).
# NOTE: trustforge.schema.COIN_POOL is a different, wider pool — keep separate.
TRAINING_COIN_POOL: tuple[str, ...] = ("BTC", "ETH", "SOL", "BNB", "XRP")
