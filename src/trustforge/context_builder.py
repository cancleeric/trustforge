"""Context Builder — produces deterministic, immutable per-run context manifests.

Freezes snapshot, question, memory, skill, tool and policy references into
one manifest per analysis run, recording exclusions with reasons. Once created,
the manifest is never modified.

Design principles:
  - One manifest per run (immutable after creation)
  - Deterministic content_hash (same input → same hash)
  - Evidence-ineligible memory excluded from scoring inputs
  - Exclusion reasons tracked for Admin disclosure
  - Zero third-party dependencies

Contract: docs/contracts/CONTEXT-MANIFEST-CONTRACT.md
Issue: #921 | Epic: #914
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .memory_os import MemoryEntry, MemoryRepository
from .memory_retrieval import MemoryRef
from .skill_loader import FrozenSkillManifest, SkillLoader
from .tool_registry import ToolRegistryRepository

# ─── Constants ───────────────────────────────────────────────────────────────

EXCLUSION_STALE = "stale"
EXCLUSION_OVER_BUDGET = "over_budget"
EXCLUSION_APPROVAL_REQUIRED = "approval_required"
EXCLUSION_EVIDENCE_INELIGIBLE = "evidence_ineligible"


# ─── Dataclasses ─────────────────────────────────────────────────────────────


@dataclass
class ExcludedRef:
    """A reference excluded from the manifest with reason."""

    ref_id: str
    ref_type: str  # memory | skill | tool | policy
    reason: str  # stale | over_budget | approval_required | evidence_ineligible

    def to_dict(self) -> dict[str, str]:
        return {"ref_id": self.ref_id, "ref_type": self.ref_type, "reason": self.reason}


@dataclass
class IncludedRefs:
    """All references included in the manifest."""

    snapshot_ref: str | None = None
    question_ref: str | None = None
    memory_refs: list[dict[str, Any]] = field(default_factory=list)
    skill_refs: list[dict[str, Any]] = field(default_factory=list)
    tool_refs: list[dict[str, Any]] = field(default_factory=list)
    policy_refs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_ref": self.snapshot_ref,
            "question_ref": self.question_ref,
            "memory_refs": self.memory_refs,
            "skill_refs": self.skill_refs,
            "tool_refs": self.tool_refs,
            "policy_refs": self.policy_refs,
        }


@dataclass
class ContextManifest:
    """An immutable per-run context manifest."""

    manifest_id: str
    run_id: str
    created_at: str
    content_hash: str
    token_budget: int
    token_used: int
    included_refs: IncludedRefs
    excluded_refs: list[ExcludedRef] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "content_hash": self.content_hash,
            "token_budget": self.token_budget,
            "token_used": self.token_used,
            "included_refs": self.included_refs.to_dict(),
            "excluded_refs": [e.to_dict() for e in self.excluded_refs],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextManifest:
        included = IncludedRefs(**data["included_refs"])
        excluded = [ExcludedRef(**e) for e in data.get("excluded_refs", [])]
        return cls(
            manifest_id=data["manifest_id"],
            run_id=data["run_id"],
            created_at=data["created_at"],
            content_hash=data["content_hash"],
            token_budget=data["token_budget"],
            token_used=data["token_used"],
            included_refs=included,
            excluded_refs=excluded,
        )


# ─── Utility Functions ───────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_db_path() -> Path:
    return Path(os.getenv("TRUSTFORGE_CONTEXT_DB", "data/context_manifests.db"))


def estimate_tokens(text: str) -> int:
    """Simple token estimation (no external tokenizer dependency).

    ~4 chars per token for ASCII, ~2 chars per token for CJK.
    """
    if not text:
        return 0
    cjk_count = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    ascii_count = len(text) - cjk_count
    return max(1, (ascii_count // 4) + (cjk_count // 2))


def compute_manifest_hash(
    run_id: str,
    included: IncludedRefs,
    excluded: list[ExcludedRef],
    token_budget: int,
    token_used: int,
) -> str:
    """Compute deterministic SHA-256 hash for a context manifest."""
    payload = {
        "run_id": run_id,
        "included_refs": included.to_dict(),
        "excluded_refs": [e.to_dict() for e in excluded],
        "token_budget": token_budget,
        "token_used": token_used,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ─── Manifest Summary ────────────────────────────────────────────────────────


def manifest_summary(manifest: ContextManifest) -> dict[str, Any]:
    """Summary for report/admin display (not Evidence)."""
    reason_counts: dict[str, int] = {}
    for e in manifest.excluded_refs:
        reason_counts[e.reason] = reason_counts.get(e.reason, 0) + 1

    included_count = (
        len(manifest.included_refs.memory_refs)
        + len(manifest.included_refs.skill_refs)
        + len(manifest.included_refs.tool_refs)
        + len(manifest.included_refs.policy_refs)
        + (1 if manifest.included_refs.snapshot_ref else 0)
        + (1 if manifest.included_refs.question_ref else 0)
    )

    return {
        "included_count": included_count,
        "excluded_count": len(manifest.excluded_refs),
        "token_budget": manifest.token_budget,
        "token_used": manifest.token_used,
        "token_used_pct": round(
            (manifest.token_used / manifest.token_budget * 100)
            if manifest.token_budget > 0
            else 0,
            1,
        ),
        "exclusion_reasons": reason_counts,
    }


def get_evidence_eligible_memories(manifest: ContextManifest) -> list[dict[str, Any]]:
    """Filter included memory_refs to only those that are evidence-eligible."""
    return [
        m for m in manifest.included_refs.memory_refs if m.get("evidence_eligible", False)
    ]


# ─── Persistence ─────────────────────────────────────────────────────────────


def _ensure_manifest_table(conn: sqlite3.Connection) -> None:
    existing = conn.execute(
        """SELECT 1 FROM sqlite_master
           WHERE type = 'table' AND name = 'context_manifests'"""
    ).fetchone()
    if existing is not None:
        return

    from .agos_db_auth import AGOS_SCHEMA_AUTH_PURPOSE, verify_db_authorization

    verify_db_authorization(AGOS_SCHEMA_AUTH_PURPOSE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS context_manifests (
            manifest_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL UNIQUE,
            content_hash TEXT NOT NULL,
            token_budget INTEGER NOT NULL,
            token_used INTEGER NOT NULL,
            included_refs TEXT NOT NULL,
            excluded_refs TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()


# ─── Context Builder ─────────────────────────────────────────────────────────


class ContextBuilder:
    """Builds deterministic, immutable per-run context manifests."""

    def __init__(
        self,
        *,
        memory_repo: MemoryRepository | None = None,
        skill_loader: SkillLoader | None = None,
        tool_registry: ToolRegistryRepository | None = None,
        db_path: Path | None = None,
    ) -> None:
        self._memory_repo = memory_repo
        self._skill_loader = skill_loader
        self._tool_registry = tool_registry
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
            _ensure_manifest_table(self._conn)
        return self._conn

    def build(
        self,
        *,
        run_id: str,
        snapshot_ref: str | None = None,
        question_ref: str | None = None,
        memory_refs: list[MemoryRef] | None = None,
        skill_manifest: FrozenSkillManifest | None = None,
        tool_refs: list[str] | None = None,
        policy_refs: list[dict[str, Any]] | None = None,
        token_budget: int = 4096,
    ) -> ContextManifest:
        """Build an immutable context manifest for a run.

        Processes references, applies exclusion logic, computes deterministic hash.
        """
        included = IncludedRefs()
        excluded: list[ExcludedRef] = []
        token_used = 0

        if snapshot_ref is not None:
            snapshot_cost = estimate_tokens(snapshot_ref)
            if snapshot_cost > token_budget:
                excluded.append(
                    ExcludedRef(
                        snapshot_ref, "snapshot", EXCLUSION_OVER_BUDGET
                    )
                )
            else:
                included.snapshot_ref = snapshot_ref
                token_used += snapshot_cost

        if question_ref is not None:
            question_cost = estimate_tokens(question_ref)
            if token_used + question_cost > token_budget:
                excluded.append(
                    ExcludedRef("question", "question", EXCLUSION_OVER_BUDGET)
                )
            else:
                included.question_ref = question_ref
                token_used += question_cost

        # 1. Process memory refs
        for mref in (memory_refs or []):
            # Re-verify evidence_eligible from DB (never trust caller flag)
            actual_eligible = False
            frozen_content_hash: str | None = None
            if self._memory_repo:
                entry = self._memory_repo.get(mref.memory_id)
                if entry is None:
                    # Memory not in DB — exclude as stale
                    excluded.append(ExcludedRef(mref.memory_id, "memory", EXCLUSION_STALE))
                    continue
                actual_eligible = entry.evidence_eligible
                # Check stale (expired)
                if entry.expires_at:
                    try:
                        exp_dt = datetime.fromisoformat(entry.expires_at.replace("Z", "+00:00"))
                        if exp_dt < datetime.now(timezone.utc):
                            excluded.append(ExcludedRef(mref.memory_id, "memory", EXCLUSION_STALE))
                            continue
                    except (ValueError, TypeError):
                        excluded.append(
                            ExcludedRef(mref.memory_id, "memory", EXCLUSION_STALE)
                        )
                        continue
                frozen_content_hash = entry.content_hash
            else:
                # No repo available — cannot verify, default to non-evidentiary
                actual_eligible = False

            # Token budget check
            token_cost = estimate_tokens(mref.content_preview)
            if token_used + token_cost > token_budget:
                excluded.append(ExcludedRef(mref.memory_id, "memory", EXCLUSION_OVER_BUDGET))
                continue

            token_used += token_cost
            included.memory_refs.append({
                "memory_id": mref.memory_id,
                "kind": mref.kind,
                "rank": mref.rank,
                "reason": mref.reason,
                "evidence_eligible": actual_eligible,  # DB-verified, not caller-supplied
                # Freeze the content identity, not only the mutable lookup key.
                "content_hash": frozen_content_hash,
            })

        # 2. Process skill manifest
        if skill_manifest:
            for entry in skill_manifest.entries:
                # Check stale via loader
                if self._skill_loader and self._skill_loader.is_stale(entry.skill_id):
                    excluded.append(ExcludedRef(entry.skill_id, "skill", EXCLUSION_STALE))
                    continue

                skill_ref = {
                    "skill_id": entry.skill_id,
                    "revision_hash": entry.revision_hash,
                    "reason": entry.reason,
                }
                skill_cost = estimate_tokens(
                    json.dumps(
                        skill_ref,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
                if token_used + skill_cost > token_budget:
                    excluded.append(
                        ExcludedRef(
                            entry.skill_id, "skill", EXCLUSION_OVER_BUDGET
                        )
                    )
                    continue
                included.skill_refs.append(skill_ref)
                token_used += skill_cost

        # 3. Process tool refs
        for tool_id in (tool_refs or []):
            if self._tool_registry is None:
                excluded.append(ExcludedRef(tool_id, "tool", EXCLUSION_STALE))
                continue
            if not self._tool_registry.is_known(tool_id):
                excluded.append(ExcludedRef(tool_id, "tool", EXCLUSION_STALE))
                continue
            if self._tool_registry.requires_approval(tool_id):
                excluded.append(ExcludedRef(tool_id, "tool", EXCLUSION_APPROVAL_REQUIRED))
                continue

            capability = self._tool_registry.get_tool(tool_id)
            if capability is None:
                excluded.append(ExcludedRef(tool_id, "tool", EXCLUSION_STALE))
                continue
            tool_ref = {"tool_id": tool_id, "version": capability.version}
            tool_cost = estimate_tokens(
                json.dumps(tool_ref, sort_keys=True, separators=(",", ":"))
            )
            if token_used + tool_cost > token_budget:
                excluded.append(
                    ExcludedRef(tool_id, "tool", EXCLUSION_OVER_BUDGET)
                )
                continue
            included.tool_refs.append(tool_ref)
            token_used += tool_cost

        # 4. Process policy refs (pass-through)
        for pref in (policy_refs or []):
            policy_cost = estimate_tokens(
                json.dumps(
                    pref,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            policy_id = str(
                pref.get("policy_id")
                or pref.get("family")
                or pref.get("revision")
                or "policy"
            )
            if token_used + policy_cost > token_budget:
                excluded.append(
                    ExcludedRef(
                        policy_id, "policy", EXCLUSION_OVER_BUDGET
                    )
                )
                continue
            included.policy_refs.append(pref)
            token_used += policy_cost

        # 5. Compute deterministic hash
        content_hash = compute_manifest_hash(
            run_id, included, excluded, token_budget, token_used
        )

        # 6. Build manifest
        manifest = ContextManifest(
            manifest_id=str(uuid4()),
            run_id=run_id,
            created_at=_now_iso(),
            content_hash=content_hash,
            token_budget=token_budget,
            token_used=token_used,
            included_refs=included,
            excluded_refs=excluded,
        )

        # 7. Persist
        return self._persist(manifest)

    def get_manifest(self, run_id: str) -> ContextManifest | None:
        """Retrieve a manifest by run_id."""
        conn = self._connect()
        row = conn.execute(
            "SELECT manifest_id, run_id, content_hash, token_budget, token_used, "
            "included_refs, excluded_refs, created_at "
            "FROM context_manifests WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return ContextManifest(
            manifest_id=row[0],
            run_id=row[1],
            created_at=row[7],
            content_hash=row[2],
            token_budget=row[3],
            token_used=row[4],
            included_refs=IncludedRefs(**json.loads(row[5])),
            excluded_refs=[ExcludedRef(**e) for e in json.loads(row[6])],
        )

    def _persist(self, manifest: ContextManifest) -> ContextManifest:
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO context_manifests
                   (manifest_id, run_id, content_hash, token_budget, token_used,
                    included_refs, excluded_refs, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    manifest.manifest_id,
                    manifest.run_id,
                    manifest.content_hash,
                    manifest.token_budget,
                    manifest.token_used,
                    json.dumps(manifest.included_refs.to_dict(), ensure_ascii=False),
                    json.dumps([e.to_dict() for e in manifest.excluded_refs], ensure_ascii=False),
                    manifest.created_at,
                ),
            )
            conn.commit()
            return manifest
        except sqlite3.IntegrityError:
            conn.rollback()
            # The run_id is an immutable lineage identity. Return the object
            # that actually persists rather than exposing a divergent
            # transient manifest to the caller.
            existing = self.get_manifest(manifest.run_id)
            if existing is None:
                raise
            return existing

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
