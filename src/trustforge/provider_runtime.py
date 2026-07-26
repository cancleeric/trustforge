"""Provider runtime wiring — connects backend_registry to actual pipeline paths."""
from __future__ import annotations

from trustforge.backend_registry import get_provider
from trustforge.composition_root import AppContext


def resolve_llm_provider() -> str:
    """Resolve the active LLM provider from runtime configuration.

    Checks in order:
    1. backend_registry (explicit set_provider call)
    2. AppContext.from_env() (env variable)
    3. Fallback: "builtin"
    """
    ctx = AppContext.from_env()
    provider = get_provider("llm")
    if provider != "builtin":
        return provider
    return "builtin"


def resolve_cache_backend() -> str:
    """Resolve the active cache backend from runtime configuration."""
    ctx = AppContext.from_env()
    return ctx.cache_backend_type


def is_training_available() -> bool:
    """Check if SageMaker training backend is available."""
    try:
        from trustforge.sagemaker_client import SageMakerClient  # noqa: F401
        return True
    except ImportError:
        return False
