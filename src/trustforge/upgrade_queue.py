"""Durable SQLite queue for approval-gated Hermes outer upgrades."""
from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .upgrade_ports import (
    ActivationHandler,
    AuthenticatedPrincipal,
    AuthorityAdapter,
    ModuleCatalog,
    RollbackHandler,
    SandboxAttestation,
)
from .upgrade_state_machine import (
    activation_transition,
    decision_transition,
    review_transition,
    rollback_transition,
    sandbox_transition,
)


def default_path() -> Path:
    return Path(os.getenv("TRUSTFORGE_SQLITE_PATH", str(Path(__file__).resolve().parents[2] / "out" / "trustforge.sqlite3")))


class UpgradeQueue:
    def __init__(
        self,
        path: Path | None = None,
        *,
        authority: AuthorityAdapter | None = None,
        catalog: ModuleCatalog | None = None,
        activation_handler: ActivationHandler | None = None,
        rollback_handler: RollbackHandler | None = None,
    ):
        self.path = path or default_path()
        self.authority = authority
        self.catalog = catalog
        self.activation_handler = activation_handler
        self.rollback_handler = rollback_handler
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

    @staticmethod
    def _canonical_payload(value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @classmethod
    def _durable_proposal_id(cls, payload_json: str) -> str:
        return "sha256:" + hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

    @staticmethod
    def _logical_id(payload_json: str) -> str:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            raise ValueError("proposal payload is corrupt") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
            raise ValueError("proposal payload logical id is missing")
        logical_id = payload["id"].strip()
        if not logical_id:
            raise ValueError("proposal payload logical id is missing")
        return logical_id

    @classmethod
    def _resolve_review_instance(
        cls,
        db: sqlite3.Connection,
        supplied_id: str,
    ) -> sqlite3.Row:
        direct = db.execute(
            "SELECT proposal_id,state,payload_json,created_at FROM upgrade_proposals "
            "WHERE proposal_id=?",
            (supplied_id,),
        ).fetchone()
        if direct is not None:
            return direct
        candidates = []
        for row in db.execute(
            "SELECT proposal_id,state,payload_json,created_at FROM upgrade_proposals"
        ):
            if (
                cls._logical_id(str(row["payload_json"])) == supplied_id
                and str(row["state"])
                not in {"approved", "rejected", "activated", "rolled_back"}
            ):
                candidates.append(row)
        if not candidates:
            raise ValueError("review proposal instance was not found")
        newest_at = max(float(row["created_at"]) for row in candidates)
        newest = [
            row for row in candidates if float(row["created_at"]) == newest_at
        ]
        if len(newest) != 1:
            raise ValueError("logical proposal id resolves ambiguously")
        return newest[0]

    def resolve_latest_reviewable_instance(
        self, logical_or_durable: str
    ) -> dict[str, str]:
        """Resolve the latest instance for an interactive sandbox operator.

        Automated review must use :meth:`resolve_exact_review_instance`; using
        logical newest for an in-flight diagnostic would permit round drift.
        """
        supplied = logical_or_durable.strip()
        if not supplied:
            raise ValueError("proposal id is required")
        with closing(self._db()) as db:
            row = self._resolve_review_instance(db, supplied)
        payload_json = str(row["payload_json"])
        return {
            "proposal_id": str(row["proposal_id"]),
            "logical_id": self._logical_id(payload_json),
            "payload_sha256": hashlib.sha256(
                payload_json.encode("utf-8")
            ).hexdigest(),
        }

    def resolve_review_instance(self, logical_or_durable: str) -> dict[str, str]:
        """Backward-compatible sandbox alias for latest reviewable resolution."""
        return self.resolve_latest_reviewable_instance(logical_or_durable)

    def resolve_latest_sandbox_instance(
        self, logical_or_durable: str
    ) -> dict[str, str]:
        """Resolve only the newest unique instance eligible for sandbox."""
        supplied = logical_or_durable.strip()
        if not supplied:
            raise ValueError("proposal id is required")
        eligible = {"llm_reviewed", "sandbox_failed"}
        with closing(self._db()) as db:
            direct = db.execute(
                "SELECT proposal_id,state,payload_json,created_at "
                "FROM upgrade_proposals WHERE proposal_id=?",
                (supplied,),
            ).fetchone()
            if direct is not None:
                candidates = [direct] if str(direct["state"]) in eligible else []
            else:
                candidates = [
                    row
                    for row in db.execute(
                        "SELECT proposal_id,state,payload_json,created_at "
                        "FROM upgrade_proposals"
                    )
                    if str(row["state"]) in eligible
                    and self._logical_id(str(row["payload_json"])) == supplied
                ]
        if not candidates:
            raise ValueError("no sandbox-eligible proposal instance was found")
        newest_at = max(float(row["created_at"]) for row in candidates)
        newest = [
            row for row in candidates if float(row["created_at"]) == newest_at
        ]
        if len(newest) != 1:
            raise ValueError("sandbox proposal id resolves ambiguously")
        row = newest[0]
        payload_json = str(row["payload_json"])
        return {
            "proposal_id": str(row["proposal_id"]),
            "logical_id": self._logical_id(payload_json),
            "payload_sha256": hashlib.sha256(
                payload_json.encode("utf-8")
            ).hexdigest(),
        }

    def resolve_exact_review_instance(
        self, proposal: dict[str, Any]
    ) -> dict[str, str]:
        """Bind one exact producer payload to its content-addressed DB row."""
        if not isinstance(proposal, dict):
            raise ValueError("review proposal payload must be an object")
        payload_json = self._canonical_payload(proposal)
        durable_id = self._durable_proposal_id(payload_json)
        with closing(self._db()) as db:
            row = db.execute(
                "SELECT proposal_id,state,payload_json FROM upgrade_proposals "
                "WHERE proposal_id=?",
                (durable_id,),
            ).fetchone()
        if row is None or str(row["payload_json"]) != payload_json:
            raise ValueError("exact review proposal instance was not ingested")
        if str(row["state"]) in {
            "approved", "rejected", "activated", "rolled_back"
        }:
            raise ValueError("exact review proposal instance is terminal")
        return {
            "proposal_id": durable_id,
            "logical_id": self._logical_id(payload_json),
            "payload_sha256": hashlib.sha256(
                payload_json.encode("utf-8")
            ).hexdigest(),
        }

    @classmethod
    def _proposal_binding(cls, payload_json: str) -> tuple[str, str]:
        payload = json.loads(payload_json)
        if not isinstance(payload, dict):
            raise ValueError("proposal payload must be an object")
        tenant_id = payload.get("tenant_id", "")
        if not isinstance(tenant_id, str):
            raise ValueError("proposal tenant_id must be a string")
        checksum = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        return tenant_id, checksum

    @classmethod
    def _validate_approved_binding(
        cls,
        db: sqlite3.Connection,
        proposal_id: str,
        payload_json: str,
    ) -> None:
        decision = db.execute(
            "SELECT payload_json FROM upgrade_decisions "
            "WHERE proposal_id=? AND decision='approve' "
            "ORDER BY decision_id DESC LIMIT 1",
            (proposal_id,),
        ).fetchone()
        if decision is None:
            raise ValueError("activation requires a bound approved decision")
        try:
            binding = json.loads(decision["payload_json"])
        except json.JSONDecodeError as exc:
            raise ValueError("approved decision binding is corrupt") from exc
        required = {
            "previous_state",
            "tenant_id",
            "proposal_payload_sha256",
        }
        if not isinstance(binding, dict) or set(binding) != required:
            raise ValueError("approved decision binding is missing or obsolete")
        tenant_id, checksum = cls._proposal_binding(payload_json)
        if (
            binding["tenant_id"] != tenant_id
            or binding["proposal_payload_sha256"] != checksum
        ):
            raise ValueError("proposal identity no longer matches approved decision")

    def sync_diagnostic(self, report: dict[str, Any]) -> int:
        now, count = time.time(), 0
        with closing(self._db()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            for item in report.get("proposals", []):
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                logical_id = str(item["id"]).strip()
                if not logical_id:
                    continue
                payload_json = self._canonical_payload(item)
                existing = None
                for row in db.execute(
                    "SELECT proposal_id,payload_json FROM upgrade_proposals"
                ):
                    if str(row["payload_json"]) == payload_json:
                        existing = row
                        break
                if existing is None:
                    proposal_id = self._durable_proposal_id(payload_json)
                    collision = db.execute(
                        "SELECT payload_json FROM upgrade_proposals WHERE proposal_id=?",
                        (proposal_id,),
                    ).fetchone()
                    if collision is not None and collision["payload_json"] != payload_json:
                        raise ValueError("content-addressed proposal id collision")
                    db.execute(
                        """INSERT INTO upgrade_proposals
                        (proposal_id,area,severity,payload_json,state,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?)""",
                        (
                            proposal_id,
                            str(item.get("area", "unknown")),
                            str(item.get("severity", "medium")),
                            payload_json,
                            "proposed",
                            now,
                            now,
                        ),
                    )
                else:
                    db.execute(
                        "UPDATE upgrade_proposals SET updated_at=? WHERE proposal_id=?",
                        (now, str(existing["proposal_id"])),
                    )
                count += 1
        return count

    def record_reviews(self, result: dict[str, Any]) -> int:
        now, count = time.time(), 0
        with closing(self._db()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            for item in result.get("reviews", []):
                if not isinstance(item, dict) or not item.get("proposal_id"):
                    continue
                supplied_id = str(item["proposal_id"])
                # Compatibility boundary for the existing LLM reviewer.  The
                # domain and durable row use one exact terminal spelling.
                raw_verdict = str(item.get("verdict", ""))
                verdict = "rejected" if raw_verdict == "reject" else raw_verdict
                row = db.execute(
                    "SELECT proposal_id,state,payload_json,created_at "
                    "FROM upgrade_proposals WHERE proposal_id=?",
                    (supplied_id,),
                ).fetchone()
                if row is None:
                    raise ValueError(
                        "record_reviews requires a durable proposal_id"
                    )
                proposal_id = str(row["proposal_id"])
                supplied_checksum = item.get("payload_sha256")
                if supplied_checksum is not None:
                    actual_checksum = hashlib.sha256(
                        str(row["payload_json"]).encode("utf-8")
                    ).hexdigest()
                    if supplied_checksum != actual_checksum:
                        raise ValueError("review proposal payload checksum mismatch")
                # Validate before writing the review row.  Invalid verdicts and
                # terminal overwrite attempts leave persistence byte-for-byte
                # untouched.
                if str(row["state"]) in {"approved", "rejected", "activated", "rolled_back"}:
                    raise ValueError("terminal proposal cannot be reviewed")
                next_state = review_transition(verdict).state
                db.execute("INSERT INTO upgrade_reviews (proposal_id,reviewer,verdict,payload_json,created_at) VALUES (?,?,?,?,?)",
                           (proposal_id, "bedrock-adversarial-reviewer", verdict,
                            json.dumps(item, ensure_ascii=False, sort_keys=True), now))
                changed = db.execute(
                    "UPDATE upgrade_proposals SET state=?,updated_at=? "
                    "WHERE proposal_id=? AND state=?",
                    (next_state, now, proposal_id, str(row["state"])),
                )
                if changed.rowcount != 1:
                    raise ValueError("proposal state changed concurrently")
                count += 1
        return count

    def status(self, limit: int = 50) -> dict[str, Any]:
        with closing(self._db()) as db:
            proposal_rows = list(db.execute(
                "SELECT proposal_id,area,severity,state,payload_json,created_at,updated_at "
                "FROM upgrade_proposals ORDER BY updated_at DESC LIMIT ?", (limit,)))
            proposals = [
                {
                    key: row[key]
                    for key in (
                        "proposal_id", "area", "severity", "state",
                        "created_at", "updated_at",
                    )
                }
                | {"logical_id": self._logical_id(str(row["payload_json"]))}
                for row in proposal_rows
            ]
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

    def record_sandbox(
        self,
        attestation: SandboxAttestation | str,
        passed: bool | None = None,
        artifact_hash: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist a bounded sandbox result; this never activates a candidate."""
        # Compatibility boundary for the repository-owned local sandbox
        # runner.  HTTP never reaches this branch: web.py accepts only an
        # injected attestation factory and rejects caller-supplied results.
        if isinstance(attestation, str):
            if (
                not isinstance(passed, bool)
                or not isinstance(artifact_hash, str)
                or not isinstance(details, dict)
                or details.get("runner") != "run_skill_sandbox.py"
                or not isinstance(details.get("candidate"), dict)
            ):
                raise PermissionError("trusted sandbox attestation is required")
            candidate = details["candidate"]
            revision = str(candidate.get("revision", ""))
            canonical = json.dumps(
                details, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            attestation = SandboxAttestation(
                proposal_id=attestation,
                candidate_family=str(candidate.get("family", "")),
                candidate_revision=revision,
                run_id=hashlib.sha256(canonical.encode()).hexdigest(),
                runner_version="run_skill_sandbox.py/compat-v1",
                artifact_hash=artifact_hash,
                details_checksum=hashlib.sha256(canonical.encode()).hexdigest(),
                passed=passed,
                completed_at=datetime.now(timezone.utc),
                details=details,
            )
        if not isinstance(attestation, SandboxAttestation):
            raise PermissionError("trusted sandbox attestation is required")
        proposal_id = attestation.proposal_id.strip()
        artifact_hash = attestation.artifact_hash.strip()
        if not proposal_id or not artifact_hash:
            raise ValueError("proposal_id and artifact_hash are required")
        if attestation.completed_at.tzinfo is None:
            raise ValueError("sandbox attestation time must be timezone-aware")
        completed = attestation.completed_at.astimezone(timezone.utc)
        wall_now = datetime.now(timezone.utc)
        if completed < wall_now - timedelta(hours=24) or completed > wall_now + timedelta(minutes=5):
            raise ValueError("sandbox attestation time is outside the acceptance window")
        if not attestation.run_id.strip() or not attestation.runner_version.strip():
            raise ValueError("sandbox attestation runner identity is required")
        details_json = json.dumps(
            attestation.details, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        checksum = hashlib.sha256(details_json.encode("utf-8")).hexdigest()
        if checksum != attestation.details_checksum:
            raise ValueError("sandbox attestation checksum mismatch")
        candidate = attestation.details.get("candidate")
        if not isinstance(candidate, dict) or (
            str(candidate.get("family")) != attestation.candidate_family
            or str(candidate.get("revision")) != attestation.candidate_revision
            or artifact_hash != f"sha256:{attestation.candidate_revision}"
        ):
            raise ValueError("sandbox attestation candidate identity mismatch")
        now = time.time()
        with closing(self._db()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM upgrade_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
            if row is None:
                raise KeyError(proposal_id)
            transition = sandbox_transition(str(row["state"]), attestation.passed)
            payload = {
                **attestation.details,
                "attestation": {
                    "run_id": attestation.run_id,
                    "runner_version": attestation.runner_version,
                    "details_checksum": attestation.details_checksum,
                    "completed_at": attestation.completed_at.isoformat(),
                },
            }
            cursor = db.execute("""INSERT INTO upgrade_sandbox_runs
                (proposal_id,passed,artifact_hash,payload_json,created_at) VALUES (?,?,?,?,?)""",
                (proposal_id, int(attestation.passed), artifact_hash,
                 json.dumps(payload, ensure_ascii=False, sort_keys=True), now))
            changed = db.execute(
                "UPDATE upgrade_proposals SET state=?,updated_at=? "
                "WHERE proposal_id=? AND state=?",
                (transition.state, now, proposal_id, str(row["state"])),
            )
            if changed.rowcount != 1:
                raise ValueError("proposal state changed concurrently")
            return {"run_id": cursor.lastrowid, "proposal_id": proposal_id, "state": transition.state,
                    "passed": attestation.passed, "artifact_hash": artifact_hash}

    @staticmethod
    def _tenant_id(payload_json: str) -> str:
        payload = json.loads(payload_json)
        value = payload.get("tenant_id", "") if isinstance(payload, dict) else ""
        return str(value)

    def _actor(
        self,
        principal: AuthenticatedPrincipal | None,
        action: str,
        tenant_id: str,
    ) -> str:
        if self.authority is None or principal is None:
            raise PermissionError("trusted authorization context is required")
        return self.authority.require(principal, action, tenant_id=tenant_id)

    def decide(
        self,
        proposal_id: str,
        decision: str,
        reason: str,
        *,
        principal: AuthenticatedPrincipal | None = None,
    ) -> dict[str, Any]:
        """Record the human gate. Approval requires the latest sandbox to pass."""
        proposal_id, decision, reason = (value.strip() for value in (proposal_id, decision, reason))
        if decision not in {"approve", "reject"} or not reason:
            raise ValueError("decision and reason are required")
        now = time.time()
        with closing(self._db()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,payload_json FROM upgrade_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
            if row is None:
                raise KeyError(proposal_id)
            tenant_id, proposal_checksum = self._proposal_binding(row["payload_json"])
            actor = self._actor(principal, f"upgrade:{decision}", tenant_id)
            transition = decision_transition(str(row["state"]), decision)
            decision_payload = {
                **transition.payload,
                "tenant_id": tenant_id,
                "proposal_payload_sha256": proposal_checksum,
            }
            cursor = db.execute("""INSERT INTO upgrade_decisions
                (proposal_id,actor,decision,reason,payload_json,created_at) VALUES (?,?,?,?,?,?)""",
                (
                    proposal_id,
                    actor,
                    decision,
                    reason,
                    json.dumps(decision_payload, sort_keys=True),
                    now,
                ))
            changed = db.execute(
                "UPDATE upgrade_proposals SET state=?,updated_at=? "
                "WHERE proposal_id=? AND state=?",
                (transition.state, now, proposal_id, str(row["state"])),
            )
            if changed.rowcount != 1:
                raise ValueError("proposal state changed concurrently")
            return {"decision_id": cursor.lastrowid, "proposal_id": proposal_id, "state": transition.state,
                "decision": decision, "actor": actor, "reason": reason, "activated": False}

    def activate(
        self,
        proposal_id: str,
        reason: str,
        *,
        principal: AuthenticatedPrincipal | None = None,
    ) -> dict[str, Any]:
        """Activate an approved outer artifact through the append-only pointer log."""
        proposal_id, reason = (value.strip() for value in (proposal_id, reason))
        if not proposal_id or not reason:
            raise ValueError("proposal_id and reason are required")
        with closing(self._db()) as db:
            proposal = db.execute("SELECT state,payload_json FROM upgrade_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
            sandbox = db.execute("SELECT passed,artifact_hash,payload_json FROM upgrade_sandbox_runs WHERE proposal_id=? ORDER BY run_id DESC LIMIT 1", (proposal_id,)).fetchone()
        if proposal is None:
            raise KeyError(proposal_id)
        with closing(self._db()) as db:
            self._validate_approved_binding(
                db, proposal_id, str(proposal["payload_json"])
            )
        actor = self._actor(principal, "upgrade:activate", self._tenant_id(proposal["payload_json"]))
        if str(proposal["state"]) == "activated":
            with closing(self._db()) as db:
                existing = db.execute(
                    "SELECT activation_id,family,revision,previous_revision FROM upgrade_activations "
                    "WHERE proposal_id=? AND action='activate' ORDER BY activation_id DESC LIMIT 1",
                    (proposal_id,),
                ).fetchone()
            if existing is None:
                raise ValueError("activated proposal has no activation record")
            return {"activation_id": existing["activation_id"], "proposal_id": proposal_id,
                    "state": "activated", "family": existing["family"],
                    "revision": existing["revision"], "previous_revision": existing["previous_revision"]}
        transition = activation_transition(str(proposal["state"]), bool(sandbox and sandbox["passed"]))
        if sandbox is None:
            raise ValueError("activation requires passed sandbox")
        payload = json.loads(sandbox["payload_json"])
        candidate = payload.get("candidate", {})
        family, revision = str(candidate.get("family", "")), str(candidate.get("revision", ""))
        if not family or not revision:
            raise ValueError("sandbox candidate identity is incomplete")
        if self.catalog is None or self.activation_handler is None:
            raise RuntimeError("upgrade activation ports are not configured")
        resolved = self.catalog.resolve(family, revision, str(sandbox["artifact_hash"]))
        operation_id = hashlib.sha256(
            f"activate\0{proposal_id}\0{family}\0{revision}".encode()
        ).hexdigest()
        with closing(self._db()) as db:
            prior = db.execute(
                "SELECT revision FROM upgrade_activations WHERE family=? "
                "ORDER BY activation_id DESC LIMIT 1",
                (family,),
            ).fetchone()
        expected_revision = self.activation_handler.current_revision(family)
        if prior is not None and str(prior["revision"]) != expected_revision:
            raise RuntimeError("activation pointer disagrees with durable queue history")
        change = self.activation_handler.activate(
            resolved,
            proposal_id=proposal_id,
            operation_id=operation_id,
            expected_revision=expected_revision,
        )
        if (
            change.operation_id != operation_id
            or change.family != family
            or change.revision != revision
        ):
            raise RuntimeError("activation handler returned mismatched receipt")
        now = time.time()
        with closing(self._db()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("INSERT INTO upgrade_activations (proposal_id,actor,action,family,revision,previous_revision,reason,created_at) VALUES (?,?,?,?,?,?,?,?)",
                                (proposal_id, actor, "activate", change.family, change.revision, change.previous_revision, reason, now))
            changed = db.execute(
                "UPDATE upgrade_proposals SET state=?,updated_at=? "
                "WHERE proposal_id=? AND state='approved'",
                (transition.state, now, proposal_id),
            )
            if changed.rowcount != 1:
                raise ValueError("proposal state changed concurrently")
        return {"activation_id": cursor.lastrowid, "proposal_id": proposal_id, "state": transition.state,
                "family": change.family, "revision": change.revision, "previous_revision": change.previous_revision}

    def rollback(
        self,
        proposal_id: str,
        target_revision: str,
        reason: str,
        *,
        principal: AuthenticatedPrincipal | None = None,
    ) -> dict[str, Any]:
        """Move an activated outer pointer back to a previously approved revision."""
        proposal_id, target_revision, reason = (value.strip() for value in (proposal_id, target_revision, reason))
        if not all((proposal_id, target_revision, reason)):
            raise ValueError("proposal_id, target_revision and reason are required")
        with closing(self._db()) as db:
            row = db.execute("SELECT state,payload_json FROM upgrade_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
            activation = db.execute("SELECT family,revision,previous_revision FROM upgrade_activations WHERE proposal_id=? AND action='activate' ORDER BY activation_id DESC LIMIT 1", (proposal_id,)).fetchone()
        if row is None or activation is None:
            raise KeyError(proposal_id)
        with closing(self._db()) as db:
            self._validate_approved_binding(
                db, proposal_id, str(row["payload_json"])
            )
        actor = self._actor(principal, "upgrade:rollback", self._tenant_id(row["payload_json"]))
        if str(row["state"]) == "rolled_back":
            with closing(self._db()) as db:
                existing = db.execute(
                    "SELECT activation_id,family,revision,previous_revision FROM upgrade_activations "
                    "WHERE proposal_id=? AND action='rollback' ORDER BY activation_id DESC LIMIT 1",
                    (proposal_id,),
                ).fetchone()
            if existing is None or str(existing["revision"]) != target_revision:
                raise ValueError("proposal already rolled back to a different revision")
            return {"activation_id": existing["activation_id"], "proposal_id": proposal_id,
                    "state": "rolled_back", "family": existing["family"],
                    "revision": existing["revision"], "previous_revision": existing["previous_revision"]}
        transition = rollback_transition(str(row["state"]))
        family = str(activation["family"])
        allowed_target = activation["previous_revision"]
        if not allowed_target or target_revision != str(allowed_target):
            raise ValueError("rollback target must equal the activation previous revision")
        if self.rollback_handler is None:
            raise RuntimeError("upgrade rollback port is not configured")
        operation_id = hashlib.sha256(
            f"rollback\0{proposal_id}\0{family}\0{activation['revision']}\0{target_revision}".encode()
        ).hexdigest()
        change = self.rollback_handler.rollback(
            family,
            target_revision,
            reason=reason,
            operation_id=operation_id,
            expected_revision=str(activation["revision"]),
        )
        if (
            change.operation_id != operation_id
            or change.family != family
            or change.revision != target_revision
            or change.previous_revision != str(activation["revision"])
        ):
            raise RuntimeError("rollback handler returned mismatched receipt")
        now = time.time()
        with closing(self._db()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("INSERT INTO upgrade_activations (proposal_id,actor,action,family,revision,previous_revision,reason,created_at) VALUES (?,?,?,?,?,?,?,?)",
                                (proposal_id, actor, "rollback", change.family, change.revision, change.previous_revision, reason, now))
            changed = db.execute(
                "UPDATE upgrade_proposals SET state=?,updated_at=? "
                "WHERE proposal_id=? AND state='activated'",
                (transition.state, now, proposal_id),
            )
            if changed.rowcount != 1:
                raise ValueError("proposal state changed concurrently")
        return {"activation_id": cursor.lastrowid, "proposal_id": proposal_id, "state": transition.state,
                "family": change.family, "revision": change.revision, "previous_revision": change.previous_revision}
