"""Agent OS DB Authorization Guard.

All Agent OS DB schema changes (#916, #917, #918) require Eric's same-day
authorization token file before migration can run.

Token file path (immutable convention):
    /tmp/eric-auth-YYYYMMDD-trustforge-{purpose}.token

The file must:
  1. Exist on disk at the expected path (today's date, correct purpose)
  2. Contain a single line matching: "authorized {purpose} YYYY-MM-DD"

If the file is missing, unreadable, malformed, or dated wrong,
migration MUST NOT proceed (fail-closed). There is NO environment
variable bypass — the only bypass is pytest (detected by the presence
of a _pytest module in sys.modules, not an env var that callers can set).

Issue: #916, #917, #918 | Epic: #914
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path


class DBAuthorizationError(PermissionError):
    """Raised when DB migration lacks valid file-based authorization."""
    pass


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _token_path(purpose: str) -> Path:
    """Canonical token file path. Convention is non-negotiable."""
    return Path(f"/tmp/eric-auth-{_today_utc()}-trustforge-{purpose}.token")


def _is_pytest() -> bool:
    """Detect if running under pytest harness (test environment only).

    Uses _pytest.config presence (only loaded when pytest is actively running,
    not merely installed). A bare `import pytest` in production code won't
    trigger this — the _pytest.config module is only populated during an
    active pytest session.
    """
    return "_pytest.config" in sys.modules


def verify_db_authorization(purpose: str) -> None:
    """Verify that Eric's same-day file-based authorization token exists.

    Expected file: /tmp/eric-auth-YYYYMMDD-trustforge-{purpose}.token
    Expected content (first line): "authorized {purpose} YYYY-MM-DD"

    Raises DBAuthorizationError if:
      - File does not exist
      - File is not readable
      - Content does not match expected format
      - Date in content does not match today

    The ONLY bypass is running under pytest. No environment variable can
    override this check.
    """
    # pytest bypass — detected by module presence, not by env var
    if _is_pytest():
        return

    token_file = _token_path(purpose)
    today_iso = _today_iso()

    if not token_file.exists():
        raise DBAuthorizationError(
            f"DB migration for '{purpose}' requires authorization.\n"
            f"Expected token file: {token_file}\n"
            f"Eric must create: echo 'authorized {purpose} {today_iso}' > {token_file}"
        )

    try:
        content = token_file.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise DBAuthorizationError(
            f"Cannot read token file {token_file}: {e}"
        ) from e

    expected_content = f"authorized {purpose} {today_iso}"
    if content != expected_content:
        raise DBAuthorizationError(
            f"Token file content mismatch.\n"
            f"  Expected: '{expected_content}'\n"
            f"  Got:      '{content}'\n"
            f"Token may be for wrong purpose or expired (must match today)."
        )
