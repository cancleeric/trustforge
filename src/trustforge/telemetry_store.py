"""Generic telemetry store protocol and SQLite adapter.

This module intentionally keeps storage mechanics separate from
``module_telemetry`` lifecycle semantics.  It has no imports from TrustForge
domain modules, so platform consumers can reuse the store contract directly.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class TelemetryStoreEvent:
    """One telemetry write event consumed by a store adapter."""

    subject_id: str
    latency_ms: float
    result: str
    ts: float
    state: str
    count_invocation: bool = True
    evidence_ref: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TelemetryStoreRecord:
    """Provider-neutral persisted telemetry snapshot."""

    subject_id: str
    last_invoked_at: str
    invocation_count: int
    last_result: str
    avg_latency_ms: float
    last_latency_ms: float = 0.0
    state: str = "registered"
    evidence_ref: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class TelemetryStore(Protocol):
    """Minimal durable store contract for runtime telemetry snapshots."""

    def initialize(self) -> None:
        """Create backing storage if needed."""
        ...

    def write_batch(self, batch: list[TelemetryStoreEvent]) -> None:
        """Persist a batch of telemetry events."""
        ...

    def get(self, subject_id: str) -> TelemetryStoreRecord | None:
        """Return one telemetry snapshot, or ``None`` if missing."""
        ...

    def list_all(self) -> list[TelemetryStoreRecord]:
        """Return all telemetry snapshots in recency order."""
        ...


class SQLiteTelemetryStore:
    """SQLite-backed implementation of :class:`TelemetryStore`."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def initialize(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS module_telemetry (
                    module_id TEXT PRIMARY KEY,
                    last_invoked_at TEXT NOT NULL,
                    invocation_count INTEGER NOT NULL DEFAULT 0,
                    last_result TEXT NOT NULL DEFAULT 'unknown',
                    total_latency_ms REAL NOT NULL DEFAULT 0.0,
                    avg_latency_ms REAL NOT NULL DEFAULT 0.0,
                    last_latency_ms REAL NOT NULL DEFAULT 0.0,
                    state TEXT NOT NULL DEFAULT 'registered',
                    evidence_ref TEXT NOT NULL DEFAULT '',
                    metadata TEXT NOT NULL DEFAULT '{}'
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def write_batch(self, batch: list[TelemetryStoreEvent]) -> None:
        if not batch:
            return
        conn = sqlite3.connect(self.db_path, timeout=5)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            for ev in batch:
                ts_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ev.ts))
                meta_json = json.dumps(ev.metadata, ensure_ascii=False) if ev.metadata else "{}"
                invocation_delta = 1 if ev.count_invocation else 0
                total_latency_delta = ev.latency_ms if ev.count_invocation else 0.0
                conn.execute("""
                    INSERT INTO module_telemetry
                        (module_id, last_invoked_at, invocation_count, last_result,
                         total_latency_ms, avg_latency_ms, last_latency_ms,
                         state, evidence_ref, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(module_id) DO UPDATE SET
                        last_invoked_at = excluded.last_invoked_at,
                        invocation_count = invocation_count + ?,
                        last_result = excluded.last_result,
                        total_latency_ms = total_latency_ms + ?,
                        avg_latency_ms = CASE
                            WHEN invocation_count + ? > 0
                            THEN (total_latency_ms + ?) / (invocation_count + ?)
                            ELSE 0.0
                        END,
                        last_latency_ms = CASE WHEN ? = 1
                            THEN excluded.last_latency_ms
                            ELSE module_telemetry.last_latency_ms
                        END,
                        state = excluded.state,
                        evidence_ref = CASE WHEN excluded.evidence_ref != ''
                                       THEN excluded.evidence_ref
                                       ELSE module_telemetry.evidence_ref END,
                        metadata = excluded.metadata
                """, (
                    ev.subject_id,
                    ts_iso,
                    invocation_delta,
                    ev.result,
                    total_latency_delta,
                    ev.latency_ms if ev.count_invocation else 0.0,
                    ev.latency_ms,
                    ev.state,
                    ev.evidence_ref,
                    meta_json,
                    invocation_delta,
                    total_latency_delta,
                    invocation_delta,
                    total_latency_delta,
                    invocation_delta,
                    invocation_delta,
                ))
            conn.commit()
        finally:
            conn.close()

    def get(self, subject_id: str) -> TelemetryStoreRecord | None:
        conn = sqlite3.connect(self.db_path, timeout=3)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM module_telemetry WHERE module_id = ?", (subject_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return _row_to_record(row)

    def list_all(self) -> list[TelemetryStoreRecord]:
        conn = sqlite3.connect(self.db_path, timeout=3)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM module_telemetry ORDER BY last_invoked_at DESC"
            ).fetchall()
        finally:
            conn.close()
        return [_row_to_record(row) for row in rows]


def default_telemetry_db_path() -> str:
    """Return the default SQLite path for module telemetry."""

    return os.getenv(
        "TRUSTFORGE_TELEMETRY_DB",
        str(Path(__file__).resolve().parents[2] / "out" / "module-telemetry.sqlite3"),
    )


def _row_to_record(row: sqlite3.Row) -> TelemetryStoreRecord:
    return TelemetryStoreRecord(
        subject_id=row["module_id"],
        last_invoked_at=row["last_invoked_at"],
        invocation_count=row["invocation_count"],
        last_result=row["last_result"],
        avg_latency_ms=row["avg_latency_ms"],
        last_latency_ms=row["last_latency_ms"],
        state=row["state"],
        evidence_ref=row["evidence_ref"],
        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
    )
