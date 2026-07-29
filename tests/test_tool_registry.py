"""Tests for Tool Capability Registry and Invocation Audit.

Issue: #918 | Epic: #914
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _authorize_schema_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "trustforge.agos_db_auth.verify_db_authorization",
        lambda purpose: None,
    )

from trustforge.tool_registry import (
    ToolCapability,
    ToolInvocation,
    ToolRegistryRepository,
    invocation_input_hash,
    invocation_output_hash,
    rollback,
    _upgrade as upgrade,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_tool_registry.db"


@pytest.fixture
def repo(db_path: Path) -> ToolRegistryRepository:
    r = ToolRegistryRepository(db_path=db_path)
    r.ensure_schema()
    yield r
    r.close()


def _make_cap(**kwargs) -> ToolCapability:
    defaults = {
        "tool_id": f"tool-{id(kwargs)}",
        "name": "Test Tool",
        "version": "1.0.0",
        "side_effect_class": "read_only",
        "evidence_class": "none",
        "approval_requirement": "never",
    }
    defaults.update(kwargs)
    return ToolCapability(**defaults)


def _make_invocation(tool_id: str = "test-tool", run_id: str = "run-1", **kwargs) -> ToolInvocation:
    defaults = {
        "invocation_id": "",
        "run_id": run_id,
        "tool_id": tool_id,
        "input_hash": "a" * 64,
        "status": "pending",
    }
    defaults.update(kwargs)
    return ToolInvocation(**defaults)


# ─── Migration Tests ─────────────────────────────────────────────────────────


class TestMigration:
    def test_upgrade_creates_tables(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        upgrade(conn)

        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "tool_capabilities" in tables
        assert "tool_invocations" in tables
        conn.close()

    def test_upgrade_idempotent(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        upgrade(conn)
        upgrade(conn)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "tool_capabilities" in tables
        conn.close()

    def test_rollback_drops_tables(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        upgrade(conn)
        rollback(conn)

        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "tool_capabilities" not in tables
        assert "tool_invocations" not in tables
        conn.close()


# ─── Tool Registration Tests ─────────────────────────────────────────────────


class TestRegistration:
    def test_register_and_get_roundtrip(self, repo: ToolRegistryRepository):
        cap = _make_cap(tool_id="coingecko-price")
        repo.register_tool(cap)

        result = repo.get_tool("coingecko-price")
        assert result is not None
        assert result.name == "Test Tool"
        assert result.side_effect_class == "read_only"
        assert result.evidence_class == "none"

    def test_duplicate_tool_id_raises(self, repo: ToolRegistryRepository):
        cap = _make_cap(tool_id="dup-tool")
        repo.register_tool(cap)

        with pytest.raises(sqlite3.IntegrityError, match="already registered"):
            repo.register_tool(_make_cap(tool_id="dup-tool"))

    def test_invalid_side_effect_raises(self, repo: ToolRegistryRepository):
        with pytest.raises(ValueError, match="invalid side_effect_class"):
            repo.register_tool(_make_cap(tool_id="bad1", side_effect_class="ultra"))

    def test_invalid_evidence_class_raises(self, repo: ToolRegistryRepository):
        with pytest.raises(ValueError, match="invalid evidence_class"):
            repo.register_tool(_make_cap(tool_id="bad2", evidence_class="super"))

    def test_invalid_approval_raises(self, repo: ToolRegistryRepository):
        with pytest.raises(ValueError, match="invalid approval_requirement"):
            repo.register_tool(_make_cap(tool_id="bad3", approval_requirement="maybe"))

    def test_approval_invariant_external_write(self, repo: ToolRegistryRepository):
        """external_write must have approval=always."""
        with pytest.raises(ValueError, match="high-risk tools must have"):
            repo.register_tool(
                _make_cap(
                    tool_id="ext-bad",
                    side_effect_class="external_write",
                    approval_requirement="never",
                )
            )

    def test_approval_invariant_deploy(self, repo: ToolRegistryRepository):
        """deploy_or_release must have approval=always."""
        with pytest.raises(ValueError, match="high-risk tools must have"):
            repo.register_tool(
                _make_cap(
                    tool_id="deploy-bad",
                    side_effect_class="deploy_or_release",
                    approval_requirement="conditional",
                )
            )

    def test_high_risk_with_always_succeeds(self, repo: ToolRegistryRepository):
        """High-risk tool with approval=always is valid."""
        cap = _make_cap(
            tool_id="ext-ok",
            side_effect_class="external_write",
            approval_requirement="always",
        )
        repo.register_tool(cap)
        assert repo.get_tool("ext-ok") is not None

    def test_list_tools_no_filter(self, repo: ToolRegistryRepository):
        repo.register_tool(_make_cap(tool_id="t1"))
        repo.register_tool(_make_cap(tool_id="t2"))
        assert len(repo.list_tools()) == 2

    def test_list_tools_filter_side_effect(self, repo: ToolRegistryRepository):
        repo.register_tool(_make_cap(tool_id="t3", side_effect_class="read_only"))
        repo.register_tool(_make_cap(tool_id="t4", side_effect_class="local_write"))

        results = repo.list_tools(side_effect_class="read_only")
        assert len(results) == 1
        assert results[0].tool_id == "t3"


# ─── is_known / requires_approval / can_produce_evidence Tests ───────────────


class TestSecurityChecks:
    def test_is_known_registered(self, repo: ToolRegistryRepository):
        repo.register_tool(_make_cap(tool_id="known-tool"))
        assert repo.is_known("known-tool") is True

    def test_is_known_unknown(self, repo: ToolRegistryRepository):
        assert repo.is_known("unknown-tool") is False

    def test_requires_approval_read_only(self, repo: ToolRegistryRepository):
        repo.register_tool(_make_cap(tool_id="ro-tool", side_effect_class="read_only"))
        assert repo.requires_approval("ro-tool") is False

    def test_requires_approval_external_write(self, repo: ToolRegistryRepository):
        repo.register_tool(
            _make_cap(
                tool_id="ew-tool",
                side_effect_class="external_write",
                approval_requirement="always",
            )
        )
        assert repo.requires_approval("ew-tool") is True

    def test_requires_approval_unknown_fail_closed(self, repo: ToolRegistryRepository):
        """Unknown tool → requires approval (fail-closed)."""
        assert repo.requires_approval("ghost-tool") is True

    def test_can_produce_evidence_none(self, repo: ToolRegistryRepository):
        repo.register_tool(_make_cap(tool_id="ev-none", evidence_class="none"))
        assert repo.can_produce_evidence("ev-none") is False

    def test_can_produce_evidence_context_only(self, repo: ToolRegistryRepository):
        repo.register_tool(_make_cap(tool_id="ev-ctx", evidence_class="context_only"))
        assert repo.can_produce_evidence("ev-ctx") is False

    def test_can_produce_evidence_candidate(self, repo: ToolRegistryRepository):
        repo.register_tool(_make_cap(tool_id="ev-cand", evidence_class="candidate_evidence"))
        assert repo.can_produce_evidence("ev-cand") is True

    def test_can_produce_evidence_trusted(self, repo: ToolRegistryRepository):
        repo.register_tool(_make_cap(tool_id="ev-trust", evidence_class="trusted_evidence"))
        assert repo.can_produce_evidence("ev-trust") is True

    def test_can_produce_evidence_unknown(self, repo: ToolRegistryRepository):
        assert repo.can_produce_evidence("unknown") is False


# ─── Invocation Audit Tests ──────────────────────────────────────────────────


class TestInvocationAudit:
    def test_associate_pending_invocation_with_resolved_run(
        self, repo: ToolRegistryRepository
    ):
        repo.register_tool(_make_cap(tool_id="associate-tool"))
        inv = _make_invocation(tool_id="associate-tool", run_id="pending-run")
        repo.record_invocation(inv)

        repo.associate_invocation_run(inv.invocation_id, "resolved-run")

        result = repo.get_invocation(inv.invocation_id)
        assert result is not None
        assert result.run_id == "resolved-run"
        assert repo.get_invocations_by_run("pending-run") == []

    def test_cannot_reassociate_completed_invocation(
        self, repo: ToolRegistryRepository
    ):
        repo.register_tool(_make_cap(tool_id="terminal-associate-tool"))
        inv = _make_invocation(tool_id="terminal-associate-tool")
        repo.record_invocation(inv)
        repo.complete_invocation(
            inv.invocation_id, output_hash=None, status="success"
        )
        with pytest.raises(ValueError, match="pending invocation not found"):
            repo.associate_invocation_run(inv.invocation_id, "other-run")

    def test_record_and_get_invocation(self, repo: ToolRegistryRepository):
        repo.register_tool(_make_cap(tool_id="inv-tool"))
        inv = _make_invocation(tool_id="inv-tool")
        repo.record_invocation(inv)

        result = repo.get_invocation(inv.invocation_id)
        assert result is not None
        assert result.tool_id == "inv-tool"
        assert result.status == "pending"

    def test_complete_invocation(self, repo: ToolRegistryRepository):
        repo.register_tool(_make_cap(tool_id="comp-tool"))
        inv = _make_invocation(tool_id="comp-tool")
        repo.record_invocation(inv)

        repo.complete_invocation(
            inv.invocation_id,
            output_hash="b" * 64,
            status="success",
            evidence_refs=["evidence://one", "evidence://two"],
        )

        result = repo.get_invocation(inv.invocation_id)
        assert result.status == "success"
        assert result.output_hash == "b" * 64
        assert result.evidence_refs == ["evidence://one", "evidence://two"]
        assert result.completed_at is not None

    def test_complete_invocation_rejects_stale_terminal_update(
        self, repo: ToolRegistryRepository
    ):
        repo.register_tool(_make_cap(tool_id="atomic-tool"))
        inv = _make_invocation(tool_id="atomic-tool")
        repo.record_invocation(inv)
        repo.complete_invocation(
            inv.invocation_id, output_hash="b" * 64, status="success"
        )

        with pytest.raises(ValueError, match="already 'success'"):
            repo.complete_invocation(
                inv.invocation_id,
                output_hash="c" * 64,
                status="failed",
                error="stale writer",
            )

        result = repo.get_invocation(inv.invocation_id)
        assert result.status == "success"
        assert result.output_hash == "b" * 64

    def test_complete_invocation_rejects_missing_row(
        self, repo: ToolRegistryRepository
    ):
        with pytest.raises(ValueError, match="invocation not found"):
            repo.complete_invocation(
                "missing", output_hash=None, status="failed"
            )

    def test_complete_invocation_failed(self, repo: ToolRegistryRepository):
        repo.register_tool(_make_cap(tool_id="fail-tool"))
        inv = _make_invocation(tool_id="fail-tool")
        repo.record_invocation(inv)

        repo.complete_invocation(
            inv.invocation_id,
            output_hash=None,
            status="failed",
            error="connection timeout",
        )

        result = repo.get_invocation(inv.invocation_id)
        assert result.status == "failed"
        assert result.error == "connection timeout"

    def test_get_invocations_by_run(self, repo: ToolRegistryRepository):
        repo.register_tool(_make_cap(tool_id="run-tool"))
        inv1 = _make_invocation(tool_id="run-tool", run_id="run-abc")
        inv2 = _make_invocation(tool_id="run-tool", run_id="run-abc", input_hash="c" * 64)
        inv3 = _make_invocation(tool_id="run-tool", run_id="run-other", input_hash="d" * 64)
        repo.record_invocation(inv1)
        repo.record_invocation(inv2)
        repo.record_invocation(inv3)

        results = repo.get_invocations_by_run("run-abc")
        assert len(results) == 2

    def test_get_nonexistent_invocation(self, repo: ToolRegistryRepository):
        assert repo.get_invocation("nonexistent") is None

    def test_invalid_status_raises(self, repo: ToolRegistryRepository):
        repo.register_tool(_make_cap(tool_id="status-tool"))
        inv = _make_invocation(tool_id="status-tool", status="success")
        with pytest.raises(ValueError, match="must be recorded with status='pending'"):
            repo.record_invocation(inv)


# ─── Hash Utility Tests ──────────────────────────────────────────────────────


class TestHashUtils:
    def test_input_hash_deterministic(self):
        h1 = invocation_input_hash("tool-x", {"query": "BTC"})
        h2 = invocation_input_hash("tool-x", {"query": "BTC"})
        assert h1 == h2
        assert len(h1) == 64

    def test_input_hash_different_args(self):
        h1 = invocation_input_hash("tool-x", {"query": "BTC"})
        h2 = invocation_input_hash("tool-x", {"query": "ETH"})
        assert h1 != h2

    def test_output_hash_dict(self):
        h1 = invocation_output_hash({"price": 100.5})
        h2 = invocation_output_hash({"price": 100.5})
        assert h1 == h2

    def test_output_hash_string(self):
        h1 = invocation_output_hash("raw response body")
        h2 = invocation_output_hash("raw response body")
        assert h1 == h2

    def test_output_hash_list(self):
        assert invocation_output_hash([{"id": 1}]) == invocation_output_hash(
            [{"id": 1}]
        )
