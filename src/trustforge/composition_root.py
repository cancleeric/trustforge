"""TrustForge composition root — centralized DI for runtime modes."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

RuntimeMode = Literal["offline", "live", "staging"]


@dataclass(frozen=True)
class AppContext:
    """Centralized dependency injection context.

    Usage::

        ctx = AppContext.from_env()
        cache = ctx.cache_backend
        if ctx.is_live:
            client = ctx.bedrock_client
    """
    mode: RuntimeMode = "offline"
    cache_backend_type: str = "dynamodb"

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def is_staging(self) -> bool:
        return self.mode == "staging"

    @classmethod
    def from_env(cls) -> "AppContext":
        mode_str = os.getenv("TRUSTFORGE_RUNTIME_MODE", "offline").strip().lower()
        if mode_str not in ("offline", "live", "staging"):
            mode_str = "offline"
        cache = os.getenv("CACHE_BACKEND", "dynamodb").strip().lower()
        return cls(mode=mode_str, cache_backend_type=cache)  # type: ignore[arg-type]
