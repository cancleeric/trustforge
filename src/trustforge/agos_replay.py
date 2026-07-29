"""Verification for replaying a frozen Agent OS context manifest."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .context_builder import ContextManifest, compute_manifest_hash
from .memory_os import memory_content_hash
from .skill_registry import revision_hash_for


@dataclass
class ReplayResult:
    """Hash verification report for a frozen context manifest."""

    passed: bool
    manifest_hash_match: bool
    skill_hash_matches: list[tuple[str, bool]] = field(default_factory=list)
    memory_hash_matches: list[tuple[str, bool]] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)


def verify_replay(
    manifest: ContextManifest,
    memory_repo: Any,
    skill_registry: Any,
) -> ReplayResult:
    """Re-derive manifest, skill, and memory hashes from stored content.

    Every frozen reference must resolve and reproduce its recorded hash.
    Missing or malformed references and repository lookup errors fail closed.
    """
    mismatches: list[str] = []

    recomputed_manifest_hash = compute_manifest_hash(
        manifest.run_id,
        manifest.included_refs,
        manifest.excluded_refs,
        manifest.token_budget,
        manifest.token_used,
    )
    manifest_match = recomputed_manifest_hash == manifest.content_hash
    if not manifest_match:
        mismatches.append(
            "manifest hash: "
            f"expected {manifest.content_hash}, got {recomputed_manifest_hash}"
        )

    skill_matches: list[tuple[str, bool]] = []
    for ref in manifest.included_refs.skill_refs:
        skill_id = ref.get("skill_id")
        expected_hash = ref.get("revision_hash")
        label = skill_id if isinstance(skill_id, str) and skill_id else "<missing>"
        match = False
        detail = "invalid frozen reference"

        if isinstance(skill_id, str) and isinstance(expected_hash, str):
            try:
                revision = skill_registry.get_revision(expected_hash)
            except Exception as exc:  # Repository failures must not verify.
                detail = f"lookup failed ({type(exc).__name__})"
            else:
                if revision is None:
                    detail = f"revision {expected_hash} not found"
                elif revision.skill_id != skill_id:
                    detail = (
                        f"revision belongs to {revision.skill_id!r}, "
                        f"expected {skill_id!r}"
                    )
                else:
                    computed_hash = revision_hash_for(revision.content)
                    match = (
                        computed_hash == expected_hash
                        and revision.revision_hash == expected_hash
                    )
                    if not match:
                        detail = (
                            f"expected {expected_hash}, got {computed_hash} "
                            f"(stored {revision.revision_hash})"
                        )

        skill_matches.append((label, match))
        if not match:
            mismatches.append(f"skill {label}: {detail}")

    memory_matches: list[tuple[str, bool]] = []
    for ref in manifest.included_refs.memory_refs:
        memory_id = ref.get("memory_id")
        expected_hash = ref.get("content_hash")
        label = memory_id if isinstance(memory_id, str) and memory_id else "<missing>"
        match = False
        detail = "invalid frozen reference"

        if isinstance(memory_id, str) and isinstance(expected_hash, str):
            try:
                entry = memory_repo.get(memory_id)
            except Exception as exc:  # Repository failures must not verify.
                detail = f"lookup failed ({type(exc).__name__})"
            else:
                if entry is None:
                    detail = "entry not found"
                elif entry.memory_id != memory_id:
                    detail = (
                        f"repository returned {entry.memory_id!r}, "
                        f"expected {memory_id!r}"
                    )
                else:
                    computed_hash = memory_content_hash(entry.content_ref)
                    match = (
                        expected_hash == entry.content_hash
                        and expected_hash == computed_hash
                    )
                    if not match:
                        detail = (
                            f"expected {expected_hash}, got {computed_hash} "
                            f"(stored {entry.content_hash})"
                        )

        memory_matches.append((label, match))
        if not match:
            mismatches.append(f"memory {label}: {detail}")

    return ReplayResult(
        passed=not mismatches,
        manifest_hash_match=manifest_match,
        skill_hash_matches=skill_matches,
        memory_hash_matches=memory_matches,
        mismatches=mismatches,
    )
