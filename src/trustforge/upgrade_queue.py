"""Durable SQLite queue for approval-gated Hermes outer upgrades."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any


def default_path() -> Path:
    return Path(os.getenv("TRUSTFORGE_SQLITE_PATH", str(Path(__file__).resolve().parents[2] / "out" / "trustforge.sqlite3")))


class UpgradeQueue:
    def __init__(self, path: Path | None = None):
        self.path = path or default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS upgrade_proposals (
              proposal_id TEXT PRIMARY KEY, area TEXT NOT NULL, severity TEXT NOT NULL,
              payload_json TEXT NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS upgrade_reviews (
              review_id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT NOT NULL,
              reviewer TEXT NOT NULL, verdict TEXT NOT NULL, payload_json TEXT NOT NULL, created_at REAL NOT NULL
            );
            """)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def sync_diagnostic(self, report: dict[str, Any]) -> int:
        now, count = time.time(), 0
        with self._db() as db:
            for item in report.get("proposals", []):
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                db.execute("""INSERT INTO upgrade_proposals
                    (proposal_id,area,severity,payload_json,state,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?) ON CONFLICT(proposal_id) DO UPDATE SET
                    area=excluded.area,severity=excluded.severity,payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at""",
                    (str(item["id"]), str(item.get("area", "unknown")), str(item.get("severity", "medium")),
                     json.dumps(item, ensure_ascii=False, sort_keys=True), "proposed", now, now))
                count += 1
        return count

    def record_reviews(self, result: dict[str, Any]) -> int:
        now, count = time.time(), 0
        with self._db() as db:
            for item in result.get("reviews", []):
                if not isinstance(item, dict) or not item.get("proposal_id"):
                    continue
                proposal_id, verdict = str(item["proposal_id"]), str(item.get("verdict", "insufficient"))
                db.execute("INSERT INTO upgrade_reviews (proposal_id,reviewer,verdict,payload_json,created_at) VALUES (?,?,?,?,?)",
                           (proposal_id, "bedrock-adversarial-reviewer", verdict,
                            json.dumps(item, ensure_ascii=False, sort_keys=True), now))
                next_state = "llm_reviewed" if verdict == "sandbox_ready" else verdict
                db.execute("UPDATE upgrade_proposals SET state=?,updated_at=? WHERE proposal_id=?", (next_state, now, proposal_id))
                count += 1
        return count

    def status(self, limit: int = 50) -> dict[str, Any]:
        with self._db() as db:
            proposals = [dict(row) for row in db.execute(
                "SELECT proposal_id,area,severity,state,created_at,updated_at FROM upgrade_proposals ORDER BY updated_at DESC LIMIT ?", (limit,))]
            reviews = [dict(row) for row in db.execute(
                "SELECT proposal_id,reviewer,verdict,created_at FROM upgrade_reviews ORDER BY review_id DESC LIMIT ?", (limit,))]
        return {"durable": True, "proposal_count": len(proposals), "proposals": proposals, "reviews": reviews}
