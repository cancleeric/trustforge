"""Snapshot-only daily Hermes replay with an explicit no-future-data boundary."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from .agent.orchestrator import run_agent_pipeline
from .bedrock import BedrockClient
from .execlog import ExecutionLog
from .ingestion.base import Document
from .schema import QuestionType


def _epoch(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def replay_snapshot(snapshot: dict[str, Any], *, query: str, qtype: QuestionType = QuestionType.MULTI_SOURCE) -> dict[str, Any]:
    """Replay only documents legal at ``snapshot_epoch``; never fetch/cache-read."""
    boundary = float(snapshot.get("snapshot_epoch", 0) or 0)
    coin = str(snapshot.get("coin", "")).upper()
    if not boundary or not coin:
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
    report, evidence = run_agent_pipeline(query, coin, qtype, docs, client=BedrockClient(offline=True), log=log, now_fn=lambda: boundary)
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
