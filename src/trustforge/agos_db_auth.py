"""Agent OS DB Authorization Guard.

All Agent OS DB schema changes (#916, #917, #918) require Eric's same-day
authorization token before migration can run. This module enforces that
requirement at the migration entry point.

The token is provided via environment variable TRUSTFORGE_AGOS_DB_AUTH_TOKEN.
The expected format is: "agos-{purpose}-{YYYY-MM-DD}" where the date must
match today (UTC).

If the token is missing or invalid, migration MUST NOT proceed (fail-closed).
In test environments (TRUSTFORGE_TESTING=1), the check is bypassed.

Issue: #916, #917, #918 | Epic: #914
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone


class DBAuthorizationError(PermissionError):
    """Raised when DB migration lacks valid authorization."""
    pass


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def verify_db_authorization(purpose: str) -> None:
    """Verify that a valid same-day authorization token exists for the given purpose.

    Token format: "agos-{purpose}-{YYYY-MM-DD}"
    Purpose examples: "memory_os", "skill_registry", "tool_registry"

    Raises DBAuthorizationError if token is missing, malformed, or expired.
    Bypassed when TRUSTFORGE_TESTING=1 (test environments only).
    """
    # Bypass in test environments
    if os.getenv("TRUSTFORGE_TESTING", "0") == "1":
        return

    # Bypass if AGOS is not enabled (schema won't be used in production)
    if os.getenv("TRUSTFORGE_AGOS_ENABLED", "0") != "1":
        return

    token = os.getenv("TRUSTFORGE_AGOS_DB_AUTH_TOKEN", "")
    if not token:
        raise DBAuthorizationError(
            f"DB migration for '{purpose}' requires authorization. "
            f"Set TRUSTFORGE_AGOS_DB_AUTH_TOKEN=agos-{purpose}-{_today_utc()} "
            f"(Eric must issue same-day token)."
        )

    # Parse and validate token
    pattern = re.compile(r"^agos-(.+)-(\d{4}-\d{2}-\d{2})$")
    match = pattern.match(token)
    if not match:
        raise DBAuthorizationError(
            f"Invalid token format. Expected: agos-{purpose}-{_today_utc()}"
        )

    token_purpose = match.group(1)
    token_date = match.group(2)
    today = _today_utc()

    if token_purpose != purpose:
        raise DBAuthorizationError(
            f"Token purpose mismatch: token is for '{token_purpose}', "
            f"but migration requires '{purpose}'"
        )

    if token_date != today:
        raise DBAuthorizationError(
            f"Token expired: issued for {token_date}, today is {today}. "
            f"Tokens are valid for same-day only."
        )
