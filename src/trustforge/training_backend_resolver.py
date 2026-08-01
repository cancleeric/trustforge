"""Connector composition root for training backend implementations."""

from __future__ import annotations

import os

from .training_backend_contracts import TrainingBackend, TrainingBackendConfigError


def resolve_training_backend(*, offline: bool = False) -> TrainingBackend:
    backend = os.getenv("TRAINING_BACKEND", "modelhub").lower().strip()
    if backend == "sagemaker":
        from .sagemaker_client import SageMakerBackend
        return SageMakerBackend(offline=offline)
    if backend == "modelhub":
        from .modelhub_backend import ModelHubBackend
        return ModelHubBackend(offline=offline)
    raise TrainingBackendConfigError(
        f"Unsupported TRAINING_BACKEND={backend!r}. "
        "Allowed values: 'modelhub', 'sagemaker'"
    )
