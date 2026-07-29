"""Tests for Agent OS DB Authorization Guard (file-based).

Issue: #916, #917, #918 | Epic: #914
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from trustforge.agos_db_auth import (
    DBAuthorizationError,
    _is_pytest,
    _token_path,
    verify_db_authorization,
)


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _today_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


class TestTokenPath:
    def test_path_format(self):
        p = _token_path("memory_os")
        assert str(p) == f"/tmp/eric-auth-{_today_compact()}-trustforge-memory_os.token"

    def test_path_varies_by_purpose(self):
        assert _token_path("memory_os") != _token_path("skill_registry")


class TestPytestBypass:
    def test_pytest_detected(self):
        """Under test, _is_pytest() should return True."""
        assert _is_pytest() is True

    def test_verify_passes_under_pytest(self):
        """Under pytest, verify always passes regardless of file existence."""
        # This should NOT raise even though no token file exists
        verify_db_authorization("memory_os")
        verify_db_authorization("skill_registry")
        verify_db_authorization("tool_registry")


class TestFileBasedAuth:
    """Test the actual authorization logic (bypass _is_pytest by patching)."""

    def test_blocks_when_file_missing(self, tmp_path: Path):
        """No token file → DBAuthorizationError."""
        with patch("trustforge.agos_db_auth._is_pytest", return_value=False):
            with patch("trustforge.agos_db_auth._token_path", return_value=tmp_path / "nonexistent.token"):
                with pytest.raises(DBAuthorizationError, match="requires authorization"):
                    verify_db_authorization("memory_os")

    def test_blocks_when_content_wrong(self, tmp_path: Path):
        """File exists but content wrong → DBAuthorizationError."""
        token_file = tmp_path / "token.token"
        token_file.write_text("wrong content", encoding="utf-8")

        with patch("trustforge.agos_db_auth._is_pytest", return_value=False):
            with patch("trustforge.agos_db_auth._token_path", return_value=token_file):
                with pytest.raises(DBAuthorizationError, match="content mismatch"):
                    verify_db_authorization("memory_os")

    def test_blocks_when_purpose_wrong(self, tmp_path: Path):
        """File contains different purpose → DBAuthorizationError."""
        token_file = tmp_path / "token.token"
        token_file.write_text(f"authorized skill_registry {_today_iso()}", encoding="utf-8")

        with patch("trustforge.agos_db_auth._is_pytest", return_value=False):
            with patch("trustforge.agos_db_auth._token_path", return_value=token_file):
                with pytest.raises(DBAuthorizationError, match="content mismatch"):
                    verify_db_authorization("memory_os")

    def test_blocks_when_date_wrong(self, tmp_path: Path):
        """File contains yesterday's date → DBAuthorizationError."""
        token_file = tmp_path / "token.token"
        token_file.write_text("authorized memory_os 2020-01-01", encoding="utf-8")

        with patch("trustforge.agos_db_auth._is_pytest", return_value=False):
            with patch("trustforge.agos_db_auth._token_path", return_value=token_file):
                with pytest.raises(DBAuthorizationError, match="content mismatch"):
                    verify_db_authorization("memory_os")

    def test_passes_valid_file(self, tmp_path: Path):
        """Correct file + content + date → passes."""
        token_file = tmp_path / "token.token"
        token_file.write_text(f"authorized memory_os {_today_iso()}", encoding="utf-8")

        with patch("trustforge.agos_db_auth._is_pytest", return_value=False):
            with patch("trustforge.agos_db_auth._token_path", return_value=token_file):
                # Should NOT raise
                verify_db_authorization("memory_os")

    def test_passes_skill_registry(self, tmp_path: Path):
        token_file = tmp_path / "token.token"
        token_file.write_text(f"authorized skill_registry {_today_iso()}", encoding="utf-8")

        with patch("trustforge.agos_db_auth._is_pytest", return_value=False):
            with patch("trustforge.agos_db_auth._token_path", return_value=token_file):
                verify_db_authorization("skill_registry")

    def test_passes_tool_registry(self, tmp_path: Path):
        token_file = tmp_path / "token.token"
        token_file.write_text(f"authorized tool_registry {_today_iso()}", encoding="utf-8")

        with patch("trustforge.agos_db_auth._is_pytest", return_value=False):
            with patch("trustforge.agos_db_auth._token_path", return_value=token_file):
                verify_db_authorization("tool_registry")

    def test_no_env_var_bypass(self, tmp_path: Path):
        """Even with TRUSTFORGE_TESTING=1 set, file check still happens when _is_pytest=False."""
        import os
        with patch("trustforge.agos_db_auth._is_pytest", return_value=False):
            with patch("trustforge.agos_db_auth._token_path", return_value=tmp_path / "no.token"):
                with patch.dict(os.environ, {"TRUSTFORGE_TESTING": "1"}):
                    with pytest.raises(DBAuthorizationError):
                        verify_db_authorization("memory_os")
