"""Tests for the fail-closed Agent OS DB authorization guard."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys
from types import ModuleType
from unittest.mock import patch

import pytest

from trustforge.agos_db_auth import (
    AGOS_SCHEMA_AUTH_PURPOSE,
    DBAuthorizationError,
    _is_pytest,
    _token_path,
    verify_db_authorization,
)
from trustforge import context_builder, memory_os, skill_registry, tool_registry


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_token_path_is_date_and_purpose_specific() -> None:
    compact = datetime.now(timezone.utc).strftime("%Y%m%d")
    assert str(_token_path("memory_os")) == (
        f"/tmp/eric-auth-{compact}-trustforge-memory_os.token"
    )
    assert _token_path("memory_os") != _token_path("skill_registry")


def test_missing_receipt_fails_closed(tmp_path: Path) -> None:
    with patch(
        "trustforge.agos_db_auth._token_path",
        return_value=tmp_path / "missing.token",
    ):
        with pytest.raises(DBAuthorizationError, match="requires authorization"):
            verify_db_authorization("memory_os")


def test_wrong_receipt_content_fails_closed(tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.token"
    receipt.write_text("authorized wrong-purpose 2000-01-01", encoding="utf-8")
    with patch("trustforge.agos_db_auth._token_path", return_value=receipt):
        with pytest.raises(DBAuthorizationError, match="content mismatch"):
            verify_db_authorization("memory_os")


def test_valid_receipt_passes(tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.token"
    receipt.write_text(
        f"authorized context_manifest {_today_iso()}", encoding="utf-8"
    )
    with patch("trustforge.agos_db_auth._token_path", return_value=receipt):
        verify_db_authorization("context_manifest")


def test_personally_created_empty_receipt_passes(tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.token"
    receipt.touch()
    with patch("trustforge.agos_db_auth._token_path", return_value=receipt):
        verify_db_authorization("memory_os")


def test_symlink_receipt_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "target.token"
    target.touch()
    receipt = tmp_path / "receipt.token"
    receipt.symlink_to(target)
    with patch("trustforge.agos_db_auth._token_path", return_value=receipt):
        with pytest.raises(DBAuthorizationError, match="regular file"):
            verify_db_authorization("memory_os")


def test_fake_pytest_module_cannot_bypass(tmp_path: Path) -> None:
    with (
        patch.dict(sys.modules, {"_pytest.config": ModuleType("_pytest.config")}),
        patch(
            "trustforge.agos_db_auth._token_path",
            return_value=tmp_path / "missing.token",
        ),
    ):
        assert _is_pytest() is False
        with pytest.raises(DBAuthorizationError):
            verify_db_authorization("memory_os")


@pytest.mark.parametrize(
    ("module", "purpose"),
    [
        (memory_os, AGOS_SCHEMA_AUTH_PURPOSE),
        (skill_registry, AGOS_SCHEMA_AUTH_PURPOSE),
        (tool_registry, AGOS_SCHEMA_AUTH_PURPOSE),
    ],
)
def test_direct_upgrade_authorizes_before_empty_db_mutation(
    module: object, purpose: str
) -> None:
    conn = sqlite3.connect(":memory:")
    with patch(
        "trustforge.agos_db_auth.verify_db_authorization",
        side_effect=DBAuthorizationError("blocked"),
    ) as authorize:
        with pytest.raises(DBAuthorizationError, match="blocked"):
            module._upgrade(conn)  # type: ignore[attr-defined]
    authorize.assert_called_once_with(purpose)
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall() == []


@pytest.mark.parametrize(
    ("module", "purpose", "version_key"),
    [
        (memory_os, AGOS_SCHEMA_AUTH_PURPOSE, "memory_os_version"),
        (skill_registry, AGOS_SCHEMA_AUTH_PURPOSE, "skill_registry_version"),
        (tool_registry, AGOS_SCHEMA_AUTH_PURPOSE, "tool_registry_version"),
    ],
)
def test_direct_upgrade_authorizes_existing_old_schema(
    module: object, purpose: str, version_key: str
) -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO _meta VALUES (?, '0')", (version_key,))
    with patch(
        "trustforge.agos_db_auth.verify_db_authorization",
        side_effect=DBAuthorizationError("blocked"),
    ) as authorize:
        with pytest.raises(DBAuthorizationError, match="blocked"):
            module._upgrade(conn)  # type: ignore[attr-defined]
    authorize.assert_called_once_with(purpose)
    assert conn.execute(
        "SELECT value FROM _meta WHERE key = ?", (version_key,)
    ).fetchone() == ("0",)


@pytest.mark.parametrize(
    ("module", "purpose"),
    [
        (memory_os, AGOS_SCHEMA_AUTH_PURPOSE),
        (skill_registry, AGOS_SCHEMA_AUTH_PURPOSE),
        (tool_registry, AGOS_SCHEMA_AUTH_PURPOSE),
    ],
)
def test_rollback_authorizes_before_mutation(
    module: object, purpose: str
) -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE sentinel (id INTEGER)")
    with patch(
        "trustforge.agos_db_auth.verify_db_authorization",
        side_effect=DBAuthorizationError("blocked"),
    ) as authorize:
        with pytest.raises(DBAuthorizationError, match="blocked"):
            module.rollback(conn)  # type: ignore[attr-defined]
    authorize.assert_called_once_with(purpose)
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE name='sentinel'"
    ).fetchone() == ("sentinel",)


def test_context_manifest_authorizes_before_mutation() -> None:
    conn = sqlite3.connect(":memory:")
    with patch(
        "trustforge.agos_db_auth.verify_db_authorization",
        side_effect=DBAuthorizationError("blocked"),
    ) as authorize:
        with pytest.raises(DBAuthorizationError, match="blocked"):
            context_builder._ensure_manifest_table(conn)
    authorize.assert_called_once_with(AGOS_SCHEMA_AUTH_PURPOSE)
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall() == []


@pytest.mark.parametrize(
    "mutation",
    [
        memory_os._upgrade,
        skill_registry._upgrade,
        tool_registry._upgrade,
        context_builder._ensure_manifest_table,
    ],
)
def test_all_schema_paths_accept_real_umbrella_receipt(
    mutation: object, tmp_path: Path
) -> None:
    """Exercise the real verifier with one umbrella receipt for every schema."""
    receipt = tmp_path / (
        f"eric-auth-{datetime.now(timezone.utc).strftime('%Y%m%d')}-"
        "trustforge-agos-schema-closeout.token"
    )
    receipt.touch()
    conn = sqlite3.connect(":memory:")

    with (
        patch("trustforge.agos_db_auth._token_path", return_value=receipt),
        patch(
            "trustforge.agos_db_auth.verify_db_authorization",
            wraps=verify_db_authorization,
        ),
    ):
        mutation(conn)  # type: ignore[operator]

    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0] > 0


@pytest.mark.parametrize(
    ("repository_class", "connects_schema"),
    [
        (memory_os.MemoryRepository, False),
        (skill_registry.SkillRegistryRepository, False),
        (tool_registry.ToolRegistryRepository, False),
        (context_builder.ContextBuilder, True),
    ],
)
def test_fresh_repository_connect_accepts_real_umbrella_receipt(
    repository_class: object,
    connects_schema: bool,
    tmp_path: Path,
) -> None:
    """Fresh repository files use the same real umbrella authorization."""
    receipt = tmp_path / (
        f"eric-auth-{datetime.now(timezone.utc).strftime('%Y%m%d')}-"
        "trustforge-agos-schema-closeout.token"
    )
    receipt.touch()
    db_path = tmp_path / f"{repository_class.__name__}.db"  # type: ignore[attr-defined]

    with (
        patch("trustforge.agos_db_auth._token_path", return_value=receipt),
        patch(
            "trustforge.agos_db_auth.verify_db_authorization",
            wraps=verify_db_authorization,
        ) as authorize,
    ):
        repository = repository_class(db_path=db_path)  # type: ignore[operator]
        if connects_schema:
            repository._connect()
        else:
            repository.ensure_schema()
        repository.close()

    assert db_path.is_file()
    assert authorize.call_count >= 1
    assert {
        call.args for call in authorize.call_args_list
    } == {(AGOS_SCHEMA_AUTH_PURPOSE,)}
