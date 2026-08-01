"""Snapshot-only daily Hermes replay with an explicit no-future-data boundary."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import math
from typing import Any, Callable

from .agent.orchestrator import run_agent_pipeline
from .bedrock import BedrockClient
from .execlog import ExecutionLog
from .ingestion.base import Document
from .schema import QuestionType


def _epoch(value: str) -> float:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("historical replay requires timezone-aware published_at")
    return parsed.astimezone(timezone.utc).timestamp()


def _snapshot_boundary(snapshot: dict[str, Any]) -> float:
    try:
        boundary = float(snapshot.get("snapshot_epoch"))
    except (TypeError, ValueError):
        raise ValueError("snapshot requires finite positive snapshot_epoch") from None
    if not math.isfinite(boundary) or boundary <= 0:
        raise ValueError("snapshot requires finite positive snapshot_epoch")
    return boundary


def replay_snapshot(snapshot: dict[str, Any], *, query: str, qtype: QuestionType = QuestionType.MULTI_SOURCE) -> dict[str, Any]:
    """Replay only documents legal at ``snapshot_epoch``; never fetch/cache-read."""
    boundary = _snapshot_boundary(snapshot)
    coin = str(snapshot.get("coin", "")).upper()
    if not coin:
        raise ValueError("snapshot requires coin and snapshot_epoch")
    docs: list[Document] = []
    for source_entry in snapshot.get("sources") or []:
        source_name = str(source_entry.get("source", ""))
        for raw in source_entry.get("documents") or []:
            if not isinstance(raw, dict) or not raw.get("published_at"):
                raise ValueError("historical replay requires published_at on every document")
            published = _epoch(str(raw["published_at"]))
            if published > boundary:
                raise ValueError("historical replay rejected future document")
            docs.append(Document(
                id=str(raw.get("id", f"{source_name}:{len(docs)}")),
                kind=str(raw.get("kind", "news")), source=str(raw.get("source", source_name)),
                text=str(raw.get("text", "")), url=str(raw.get("url", "")), ts=published,
                meta=dict(raw.get("meta") or {}),
            ))
    if not docs:
        raise ValueError("historical replay snapshot has no eligible documents")
    log = ExecutionLog(now_fn=lambda: boundary)
    log.record("historical_replay.start", params={"snapshot_epoch": boundary, "archive_type": snapshot.get("archive_type", "scheduled_snapshot")})
    # #960 run_scope_id（契約 §2.2）：snapshot-scoped，不是 invocation-scoped。
    # 優先用快照穩定 id；缺 id 時用「canonical 快照內容」的 SHA-256 fallback——
    # replay 同一 snapshot_id 重現相同 claim_ids（deterministic replay）；不同 snapshot
    # 產出 disjoint ids。FORBIDDEN 用 `replay-{n}` process-local 計數器（跨 snapshot 會撞）。
    snapshot_id = snapshot.get("snapshot_id")
    if snapshot_id:
        run_scope_id = str(snapshot_id).replace(":", "-")
    else:
        run_scope_id = "replay-" + hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
    report, evidence = run_agent_pipeline(query, coin, qtype, docs, client=BedrockClient(offline=True), log=log, now_fn=lambda: boundary, run_scope_id=run_scope_id)
    log.record("historical_replay.done", params={"eligible_documents": len(docs), "evidence_count": len(evidence)})
    return {"coin": coin, "snapshot_at": snapshot.get("snapshot_at"), "snapshot_epoch": boundary, "archive_type": snapshot.get("archive_type", "scheduled_snapshot"), "report": asdict(report), "evidence": [item.to_dict() for item in evidence], "execution_log_jsonl": log.to_jsonl()}


def replay_date_range(
    coin: str, start: date, end: date, *, query: str,
    load_snapshot: Callable[[str, str], dict[str, Any] | None],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Replay each available UTC snapshot, recording missing/invalid days honestly."""
    results: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    day = start
    while day <= end:
        date_str = day.isoformat()
        snapshot = load_snapshot(coin, date_str)
        if snapshot is None:
            skipped.append({"date": date_str, "reason": "snapshot_missing"})
        else:
            try:
                results.append(replay_snapshot(snapshot, query=query))
            except ValueError as exc:
                skipped.append({"date": date_str, "reason": str(exc)})
        day += timedelta(days=1)
    return results, skipped
