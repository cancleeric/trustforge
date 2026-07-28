"""Provider-neutral AWS Bedrock AgentCore adapter."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from ..backend_registry import get_provider


def invoke_agent(
    agent_name: str,
    input_text: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Invoke through the selected provider using one stable response shape."""

    if get_provider("llm") == "agentcore":
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


def agentcore_status() -> dict[str, Any]:
    """Return non-sensitive readiness state for the UI and diagnostics."""

    selected = get_provider("llm") == "agentcore"
    runtime_configured = bool(
        os.getenv("TRUSTFORGE_AGENTCORE_RUNTIME_ARN", "").strip()
    )
    if selected and runtime_configured:
        state = "configured"
    elif selected:
        state = "misconfigured"
    else:
        state = "inactive"
    return {
        "provider": "agentcore" if selected else "builtin",
        "selected": selected,
        "runtime_configured": runtime_configured,
        "state": state,
    }


def _read_stream(response: Any) -> bytes:
    if response is None:
        return b""
    if hasattr(response, "read"):
        return response.read()
    chunks: list[bytes] = []
    for event in response:
        chunk = event.get("chunk", {}).get("bytes", b"")
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        chunks.append(chunk)
    return b"".join(chunks)


def _agentcore_invoke(
    *,
    agent_name: str,
    input_text: str,
    session_id: str | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Invoke the documented ``bedrock-agentcore`` runtime API.

    Missing SDK or runtime identity is a failed result.  The old prototype
    returned ``succeeded`` while offline, which made the UI claim a connection
    that did not exist.
    """

    runtime_arn = os.getenv("TRUSTFORGE_AGENTCORE_RUNTIME_ARN", "").strip()
    if not runtime_arn:
        return {
            "run_id": "",
            "status": "failed",
            "output": {"error": "AgentCore runtime is not configured"},
        }

    if client is None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError:
            return {
                "run_id": "",
                "status": "failed",
                "output": {"error": "AgentCore SDK is unavailable"},
            }
        client = boto3.client(
            "bedrock-agentcore",
            config=Config(
                retries={"max_attempts": 2, "mode": "standard"},
                connect_timeout=10,
                read_timeout=120,
            ),
        )

    payload = json.dumps(
        {"prompt": input_text, "agent_name": agent_name},
        ensure_ascii=False,
    ).encode("utf-8")
    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=session_id or str(uuid.uuid4()),
            contentType="application/json",
            accept="application/json",
            payload=payload,
        )
        raw = _read_stream(response.get("response"))
        output = json.loads(raw.decode("utf-8")) if raw else {}
        return {
            "run_id": str(response.get("runtimeSessionId", "")),
            "status": "succeeded"
            if int(response.get("statusCode", 200)) < 400
            else "failed",
            "output": output,
        }
    except Exception as exc:
        return {
            "run_id": "",
            "status": "failed",
            "output": {"error": f"AgentCore invocation failed: {type(exc).__name__}"},
        }


def _builtin_invoke(
    *,
    agent_name: str,
    input_text: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    return {
        "run_id": f"builtin-{agent_name}",
        "status": "succeeded",
        "output": {
            "completion": f"[builtin] processed: {input_text[:200]}",
            "session_id": session_id,
        },
    }


def is_agentcore_active() -> bool:
    return get_provider("llm") == "agentcore"
