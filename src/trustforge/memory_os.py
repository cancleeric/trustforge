"""Memory OS — persistence layer for Agent OS memory entries and links.

Provides typed memory storage (episodic/semantic/procedural/dialogue),
content-addressed hashing, evidence eligibility validation, and relationship
links between memory entries.

Design principles:
  - Evidence-ineligible by default (fail-closed)
  - Historical conclusions (hermes-* provider) NEVER become Evidence
  - Append-only: entries cannot be deleted
  - Zero third-party dependencies (stdlib sqlite3 only)

Contract: docs/contracts/MEMORY-OS-CONTRACT.md
Issue: #916 | Epic: #914
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

# ─── Constants ───────────────────────────────────────────────────────────────

VALID_KINDS = frozenset({"episodic", "semantic", "procedural", "dialogue"})

VALID_RELATIONS = frozenset({"derived_from", "supersedes", "contradicts", "supports"})

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_MIGRATION_VERSION = 1


# ─── Dataclasses ─────────────────────────────────────────────────────────────


@dataclass
class MemoryEntry:
    """A single memory entry in the Memory OS."""

    memory_id: str
    kind: str  # episodic | semantic | procedural | dialogue
    provider: str
    content_hash: str
    content_ref: str
    published_at: str | None
    retrieved_at: str
    expires_at: str | None = None
    evidence_eligible: bool = False
    run_id: str | None = None
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _now_iso()
        if not self.memory_id:
            self.memory_id = str(uuid4())


@dataclass
class MemoryLink:
    """A directional relationship between two memory entries."""

    link_id: str
    from_memory_id: str
    to_memory_id: str
    relation: str  # derived_from | supersedes | contradicts | supports
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _now_iso()
        if not self.link_id:
            self.link_id = str(uuid4())


# ─── Utility Functions ───────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_db_path() -> Path:
    """Return the SQLite DB path, controlled by env var."""
    return Path(os.getenv("TRUSTFORGE_MEMORY_DB", "data/memory_os.db"))


def memory_content_hash(content: dict[str, Any] | str) -> str:
    """Compute deterministic SHA-256 hash for memory content.

    Uses the same canonical JSON format as skills.py::canonical_json().
    """
    if isinstance(content, dict):
        payload = json.dumps(
            content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    else:
        payload = content
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ─── Evidence Eligibility Validation ─────────────────────────────────────────


def validate_evidence_eligible(entry: MemoryEntry) -> None:
    """Fail-closed validation before allowing evidence_eligible=true.

    Raises ValueError if any condition is not met.
    """
    errors: list[str] = []

    if not entry.provider:
        errors.append("provider is required for evidence eligibility")

    if not entry.published_at:
        errors.append("published_at is required for evidence eligibility")

    if not entry.retrieved_at:
        errors.append("retrieved_at is required for evidence eligibility")

    if not entry.content_hash or not _SHA256_PATTERN.match(entry.content_hash):
        errors.append("valid SHA-256 content_hash is required (64 hex chars)")

    if entry.kind == "dialogue":
        errors.append("dialogue memory cannot be evidence")

    # Historical conclusion guard: hermes-* provider + semantic kind
    if entry.kind == "semantic" and entry.provider.startswith("hermes-"):
        errors.append(
            "historical conclusions (hermes-* provider) cannot be evidence"
        )

    if errors:
        raise ValueError(
            f"evidence_eligible validation failed: {'; '.join(errors)}"
        )


# ─── Migration ───────────────────────────────────────────────────────────────


def upgrade(conn: sqlite3.Connection) -> None:
    """Create Memory OS tables (idempotent)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )

    current = _get_version(conn)
    if current >= _MIGRATION_VERSION:
        return

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS memory_entries (
            memory_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL CHECK (kind IN ('episodic','semantic','procedural','dialogue')),
            provider TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            content_ref TEXT NOT NULL,
            published_at TEXT,
            retrieved_at TEXT NOT NULL,
            expires_at TEXT,
            evidence_eligible INTEGER NOT NULL DEFAULT 0,
            run_id TEXT,
            created_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_provider_hash
            ON memory_entries(provider, content_hash);

        CREATE INDEX IF NOT EXISTS idx_memory_kind_eligible
            ON memory_entries(kind, evidence_eligible);

        CREATE INDEX IF NOT EXISTS idx_memory_run
            ON memory_entries(run_id);

        CREATE TABLE IF NOT EXISTS memory_links (
            link_id TEXT PRIMARY KEY,
            from_memory_id TEXT NOT NULL REFERENCES memory_entries(memory_id),
            to_memory_id TEXT NOT NULL REFERENCES memory_entries(memory_id),
            relation TEXT NOT NULL CHECK (relation IN ('derived_from','supersedes','contradicts','supports')),
            created_at TEXT NOT NULL,
            CHECK (from_memory_id != to_memory_id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_link_unique
            ON memory_links(from_memory_id, to_memory_id, relation);
    """)

    _set_version(conn, _MIGRATION_VERSION)
    conn.commit()


def rollback(conn: sqlite3.Connection) -> None:
    """Drop Memory OS tables."""
    conn.executescript("""
        DROP TABLE IF EXISTS memory_links;
        DROP TABLE IF EXISTS memory_entries;
    """)
    _set_version(conn, 0)
    conn.commit()


def _get_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "SELECT value FROM _meta WHERE key='memory_os_version'"
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('memory_os_version', ?)",
        (str(version),),
    )


# ─── Repository ──────────────────────────────────────────────────────────────


class MemoryRepository:
    """SQLite-backed repository for Memory OS entries and links."""

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

    def save(self, entry: MemoryEntry) -> None:
        """Insert a memory entry. Fails on duplicate (provider, content_hash).

        If evidence_eligible=True, validates eligibility first.
        """
        if entry.kind not in VALID_KINDS:
            raise ValueError(f"invalid memory kind: {entry.kind!r}")

        if entry.evidence_eligible:
            validate_evidence_eligible(entry)

        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO memory_entries
                   (memory_id, kind, provider, content_hash, content_ref,
                    published_at, retrieved_at, expires_at, evidence_eligible,
                    run_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.memory_id,
                    entry.kind,
                    entry.provider,
                    entry.content_hash,
                    entry.content_ref,
                    entry.published_at,
                    entry.retrieved_at,
                    entry.expires_at,
                    1 if entry.evidence_eligible else 0,
                    entry.run_id,
                    entry.created_at,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError as e:
            conn.rollback()
            raise sqlite3.IntegrityError(
                f"duplicate memory entry (provider={entry.provider!r}, "
                f"content_hash={entry.content_hash[:16]}...): {e}"
            ) from e

    def get(self, memory_id: str) -> MemoryEntry | None:
        """Retrieve a memory entry by ID."""
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM memory_entries WHERE memory_id = ?", (memory_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_entry(row)

    def find_by_kind(self, kind: str, *, limit: int = 100) -> list[MemoryEntry]:
        """Find memory entries by kind."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM memory_entries WHERE kind = ? ORDER BY created_at DESC LIMIT ?",
            (kind, limit),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def find_by_run(self, run_id: str) -> list[MemoryEntry]:
        """Find all memory entries produced by a specific run."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM memory_entries WHERE run_id = ? ORDER BY created_at",
            (run_id,),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def find_eligible_evidence(self, *, limit: int = 50) -> list[MemoryEntry]:
        """Find memory entries that are evidence-eligible."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM memory_entries WHERE evidence_eligible = 1 "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def link(self, from_id: str, to_id: str, relation: str) -> None:
        """Create a relationship link between two memory entries.

        Raises ValueError on self-link or invalid relation.
        Raises IntegrityError on duplicate link.
        """
        if from_id == to_id:
            raise ValueError("self-link is not allowed (from_id == to_id)")

        if relation not in VALID_RELATIONS:
            raise ValueError(
                f"invalid relation: {relation!r}; must be one of {sorted(VALID_RELATIONS)}"
            )

        conn = self._connect()
        link_id = str(uuid4())
        try:
            conn.execute(
                """INSERT INTO memory_links
                   (link_id, from_memory_id, to_memory_id, relation, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (link_id, from_id, to_id, relation, _now_iso()),
            )
            conn.commit()
        except sqlite3.IntegrityError as e:
            conn.rollback()
            raise sqlite3.IntegrityError(f"duplicate or invalid link: {e}") from e

    def get_links(self, memory_id: str) -> list[MemoryLink]:
        """Get all links where the given memory is either source or target."""
        conn = self._connect()
        rows = conn.execute(
            """SELECT link_id, from_memory_id, to_memory_id, relation, created_at
               FROM memory_links
               WHERE from_memory_id = ? OR to_memory_id = ?""",
            (memory_id, memory_id),
        ).fetchall()
        return [
            MemoryLink(
                link_id=r[0],
                from_memory_id=r[1],
                to_memory_id=r[2],
                relation=r[3],
                created_at=r[4],
            )
            for r in rows
        ]

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    @staticmethod
    def _row_to_entry(row: tuple) -> MemoryEntry:
        return MemoryEntry(
            memory_id=row[0],
            kind=row[1],
            provider=row[2],
            content_hash=row[3],
            content_ref=row[4],
            published_at=row[5],
            retrieved_at=row[6],
            expires_at=row[7],
            evidence_eligible=bool(row[8]),
            run_id=row[9],
            created_at=row[10],
        )
