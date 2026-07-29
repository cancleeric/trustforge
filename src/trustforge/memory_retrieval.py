"""Memory Retrieval Adapter — maps retrieval results to formal memory references.

Wraps existing retrieval sources (question bank, dialogue history, custom RAG)
into typed MemoryRef objects with lineage tracking. Ensures historical
conclusions never enter scoring input.

Design principles:
  - Adapter pattern: wraps existing sources, doesn't replace them
  - Historical conclusions (hermes-* provider) always non-evidentiary
  - Retrieval lineage written to execution log
  - Zero third-party dependencies

Contract: docs/contracts/MEMORY-OS-CONTRACT.md §5 Retrieval Lineage
Issue: #919 | Epic: #914
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .memory_os import (
    MemoryEntry,
    MemoryRepository,
    memory_content_hash,
)

# ─── Dataclasses ─────────────────────────────────────────────────────────────


@dataclass
class MemoryRef:
    """A typed reference to a retrieved memory entry with lineage."""

    memory_id: str
    kind: str
    rank: int  # retrieval rank (1-based)
    reason: str  # e.g. "question_rag_similarity", "dialogue_recent"
    evidence_eligible: bool
    content_preview: str  # truncated content for lineage display
    run_id: str
    retrieved_at: str = ""

    def __post_init__(self) -> None:
        if not self.retrieved_at:
            self.retrieved_at = _now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "kind": self.kind,
            "rank": self.rank,
            "reason": self.reason,
            "evidence_eligible": self.evidence_eligible,
            "content_preview": self.content_preview,
            "run_id": self.run_id,
            "retrieved_at": self.retrieved_at,
        }


# ─── Utility Functions ───────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _truncate(text: str, max_len: int = 150) -> str:
    """Truncate text for preview."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _is_historical_conclusion(entry: MemoryEntry) -> bool:
    """Agent's own past conclusions cannot be Evidence.

    Detects entries produced by Hermes itself (provider starts with 'hermes-').
    """
    return entry.kind == "semantic" and entry.provider.startswith("hermes-")


def default_execlog_path() -> Path:
    """Return execution log path."""
    return Path(os.getenv("TRUSTFORGE_EXECLOG_PATH", "out/execution_log.jsonl"))


# ─── Retrieval Event Emission ────────────────────────────────────────────────


def emit_retrieval_event(
    run_id: str,
    refs: list[MemoryRef],
    *,
    execlog_path: Path | None = None,
) -> dict[str, Any]:
    """Emit a memory_retrieval event to execution log.

    Returns the event dict (also written to file if path is writable).
    """
    event = {
        "event": "memory_retrieval",
        "run_id": run_id,
        "timestamp": _now_iso(),
        "count": len(refs),
        "memories": [
            {
                "memory_id": r.memory_id,
                "kind": r.kind,
                "rank": r.rank,
                "reason": r.reason,
                "evidence_eligible": r.evidence_eligible,
            }
            for r in refs
        ],
    }

    path = execlog_path or default_execlog_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass  # Graceful degradation: log failure doesn't block retrieval

    return event


# ─── Category Counter ────────────────────────────────────────────────────────


def count_by_category(
    repo: MemoryRepository, run_id: str
) -> dict[str, int]:
    """Count memory entries by category for a run.

    Returns:
        {"historical": N, "evidence": M, "used_as_evidence": K}
    """
    entries = repo.find_by_run(run_id)
    historical = sum(1 for e in entries if not e.evidence_eligible)
    evidence = sum(1 for e in entries if e.evidence_eligible)
    used_as_evidence = repo.count_used_as_evidence(run_id)
    return {"historical": historical, "evidence": evidence, "used_as_evidence": used_as_evidence}


# ─── Memory Retrieval Adapter ────────────────────────────────────────────────


