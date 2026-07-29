"""Agent OS Runtime Integration — wires Agent OS into the analysis flow.

Provides feature-flagged hook points that build context manifests, record
skill selection lineage, memory retrieval lineage, and tool invocation audit
during analysis runs. Gracefully degrades on failure.

Design principles:
  - Feature flag: TRUSTFORGE_AGOS_ENABLED=1 to activate (default OFF)
  - Graceful degradation: Agent OS failure → log warning, continue run
  - Minimal invasion: hook points in existing flow, not restructuring
  - Trust Kernel scoring inputs remain unchanged
  - Zero third-party dependencies

Issue: #922 | Epic: #914
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .context_builder import ContextBuilder, ContextManifest
from .memory_os import MemoryRepository
from .memory_retrieval import MemoryRef, MemoryRetrievalAdapter, emit_retrieval_event
from .skill_loader import FrozenSkillManifest, SkillLoader
from .skill_registry import SkillRegistryRepository
from .tool_registry import (
    ToolCapability,
    ToolInvocation,
    ToolRegistryRepository,
    invocation_input_hash,
    invocation_output_hash,
)

logger = logging.getLogger(__name__)

_BUILTIN_TOOL_CAPABILITIES = (
    ToolCapability(
        tool_id="ingestion-collect",
        name="Source ingestion collector",
        side_effect_class="read_only",
        evidence_class="candidate_evidence",
        owner="trustforge",
    ),
    ToolCapability(
        tool_id="bedrock-claim-extraction",
        name="Bedrock claim extraction",
        side_effect_class="read_only",
        evidence_class="candidate_evidence",
        owner="trustforge",
    ),
    ToolCapability(
        tool_id="bedrock-narrative-generation",
        name="Bedrock narrative generation",
        side_effect_class="read_only",
        evidence_class="context_only",
        owner="trustforge",
    ),
)

# ─── Feature Flag ────────────────────────────────────────────────────────────


def agos_enabled() -> bool:
    """Check if Agent OS is enabled via environment variable."""
    return os.getenv("TRUSTFORGE_AGOS_ENABLED", "0") == "1"


# ─── Utility ─────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_data_dir() -> Path:
    return Path(os.getenv("TRUSTFORGE_DATA_DIR", "data"))


# ─── Lineage Query ───────────────────────────────────────────────────────────


class AgosLineageQuery:
    """Internal query interface for lineage data. Used by Admin API (#923)."""

    def __init__(self, runtime: AgosRuntime) -> None:
        self._runtime = runtime

    def get_run_context(self, run_id: str) -> ContextManifest | None:
        """Get context manifest for a run."""
        builder = self._runtime._context_builder
        if builder is None:
            return None
        return builder.get_manifest(run_id)

    def get_run_memories(self, run_id: str) -> list[dict[str, Any]]:
        """Get memory entries for a run."""
        repo = self._runtime._memory_repo
        if repo is None:
            return []
        entries = repo.find_by_run(run_id)
        return [
            {
                "memory_id": e.memory_id,
                "kind": e.kind,
                "provider": e.provider,
                "evidence_eligible": e.evidence_eligible,
                "content_ref": e.content_ref,
                "retrieved_at": e.retrieved_at,
            }
            for e in entries
        ]

    def get_run_skills(self, run_id: str) -> FrozenSkillManifest | None:
        """Get frozen skill manifest for a run."""
        loader = self._runtime._skill_loader
        if loader is None:
            return None
        return loader.get_frozen_manifest(run_id)

    def get_run_invocations(self, run_id: str) -> list[dict[str, Any]]:
        """Get tool invocations for a run."""
        registry = self._runtime._tool_registry
        if registry is None:
            return []
        invocations = registry.get_invocations_by_run(run_id)
        return [
            {
                "invocation_id": inv.invocation_id,
                "tool_id": inv.tool_id,
                "input_hash": inv.input_hash,
                "output_hash": inv.output_hash,
                "status": inv.status,
                "error": inv.error,
                "started_at": inv.started_at,
                "completed_at": inv.completed_at,
            }
            for inv in invocations
        ]

    def get_run_memory_counts(self, run_id: str) -> dict[str, int]:
        """Get persisted historical/evidence usage counts for a run."""
        return self._runtime.memory_counts(run_id)


# ─── Runtime ─────────────────────────────────────────────────────────────────


class AgosRuntime:
    """Agent OS Runtime — lazy-initialized integration layer.

    All components are created on first use. If AGOS is disabled,
    all methods are no-ops.
    """

    def __init__(self, *, data_dir: Path | None = None) -> None:
        self._data_dir = data_dir or _default_data_dir()
        self._memory_repo: MemoryRepository | None = None
        self._skill_registry: SkillRegistryRepository | None = None
        self._skill_loader: SkillLoader | None = None
        self._tool_registry: ToolRegistryRepository | None = None
        self._context_builder: ContextBuilder | None = None
        self._retrieval_adapter: MemoryRetrievalAdapter | None = None
        self._initialized = False

    def _ensure_init(self) -> None:
        """Lazy-initialize all Agent OS components."""
        if self._initialized:
            return
        self._initialized = True

        try:
            self._memory_repo = MemoryRepository(db_path=self._data_dir / "memory_os.db")
            self._memory_repo.ensure_schema()

            self._skill_registry = SkillRegistryRepository(
                db_path=self._data_dir / "skill_registry.db"
            )
            self._skill_registry.ensure_schema()

            self._skill_loader = SkillLoader(self._skill_registry)

            self._tool_registry = ToolRegistryRepository(
                db_path=self._data_dir / "tool_registry.db"
            )
            self._tool_registry.ensure_schema()
            # Product-owned runtime capabilities are bootstrapped explicitly so
            # enabling AGOS does not turn every normal pipeline call into an
            # unknown-tool denial. The existence check makes repeated startup
            # idempotent while preserving registry append-only semantics.
            for capability in _BUILTIN_TOOL_CAPABILITIES:
                if not self._tool_registry.is_known(capability.tool_id):
                    self._tool_registry.register_tool(capability)

            self._context_builder = ContextBuilder(
                memory_repo=self._memory_repo,
                skill_loader=self._skill_loader,
                tool_registry=self._tool_registry,
                db_path=self._data_dir / "context_manifests.db",
            )

            self._retrieval_adapter = MemoryRetrievalAdapter(self._memory_repo)
        except Exception as e:
            logger.warning(f"Agent OS initialization failed: {e}")

    @property
    def lineage(self) -> AgosLineageQuery:
        """Lineage query interface."""
        self._ensure_init()
        return AgosLineageQuery(self)

    # ─── Hook Points ─────────────────────────────────────────────────────

    def build_context(
        self,
        run_id: str,
        *,
        question: str | None = None,
        snapshot_ref: str | None = None,
        memory_refs: list[MemoryRef] | None = None,
        skill_ids: list[str] | None = None,
        tool_ids: list[str] | None = None,
        policy_refs: list[dict[str, Any]] | None = None,
        token_budget: int = 4096,
    ) -> ContextManifest | None:
        """Build context manifest for a run. Returns None on failure.

        Called at the start of an analysis run.
        """
        if not agos_enabled():
            return None

        try:
            self._ensure_init()

            # Freeze skills if requested
            skill_manifest: FrozenSkillManifest | None = None
            if skill_ids and self._skill_loader:
                try:
                    skill_manifest = self._skill_loader.freeze_manifest(
                        run_id, skill_ids
                    )
                except ValueError as e:
                    logger.warning(f"Skill freeze failed for run {run_id}: {e}")

            # Build context manifest
            if self._context_builder:
                manifest = self._context_builder.build(
                    run_id=run_id,
                    snapshot_ref=snapshot_ref,
                    question_ref=question,
                    memory_refs=memory_refs,
                    skill_manifest=skill_manifest,
                    tool_refs=tool_ids,
                    policy_refs=policy_refs,
                    token_budget=token_budget,
                )
                self._emit_skill_selection_event(run_id, skill_manifest)
                return manifest
        except Exception as e:
            logger.warning(f"Agent OS context build failed for run {run_id}: {e}")

        return None

    def record_tool_invocation(
        self,
        run_id: str,
        tool_id: str,
        args: dict[str, Any],
    ) -> str | None:
        """Record a pending tool invocation. Returns invocation_id or None."""
        if not agos_enabled():
            return None

        try:
            self._ensure_init()
            if self._tool_registry is None:
                return None

            inv_id = str(uuid4())
            input_hash = invocation_input_hash(tool_id, args)
            inv = ToolInvocation(
                invocation_id=inv_id,
                run_id=run_id,
                tool_id=tool_id,
                input_hash=input_hash,
                status="pending",
            )
            self._tool_registry.record_invocation(inv)
            return inv_id
        except Exception as e:
            logger.warning(f"Tool invocation record failed: {e}")
            return None

    def complete_tool_invocation(
        self,
        invocation_id: str,
        *,
        output: Any = None,
        status: str = "success",
        error: str | None = None,
        evidence_refs: list[str] | None = None,
    ) -> None:
        """Complete a tool invocation with result."""
        if not agos_enabled():
            return

        try:
            self._ensure_init()
            if self._tool_registry is None:
                raise RuntimeError(
                    "tool invocation completion unavailable: Agent OS tool "
                    "registry failed to initialize"
                )

            output_hash = None
            if output is not None:
                output_hash = invocation_output_hash(output)

            self._tool_registry.complete_invocation(
                invocation_id,
                output_hash=output_hash,
                status=status,
                error=error,
                evidence_refs=evidence_refs,
            )
        except Exception:
            logger.exception("Tool invocation complete failed")
            raise

    def tool_audited_fetch(
        self,
        tool_id: str,
        fetch_fn: Any,
        args: dict[str, Any],
        *,
        run_id: str,
    ) -> Any:
        """Wrap a fetch call with tool invocation audit.

        Enforces the tool execution gate: unknown or high-risk tools
        are blocked with PermissionError. If AGOS is disabled, the fetch
        executes without checks (backward-compatible).
        """
        if agos_enabled():
            self._ensure_init()
            # Enforcement gate — raises PermissionError if tool can't run.
            # If registry failed to initialize, we BLOCK (fail-closed).
            if self._tool_registry is None:
                raise PermissionError(
                    f"tool '{tool_id}' cannot execute: Agent OS tool registry "
                    f"failed to initialize (fail-closed)"
                )
            self._tool_registry.assert_executable(tool_id)

        inv_id = self.record_tool_invocation(run_id, tool_id, args)
        if agos_enabled() and inv_id is None:
            raise PermissionError(
                f"tool '{tool_id}' cannot execute: invocation receipt "
                "could not be persisted (fail-closed)"
            )

        try:
            result = fetch_fn(**args)
        except Exception as e:
            if inv_id:
                self.complete_tool_invocation(
                    inv_id, status="failed", error=str(e)
                )
            raise
        if inv_id:
            self.complete_tool_invocation(
                inv_id,
                output=result,
                status="success",
            )
        return result

    def finalize_run(self, run_id: str) -> None:
        """Finalize lineage for a run (called at end of analysis)."""
        if not agos_enabled():
            return
        # Currently a no-op placeholder for future finalization logic
        logger.debug(f"Agent OS run finalized: {run_id}")

    def memory_counts(self, run_id: str) -> dict[str, int]:
        """Return persisted retrieval/evidence lineage counts for a run."""
        if not agos_enabled():
            return {"historical": 0, "evidence": 0, "used_as_evidence": 0}
        self._ensure_init()
        if self._memory_repo is None:
            return {"historical": 0, "evidence": 0, "used_as_evidence": 0}
        from .memory_retrieval import count_by_category

        return count_by_category(self._memory_repo, run_id)

    # ─── Internal ────────────────────────────────────────────────────────

    def _emit_skill_selection_event(
        self, run_id: str, manifest: FrozenSkillManifest | None
    ) -> None:
        """Emit skill selection event to execution log."""
        if manifest is None:
            return

        event = {
            "event": "skill_selection",
            "run_id": run_id,
            "timestamp": _now_iso(),
            "selected_skills": [
                {
                    "skill_id": e.skill_id,
                    "revision_hash": e.revision_hash,
                    "reason": e.reason,
                }
                for e in manifest.entries
            ],
        }

        execlog_path = Path(os.getenv("TRUSTFORGE_EXECLOG_PATH", "out/execution_log.jsonl"))
        try:
            execlog_path.parent.mkdir(parents=True, exist_ok=True)
            with open(execlog_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:
            pass  # Graceful degradation

    def close(self) -> None:
        """Close all database connections."""
        if self._context_builder:
            self._context_builder.close()
        if self._tool_registry:
            self._tool_registry.close()
        if self._skill_registry:
            self._skill_registry.close()
        if self._memory_repo:
            self._memory_repo.close()
