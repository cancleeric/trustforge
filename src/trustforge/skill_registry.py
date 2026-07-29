"""Task Skill Registry — persistence for skills, immutable revisions and dependencies.

Provides repo-local task skill metadata, content-addressed immutable revisions,
dependency graph with cycle detection, and lifecycle governance.

Coexists with existing outer-policy family system (skills.py) without modification.

Design principles:
  - Revision content is immutable and content-addressed (SHA-256)
  - High-risk skills cannot skip staged lifecycle
  - Self-cycle and transitive cycles are rejected (fail-closed)
  - Zero third-party dependencies (stdlib sqlite3 only)

Contract: docs/contracts/TASK-SKILL-CONTRACT.md
Issue: #917 | Epic: #914
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

VALID_FAMILIES = frozenset({"source", "analysis", "report", "evaluation", "improvement"})

VALID_RISK_CLASSES = frozenset({"read_only", "local_write", "external_write", "deploy_or_release"})

VALID_LIFECYCLES = frozenset({"draft", "staged", "active", "frozen", "retired"})

VALID_RELATIONS = frozenset({"requires", "optional", "conflicts"})

HIGH_RISK_CLASSES = frozenset({"external_write", "deploy_or_release"})

_MIGRATION_VERSION = 2

_MAX_CYCLE_DEPTH = 10


# ─── Dataclasses ─────────────────────────────────────────────────────────────


@dataclass
class TaskSkill:
    """A task skill definition."""

    skill_id: str
    family: str
    name: str
    description: str = ""
    risk_class: str = "read_only"
    side_effect_class: str = ""
    verification_preconditions: list[str] | None = None
    verification_postconditions: list[str] | None = None
    lifecycle: str = "draft"
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _now_iso()
        if self.verification_preconditions is None:
            self.verification_preconditions = []
        if self.verification_postconditions is None:
            self.verification_postconditions = []


@dataclass
class SkillRevision:
    """An immutable, content-addressed skill revision."""

    revision_hash: str
    skill_id: str
    content: dict[str, Any]
    is_active: bool = False
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _now_iso()


@dataclass
class SkillDependency:
    """A directional dependency edge between two skills."""

    from_skill_id: str
    to_skill_id: str
    relation: str  # requires | optional | conflicts
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _now_iso()


# ─── Utility Functions ───────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_db_path() -> Path:
    """Return the SQLite DB path, controlled by env var."""
    return Path(os.getenv("TRUSTFORGE_SKILL_REGISTRY_DB", "data/skill_registry.db"))


def revision_hash_for(content: dict[str, Any]) -> str:
    """Calculate deterministic SHA-256 hash for skill revision content."""
    payload = json.dumps(
        content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ─── Migration ───────────────────────────────────────────────────────────────


def _upgrade(conn: sqlite3.Connection) -> None:
    """Create Skill Registry tables (idempotent).

    Authorization is checked here so direct callers cannot bypass the guard.
    """
    current = _get_version(conn)
    if current >= _MIGRATION_VERSION:
        return

    from .agos_db_auth import AGOS_SCHEMA_AUTH_PURPOSE, verify_db_authorization

    verify_db_authorization(AGOS_SCHEMA_AUTH_PURPOSE)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS skills (
            skill_id TEXT PRIMARY KEY,
            family TEXT NOT NULL CHECK (family IN ('source','analysis','report','evaluation','improvement')),
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            risk_class TEXT NOT NULL DEFAULT 'read_only'
                CHECK (risk_class IN ('read_only','local_write','external_write','deploy_or_release')),
            side_effect_class TEXT NOT NULL DEFAULT '',
            verification_preconditions TEXT NOT NULL DEFAULT '[]',
            verification_postconditions TEXT NOT NULL DEFAULT '[]',
            lifecycle TEXT NOT NULL DEFAULT 'draft'
                CHECK (lifecycle IN ('draft','staged','active','frozen','retired')),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS skill_revisions (
            revision_hash TEXT PRIMARY KEY,
            skill_id TEXT NOT NULL REFERENCES skills(skill_id),
            content TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_active
            ON skill_revisions(skill_id) WHERE is_active = 1;

        CREATE TABLE IF NOT EXISTS skill_dependencies (
            from_skill_id TEXT NOT NULL REFERENCES skills(skill_id),
            to_skill_id TEXT NOT NULL REFERENCES skills(skill_id),
            relation TEXT NOT NULL CHECK (relation IN ('requires','optional','conflicts')),
            created_at TEXT NOT NULL,
            PRIMARY KEY (from_skill_id, to_skill_id, relation),
            CHECK (from_skill_id != to_skill_id)
        );

        CREATE TABLE IF NOT EXISTS frozen_skill_manifests (
            run_id TEXT NOT NULL,
            skill_id TEXT NOT NULL,
            revision_hash TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, skill_id)
        );

        CREATE TABLE IF NOT EXISTS activation_proposals (
            proposal_id TEXT PRIMARY KEY,
            skill_id TEXT NOT NULL,
            revision_hash TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            proposed_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','approved','rejected')),
            sandbox_passed INTEGER NOT NULL DEFAULT 0,
            decided_by TEXT,
            decided_at TEXT
        );
    """)

    _set_version(conn, _MIGRATION_VERSION)
    conn.commit()


