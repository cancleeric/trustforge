"""One-shot, event-driven AgentCore analysis coordinator.

This replaces the archived forever-loop daemon.  Scheduling remains an
explicit operator decision; each invocation detects changed inputs and returns
a receipt that can be persisted by the caller.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .agentcore_adapter import invoke_agent


def newest_mtime(path: Path) -> float:
    if not path.exists():
        return 0.0
    if path.is_file():
        return path.stat().st_mtime
    return max(
        (item.stat().st_mtime for item in path.rglob("*") if item.is_file()),
        default=0.0,
    )


def changed_coins(
    sources: dict[str, Path],
    previous: dict[str, float] | None = None,
) -> tuple[list[str], dict[str, float]]:
    """Return changed coin names and a new immutable timestamp snapshot."""

    old = previous or {}
    snapshot = {
        coin.upper(): newest_mtime(path) for coin, path in sorted(sources.items())
    }
    changed = [
        coin
        for coin, timestamp in snapshot.items()
        if timestamp > float(old.get(coin, 0.0))
    ]
    return changed, snapshot


def run_changed_analyses(
    sources: dict[str, Path],
    *,
    previous: dict[str, float] | None = None,
    query_template: str = "分析 {coin} 最新多來源市場訊號",
    invoke: Callable[..., dict[str, Any]] = invoke_agent,
) -> dict[str, Any]:
    """Invoke AgentCore once per changed coin and return an auditable receipt."""

    changed, snapshot = changed_coins(sources, previous)
    results = []
    for coin in changed:
        result = invoke(
            "hermes",
            query_template.format(coin=coin),
            session_id=f"scheduled-{coin.lower()}",
        )
        results.append({"coin": coin, "result": result})
    return {
        "kind": "agentcore_event_analysis_receipt",
        "changed_coins": changed,
        "snapshot": snapshot,
        "results": results,
    }

