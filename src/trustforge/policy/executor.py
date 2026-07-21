"""PolicyExecutor — resolve and apply approved outer-skill policies (read-only).

The executor is the public interface for the pipeline to obtain typed policy
values at run start.  It resolves effective policies (excluding staged
candidates), produces serializable snapshots for the execution log, and
exposes typed accessors for pipeline consumers.

Key invariant: the executor never mutates state.  It is a read-only projection
of the approved skill artifacts into the current run context.

Usage:
    executor = PolicyExecutor(root=..., log_path=...)
    policies = executor.resolve_effective()
    snapshot = executor.snapshot_for_log()
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from ..skill_changes import active_revision, default_log_path
from ..skills import SKILL_FAMILIES, default_skill_root, skill_id_for
from .loader import _resolve_revision, load_approved_policy
from .schema import FAMILY_SCHEMA


class PolicyExecutor:
    """Resolves and applies approved outer-skill policies to a run context.

    This class is instantiated once per pipeline run.  After calling
    resolve_effective(), the frozen policies are cached for the run duration.

    Attributes:
        requires_human_approval: Always True — policy changes must be approved
            by a human before they affect any formal run.
    """

    requires_human_approval: bool = True

    def __init__(
        self,
        *,
        root: Path | None = None,
        log_path: Path | None = None,
    ):
        self._root = root or default_skill_root()
        self._log_path = log_path or default_log_path()
        self._effective: dict[str, Any] | None = None
        self._revisions: dict[str, tuple[str, str]] | None = None

    def resolve_effective(self) -> dict[str, Any]:
        """Return frozen effective policies; staged revisions are excluded.

        The result is cached for the lifetime of this executor instance,
        ensuring a consistent view within a single run even if approvals
        happen concurrently.

        Returns:
            Dict mapping family name → frozen TypedPolicy instance.
        """
        if self._effective is not None:
            return self._effective

        policies: dict[str, Any] = {}
        revisions: dict[str, tuple[str, str]] = {}

        for family in sorted(SKILL_FAMILIES):
            revision_hash, origin = _resolve_revision(
                family, root=self._root, log_path=self._log_path
            )
            policy = load_approved_policy(
                family, revision_hash, root=self._root, log_path=self._log_path
            )
            policies[family] = policy
            revisions[family] = (revision_hash, origin)

        self._effective = policies
        self._revisions = revisions
        return policies

    def get_policy(self, family: str) -> Any:
        """Get the effective policy for a specific family.

        Raises:
            ValueError: If family is not in SKILL_FAMILIES.
            RuntimeError: If resolve_effective() has not been called.
        """
        if family not in FAMILY_SCHEMA:
            raise ValueError(f"unsupported policy family: {family!r}")
        if self._effective is None:
            raise RuntimeError(
                "resolve_effective() must be called before get_policy()"
            )
        return self._effective[family]

    def snapshot_for_log(self) -> dict[str, Any]:
        """Produce a serializable snapshot for execution_log.jsonl.

        The snapshot contains each family's revision hash and a simplified
        rule summary (the dataclass fields as a dict), enabling full
        reconstruction of the effective policy from the log alone.

        Returns:
            Dict suitable for JSON serialization as a policy_snapshot event.
        """
        if self._effective is None or self._revisions is None:
            # If not yet resolved, resolve now.
            self.resolve_effective()

        assert self._effective is not None
        assert self._revisions is not None

        families: dict[str, Any] = {}
        for family in sorted(SKILL_FAMILIES):
            revision_hash, origin = self._revisions[family]
            policy = self._effective[family]
            families[family] = {
                "revision": revision_hash,
                "origin": origin,
                "policy_summary": dataclasses.asdict(policy),
            }

        return {
            "event": "policy_snapshot",
            "requires_human_approval": self.requires_human_approval,
            "policies": families,
        }
