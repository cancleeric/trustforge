"""Runtime backend provider registry for Admin AgentCore switches."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


VALID_PROVIDERS = ("builtin", "agentcore")
PROVIDER_KEYS = ("memory", "policy", "eval", "llm", "gateway", "observability", "upgrade")
_DEFAULT_PATH = Path("out/admin_backend_providers.json")


def _store_path() -> Path:
    return Path(os.getenv("TRUSTFORGE_BACKEND_REGISTRY_PATH", str(_DEFAULT_PATH)))


def _read_store() -> dict[str, str]:
    path = _store_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        key: provider
        for key, provider in raw.items()
        if key in PROVIDER_KEYS and provider in VALID_PROVIDERS
    }


def _write_store(values: dict[str, str]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _validate_key(key: str) -> None:
    if key not in PROVIDER_KEYS:
        raise ValueError(f"unsupported backend provider key: {key}")


def _validate_provider(provider: str) -> None:
    if provider not in VALID_PROVIDERS:
        raise ValueError(f"unsupported backend provider: {provider}")


def get_provider(key: str) -> str:
    """Return the active provider for one module, defaulting fail-safe builtin."""
    _validate_key(key)
    return _read_store().get(key, "builtin")


def set_provider(key: str, provider: str) -> dict[str, Any]:
    """Persist one module provider and return the full registry snapshot."""
    _validate_key(key)
    _validate_provider(provider)
    values = get_all_providers()
    values[key] = provider
    _write_store(values)
    return provider_snapshot(values)


def get_all_providers() -> dict[str, str]:
    values = _read_store()
    return {key: values.get(key, "builtin") for key in PROVIDER_KEYS}


def set_all_providers(provider: str) -> dict[str, Any]:
    _validate_provider(provider)
    values = {key: provider for key in PROVIDER_KEYS}
    _write_store(values)
    return provider_snapshot(values)


def provider_snapshot(values: dict[str, str] | None = None) -> dict[str, Any]:
    providers = values or get_all_providers()
    return {
        "kind": "backend_provider_registry",
        "providers": providers,
        "valid_providers": list(VALID_PROVIDERS),
        "provider_keys": list(PROVIDER_KEYS),
        "hot_config": True,
        "restart_required": False,
    }
