from __future__ import annotations

import hashlib
import json
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


SENSITIVE_TOKEN_KEYS = frozenset({
    "TRUSTFORGE_ADMIN_TOKEN",
    "TRUSTFORGE_LIVE_TOKEN",
    "TRUSTFORGE_TOKEN_SSM_PREFIX",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
})

_NGINX_CONF_PATHS = (
    "/etc/nginx/conf.d/trustforge.conf",
    "/etc/nginx/sites-enabled/trustforge.conf",
)

_SYSTEMD_ENV_KEYS = {
    "BEDROCK_MODEL_ID",
    "CACHE_BACKEND",
    "TRUSTFORGE_CACHE_TABLE",
    "TRUSTFORGE_COST_LEDGER_TABLE",
    "COST_LEDGER_BACKEND",
    "AWS_REGION",
    "TRUSTFORGE_CSP_MODE",
    "TRUSTFORGE_BUDGET_GUARD_BACKEND",
    "TRUSTFORGE_BUDGET_COUNTER_TABLE",
    "TRUSTFORGE_CW_METRICS",
    "TRUSTFORGE_IDEMPOTENCY_LEASE_BACKEND",
    "TRUSTFORGE_LEASE_TABLE",
    "TRUSTFORGE_WHALE_ALERT_SSM_PARAMETER",
}


@dataclass(frozen=True)
class ConfigSnapshot:
    identity: str
    captured_at: str
    captured_host: str
    payload: str

    @classmethod
    def capture(cls, *, host: str | None = None) -> ConfigSnapshot:
        captured_at = datetime.now(timezone.utc).isoformat()
        captured_host = host or socket.gethostname()
        payload = _capture_config_json()
        identity = "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return cls(
            identity=identity,
            captured_at=captured_at,
            captured_host=captured_host,
            payload=payload,
        )

    def to_bytes(self) -> bytes:
        return json.dumps({
            "identity": self.identity,
            "captured_at": self.captured_at,
            "captured_host": self.captured_host,
            "payload": self.payload,
        }, sort_keys=True, ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_bytes(cls, raw: bytes) -> ConfigSnapshot:
        d = json.loads(raw.decode("utf-8"))
        return cls(**d)


def _capture_config_json() -> str:
    items: dict[str, object] = {}

    for key in sorted(_SYSTEMD_ENV_KEYS):
        value = os.environ.get(key)
        if key in SENSITIVE_TOKEN_KEYS:
            items[key] = {"present": value is not None and len(value) > 0}
        elif value is not None:
            items[key] = value
        else:
            items[key] = None

    items["nginx_conf_symlink_target"] = _resolve_nginx_conf_symlink()

    return json.dumps(items, sort_keys=True, ensure_ascii=False)


def _resolve_nginx_conf_symlink() -> str | None:
    for cand in _NGINX_CONF_PATHS:
        path = Path(cand)
        if path.is_symlink():
            try:
                return str(path.readlink())
            except OSError:
                return f"<broken:{cand}>"
        elif path.is_file():
            return f"<regular-file:{cand}>"
    return None


def current_config_identity() -> str:
    snapshot = ConfigSnapshot.capture()
    return snapshot.identity
