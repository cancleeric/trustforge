"""Runtime start/stop switch for bounded TrustForge background work.

The web process may stay up in production, but continuous Hermes work must be
fail-closed there. Local development can default on and be stopped with a small
state file so a developer can pause recurring work without changing env vars.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from datetime import datetime, timezone

_TRUTHY = frozenset({"1", "true", "yes", "on", "start", "enabled"})
_FALSY = frozenset({"0", "false", "no", "off", "stop", "disabled"})


@dataclass(frozen=True)
class RuntimeControl:
    enabled: bool
    source: str
    production: bool
    production_continuous_allowed: bool
    state_path: str
    reason: str = ""


def _parse_bool(raw: str | None) -> bool | None:
    if raw is None or raw.strip() == "":
        return None
    value = raw.strip().lower()
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    return None


def _root() -> Path:
    return Path(os.getenv("TRUSTFORGE_HOME", str(Path(__file__).resolve().parents[2])))


def runtime_state_path() -> Path:
    configured = os.getenv("TRUSTFORGE_RUNTIME_STATE_PATH", "").strip()
    if configured:
        return Path(configured)
    return _root() / "out" / "trustforge-runtime-control.json"


def is_production_environment() -> bool:
    env = os.getenv("TRUSTFORGE_ENV", "").strip().lower()
    if env in {"prod", "production"}:
        return True
    if os.getenv("CACHE_BACKEND", "").strip().lower() == "dynamodb":
        return True
    return False


def production_continuous_allowed() -> bool:
    return _parse_bool(os.getenv("TRUSTFORGE_ALLOW_PRODUCTION_CONTINUOUS")) is True


def _read_state(path: Path) -> tuple[bool | None, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "state_missing"
    except Exception as exc:
        return None, f"state_read_error:{type(exc).__name__}"
    value = raw.get("enabled")
    if isinstance(value, bool):
        return value, str(raw.get("reason") or "")
    return None, "state_invalid"


def set_runtime_enabled(enabled: bool, *, reason: str = "cli", actor: str = "local") -> RuntimeControl:
    path = runtime_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "enabled": bool(enabled),
        "reason": reason,
        "actor": actor,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return runtime_control()


def runtime_control() -> RuntimeControl:
    path = runtime_state_path()
    production = is_production_environment()
    allow_production = production_continuous_allowed()

    env_switch = _parse_bool(os.getenv("TRUSTFORGE_RUNTIME_SWITCH"))
    if env_switch is not None:
        if production and env_switch and not allow_production:
            return RuntimeControl(False, "production_guard", production, allow_production, str(path), "set TRUSTFORGE_ALLOW_PRODUCTION_CONTINUOUS=1 to permit continuous production work")
        return RuntimeControl(env_switch, "env", production, allow_production, str(path))

    if production:
        return RuntimeControl(False, "production_default", production, allow_production, str(path), "production continuous work is off unless explicitly allowed")

    state_enabled, reason = _read_state(path)
    if state_enabled is not None:
        return RuntimeControl(state_enabled, "state_file", production, allow_production, str(path), reason)
    return RuntimeControl(True, "local_default", production, allow_production, str(path))
