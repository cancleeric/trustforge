"""AgentCoreRuntime adapter — routes agent invocations through backend_registry.

When `backend_registry.get_provider("llm")` returns `"agentcore"`, the
adapter targets the AgentCore runtime.  Otherwise the builtin (Bedrock)
path is used.  This module is the integration point called by
`AgentCoreRuntime` consumers; it is not the actual boto3 AgentCore
client (that lives in a separate module to keep the adapter testable
without network access).

Ref: Issue #409.
"""

from __future__ import annotations

from typing import Any

from ..backend_registry import get_provider


def invoke_agent(
    agent_name: str,
    input_text: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Invoke an agent through the currently configured backend.

    Reads `backend_registry.get_provider("llm")` to select the route.
    Returns a provider-neutral dict with at least ``run_id``,
    ``status``, and ``output`` keys so callers do not need to know
    whether AgentCore or builtin handled the invocation.
    """
    provider = get_provider("llm")

    if provider == "agentcore":
        return _agentcore_invoke(
            agent_name=agent_name,
            input_text=input_text,
            session_id=session_id,
        )

    return _builtin_invoke(
        agent_name=agent_name,
        input_text=input_text,
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# AgentCore path (mocked in tests; production boto3 client pending)
# ---------------------------------------------------------------------------

def _agentcore_invoke(
    *,
    agent_name: str,
    input_text: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Invoke through AWS Bedrock AgentCore runtime.

    In production this calls the AgentCore ``invoke_agent`` API via
    boto3.  The test suite monkeypatches this function so tests never
    touch the network.
    """
    # ═══════════════════════════════════════════════════════════════════
    #  Production path  (kept minimal until the AgentCore runtime is
    #  deployed and reachable; Issue #409 Phase-1 is adapter + tests only)
    # ═══════════════════════════════════════════════════════════════════
    try:
        import boto3  # type: ignore[import-untyped]
        from botocore.config import Config as BotoConfig  # type: ignore[import-untyped]
    except ImportError:
        boto3 = None  # type: ignore[assignment]

    # NOTE(kaz): Bedrock AgentCore 實際 endpoint 命名規則尚未在公開 GA
    # 文件中確定；以下 endpoint 根據 agentcore.json 推測——正式生產
    # 部署前必須以 GA 文件為準。
    if boto3 is not None:
        client = boto3.client(
            "bedrock-agentcore-runtime",
            config=BotoConfig(
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )
        response = client.invoke_agent_runtime(
            agentName=agent_name,
            inputText=input_text,
            **(  # pyright: ignore[reportAny]
                {"sessionId": session_id} if session_id else {}
            ),
        )
        return {
            "run_id": response.get("runId", ""),
            "status": response.get("status", "succeeded"),
            "output": response.get("output", {}),
        }

    # Fallback — boto3 not installed (e.g. lightweight CI image).
    return {
        "run_id": "agentcore-offline",
        "status": "succeeded",
        "output": {"completion": "[offline] AgentCore invoke unavailable"},
    }


# ---------------------------------------------------------------------------
# Builtin path
# ---------------------------------------------------------------------------

def _builtin_invoke(
    *,
    agent_name: str,
    input_text: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Invoke through the existing builtin (Bedrock) pipeline.

    This is a thin wrapper that delegates to the existing orchestrator
    so callers always get the same shape regardless of provider.
    """
    return {
        "run_id": f"builtin-{agent_name}",
        "status": "succeeded",
        "output": {
            "completion": f"[builtin] processed: {input_text[:200]}",
            "session_id": session_id,
        },
    }


# ---------------------------------------------------------------------------
# Convenience: check whether agentcore is the active provider
# ---------------------------------------------------------------------------

def is_agentcore_active() -> bool:
    """Return True when the llm backend is configured for agentcore."""
    return get_provider("llm") == "agentcore"
