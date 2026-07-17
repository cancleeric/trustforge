"""Append-only Bronze archive for every successful connector fetch."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from .data_contracts import DOCUMENT_SCHEMA_VERSION
from .ingestion.base import Document
from .ingestion.cache import doc_to_dict

SOURCE_EVENT_SCHEMA_VERSION = "1.0.0"


def _default_path() -> Path:
    home = Path(os.getenv("TRUSTFORGE_HOME", str(Path(__file__).resolve().parents[2])))
    shared = os.getenv("TRUSTFORGE_SQLITE_PATH", str(home / "out" / "trustforge.sqlite3"))
    return Path(os.getenv("TRUSTFORGE_SOURCE_ARCHIVE_PATH", shared))


class SourceEventArchive:
    """Immutable event log; connector_cache remains only a latest-value projection."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else _default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path, timeout=10.0, check_same_thread=False, isolation_level=None,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS source_events (
                event_id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                document_schema_version TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                coin TEXT NOT NULL,
                fetched_at REAL NOT NULL,
                published_at REAL,
                expires_at REAL,
                raw_payload_json TEXT NOT NULL,
                raw_payload_ref TEXT,
                payload_format TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                canonical_url TEXT,
                source_url TEXT,
                http_status INTEGER,
                etag TEXT,
                last_modified TEXT,
                content_type TEXT,
                fetch_run_id TEXT NOT NULL,
                scheduler_run_id TEXT NOT NULL,
                quality_state TEXT NOT NULL,
                document_count INTEGER NOT NULL,
                fetch_duration_ms REAL,
                created_at REAL NOT NULL
            )
            """
        )
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(source_events)")}
        if "fetch_duration_ms" not in columns:
            self._conn.execute("ALTER TABLE source_events ADD COLUMN fetch_duration_ms REAL")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_source_events_lookup "
            "ON source_events(source_id, coin, fetched_at)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_source_events_hash "
            "ON source_events(content_hash)"
        )
        self._conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS source_events_no_update
            BEFORE UPDATE ON source_events BEGIN
                SELECT RAISE(ABORT, 'source_events is append-only');
            END
            """
        )
        self._conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS source_events_no_delete
            BEFORE DELETE ON source_events BEGIN
                SELECT RAISE(ABORT, 'source_events is append-only');
            END
            """
        )

    def append_fetch(
        self,
        *,
        source_id: str,
        source_kind: str,
        coin: str,
        documents: Iterable[Document],
        fetched_at: float,
        expires_at: float | None,
        fetch_run_id: str | None = None,
        scheduler_run_id: str | None = None,
        quality_state: str = "accepted",
        fetch_duration_ms: float | None = None,
    ) -> str:
        docs = list(documents)
        payload = [doc_to_dict(doc) for doc in docs]
        raw_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
        timestamps = [float(doc.ts) for doc in docs if float(doc.ts or 0.0) > 0]
        first_meta: dict[str, Any] = docs[0].meta if docs else {}
        event_id = str(uuid.uuid4())
        fetch_id = fetch_run_id or f"fetch-{uuid.uuid4()}"
        scheduler_id = scheduler_run_id or fetch_id
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO source_events (
                    event_id, schema_version, document_schema_version, source_id,
                    source_kind, coin, fetched_at, published_at, expires_at,
                    raw_payload_json, raw_payload_ref, payload_format, content_hash,
                    canonical_url, source_url, http_status, etag, last_modified,
                    content_type, fetch_run_id, scheduler_run_id, quality_state,
                    document_count, fetch_duration_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, SOURCE_EVENT_SCHEMA_VERSION, DOCUMENT_SCHEMA_VERSION,
                    source_id, source_kind, coin.upper(), float(fetched_at),
                    max(timestamps) if timestamps else None, expires_at, raw_json, None,
                    "normalized-document-batch.v1", digest,
                    first_meta.get("canonical_url"), docs[0].url if docs else None,
                    first_meta.get("http_status"), first_meta.get("etag"),
                    first_meta.get("last_modified"), first_meta.get("content_type"),
                    fetch_id, scheduler_id, quality_state, len(docs), fetch_duration_ms, time.time(),
                ),
            )
        return event_id

    def observability_snapshot(self, *, window_seconds: float = 86400.0, now: float | None = None) -> list[dict[str, Any]]:
        """Return deterministic per-source Bronze volume, freshness and latency metrics."""
        boundary = float(now if now is not None else time.time()) - window_seconds
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            rows = self._conn.execute(
                "SELECT source_id, fetched_at, content_hash, document_count, quality_state, "
                "fetch_duration_ms FROM source_events WHERE fetched_at >= ? ORDER BY source_id, fetched_at",
                (boundary,),
            ).fetchall()
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(str(row["source_id"]), []).append(row)
        current = float(now if now is not None else time.time())
        output: list[dict[str, Any]] = []
        for source_id, source_rows in sorted(grouped.items()):
            durations = sorted(
                float(row["fetch_duration_ms"]) for row in source_rows
                if row["fetch_duration_ms"] is not None
            )
            hashes = [str(row["content_hash"]) for row in source_rows]

            def percentile(values: list[float], ratio: float) -> float | None:
                if not values:
                    return None
                rank = max(1, int(len(values) * ratio + 0.999999))
                return values[min(len(values) - 1, rank - 1)]

            latest = max(float(row["fetched_at"]) for row in source_rows)
            output.append({
                "source": source_id,
                "fetches": len(source_rows),
                "documents": sum(int(row["document_count"]) for row in source_rows),
                "empty_fetches": sum(str(row["quality_state"]) == "empty" for row in source_rows),
                "latest_fetched_at": latest,
                "freshness_age_seconds": max(0.0, current - latest),
                "duplicate_fetch_ratio": round(1.0 - len(set(hashes)) / len(hashes), 4),
                "latency_p50_ms": percentile(durations, 0.50),
                "latency_p95_ms": percentile(durations, 0.95),
            })
        return output

    def get(self, event_id: str) -> dict[str, Any] | None:
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM source_events WHERE event_id = ?", (event_id,)
            ).fetchone()
        return dict(row) if row else None

    def count(self, *, source_id: str | None = None) -> int:
        with self._lock:
            if source_id is None:
                row = self._conn.execute("SELECT COUNT(*) FROM source_events").fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM source_events WHERE source_id = ?", (source_id,)
                ).fetchone()
        return int(row[0])

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                conn, self._conn = self._conn, None
                conn.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
