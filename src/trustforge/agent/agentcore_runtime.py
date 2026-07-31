"""Portable AgentCore entrypoint helpers.

This module is the maintained counterpart of the archived
``app/TrustForge/main.py`` prototype.  It deliberately keeps AWS/Strands
imports outside the core functions so the existing TrustForge test and
offline paths do not acquire a hard AgentCore dependency.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..execution_event_log import to_public_events
from ..pipeline import run
from ..schema import COIN_POOL, QuestionType


SYSTEM_PROMPT = """你是 TrustForge Hermes 加密市場信任提煉分析師。
分析時必須標示來源與信任分數；證據不足時明確拒答，不得編造資料。
AgentCore 是可拔插執行層，TrustForge 的信任計算與預算閘仍是唯一真相來源。
"""


def list_supported_coins() -> list[str]:
    """Return the canonical coin pool instead of duplicating a runtime list."""

    return sorted(COIN_POOL)


def analyze_market(
    coin: str,
    query: str,
    *,
    question_type: str = QuestionType.MULTI_SOURCE.value,
    data_mode: str = "live",
    llm_mode: str = "bedrock",
) -> dict[str, Any]:
    """Run the existing governed pipeline and return a serializable payload."""

    normalized_coin = coin.strip().upper()
    if normalized_coin not in COIN_POOL:
        raise ValueError(f"unsupported coin: {normalized_coin}")
    try:
        qtype = QuestionType(question_type)
    except ValueError as exc:
        raise ValueError(f"unsupported question_type: {question_type}") from exc

    report, evidence, execution_log = run(
        normalized_coin,
        query,
        qtype,
        data_mode=data_mode,
        llm_mode=llm_mode,
    )
    return {
        "report": asdict(report),
        "evidence": [asdict(item) for item in evidence],
        "execution_log": to_public_events(execution_log.events),
    }


def invoke_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate an AgentCore HTTP payload and invoke the canonical pipeline."""

    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    coin_value = payload.get("coin")
    query_value = payload.get("query") or payload.get("prompt")
    if not isinstance(coin_value, str):
        raise ValueError("coin must be a string")
    if not isinstance(query_value, str):
        raise ValueError("query must be a string")
    coin = coin_value.strip()
    query = query_value.strip()
    if not coin:
        raise ValueError("coin is required")
    if not query:
        raise ValueError("query is required")
    if len(coin) > 10:
        raise ValueError("coin is too long")
    if len(query) > 4000:
        raise ValueError("query is too long")
    for key in ("question_type", "data_mode", "llm_mode"):
        value = payload.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
    return analyze_market(
        coin,
        query,
        question_type=str(
            payload.get("question_type", QuestionType.MULTI_SOURCE.value)
        ),
        data_mode=str(payload.get("data_mode", "live")),
        llm_mode=str(payload.get("llm_mode", "bedrock")),
    )
