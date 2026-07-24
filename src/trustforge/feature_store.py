"""Point-in-time correct Trust Feature Store backed by SQLite."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

FEATURE_SCHEMA_VERSION = "1.0.0"


def _default_path() -> Path:
    home = Path(os.getenv("TRUSTFORGE_HOME", str(Path(__file__).resolve().parents[2])))
    return Path(os.getenv("TRUSTFORGE_SQLITE_PATH", str(home / "out" / "trustforge.sqlite3")))


class TrustFeatureStore:
    def __init__(
        self, path: str | Path | None = None, *, connection: sqlite3.Connection | None = None,
        initialize: bool = True,
    ):
        self._owns_connection = connection is None
        self._conn = connection or sqlite3.connect(path or _default_path(), isolation_level=None)
        if initialize:
            self.ensure_schema(self._conn)

    @staticmethod
    def ensure_schema(conn: sqlite3.Connection) -> None:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS trust_feature_values (
          feature_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL,
          feature_set TEXT NOT NULL, entity_key TEXT NOT NULL, feature_name TEXT NOT NULL,
          event_time REAL NOT NULL, available_at REAL NOT NULL, value_json TEXT NOT NULL,
          snapshot_id TEXT, run_id TEXT, source_reference TEXT, created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_trust_features_asof
          ON trust_feature_values(entity_key,feature_set,feature_name,event_time,available_at);
        CREATE TRIGGER IF NOT EXISTS trust_features_no_update
          BEFORE UPDATE ON trust_feature_values BEGIN
            SELECT RAISE(ABORT, 'trust_feature_values is append-only');
          END;
        CREATE TRIGGER IF NOT EXISTS trust_features_no_delete
          BEFORE DELETE ON trust_feature_values BEGIN
            SELECT RAISE(ABORT, 'trust_feature_values is append-only');
          END;
        """)

    def put_many(
        self, *, feature_set: str, entity_key: str, features: dict[str, Any],
        event_time: float, available_at: float, snapshot_id: str | None = None,
        run_id: str | None = None, source_reference: str | None = None,
    ) -> list[str]:
        if available_at < event_time:
            raise ValueError("available_at cannot precede event_time")
        ids = []
        for feature_name, value in sorted(features.items()):
            feature_id = f"feature-{uuid.uuid4().hex[:20]}"
            self._conn.execute(
                "INSERT INTO trust_feature_values VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (feature_id, FEATURE_SCHEMA_VERSION, feature_set, entity_key, feature_name,
                 float(event_time), float(available_at),
                 json.dumps(value, ensure_ascii=False, sort_keys=True), snapshot_id, run_id,
                 source_reference, time.time()),
            )
            ids.append(feature_id)
        return ids

    def get_as_of(
        self, *, feature_set: str, entity_key: str, as_of: float,
        feature_names: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        params: list[Any] = [entity_key, feature_set, float(as_of), float(as_of)]
        clause = ""
        names = sorted(set(feature_names or ()))
        if names:
            clause = f" AND feature_name IN ({','.join('?' for _ in names)})"
            params.extend(names)
        rows = self._conn.execute(
            "SELECT feature_name,value_json,event_time,available_at,snapshot_id,run_id "
            "FROM trust_feature_values WHERE entity_key=? AND feature_set=? "
            "AND event_time<=? AND available_at<=?" + clause +
            " ORDER BY event_time DESC,available_at DESC,created_at DESC", params,
        ).fetchall()
        output: dict[str, Any] = {}
        for feature_name, value_json, event_time, available_at, snapshot_id, run_id in rows:
            if feature_name in output:
                continue
            output[feature_name] = {
                "value": json.loads(value_json), "event_time": event_time,
                "available_at": available_at, "snapshot_id": snapshot_id, "run_id": run_id,
            }
        return output

    def close(self) -> None:
        if self._owns_connection and self._conn is not None:
            conn, self._conn = self._conn, None
            conn.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
