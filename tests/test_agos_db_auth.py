"""Tests for Agent OS DB Authorization Guard.

Issue: #916, #917, #918 | Epic: #914
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from trustforge.agos_db_auth import DBAuthorizationError, verify_db_authorization


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class TestDBAuthorization:
    def test_bypass_in_testing_mode(self):
        """TRUSTFORGE_TESTING=1 bypasses all checks."""
        with patch.dict(os.environ, {"TRUSTFORGE_TESTING": "1", "TRUSTFORGE_AGOS_ENABLED": "1", "TRUSTFORGE_AGOS_DB_AUTH_TOKEN": ""}):
            # Should NOT raise even without token
            verify_db_authorization("memory_os")

    def test_bypass_when_agos_disabled(self):
        """When AGOS is not enabled, no authorization needed."""
        with patch.dict(os.environ, {"TRUSTFORGE_TESTING": "0", "TRUSTFORGE_AGOS_ENABLED": "0", "TRUSTFORGE_AGOS_DB_AUTH_TOKEN": ""}):
            verify_db_authorization("memory_os")

    def test_blocks_without_token(self):
        """When AGOS enabled + no testing + no token → blocked."""
        with patch.dict(os.environ, {"TRUSTFORGE_TESTING": "0", "TRUSTFORGE_AGOS_ENABLED": "1", "TRUSTFORGE_AGOS_DB_AUTH_TOKEN": ""}):
            with pytest.raises(DBAuthorizationError, match="requires authorization"):
                verify_db_authorization("memory_os")

    def test_blocks_wrong_format(self):
        """Malformed token → blocked."""
        with patch.dict(os.environ, {"TRUSTFORGE_TESTING": "0", "TRUSTFORGE_AGOS_ENABLED": "1", "TRUSTFORGE_AGOS_DB_AUTH_TOKEN": "bad-token"}):
            with pytest.raises(DBAuthorizationError, match="Invalid token format"):
                verify_db_authorization("memory_os")

    def test_blocks_wrong_purpose(self):
        """Token for different purpose → blocked."""
        token = f"agos-skill_registry-{_today()}"
        with patch.dict(os.environ, {"TRUSTFORGE_TESTING": "0", "TRUSTFORGE_AGOS_ENABLED": "1", "TRUSTFORGE_AGOS_DB_AUTH_TOKEN": token}):
            with pytest.raises(DBAuthorizationError, match="purpose mismatch"):
                verify_db_authorization("memory_os")

    def test_blocks_expired_token(self):
        """Token from yesterday → blocked."""
        token = "agos-memory_os-2020-01-01"
        with patch.dict(os.environ, {"TRUSTFORGE_TESTING": "0", "TRUSTFORGE_AGOS_ENABLED": "1", "TRUSTFORGE_AGOS_DB_AUTH_TOKEN": token}):
            with pytest.raises(DBAuthorizationError, match="Token expired"):
                verify_db_authorization("memory_os")

    def test_passes_valid_token(self):
        """Correct purpose + today's date → passes."""
        token = f"agos-memory_os-{_today()}"
        with patch.dict(os.environ, {"TRUSTFORGE_TESTING": "0", "TRUSTFORGE_AGOS_ENABLED": "1", "TRUSTFORGE_AGOS_DB_AUTH_TOKEN": token}):
            verify_db_authorization("memory_os")

    def test_passes_skill_registry_token(self):
        """Skill registry token works for skill_registry purpose."""
        token = f"agos-skill_registry-{_today()}"
        with patch.dict(os.environ, {"TRUSTFORGE_TESTING": "0", "TRUSTFORGE_AGOS_ENABLED": "1", "TRUSTFORGE_AGOS_DB_AUTH_TOKEN": token}):
            verify_db_authorization("skill_registry")

    def test_passes_tool_registry_token(self):
        """Tool registry token works for tool_registry purpose."""
        token = f"agos-tool_registry-{_today()}"
        with patch.dict(os.environ, {"TRUSTFORGE_TESTING": "0", "TRUSTFORGE_AGOS_ENABLED": "1", "TRUSTFORGE_AGOS_DB_AUTH_TOKEN": token}):
            verify_db_authorization("tool_registry")
