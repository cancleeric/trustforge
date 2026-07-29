"""Agent OS DB authorization guard.

Every Agent OS schema mutation requires Eric's same-day, purpose-specific
authorization receipt. Missing, unreadable, malformed, or stale receipts fail
closed. There is deliberately no runtime or environment bypass; tests patch
``verify_db_authorization`` explicitly at the mutation boundary.

Issue: #916, #917, #918 | Epic: #914
"""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import stat


AGOS_SCHEMA_AUTH_PURPOSE = "agos-schema-closeout"


class DBAuthorizationError(PermissionError):
    """Raised when a DB migration lacks valid file-based authorization."""


def _today_local() -> datetime:
    """Return the operator-local date used by the documented shell receipt."""
    return datetime.now().astimezone()


def _today_iso() -> str:
    return _today_local().strftime("%Y-%m-%d")


def _token_path(purpose: str) -> Path:
    """Return the canonical, immutable authorization receipt path."""
    return Path(
        f"/tmp/eric-auth-{_today_local():%Y%m%d}-trustforge-{purpose}.token"
    )


def _is_pytest() -> bool:
    """Compatibility hook with no automatic runtime detection.

    Tests that exercise non-DB evidence gates may patch this function
    explicitly. Importing or fabricating pytest modules never changes it.
    """
    return False


def verify_db_authorization(purpose: str) -> None:
    """Verify Eric's same-day, file-based authorization receipt.

    Expected path:
        /tmp/eric-auth-YYYYMMDD-trustforge-{purpose}.token
    Expected content:
        authorized {purpose} YYYY-MM-DD
    """
    token_file = _token_path(purpose)
    today_iso = _today_iso()

    if not token_file.exists():
        raise DBAuthorizationError(
            f"DB migration '{purpose}' requires authorization.\n"
            f"Expected token file: {token_file}\n"
            f"Eric must create: echo 'authorized {purpose} {today_iso}' "
            f"> {token_file}"
        )

    try:
        metadata = token_file.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise DBAuthorizationError(
                f"Authorization receipt must be a regular file: {token_file}"
            )
        if metadata.st_uid != os.getuid():
            raise DBAuthorizationError(
                f"Authorization receipt has unexpected owner: {token_file}"
            )
        if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise DBAuthorizationError(
                f"Authorization receipt must not be group/world writable: "
                f"{token_file}"
            )
        content = token_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise DBAuthorizationError(
            f"Cannot read token file {token_file}: {exc}"
        ) from exc

    expected_content = f"authorized {purpose} {today_iso}"
    # Eric's personally-created empty receipt is valid. If content is supplied,
    # it must be the exact same-day, purpose-specific assertion.
    if content and content != expected_content:
        raise DBAuthorizationError(
            "Token file content mismatch.\n"
            f"  Expected: '{expected_content}'\n"
            f"  Got: '{content}'\n"
            "Token may be for the wrong purpose or expired (must match today)."
        )
