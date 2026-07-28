"""Optional AgentCore memory construction.

The archived prototype hard-coded SDK construction in the runtime module.
This version keeps it optional, validates identity fields, and returns
``None`` while the memory provider is disabled.
"""

from __future__ import annotations

import os
from typing import Any, Callable


def memory_enabled() -> bool:
    return os.getenv("TRUSTFORGE_AGENTCORE_MEMORY_ENABLED", "").lower() in {
        "1",
        "true",
        "yes",
    }


def build_memory_session_manager(
    *,
    actor_id: str,
    session_id: str,
    factory: Callable[..., Any] | None = None,
) -> Any | None:
    """Build the SDK session manager only when explicitly enabled."""

    if not memory_enabled():
        return None
    memory_id = os.getenv("TRUSTFORGE_AGENTCORE_MEMORY_ID", "").strip()
    if not memory_id:
        raise RuntimeError("TRUSTFORGE_AGENTCORE_MEMORY_ID is required")
    if not actor_id.strip() or not session_id.strip():
        raise ValueError("actor_id and session_id are required")

    if factory is None:
        try:
            from bedrock_agentcore.memory.integrations.strands.session_manager import (
                AgentCoreMemorySessionManager,
            )
        except ImportError as exc:
            raise RuntimeError("AgentCore memory SDK is unavailable") from exc
        factory = AgentCoreMemorySessionManager

    return factory(
        agentcore_memory_id=memory_id,
        actor_id=actor_id,
        session_id=session_id,
    )
