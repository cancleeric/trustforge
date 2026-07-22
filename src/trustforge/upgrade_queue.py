"""Durable SQLite queue for approval-gated Hermes outer upgrades."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any


_AUTOMATED_APPROVAL_ACTOR_TOKENS = (
    "agent",
    "auto",
    "automation",
    "bedrock",
    "bot",
    "claude",
    "codex",
    "gemini",
    "gpt",
    "llm",
    "model",
    "openai",
    "service",
)


def default_path() -> Path:
    return Path(os.getenv("TRUSTFORGE_SQLITE_PATH", str(Path(__file__).resolve().parents[2] / "out" / "trustforge.sqlite3")))


def _is_human_approval_actor(actor: str) -> bool:
    normalized = actor.strip().lower()
    if not normalized:
        return False
    return not any(token in normalized for token in _AUTOMATED_APPROVAL_ACTOR_TOKENS)


class UpgradeQueue:
    def __init__(self, path: Path | None = None):
        self.path = path or default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._db()) as db, db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS upgrade_proposals (
              proposal_id TEXT PRIMARY KEY, area TEXT NOT NULL, severity TEXT NOT NULL,
              payload_json TEXT NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS upgrade_reviews (
              review_id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT NOT NULL,
              reviewer TEXT NOT NULL, verdict TEXT NOT NULL, payload_json TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS upgrade_sandbox_runs (
              run_id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT NOT NULL,
              passed INTEGER NOT NULL, artifact_hash TEXT NOT NULL,
              payload_json TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS upgrade_decisions (
              decision_id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT NOT NULL,
              actor TEXT NOT NULL, decision TEXT NOT NULL, reason TEXT NOT NULL,
              payload_json TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS upgrade_activations (
              activation_id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT NOT NULL,
              actor TEXT NOT NULL, action TEXT NOT NULL, family TEXT NOT NULL,
              revision TEXT NOT NULL, previous_revision TEXT,
              reason TEXT NOT NULL, created_at REAL NOT NULL
            );
            """)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def sync_diagnostic(self, report: dict[str, Any]) -> int:
        now, count = time.time(), 0
        with closing(self._db()) as db, db:
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
        with closing(self._db()) as db, db:
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
        with closing(self._db()) as db:
            proposals = [dict(row) for row in db.execute(
                "SELECT proposal_id,area,severity,state,created_at,updated_at FROM upgrade_proposals ORDER BY updated_at DESC LIMIT ?", (limit,))]
            reviews = [dict(row) for row in db.execute(
                "SELECT proposal_id,reviewer,verdict,created_at FROM upgrade_reviews ORDER BY review_id DESC LIMIT ?", (limit,))]
            sandbox_runs = [dict(row) for row in db.execute(
                "SELECT run_id,proposal_id,passed,artifact_hash,created_at FROM upgrade_sandbox_runs ORDER BY run_id DESC LIMIT ?", (limit,))]
            decisions = [dict(row) for row in db.execute(
                "SELECT decision_id,proposal_id,actor,decision,reason,created_at FROM upgrade_decisions ORDER BY decision_id DESC LIMIT ?", (limit,))]
            activations = [dict(row) for row in db.execute(
                "SELECT activation_id,proposal_id,actor,action,family,revision,previous_revision,reason,created_at FROM upgrade_activations ORDER BY activation_id DESC LIMIT ?", (limit,))]
        return {"durable": True, "proposal_count": len(proposals), "proposals": proposals,
                "reviews": reviews, "sandbox_runs": sandbox_runs, "decisions": decisions,
                "activations": activations}

    def record_sandbox(self, proposal_id: str, passed: bool, artifact_hash: str,
                       details: dict[str, Any] | None = None) -> dict[str, Any]:
        """Persist a bounded sandbox result; this never activates a candidate."""
        proposal_id, artifact_hash = proposal_id.strip(), artifact_hash.strip()
        if not proposal_id or not artifact_hash:
            raise ValueError("proposal_id and artifact_hash are required")
        now = time.time()
        with closing(self._db()) as db, db:
            row = db.execute("SELECT state FROM upgrade_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
            if row is None:
                raise KeyError(proposal_id)
            if row["state"] in {"approved", "rejected", "activated", "rolled_back"}:
                raise ValueError("terminal proposal cannot be sandboxed")
            payload = details if isinstance(details, dict) else {}
            cursor = db.execute("""INSERT INTO upgrade_sandbox_runs
                (proposal_id,passed,artifact_hash,payload_json,created_at) VALUES (?,?,?,?,?)""",
                (proposal_id, int(passed), artifact_hash,
                 json.dumps(payload, ensure_ascii=False, sort_keys=True), now))
            state = "sandbox_passed" if passed else "sandbox_failed"
            db.execute("UPDATE upgrade_proposals SET state=?,updated_at=? WHERE proposal_id=?",
                       (state, now, proposal_id))
        return {"run_id": cursor.lastrowid, "proposal_id": proposal_id, "state": state,
                "passed": passed, "artifact_hash": artifact_hash}

    def decide(self, proposal_id: str, decision: str, actor: str, reason: str) -> dict[str, Any]:
        """Record the human gate. Approval requires the latest sandbox to pass."""
        proposal_id, decision, actor, reason = (value.strip() for value in (proposal_id, decision, actor, reason))
        if decision not in {"approve", "reject"} or not actor or not reason:
            raise ValueError("decision, actor and reason are required")
        if decision == "approve" and not _is_human_approval_actor(actor):
            raise ValueError("approval requires human actor")
        now = time.time()
        with closing(self._db()) as db, db:
            row = db.execute("SELECT state FROM upgrade_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
            if row is None:
                raise KeyError(proposal_id)
            if row["state"] in {"approved", "rejected", "activated", "rolled_back"}:
                raise ValueError("proposal already has a terminal decision")
            if decision == "approve" and row["state"] != "sandbox_passed":
                raise ValueError("approval requires a passed sandbox")
            state = "approved" if decision == "approve" else "rejected"
            payload = {"previous_state": row["state"]}
            cursor = db.execute("""INSERT INTO upgrade_decisions
                (proposal_id,actor,decision,reason,payload_json,created_at) VALUES (?,?,?,?,?,?)""",
                (proposal_id, actor, decision, reason, json.dumps(payload, sort_keys=True), now))
            db.execute("UPDATE upgrade_proposals SET state=?,updated_at=? WHERE proposal_id=?",
                       (state, now, proposal_id))
        return {"decision_id": cursor.lastrowid, "proposal_id": proposal_id, "state": state,
                "decision": decision, "actor": actor, "reason": reason, "activated": False}

    def activate(self, proposal_id: str, actor: str, reason: str, *, log_path: Path | None = None) -> dict[str, Any]:
        """Activate an approved outer artifact through the append-only pointer log."""
        from .skill_changes import active_revision, approve, change_history, stage
        from .skills import canonical_json, load_artifact, skill_id_for

        proposal_id, actor, reason = (value.strip() for value in (proposal_id, actor, reason))
        if not proposal_id or not actor or not reason:
            raise ValueError("proposal_id, actor and reason are required")
        with closing(self._db()) as db:
            proposal = db.execute("SELECT state FROM upgrade_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
            sandbox = db.execute("SELECT passed,artifact_hash,payload_json FROM upgrade_sandbox_runs WHERE proposal_id=? ORDER BY run_id DESC LIMIT 1", (proposal_id,)).fetchone()
        if proposal is None:
            raise KeyError(proposal_id)
        if proposal["state"] != "approved":
            raise ValueError("activation requires an approved proposal")
        if sandbox is None or not sandbox["passed"]:
            raise ValueError("activation requires a passed sandbox")
        payload = json.loads(sandbox["payload_json"])
        candidate = payload.get("candidate", {})
        family, revision = str(candidate.get("family", "")), str(candidate.get("revision", ""))
        if not family or not revision or sandbox["artifact_hash"] != f"sha256:{revision}":
            raise ValueError("sandbox candidate identity is incomplete")
        artifact = load_artifact(family, revision)
        skill_id = skill_id_for(family)
        history = change_history(log_path=log_path)
        previous = active_revision(skill_id, history)
        if not any(item.get("action") == "staged" and item.get("skill_id") == skill_id and item.get("skill_hash") == revision for item in history):
            stage(skill_id, canonical_json(artifact), f"Hermes proposal {proposal_id}", log_path=log_path)
        if previous != revision:
            approve(skill_id, revision, {"proposal_id": proposal_id, "artifact_hash": sandbox["artifact_hash"]}, log_path=log_path)
        now = time.time()
        with closing(self._db()) as db, db:
            cursor = db.execute("INSERT INTO upgrade_activations (proposal_id,actor,action,family,revision,previous_revision,reason,created_at) VALUES (?,?,?,?,?,?,?,?)",
                                (proposal_id, actor, "activate", family, revision, previous, reason, now))
            db.execute("UPDATE upgrade_proposals SET state='activated',updated_at=? WHERE proposal_id=?", (now, proposal_id))
        return {"activation_id": cursor.lastrowid, "proposal_id": proposal_id, "state": "activated",
                "family": family, "revision": revision, "previous_revision": previous}

    def rollback(self, proposal_id: str, target_revision: str, actor: str, reason: str,
                 *, log_path: Path | None = None) -> dict[str, Any]:
        """Move an activated outer pointer back to a previously approved revision."""
        from .skill_changes import active_revision, rollback
        from .skills import skill_id_for

        proposal_id, target_revision, actor, reason = (value.strip() for value in (proposal_id, target_revision, actor, reason))
        if not all((proposal_id, target_revision, actor, reason)):
            raise ValueError("proposal_id, target_revision, actor and reason are required")
        with closing(self._db()) as db:
            row = db.execute("SELECT state FROM upgrade_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
            activation = db.execute("SELECT family FROM upgrade_activations WHERE proposal_id=? AND action='activate' ORDER BY activation_id DESC LIMIT 1", (proposal_id,)).fetchone()
        if row is None or activation is None:
            raise KeyError(proposal_id)
        if row["state"] != "activated":
            raise ValueError("rollback requires an activated proposal")
        family = str(activation["family"])
        skill_id = skill_id_for(family)
        previous = active_revision(skill_id, log_path=log_path)
        rollback(skill_id, target_revision, reason, log_path=log_path)
        now = time.time()
        with closing(self._db()) as db, db:
            cursor = db.execute("INSERT INTO upgrade_activations (proposal_id,actor,action,family,revision,previous_revision,reason,created_at) VALUES (?,?,?,?,?,?,?,?)",
                                (proposal_id, actor, "rollback", family, target_revision, previous, reason, now))
            db.execute("UPDATE upgrade_proposals SET state='rolled_back',updated_at=? WHERE proposal_id=?", (now, proposal_id))
        return {"activation_id": cursor.lastrowid, "proposal_id": proposal_id, "state": "rolled_back",
                "family": family, "revision": target_revision, "previous_revision": previous}
