"""Cold-start secret hydration for the AWS Lambda entry point.

Only the secret ARN is stored in Lambda configuration.  The plaintext is read
once per execution environment and placed in process memory before ``web`` is
imported, because that module snapshots its environment-backed defaults during
import.  This module deliberately performs no logging.
"""
from __future__ import annotations

import os
from typing import Any


_SECRET_ARN_ENV = "TRUSTFORGE_LIVE_TOKEN_SECRET_ARN"
_SECRET_VERSION_ENV = "TRUSTFORGE_LIVE_TOKEN_SECRET_VERSION_ID"
_TOKEN_ENV = "TRUSTFORGE_LIVE_TOKEN"
_hydrated = False


def hydrate_live_token(*, client: Any | None = None) -> bool:
    """Load the live token from Secrets Manager once, failing closed on errors.

    Returns ``False`` when no secret ARN is configured (the existing offline
    deployment contract).  A configured ARN must resolve to a non-empty
    ``SecretString``.  Plaintext and ARN configuration may not coexist.
    """
    global _hydrated
    if _hydrated:
        return bool(os.environ.get(_TOKEN_ENV))

    secret_arn = os.environ.get(_SECRET_ARN_ENV, "").strip()
    if not secret_arn:
        _hydrated = True
        return False
    if os.environ.get(_TOKEN_ENV):
        raise RuntimeError(
            f"{_TOKEN_ENV} must not be configured when {_SECRET_ARN_ENV} is set"
        )

    if client is None:
        import boto3

        client = boto3.client("secretsmanager")

    request = {"SecretId": secret_arn}
    secret_version = os.environ.get(_SECRET_VERSION_ENV, "").strip()
    if secret_version:
        request["VersionId"] = secret_version
    response = client.get_secret_value(**request)
    if not isinstance(response, dict):
        raise RuntimeError("configured live-token secret returned an invalid response")
    token = response.get("SecretString")
    if not isinstance(token, str) or not token.strip():
        raise RuntimeError("configured live-token secret has no non-empty SecretString")

    os.environ[_TOKEN_ENV] = token
    _hydrated = True
    return True
