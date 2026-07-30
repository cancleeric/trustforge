"""Write-only Whale Alert credential control backed by AWS SSM SecureString."""

from __future__ import annotations

import json
import os
import re
import stat
import threading
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlencode

from .ingestion.safe_fetch import fetch_url

_PARAMETER_ENV = "TRUSTFORGE_WHALE_ALERT_SSM_PARAMETER"
_LOCAL_KEY_ENV = "WHALE_ALERT_API_KEY"
_LOCAL_KEY_FILE_ENV = "TRUSTFORGE_WHALE_ALERT_API_KEY_FILE"
_DEFAULT_PARAMETER = "/trustforge/production/whale-alert-api-key"
_PARAMETER_RE = re.compile(r"^/[A-Za-z0-9_.\-/]{1,255}$")
_CACHE_TTL_SECONDS = 300.0
_USER_AGENT = "TrustForge/1.0 (WhaleAlert credential verification)"
_VERIFY_URL = "https://api.whale-alert.io/v1/transactions"

_lock = threading.Lock()
_cached_value: str | None = None
_cached_at = 0.0
_last_verified_at: str | None = None


@dataclass(frozen=True)
class SecretStatus:
    configured: bool
    source: str
    last_verified_at: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "configured": self.configured,
            "source": self.source,
            "last_verified_at": self.last_verified_at,
        }


def _parameter_name() -> str:
    name = os.getenv(_PARAMETER_ENV, _DEFAULT_PARAMETER).strip()
    if not _PARAMETER_RE.fullmatch(name) or ".." in name.split("/"):
        raise ValueError("invalid Whale Alert SSM parameter name")
    return name


def _ssm_client():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "ssm",
        config=Config(
            connect_timeout=2,
            read_timeout=3,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


def _validate_secret(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("API key must be a string")
    clean = value.strip()
    if len(clean) < 16 or len(clean) > 512 or any(ch.isspace() for ch in clean):
        raise ValueError("API key format is invalid")
    return clean


def invalidate_cache() -> None:
    global _cached_value, _cached_at
    with _lock:
        _cached_value = None
        _cached_at = 0.0


def resolve_api_key(*, force_refresh: bool = False) -> tuple[str | None, str]:
    """Resolve the secret without ever logging or returning it through Admin APIs."""
    global _cached_value, _cached_at
    parameter = os.getenv(_PARAMETER_ENV, "").strip()
    if not parameter:
        key_file = os.getenv(_LOCAL_KEY_FILE_ENV, "").strip()
        if key_file:
            try:
                path_stat = os.lstat(key_file)
                if (
                    not stat.S_ISREG(path_stat.st_mode)
                    or stat.S_IMODE(path_stat.st_mode) != 0o600
                    or path_stat.st_uid != os.getuid()
                    or path_stat.st_size > 512
                ):
                    return None, "unavailable"
                with open(key_file, encoding="utf-8") as handle:
                    local_file_value = handle.read(513).strip()
                return _validate_secret(local_file_value), "file"
            except (OSError, ValueError):
                return None, "unavailable"
        local = os.getenv(_LOCAL_KEY_ENV, "").strip()
        return (local or None), ("environment" if local else "unconfigured")

    now = time.monotonic()
    with _lock:
        if not force_refresh and _cached_value and now - _cached_at < _CACHE_TTL_SECONDS:
            return _cached_value, "ssm"

    try:
        response = _ssm_client().get_parameter(
            Name=_parameter_name(),
            WithDecryption=True,
        )
    except Exception:
        invalidate_cache()
        return None, "unavailable"
    value = response.get("Parameter", {}).get("Value")
    if not isinstance(value, str) or not value.strip():
        invalidate_cache()
        return None, "unconfigured"
    clean = value.strip()
    with _lock:
        _cached_value = clean
        _cached_at = now
    return clean, "ssm"


def status() -> SecretStatus:
    value, source = resolve_api_key()
    verified_at = _last_verified_at
    if value and source == "ssm":
        try:
            tags = _ssm_client().list_tags_for_resource(
                ResourceType="Parameter",
                ResourceId=_parameter_name(),
            ).get("TagList", [])
            verified_at = next(
                (
                    tag.get("Value")
                    for tag in tags
                    if tag.get("Key") == "TrustForgeLastVerifiedAt"
                ),
                verified_at,
            )
        except Exception:
            pass
    return SecretStatus(bool(value), source, verified_at)


def put_api_key(value: str) -> SecretStatus:
    global _last_verified_at
    clean = _validate_secret(value)
    _ssm_client().put_parameter(
        Name=_parameter_name(),
        Value=clean,
        Type="SecureString",
        Overwrite=True,
        Tier="Standard",
    )
    invalidate_cache()
    resolved, source = resolve_api_key(force_refresh=True)
    if resolved != clean:
        invalidate_cache()
        raise RuntimeError("Whale Alert API key activation verification failed")
    _last_verified_at = None
    return SecretStatus(True, source, _last_verified_at)


def clear_api_key() -> SecretStatus:
    global _last_verified_at
    client = _ssm_client()
    try:
        client.delete_parameter(Name=_parameter_name())
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if code != "ParameterNotFound":
            raise
    invalidate_cache()
    _last_verified_at = None
    return SecretStatus(False, "unconfigured", _last_verified_at)


def verify_connection(
    *,
    fetcher: Callable[..., bytes] = fetch_url,
) -> SecretStatus:
    global _last_verified_at
    key, source = resolve_api_key(force_refresh=True)
    if not key:
        raise RuntimeError("Whale Alert API key is not configured")
    query = urlencode(
        {
            "api_key": key,
            "min_value": 500000,
            "start": int(time.time()) - 300,
        }
    )
    payload = fetcher(
        f"{_VERIFY_URL}?{query}",
        user_agent=_USER_AGENT,
        timeout=5,
        max_bytes=64 * 1024,
        max_redirects=0,
    )
    document = json.loads(payload)
    if not isinstance(document, dict) or document.get("result") != "success":
        raise RuntimeError("Whale Alert rejected the configured credential")
    _last_verified_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if source == "ssm":
        _ssm_client().add_tags_to_resource(
            ResourceType="Parameter",
            ResourceId=_parameter_name(),
            Tags=[{"Key": "TrustForgeLastVerifiedAt", "Value": _last_verified_at}],
        )
    return SecretStatus(True, source, _last_verified_at)
