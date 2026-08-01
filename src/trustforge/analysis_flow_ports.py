"""Composition ports used by the agent flow without importing web adapters."""

from __future__ import annotations

from collections.abc import Callable

_bedrock_allowed_provider: Callable[[], bool] = lambda: False


def register_bedrock_allowed(provider: Callable[[], bool]) -> None:
    if not callable(provider):
        raise TypeError("bedrock allowed provider must be callable")
    global _bedrock_allowed_provider
    _bedrock_allowed_provider = provider


def bedrock_allowed() -> bool:
    """Fail closed until a composition root installs the live policy."""
    try:
        return bool(_bedrock_allowed_provider())
    except Exception:
        return False