def rollback(conn: sqlite3.Connection) -> None:
    """Drop Skill Registry tables."""
    from .agos_db_auth import AGOS_SCHEMA_AUTH_PURPOSE, verify_db_authorization

    verify_db_authorization(AGOS_SCHEMA_AUTH_PURPOSE)
    conn.executescript("""
        DROP TABLE IF EXISTS activation_proposals;
        DROP TABLE IF EXISTS frozen_skill_manifests;
        DROP TABLE IF EXISTS skill_dependencies;
        DROP TABLE IF EXISTS skill_revisions;
        DROP TABLE IF EXISTS skills;
    """)
    _set_version(conn, 0)
    conn.commit()


def _get_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "SELECT value FROM _meta WHERE key='skill_registry_version'"
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('skill_registry_version', ?)",
        (str(version),),
    )


# ─── Repository ──────────────────────────────────────────────────────────────


class SkillRegistryRepository:
    """SQLite-backed repository for Task Skills, Revisions, and Dependencies."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or default_db_path()
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            if not self._db_path.exists():
                from .agos_db_auth import (
                    AGOS_SCHEMA_AUTH_PURPOSE,
                    verify_db_authorization,
                )
                verify_db_authorization(AGOS_SCHEMA_AUTH_PURPOSE)
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def ensure_schema(self) -> None:
        """Create tables if they don't exist."""
        _upgrade(self._connect())

    # ─── Skill CRUD ──────────────────────────────────────────────────────

    def save_skill(self, skill: TaskSkill) -> None:
        """Insert a new skill. Validates family, risk_class, lifecycle."""
        if skill.family not in VALID_FAMILIES:
            raise ValueError(f"invalid family: {skill.family!r}")
        if skill.risk_class not in VALID_RISK_CLASSES:
            raise ValueError(f"invalid risk_class: {skill.risk_class!r}")
        if skill.lifecycle not in VALID_LIFECYCLES:
            raise ValueError(f"invalid lifecycle: {skill.lifecycle!r}")

        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO skills
                   (skill_id, family, name, description, risk_class,
                    side_effect_class, verification_preconditions,
                    verification_postconditions, lifecycle, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    skill.skill_id,
                    skill.family,
                    skill.name,
                    skill.description,
                    skill.risk_class,
                    skill.side_effect_class,
                    json.dumps(skill.verification_preconditions or []),
                    json.dumps(skill.verification_postconditions or []),
                    skill.lifecycle,
                    skill.created_at,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError as e:
            conn.rollback()
            raise sqlite3.IntegrityError(
                f"skill_id '{skill.skill_id}' already exists: {e}"
            ) from e

    def get_skill(self, skill_id: str) -> TaskSkill | None:
        """Retrieve a skill by ID."""
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM skills WHERE skill_id = ?", (skill_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_skill(row)

    def list_skills(
        self, *, family: str | None = None, lifecycle: str | None = None
    ) -> list[TaskSkill]:
        """List skills with optional filtering."""
        conn = self._connect()
        query = "SELECT * FROM skills WHERE 1=1"
        params: list[str] = []
        if family:
            query += " AND family = ?"
            params.append(family)
        if lifecycle:
            query += " AND lifecycle = ?"
            params.append(lifecycle)
        query += " ORDER BY created_at"
        rows = conn.execute(query, params).fetchall()
        return [self._row_to_skill(r) for r in rows]

    def update_lifecycle(self, skill_id: str, new_lifecycle: str) -> None:
        """Update skill lifecycle with validation.

        High-risk skills cannot skip staged → active.
        """
        if new_lifecycle not in VALID_LIFECYCLES:
            raise ValueError(f"invalid lifecycle: {new_lifecycle!r}")

        skill = self.get_skill(skill_id)
        if skill is None:
            raise ValueError(f"skill not found: {skill_id!r}")

        # High-risk lifecycle guard
        if (
            skill.risk_class in HIGH_RISK_CLASSES
            and skill.lifecycle == "draft"
            and new_lifecycle == "active"
        ):
            raise ValueError(
                f"high-risk skill '{skill_id}' cannot skip staged; "
                f"must transition draft → staged → active"
            )

        conn = self._connect()
        conn.execute(
            "UPDATE skills SET lifecycle = ? WHERE skill_id = ?",
            (new_lifecycle, skill_id),
        )
        conn.commit()

    # ─── Revision CRUD ───────────────────────────────────────────────────

    def save_revision(self, revision: SkillRevision) -> None:
        """Save an immutable skill revision. Content-addressed by hash.

        - Same hash + same content → no-op (idempotent)
        - Same hash + different content → hash collision error
        - New hash → insert
        """
        expected_hash = revision_hash_for(revision.content)
        if revision.revision_hash != expected_hash:
            raise ValueError(
                f"revision_hash mismatch: declared={revision.revision_hash[:16]}... "
                f"computed={expected_hash[:16]}..."
            )

        conn = self._connect()
        existing = conn.execute(
            "SELECT content FROM skill_revisions WHERE revision_hash = ?",
            (revision.revision_hash,),
        ).fetchone()

        if existing is not None:
            existing_content = json.loads(existing[0])
            if existing_content == revision.content:
                return  # Idempotent: same hash, same content
            raise ValueError(
                f"hash collision: revision_hash={revision.revision_hash[:16]}... "
                f"already exists with different content"
            )

        try:
            conn.execute(
                """INSERT INTO skill_revisions
                   (revision_hash, skill_id, content, is_active, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    revision.revision_hash,
                    revision.skill_id,
                    json.dumps(revision.content, ensure_ascii=False, sort_keys=True),
                    1 if revision.is_active else 0,
                    revision.created_at,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError as e:
            conn.rollback()
            raise sqlite3.IntegrityError(f"revision save failed: {e}") from e

    def get_revision(self, revision_hash: str) -> SkillRevision | None:
        """Retrieve a revision by hash."""
        conn = self._connect()
        row = conn.execute(
            "SELECT revision_hash, skill_id, content, is_active, created_at "
            "FROM skill_revisions WHERE revision_hash = ?",
            (revision_hash,),
        ).fetchone()
        if row is None:
            return None
        return SkillRevision(
            revision_hash=row[0],
            skill_id=row[1],
            content=json.loads(row[2]),
            is_active=bool(row[3]),
            created_at=row[4],
        )

    def get_active_revision(self, skill_id: str) -> SkillRevision | None:
        """Get the active revision for a skill (at most one)."""
        conn = self._connect()
        row = conn.execute(
            "SELECT revision_hash, skill_id, content, is_active, created_at "
            "FROM skill_revisions WHERE skill_id = ? AND is_active = 1",
            (skill_id,),
        ).fetchone()
        if row is None:
            return None
        return SkillRevision(
            revision_hash=row[0],
            skill_id=row[1],
            content=json.loads(row[2]),
            is_active=True,
            created_at=row[4],
        )

    def set_active(self, skill_id: str, revision_hash: str) -> None:
        """Switch the active revision for a skill.

        Transaction: unset old active → set new active.
        Validates that revision exists and belongs to skill.
        """
        conn = self._connect()

        # Verify revision exists and belongs to this skill
        row = conn.execute(
            "SELECT skill_id FROM skill_revisions WHERE revision_hash = ?",
            (revision_hash,),
        ).fetchone()
        if row is None:
            raise ValueError(f"revision not found: {revision_hash[:16]}...")
        if row[0] != skill_id:
            raise ValueError(
                f"revision {revision_hash[:16]}... belongs to skill '{row[0]}', "
                f"not '{skill_id}'"
            )

        # Atomic switch
        conn.execute(
            "UPDATE skill_revisions SET is_active = 0 WHERE skill_id = ? AND is_active = 1",
            (skill_id,),
        )
        conn.execute(
            "UPDATE skill_revisions SET is_active = 1 WHERE revision_hash = ?",
            (revision_hash,),
        )
        conn.commit()

    # ─── Dependency Management ───────────────────────────────────────────

    def add_dependency(self, dep: SkillDependency) -> None:
        """Add a dependency edge. Checks for self-cycle and transitive cycles."""
        if dep.from_skill_id == dep.to_skill_id:
            raise ValueError(
                f"self-cycle not allowed: {dep.from_skill_id!r} → {dep.to_skill_id!r}"
            )
        if dep.relation not in VALID_RELATIONS:
            raise ValueError(f"invalid relation: {dep.relation!r}")

        # Cycle detection (only for 'requires' edges)
        if dep.relation == "requires":
            if self._has_cycle(dep.to_skill_id, dep.from_skill_id, set(), 0):
                raise ValueError(
                    f"transitive cycle detected: adding {dep.from_skill_id!r} → "
                    f"{dep.to_skill_id!r} would create a cycle"
                )

        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO skill_dependencies
                   (from_skill_id, to_skill_id, relation, created_at)
                   VALUES (?, ?, ?, ?)""",
                (dep.from_skill_id, dep.to_skill_id, dep.relation, dep.created_at),
            )
            conn.commit()
        except sqlite3.IntegrityError as e:
            conn.rollback()
            raise sqlite3.IntegrityError(f"dependency already exists: {e}") from e

    def get_dependencies(self, skill_id: str) -> list[SkillDependency]:
        """Get all dependencies OF a skill (outgoing edges)."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT from_skill_id, to_skill_id, relation, created_at "
            "FROM skill_dependencies WHERE from_skill_id = ?",
            (skill_id,),
        ).fetchall()
        return [
            SkillDependency(
                from_skill_id=r[0], to_skill_id=r[1], relation=r[2], created_at=r[3]
            )
            for r in rows
        ]

    def get_dependents(self, skill_id: str) -> list[SkillDependency]:
        """Get all skills that depend ON this skill (incoming edges)."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT from_skill_id, to_skill_id, relation, created_at "
            "FROM skill_dependencies WHERE to_skill_id = ?",
            (skill_id,),
        ).fetchall()
        return [
            SkillDependency(
                from_skill_id=r[0], to_skill_id=r[1], relation=r[2], created_at=r[3]
            )
            for r in rows
        ]

    def _has_cycle(
        self, current: str, target: str, visited: set[str], depth: int
    ) -> bool:
        """DFS cycle detection on 'requires' edges."""
        if depth > _MAX_CYCLE_DEPTH:
            return True  # Too deep, treat as cycle
        if current == target:
            return True
        if current in visited:
            return False
        visited.add(current)

        conn = self._connect()
        rows = conn.execute(
            "SELECT to_skill_id FROM skill_dependencies "
            "WHERE from_skill_id = ? AND relation = 'requires'",
            (current,),
        ).fetchall()

        for (next_id,) in rows:
            if self._has_cycle(next_id, target, visited, depth + 1):
                return True
        return False

    # ─── Helpers ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    @staticmethod
    def _row_to_skill(row: tuple) -> TaskSkill:
        return TaskSkill(
            skill_id=row[0],
            family=row[1],
            name=row[2],
            description=row[3],
            risk_class=row[4],
            side_effect_class=row[5],
            verification_preconditions=json.loads(row[6]),
            verification_postconditions=json.loads(row[7]),
            lifecycle=row[8],
            created_at=row[9],
        )
