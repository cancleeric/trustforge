"""Tool Capability Registry — metadata and invocation audit for Agent OS tools.

Records tool capability metadata (side-effect class, evidence class, approval
requirement) and provides an append-only invocation audit trail.

Core security invariants:
  - Unknown tools cannot execute (fail-closed)
  - external_write / deploy_or_release require human approval (always)
  - context_only output cannot enter Evidence scoring
  - Invocation records are append-only (no DELETE)

Contract: docs/contracts/TOOL-CAPABILITY-CONTRACT.md
Issue: #918 | Epic: #914
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

# ─── Constants ───────────────────────────────────────────────────────────────

VALID_SIDE_EFFECTS = frozenset({"read_only", "local_write", "external_write", "deploy_or_release"})

VALID_EVIDENCE_CLASSES = frozenset({"none", "context_only", "candidate_evidence", "trusted_evidence"})

VALID_APPROVAL_REQS = frozenset({"never", "always", "conditional"})

VALID_STATUSES = frozenset({"pending", "success", "failed", "timeout", "rejected"})

_HIGH_RISK_SIDE_EFFECTS = frozenset({"external_write", "deploy_or_release"})

_MIGRATION_VERSION = 1


# ─── Dataclasses ─────────────────────────────────────────────────────────────


@dataclass
class ToolCapability:
    """Metadata for a registered tool capability."""

    tool_id: str
    name: str
    version: str = "1.0.0"
    side_effect_class: str = "read_only"
    evidence_class: str = "none"
    approval_requirement: str = "never"
    timeout_sec: int = 30
    max_retries: int = 0
    backoff_sec: float = 1.0
    owner: str = ""
    schema_input: dict[str, Any] | None = None
    schema_output: dict[str, Any] | None = None
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _now_iso()
        if self.schema_input is None:
            self.schema_input = {}
        if self.schema_output is None:
            self.schema_output = {}


@dataclass
class ToolInvocation:
    """An audit record of a single tool invocation."""

    invocation_id: str
    run_id: str
    tool_id: str
    input_hash: str
    output_hash: str | None = None
    status: str = "pending"
    error: str | None = None
    evidence_refs: list[str] | None = None
    started_at: str = ""
    completed_at: str | None = None

    def __post_init__(self) -> None:
        if not self.started_at:
            self.started_at = _now_iso()
        if not self.invocation_id:
            self.invocation_id = str(uuid4())
        if self.evidence_refs is None:
            self.evidence_refs = []


# ─── Utility Functions ───────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_db_path() -> Path:
    """Return the SQLite DB path, controlled by env var."""
    return Path(os.getenv("TRUSTFORGE_TOOL_REGISTRY_DB", "data/tool_registry.db"))


def invocation_input_hash(tool_id: str, args: dict[str, Any]) -> str:
    """Compute deterministic hash for tool invocation input."""
    payload = json.dumps(
        {"tool_id": tool_id, "args": args},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def invocation_output_hash(output: dict[str, Any] | str) -> str:
    """Compute deterministic hash for tool invocation output."""
    if isinstance(output, dict):
        payload = json.dumps(
            output, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    else:
        payload = output
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ─── Migration ───────────────────────────────────────────────────────────────


def upgrade(conn: sqlite3.Connection) -> None:
    """Create Tool Registry tables (idempotent)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )

    current = _get_version(conn)
    if current >= _MIGRATION_VERSION:
        return

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tool_capabilities (
            tool_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            version TEXT NOT NULL DEFAULT '1.0.0',
            side_effect_class TEXT NOT NULL DEFAULT 'read_only'
                CHECK (side_effect_class IN ('read_only','local_write','external_write','deploy_or_release')),
            evidence_class TEXT NOT NULL DEFAULT 'none'
                CHECK (evidence_class IN ('none','context_only','candidate_evidence','trusted_evidence')),
            approval_requirement TEXT NOT NULL DEFAULT 'never'
                CHECK (approval_requirement IN ('never','always','conditional')),
            timeout_sec INTEGER NOT NULL DEFAULT 30,
            max_retries INTEGER NOT NULL DEFAULT 0,
            backoff_sec REAL NOT NULL DEFAULT 1.0,
            owner TEXT NOT NULL DEFAULT '',
            schema_input TEXT NOT NULL DEFAULT '{}',
            schema_output TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tool_invocations (
            invocation_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            tool_id TEXT NOT NULL REFERENCES tool_capabilities(tool_id),
            input_hash TEXT NOT NULL,
            output_hash TEXT,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','success','failed','timeout','rejected')),
            error TEXT,
            evidence_refs TEXT NOT NULL DEFAULT '[]',
            started_at TEXT NOT NULL,
            completed_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_invocation_run
            ON tool_invocations(run_id);

        CREATE INDEX IF NOT EXISTS idx_invocation_tool_time
            ON tool_invocations(tool_id, started_at);
    """)

    _set_version(conn, _MIGRATION_VERSION)
    conn.commit()


def rollback(conn: sqlite3.Connection) -> None:
    """Drop Tool Registry tables."""
    conn.executescript("""
        DROP TABLE IF EXISTS tool_invocations;
        DROP TABLE IF EXISTS tool_capabilities;
    """)
    _set_version(conn, 0)
    conn.commit()


def _get_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "SELECT value FROM _meta WHERE key='tool_registry_version'"
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('tool_registry_version', ?)",
        (str(version),),
    )


# ─── Repository ──────────────────────────────────────────────────────────────


class ToolRegistryRepository:
    """SQLite-backed repository for Tool Capabilities and Invocation Audit."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or default_db_path()
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def ensure_schema(self) -> None:
        """Create tables if they don't exist."""
        upgrade(self._connect())

    # ─── Tool Capability CRUD ────────────────────────────────────────────

    def register_tool(self, cap: ToolCapability) -> None:
        """Register a tool capability. Enforces approval invariant.

        Raises ValueError if high-risk tool doesn't have approval=always.
        Raises IntegrityError if tool_id already exists.
        """
        if cap.side_effect_class not in VALID_SIDE_EFFECTS:
            raise ValueError(f"invalid side_effect_class: {cap.side_effect_class!r}")
        if cap.evidence_class not in VALID_EVIDENCE_CLASSES:
            raise ValueError(f"invalid evidence_class: {cap.evidence_class!r}")
        if cap.approval_requirement not in VALID_APPROVAL_REQS:
            raise ValueError(f"invalid approval_requirement: {cap.approval_requirement!r}")

        # Approval invariant: high-risk must have approval=always
        if (
            cap.side_effect_class in _HIGH_RISK_SIDE_EFFECTS
            and cap.approval_requirement != "always"
        ):
            raise ValueError(
                f"tool '{cap.tool_id}' has side_effect_class='{cap.side_effect_class}' "
                f"but approval_requirement='{cap.approval_requirement}'; "
                f"high-risk tools must have approval_requirement='always'"
            )

        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO tool_capabilities
                   (tool_id, name, version, side_effect_class, evidence_class,
                    approval_requirement, timeout_sec, max_retries, backoff_sec,
                    owner, schema_input, schema_output, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cap.tool_id,
                    cap.name,
                    cap.version,
                    cap.side_effect_class,
                    cap.evidence_class,
                    cap.approval_requirement,
                    cap.timeout_sec,
                    cap.max_retries,
                    cap.backoff_sec,
                    cap.owner,
                    json.dumps(cap.schema_input or {}),
                    json.dumps(cap.schema_output or {}),
                    cap.created_at,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError as e:
            conn.rollback()
            raise sqlite3.IntegrityError(
                f"tool_id '{cap.tool_id}' already registered: {e}"
            ) from e

    def get_tool(self, tool_id: str) -> ToolCapability | None:
        """Retrieve a tool capability by ID."""
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM tool_capabilities WHERE tool_id = ?", (tool_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_capability(row)

    def list_tools(self, *, side_effect_class: str | None = None) -> list[ToolCapability]:
        """List tools with optional filtering by side-effect class."""
        conn = self._connect()
        if side_effect_class:
            rows = conn.execute(
                "SELECT * FROM tool_capabilities WHERE side_effect_class = ? ORDER BY created_at",
                (side_effect_class,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tool_capabilities ORDER BY created_at"
            ).fetchall()
        return [self._row_to_capability(r) for r in rows]

    def is_known(self, tool_id: str) -> bool:
        """Check if a tool is registered. Unknown tools cannot execute."""
        conn = self._connect()
        row = conn.execute(
            "SELECT 1 FROM tool_capabilities WHERE tool_id = ?", (tool_id,)
        ).fetchone()
        return row is not None

    def requires_approval(self, tool_id: str) -> bool:
        """Check if a tool requires human approval before execution.

        Unknown tools → True (fail-closed).
        """
        tool = self.get_tool(tool_id)
        if tool is None:
            return True  # Unknown tool: fail-closed
        if tool.side_effect_class in _HIGH_RISK_SIDE_EFFECTS:
            return True
        return tool.approval_requirement == "always"

    def can_produce_evidence(self, tool_id: str) -> bool:
        """Check if a tool's output can become Evidence.

        Returns False for unknown tools, 'none', and 'context_only'.
        """
        tool = self.get_tool(tool_id)
        if tool is None:
            return False
        return tool.evidence_class in ("candidate_evidence", "trusted_evidence")

    # ─── Invocation Audit ────────────────────────────────────────────────

    def record_invocation(self, inv: ToolInvocation) -> None:
        """Record a tool invocation (append-only)."""
        if inv.status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {inv.status!r}")

        conn = self._connect()
        conn.execute(
            """INSERT INTO tool_invocations
               (invocation_id, run_id, tool_id, input_hash, output_hash,
                status, error, evidence_refs, started_at, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                inv.invocation_id,
                inv.run_id,
                inv.tool_id,
                inv.input_hash,
                inv.output_hash,
                inv.status,
                inv.error,
                json.dumps(inv.evidence_refs or []),
                inv.started_at,
                inv.completed_at,
            ),
        )
        conn.commit()

    def complete_invocation(
        self,
        invocation_id: str,
        *,
        output_hash: str | None,
        status: str,
        error: str | None = None,
    ) -> None:
        """Update an invocation's completion status."""
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {status!r}")

        conn = self._connect()
        conn.execute(
            """UPDATE tool_invocations
               SET output_hash = ?, status = ?, error = ?, completed_at = ?
               WHERE invocation_id = ?""",
            (output_hash, status, error, _now_iso(), invocation_id),
        )
        conn.commit()

    def get_invocation(self, invocation_id: str) -> ToolInvocation | None:
        """Retrieve a single invocation record."""
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM tool_invocations WHERE invocation_id = ?",
            (invocation_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_invocation(row)

    def get_invocations_by_run(self, run_id: str) -> list[ToolInvocation]:
        """Get all invocations for a specific run."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM tool_invocations WHERE run_id = ? ORDER BY started_at",
            (run_id,),
        ).fetchall()
        return [self._row_to_invocation(r) for r in rows]

    # ─── Helpers ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    @staticmethod
    def _row_to_capability(row: tuple) -> ToolCapability:
        return ToolCapability(
            tool_id=row[0],
            name=row[1],
            version=row[2],
            side_effect_class=row[3],
            evidence_class=row[4],
            approval_requirement=row[5],
            timeout_sec=row[6],
            max_retries=row[7],
            backoff_sec=row[8],
            owner=row[9],
            schema_input=json.loads(row[10]),
            schema_output=json.loads(row[11]),
            created_at=row[12],
        )

    @staticmethod
    def _row_to_invocation(row: tuple) -> ToolInvocation:
        return ToolInvocation(
            invocation_id=row[0],
            run_id=row[1],
            tool_id=row[2],
            input_hash=row[3],
            output_hash=row[4],
            status=row[5],
            error=row[6],
            evidence_refs=json.loads(row[7]),
            started_at=row[8],
            completed_at=row[9],
        )
