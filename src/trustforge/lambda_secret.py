"""Cold-start secret hydration for the AWS Lambda entry point.

Only the secret ARN is stored in Lambda configuration.  The plaintext is read
once per execution environment and placed in process memory before ``web`` is
imported, because that module snapshots its environment-backed defaults during
import.  This module deliberately performs no logging.
"""
from __future__ import annotations

import os
from typing import Any


_SECRET_SPECS = (
    (
        "live-token",
        "TRUSTFORGE_LIVE_TOKEN_SECRET_ARN",
        "TRUSTFORGE_LIVE_TOKEN_SECRET_VERSION_ID",
        "TRUSTFORGE_LIVE_TOKEN",
    ),
    (
        "arkham",
        "TRUSTFORGE_ARKHAM_SECRET_ARN",
        "TRUSTFORGE_ARKHAM_SECRET_VERSION_ID",
        "ARKHAM_API_KEY",
    ),
    (
        "coinmarketcap",
        "TRUSTFORGE_CMC_SECRET_ARN",
        "TRUSTFORGE_CMC_SECRET_VERSION_ID",
        "CMC_PRO_API_KEY",
    ),
    (
        "etherscan",
        "TRUSTFORGE_ETHERSCAN_SECRET_ARN",
        "TRUSTFORGE_ETHERSCAN_SECRET_VERSION_ID",
        "ETHERSCAN_API_KEY",
    ),
    (
        "whale-alert",
        "TRUSTFORGE_WHALE_ALERT_SECRET_ARN",
        "TRUSTFORGE_WHALE_ALERT_SECRET_VERSION_ID",
        "WHALE_ALERT_API_KEY",
    ),
)
_hydrated = False


def hydrate_lambda_secrets(*, client: Any | None = None) -> bool:
    """Atomically load configured Lambda secrets once, failing closed on errors.

    Returns ``False`` when no secret ARN is configured, preserving the offline
    deployment contract.  Every configured secret must pin a VersionId and
    resolve to a non-empty ``SecretString``.  Values are applied to the process
    environment only after all reads validate, so a partial failure cannot
    leave a half-hydrated execution environment.
    """
    global _hydrated
    if _hydrated:
        return any(os.environ.get(target_env) for _, _, _, target_env in _SECRET_SPECS)

    configured = []
    for label, arn_env, version_env, target_env in _SECRET_SPECS:
        secret_arn = os.environ.get(arn_env, "").strip()
        secret_version = os.environ.get(version_env, "").strip()
        if not secret_arn and not secret_version:
            continue
        if not secret_arn or not secret_version:
            raise RuntimeError(f"configured {label} secret requires ARN and VersionId")
        if target_env in os.environ:
            raise RuntimeError(
                f"{target_env} must not be configured when {arn_env} is set"
            )
        configured.append((label, secret_arn, secret_version, target_env))

    if not configured:
        _hydrated = True
        return False

    if client is None:
        import boto3

        client = boto3.client("secretsmanager")

    loaded = {}
    for label, secret_arn, secret_version, target_env in configured:
        response = client.get_secret_value(
            SecretId=secret_arn,
            VersionId=secret_version,
        )
        if not isinstance(response, dict):
            raise RuntimeError(f"configured {label} secret returned an invalid response")
        value = response.get("SecretString")
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise RuntimeError(
                f"configured {label} secret has no non-empty SecretString"
            )
        loaded[target_env] = value.strip()

    try:
        for target_env, value in loaded.items():
            _set_environment_value(target_env, value)
    except Exception:
        for target_env in loaded:
            os.environ.pop(target_env, None)
        raise
    _hydrated = True
    return True


def hydrate_live_token(*, client: Any | None = None) -> bool:
    """Backward-compatible entry point; hydrates all configured Lambda secrets."""
    hydrate_lambda_secrets(client=client)
    return bool(os.environ.get("TRUSTFORGE_LIVE_TOKEN"))


def _set_environment_value(name: str, value: str) -> None:
    """Single assignment seam used to prove commit-stage rollback in tests."""
    os.environ[name] = value
