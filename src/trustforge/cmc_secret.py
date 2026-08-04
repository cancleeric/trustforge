"""Write-only CoinMarketCap credential control backed by AWS SSM SecureString."""

from __future__ import annotations

import json
import os
import re
import stat
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .ingestion.safe_fetch import fetch_url

_PARAMETER_ENV = "TRUSTFORGE_CMC_SSM_PARAMETER"
_LOCAL_KEY_ENV = "CMC_PRO_API_KEY"
_LOCAL_KEY_FILE_ENV = "TRUSTFORGE_CMC_API_KEY_FILE"
_DEFAULT_PARAMETER = "/trustforge/production/cmc-api-key"
_PARAMETER_RE = re.compile(r"^/[A-Za-z0-9_.\-/]{1,255}$")
_CACHE_TTL_SECONDS = 15.0
_USER_AGENT = "TrustForge/1.0 (CMC credential verification)"
_VERIFY_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest?symbol=BTC"

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
        raise ValueError("invalid CoinMarketCap SSM parameter name")
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


def _fallback_local_or_env() -> tuple[str | None, str]:
    """本機檔案 / 環境變數 fallback（SSM 未啟用或參數尚未建立時使用）。

    抽自 `resolve_api_key`，讓「SSM ParameterNotFound = 遷移中」的回落與
    「SSM 未啟用」的回落共用同一份邏輯（保既有安裝升級後不靜默斷料）。
    """
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


def resolve_api_key(
    *,
    force_refresh: bool = False,
    allow_env_fallback_on_missing_ssm: bool = True,
) -> tuple[str | None, str]:
    """Resolve the secret without ever logging or returning it through Admin APIs."""
    global _cached_value, _cached_at
    parameter = os.getenv(_PARAMETER_ENV, "").strip()
    if not parameter:
        return _fallback_local_or_env()

    now = time.monotonic()
    with _lock:
        if not force_refresh and _cached_value and now - _cached_at < _CACHE_TTL_SECONDS:
            return _cached_value, "ssm"

    try:
        response = _ssm_client().get_parameter(
            Name=_parameter_name(),
            WithDecryption=True,
        )
    except Exception as exc:
        invalidate_cache()
        # ParameterNotFound = SSM 參數尚未建立（部署升級遷移中）→ 回落
        # env/file 保既有安裝不停料；其他錯誤（網路/權限/解密失敗）= 真故障
        # → fail-closed unavailable。用 getattr 鴨子型別判定，不耦合 botocore。
        # allow_env_fallback_on_missing_ssm=False 時（put 驗證）即使是
        # ParameterNotFound 也 fail-closed unavailable——避免把 env 值誤判成
        # 啟用成功，也讓 clear 後的 status 如實反映 SSM 已不存在。
        response = getattr(exc, "response", None)
        code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
        if code == "ParameterNotFound" and allow_env_fallback_on_missing_ssm:
            return _fallback_local_or_env()
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
    client = _ssm_client()
    client.put_parameter(
        Name=_parameter_name(),
        Value=clean,
        Type="SecureString",
        Overwrite=True,
        Tier="Standard",
    )
    client.remove_tags_from_resource(
        ResourceType="Parameter",
        ResourceId=_parameter_name(),
        TagKeys=["TrustForgeLastVerifiedAt"],
    )
    invalidate_cache()
    resolved, source = resolve_api_key(
        force_refresh=True, allow_env_fallback_on_missing_ssm=False
    )
    # 只有 SSM 真的回傳寫入值（source=="ssm" 且值一致）才算啟用成功；
    # 否則（值 drift、或回落到 env 把同值當成功）一律 RuntimeError。
    if source != "ssm" or resolved != clean:
        invalidate_cache()
        raise RuntimeError("CoinMarketCap API key activation verification failed")
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
    # 不 reread SSM：SSM eventual consistency 可能在剛 delete 後仍回傳舊值並被
    # cache 300s，反而讓攝取續用已撤銷憑證。直接由 env/file fallback 決定
    # post-clear 真實狀態（SSM 已知不存在）：only-SSM→(None,"unconfigured")=
    # 撤銷成功；env/file 並存→如實揭露仍在現役，操作員須另行移除才完整撤銷。
    value, source = _fallback_local_or_env()
    return SecretStatus(bool(value), source, _last_verified_at)


def verify_connection(
    *,
    fetcher: Callable[..., bytes] = fetch_url,
) -> SecretStatus:
    global _last_verified_at
    key, source = resolve_api_key(force_refresh=True)
    if not key:
        raise RuntimeError("CoinMarketCap API key is not configured")
    # key 一律透過 HTTP header（X-CMC_PRO_API_KEY，與官方文件一致的 header 名）
    # 傳遞，**絕不**附加在 URL query param 上——避免 proxy/tracing/access-log/
    # 例外訊息等常見「記錄請求 URL」路徑 side-channel 側錄外洩（security：
    # 本 connector 為 key-based，header 是 CMC 官方指定的認證方式）。URL 全程
    # （含實際發出的 HTTP request）保持乾淨、不含 key。
    payload = fetcher(
        _VERIFY_URL,
        user_agent=_USER_AGENT,
        timeout=5,
        max_bytes=64 * 1024,
        max_redirects=0,
        extra_headers={"X-CMC_PRO_API_KEY": key},
    )
    document = json.loads(payload)
    # 成功條件：回應 JSON 含 data.BTC.quote.USD.price（schema 驗證，非 whale-alert
    # 的 result=="success"）。缺欄/非數字一律視為憑證被拒（不造假成功）。
    price = None
    try:
        price = document["data"]["BTC"]["quote"]["USD"]["price"]
    except (KeyError, TypeError):
        price = None
    if not isinstance(price, (int, float)) or isinstance(price, bool):
        raise RuntimeError("CoinMarketCap rejected the configured credential")
    _last_verified_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if source == "ssm":
        _ssm_client().add_tags_to_resource(
            ResourceType="Parameter",
            ResourceId=_parameter_name(),
            Tags=[{"Key": "TrustForgeLastVerifiedAt", "Value": _last_verified_at}],
        )
    return SecretStatus(True, source, _last_verified_at)
