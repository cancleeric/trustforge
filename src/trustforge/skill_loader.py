"""Task Skill Loader — discovery, frozen manifest and activation governance.

Provides runtime skill selection: discovery by trigger/family/risk/status,
dependency resolution with topological order, per-run frozen revision
manifests, stale detection, and proposal/approval governance for high-risk
skills.

Design principles:
  - Each run freezes exact revision hashes (immutable for that run)
  - Active pointer changes don't affect existing runs
  - High-risk skills require proposal → approval before activation
  - Stale/broken dependencies fail closed
  - Skill outputs cannot override Trust Kernel / security / cost / deploy

Contract: docs/contracts/TASK-SKILL-CONTRACT.md §7 Frozen Manifest
Issue: #920 | Epic: #914
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .skill_registry import (
    HIGH_RISK_CLASSES,
    SkillRegistryRepository,
    SkillRevision,
    TaskSkill,
)

# ─── Dataclasses ─────────────────────────────────────────────────────────────


@dataclass
class FrozenSkillEntry:
    """A single skill frozen into a run manifest."""

    skill_id: str
    revision_hash: str
    reason: str = ""


@dataclass
class FrozenSkillManifest:
    """Per-run frozen skill manifest — immutable after creation."""

    run_id: str
    entries: list[FrozenSkillEntry] = field(default_factory=list)
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "entries": [
                {"skill_id": e.skill_id, "revision_hash": e.revision_hash, "reason": e.reason}
                for e in self.entries
            ],
        }


@dataclass
class ActivationProposal:
    """A governance proposal for activating a high-risk skill."""

    proposal_id: str
    skill_id: str
    revision_hash: str
    reason: str
    proposed_at: str = ""
    status: str = "pending"  # pending | approved | rejected
    decided_by: str | None = None
    decided_at: str | None = None

    def __post_init__(self) -> None:
        if not self.proposal_id:
            self.proposal_id = str(uuid4())
        if not self.proposed_at:
            self.proposed_at = _now_iso()


# ─── Utility ─────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── Skill Loader ────────────────────────────────────────────────────────────


class SkillLoader:
    """Runtime skill loader with discovery, resolution, freeze and governance."""

    def __init__(self, registry: SkillRegistryRepository) -> None:
        self._registry = registry
        self._ensure_loader_tables()

    def _ensure_loader_tables(self) -> None:
        """Create loader-specific tables (frozen manifests, proposals)."""
        conn = self._registry._connect()
        conn.executescript("""
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
        conn.commit()

    # ─── Discovery ───────────────────────────────────────────────────────

    def discover(
        self,
        *,
        trigger: str | None = None,
        family: str | None = None,
        risk_class: str | None = None,
        lifecycle: str = "active",
    ) -> list[TaskSkill]:
        """Find skills matching criteria. Only active lifecycle by default."""
        skills = self._registry.list_skills(family=family, lifecycle=lifecycle)
        if risk_class:
            skills = [s for s in skills if s.risk_class == risk_class]
        # trigger filtering is extensible — for MVP just return filtered list
        return skills

    # ─── Dependency Resolution ───────────────────────────────────────────

    def resolve_dependencies(self, skill_id: str) -> list[SkillRevision]:
        """Resolve all transitive `requires` dependencies in topological order.

        Returns leaf-first order (dependencies before dependents).
        Raises ValueError if any dependency is stale (no active revision).
        """
        visited: set[str] = set()
        order: list[SkillRevision] = []
        self._dfs_resolve(skill_id, visited, order)
        return order

    def _dfs_resolve(
        self, skill_id: str, visited: set[str], order: list[SkillRevision]
    ) -> None:
        if skill_id in visited:
            return
        visited.add(skill_id)

        deps = self._registry.get_dependencies(skill_id)
        for dep in deps:
            if dep.relation == "requires":
                self._dfs_resolve(dep.to_skill_id, visited, order)

        rev = self._registry.get_active_revision(skill_id)
        if rev is None:
            raise ValueError(
                f"stale dependency: skill '{skill_id}' has no active revision"
            )
        order.append(rev)

    # ─── Stale Detection ─────────────────────────────────────────────────

    def is_stale(self, skill_id: str) -> bool:
        """Check if a skill is stale (cannot be selected)."""
        skill = self._registry.get_skill(skill_id)
        if skill is None:
            return True
        if skill.lifecycle in ("frozen", "retired"):
            return True
        if self._registry.get_active_revision(skill_id) is None:
            return True
        # Check transitive deps
        deps = self._registry.get_dependencies(skill_id)
        for dep in deps:
            if dep.relation == "requires" and self.is_stale(dep.to_skill_id):
                return True
        return False

    # ─── Frozen Manifest ─────────────────────────────────────────────────

    def freeze_manifest(
        self,
        run_id: str,
        skill_ids: list[str],
        reasons: dict[str, str] | None = None,
    ) -> FrozenSkillManifest:
        """Freeze selected skills + all transitive deps for a run.

        Raises ValueError if any skill is stale or high-risk without approval.
        """
        reasons = reasons or {}
        entries: list[FrozenSkillEntry] = []
        seen: set[str] = set()

        for sid in skill_ids:
            if self.is_stale(sid):
                raise ValueError(f"cannot freeze stale skill: {sid!r}")

            # Check high-risk approval
            skill = self._registry.get_skill(sid)
            if skill and skill.risk_class in HIGH_RISK_CLASSES:
                rev = self._registry.get_active_revision(sid)
                if rev and not self.is_activation_approved(sid, rev.revision_hash):
                    raise ValueError(
                        f"high-risk skill '{sid}' requires activation approval "
                        f"before freezing into manifest"
                    )

            revs = self.resolve_dependencies(sid)
            for rev in revs:
                if rev.skill_id not in seen:
                    seen.add(rev.skill_id)
                    # Check transitive high-risk approval (every dep, not just top-level)
                    dep_skill = self._registry.get_skill(rev.skill_id)
                    if dep_skill and dep_skill.risk_class in HIGH_RISK_CLASSES:
                        if not self.is_activation_approved(rev.skill_id, rev.revision_hash):
                            raise ValueError(
                                f"transitive high-risk dependency '{rev.skill_id}' "
                                f"(risk_class={dep_skill.risk_class}) requires "
                                f"activation approval before freezing"
                            )
                        if not self._sandbox_verified(rev.skill_id, rev.revision_hash):
                            raise ValueError(
                                f"high-risk skill '{rev.skill_id}' must pass sandbox "
                                f"verification before freezing into manifest"
                            )
                    entries.append(
                        FrozenSkillEntry(
                            skill_id=rev.skill_id,
                            revision_hash=rev.revision_hash,
                            reason=reasons.get(rev.skill_id, "transitive_dependency"),
                        )
                    )

        manifest = FrozenSkillManifest(run_id=run_id, entries=entries)
        self._persist_manifest(manifest)
        return manifest

    def get_frozen_manifest(self, run_id: str) -> FrozenSkillManifest | None:
        """Retrieve frozen manifest for a run."""
        conn = self._registry._connect()
        rows = conn.execute(
            "SELECT skill_id, revision_hash, reason, created_at "
            "FROM frozen_skill_manifests WHERE run_id = ? ORDER BY rowid",
            (run_id,),
        ).fetchall()
        if not rows:
            return None
        entries = [
            FrozenSkillEntry(skill_id=r[0], revision_hash=r[1], reason=r[2])
            for r in rows
        ]
        created_at = rows[0][3] if rows else _now_iso()
        return FrozenSkillManifest(run_id=run_id, entries=entries, created_at=created_at)

    def _persist_manifest(self, manifest: FrozenSkillManifest) -> None:
        conn = self._registry._connect()
        for entry in manifest.entries:
            conn.execute(
                """INSERT OR IGNORE INTO frozen_skill_manifests
                   (run_id, skill_id, revision_hash, reason, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    manifest.run_id,
                    entry.skill_id,
                    entry.revision_hash,
                    entry.reason,
                    manifest.created_at,
                ),
            )
        conn.commit()

    # ─── Activation Governance ───────────────────────────────────────────

    def propose_activation(
        self, skill_id: str, revision_hash: str, reason: str
    ) -> ActivationProposal:
        """Create activation proposal for a high-risk skill.

        Only high-risk skills (external_write / deploy_or_release) need proposals.
        """
        skill = self._registry.get_skill(skill_id)
        if skill is None:
            raise ValueError(f"skill not found: {skill_id!r}")
        if skill.risk_class not in HIGH_RISK_CLASSES:
            raise ValueError(
                f"skill '{skill_id}' is not high-risk (risk_class={skill.risk_class!r}); "
                f"only external_write/deploy_or_release require activation proposals"
            )

        proposal = ActivationProposal(
            proposal_id=str(uuid4()),
            skill_id=skill_id,
            revision_hash=revision_hash,
            reason=reason,
        )

        conn = self._registry._connect()
        conn.execute(
            """INSERT INTO activation_proposals
               (proposal_id, skill_id, revision_hash, reason, proposed_at, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                proposal.proposal_id,
                proposal.skill_id,
                proposal.revision_hash,
                proposal.reason,
                proposal.proposed_at,
                proposal.status,
            ),
        )
        conn.commit()
        return proposal

    def approve_activation(self, proposal_id: str, approved_by: str, *, sandbox_passed: bool = False) -> None:
        """Approve an activation proposal.

        sandbox_passed must be True for high-risk skills — the sandbox gate
        is mandatory, not optional.
        """
        conn = self._registry._connect()
        row = conn.execute(
            "SELECT status FROM activation_proposals WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"proposal not found: {proposal_id!r}")
        if row[0] != "pending":
            raise ValueError(f"proposal is already {row[0]}")

        if not sandbox_passed:
            raise ValueError(
                "sandbox_passed=True is required to approve high-risk activation; "
                "skill must pass sandbox dry-run before approval"
            )

        conn.execute(
            "UPDATE activation_proposals SET status='approved', sandbox_passed=1, "
            "decided_by=?, decided_at=? WHERE proposal_id = ?",
            (approved_by, _now_iso(), proposal_id),
        )
        conn.commit()

    def reject_activation(
        self, proposal_id: str, rejected_by: str, reason: str = ""
    ) -> None:
        """Reject an activation proposal."""
        conn = self._registry._connect()
        row = conn.execute(
            "SELECT status FROM activation_proposals WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"proposal not found: {proposal_id!r}")
        if row[0] != "pending":
            raise ValueError(f"proposal is already {row[0]}")

        conn.execute(
            "UPDATE activation_proposals SET status='rejected', decided_by=?, decided_at=? "
            "WHERE proposal_id = ?",
            (rejected_by, _now_iso(), proposal_id),
        )
        conn.commit()

    def is_activation_approved(self, skill_id: str, revision_hash: str) -> bool:
        """Check if a specific revision has been approved for activation."""
        conn = self._registry._connect()
        row = conn.execute(
            "SELECT 1 FROM activation_proposals "
            "WHERE skill_id = ? AND revision_hash = ? AND status = 'approved'",
            (skill_id, revision_hash),
        ).fetchone()
        return row is not None

    def _sandbox_verified(self, skill_id: str, revision_hash: str) -> bool:
        """Check if a high-risk skill revision has passed sandbox verification.

        Sandbox record is written when propose_activation is approved AND
        sandbox_passed is explicitly set. Without sandbox evidence, the skill
        cannot be frozen (fail-closed).
        """
        conn = self._registry._connect()
        row = conn.execute(
            "SELECT sandbox_passed FROM activation_proposals "
            "WHERE skill_id = ? AND revision_hash = ? AND status = 'approved' "
            "AND sandbox_passed = 1",
            (skill_id, revision_hash),
        ).fetchone()
        return row is not None