class MemoryRetrievalAdapter:
    """Maps retrieval results to formal memory references with lineage.

    This adapter:
    1. Takes retrieval results from any source
    2. Saves them as MemoryEntry (if new)
    3. Applies historical conclusion guard
    4. Returns typed MemoryRef list with rank/reason
    5. Emits retrieval lineage event
    """

    def __init__(
        self,
        memory_repo: MemoryRepository,
        *,
        execlog_path: Path | None = None,
    ) -> None:
        self._repo = memory_repo
        self._execlog_path = execlog_path

    def retrieve_from_source(
        self,
        items: list[dict[str, Any]],
        *,
        run_id: str,
        source_provider: str,
        kind: str = "semantic",
        reason: str = "source_retrieval",
    ) -> list[MemoryRef]:
        """Generic retrieval: convert source items to memory refs.

        Each item dict should have at minimum:
          - "content": str (the text content)
          - "published_at": str | None (optional)

        Returns ranked MemoryRef list with lineage emitted.
        """
        refs: list[MemoryRef] = []

        for rank, item in enumerate(items, start=1):
            content = item.get("content", "")
            published_at = item.get("published_at")
            content_hash = memory_content_hash(content)

            # Create or find existing memory entry
            entry = self._get_or_create_entry(
                kind=kind,
                provider=source_provider,
                content=content,
                content_hash=content_hash,
                published_at=published_at,
                run_id=run_id,
            )

            # Apply historical conclusion guard
            evidence_eligible = entry.evidence_eligible
            if _is_historical_conclusion(entry):
                evidence_eligible = False

            refs.append(
                MemoryRef(
                    memory_id=entry.memory_id,
                    kind=entry.kind,
                    rank=rank,
                    reason=reason,
                    evidence_eligible=evidence_eligible,
                    content_preview=_truncate(content),
                    run_id=run_id,
                )
            )

        # Emit lineage event
        if refs:
            emit_retrieval_event(run_id, refs, execlog_path=self._execlog_path)

        return refs

    def retrieve_question_memory(
        self,
        query: str,
        *,
        run_id: str,
        limit: int = 10,
    ) -> list[MemoryRef]:
        """Retrieve question-related memory entries.

        Maps question bank queries into formal memory references.
        In MVP, this creates a memory entry for the query itself.
        """
        content = f"question:{query}"
        content_hash = memory_content_hash(content)

        entry = self._get_or_create_entry(
            kind="episodic",
            provider="question_bank",
            content=content,
            content_hash=content_hash,
            published_at=_now_iso(),
            run_id=run_id,
        )

        ref = MemoryRef(
            memory_id=entry.memory_id,
            kind="episodic",
            rank=1,
            reason="question_rag_query",
            evidence_eligible=False,  # Questions themselves are not evidence
            content_preview=_truncate(query),
            run_id=run_id,
        )

        emit_retrieval_event(run_id, [ref], execlog_path=self._execlog_path)
        return [ref]

    def retrieve_dialogue_memory(
        self,
        session_id: str,
        *,
        run_id: str,
        messages: list[dict[str, str]] | None = None,
        limit: int = 5,
    ) -> list[MemoryRef]:
        """Retrieve dialogue history as memory references.

        Dialogue memory is ALWAYS non-evidentiary.
        """
        refs: list[MemoryRef] = []
        messages = messages or []

        for rank, msg in enumerate(messages[:limit], start=1):
            content = msg.get("content", "")
            content_hash = memory_content_hash(f"dialogue:{session_id}:{content}")

            entry = self._get_or_create_entry(
                kind="dialogue",
                provider=f"dialogue:{session_id}",
                content=f"dialogue:{session_id}:{content}",
                content_hash=content_hash,
                published_at=None,  # Dialogue has no external publish time
                run_id=run_id,
            )

            refs.append(
                MemoryRef(
                    memory_id=entry.memory_id,
                    kind="dialogue",
                    rank=rank,
                    reason="dialogue_recent",
                    evidence_eligible=False,  # Dialogue NEVER evidence
                    content_preview=_truncate(content),
                    run_id=run_id,
                )
            )

        if refs:
            emit_retrieval_event(run_id, refs, execlog_path=self._execlog_path)

        return refs

    def retrieve_by_kind(
        self,
        kind: str,
        *,
        run_id: str,
        limit: int = 20,
    ) -> list[MemoryRef]:
        """Retrieve existing memory entries by kind from the repository."""
        entries = self._repo.find_by_kind(kind, limit=limit)
        refs: list[MemoryRef] = []

        for rank, entry in enumerate(entries, start=1):
            self._repo.record_retrieval(entry.memory_id, run_id)
            evidence_eligible = entry.evidence_eligible
            if _is_historical_conclusion(entry):
                evidence_eligible = False

            refs.append(
                MemoryRef(
                    memory_id=entry.memory_id,
                    kind=entry.kind,
                    rank=rank,
                    reason=f"kind_retrieval:{kind}",
                    evidence_eligible=evidence_eligible,
                    content_preview=_truncate(entry.content_ref),
                    run_id=run_id,
                )
            )

        if refs:
            emit_retrieval_event(run_id, refs, execlog_path=self._execlog_path)

        return refs

    # ─── Internal ────────────────────────────────────────────────────────

    def _get_or_create_entry(
        self,
        *,
        kind: str,
        provider: str,
        content: str,
        content_hash: str,
        published_at: str | None,
        run_id: str,
    ) -> MemoryEntry:
        """Get existing entry by hash or create new one.

        Evidence eligibility is determined by calling the canonical
        validate_evidence_eligible(). If validation fails, the entry is
        stored as non-evidentiary (evidence_eligible=False). This ensures
        the adapter NEVER silently promotes ineligible content.

        On duplicate (same provider + content_hash), returns the already-
        persisted entry rather than a phantom object.
        """
        from .memory_os import validate_evidence_eligible

        # Build candidate entry with evidence_eligible=False initially
        entry = MemoryEntry(
            memory_id=str(uuid4()),
            kind=kind,
            provider=provider,
            content_hash=content_hash,
            content_ref=content,
            published_at=published_at,
            retrieved_at=_now_iso(),
            evidence_eligible=False,
            run_id=run_id,
        )

        # Attempt to promote to evidence-eligible via canonical validation
        try:
            validate_evidence_eligible(entry)
            # Validation passed — safe to mark eligible
            entry.evidence_eligible = True
        except ValueError:
            # Validation failed — stays non-evidentiary (fail-closed)
            entry.evidence_eligible = False

        # Persist (or discover existing)
        try:
            self._repo.save(entry)
        except sqlite3.IntegrityError:
            # Duplicate (provider, content_hash) already exists.
            # Find the existing record so we return the real persisted ID.
            existing = self._find_existing(provider, content_hash, kind)
            if existing is not None:
                self._repo.record_retrieval(existing.memory_id, run_id)
                return existing
            # An integrity failure without a resolvable duplicate must not
            # produce a phantom lineage reference.
            raise

        return entry

    def _find_existing(
        self, provider: str, content_hash: str, kind: str
    ) -> MemoryEntry | None:
        """Look up an existing entry by provider + content_hash."""
        entry = self._repo.find_by_provider_hash(provider, content_hash)
        return entry if entry is not None and entry.kind == kind else None
