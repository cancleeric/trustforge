"""Provider-neutral AWS Bedrock AgentCore adapter."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from ..backend_registry import get_provider

_MAX_RESPONSE_BYTES = 1_048_576
_MAX_RESPONSE_EVENTS = 4_096


def invoke_agent(
    agent_name: str,
    input_text: str,
    *,
    session_id: str | None = None,
    runtime_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Invoke through the selected provider using one stable response shape."""

    if get_provider("llm") == "agentcore":
        kwargs: dict[str, Any] = {
            "agent_name": agent_name,
            "input_text": input_text,
            "session_id": session_id,
        }
        if runtime_payload is not None:
            kwargs["runtime_payload"] = runtime_payload
        return _agentcore_invoke(**kwargs)
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
        raw = response.read(_MAX_RESPONSE_BYTES + 1)
        if not isinstance(raw, bytes):
            raise TypeError("AgentCore response body must be bytes")
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ValueError("AgentCore response body is too large")
        return raw
    body = bytearray()
    for event_count, event in enumerate(response, start=1):
        if event_count > _MAX_RESPONSE_EVENTS:
            raise ValueError("AgentCore response has too many events")
        chunk = event.get("chunk", {}).get("bytes", b"")
        if not isinstance(chunk, bytes):
            raise TypeError("AgentCore response chunk must be bytes")
        if not chunk:
            continue
        if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
            raise ValueError("AgentCore response body is too large")
        body.extend(chunk)
    return bytes(body)


def _agentcore_invoke(
    *,
    agent_name: str,
    input_text: str,
    session_id: str | None = None,
    runtime_payload: dict[str, Any] | None = None,
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

    request_payload = dict(runtime_payload or {})
    request_payload.setdefault("prompt", input_text)
    request_payload["agent_name"] = agent_name
    payload = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
    runtime_session_id = session_id or str(uuid.uuid4())
    if (
        not isinstance(runtime_session_id, str)
        or not 33 <= len(runtime_session_id) <= 256
    ):
        return {
            "run_id": "",
            "status": "failed",
            "output": {"error": "AgentCore session ID is invalid"},
        }
    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=runtime_session_id,
            contentType="application/json",
            accept="application/json",
            payload=payload,
        )
        raw = _read_stream(response.get("response"))
        status_code = response.get("statusCode")
        if not isinstance(status_code, int) or not 200 <= status_code < 300:
            raise ValueError("AgentCore response has no successful status code")
        if not raw:
            raise ValueError("AgentCore response body is empty")
        output = json.loads(raw.decode("utf-8"))
        if not isinstance(output, dict) or not output:
            raise ValueError("AgentCore response body is invalid")
        return {
            "run_id": str(response.get("runtimeSessionId", "")),
            "status": "succeeded",
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
