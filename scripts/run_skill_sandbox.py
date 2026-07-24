#!/usr/bin/env python3
"""Validate and submit through the sole trusted local sandbox runner path.

The retired Web endpoint never accepts sandbox results.  This process issues
and consumes its internal capability locally and never emits that capability.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from trustforge.skills import artifact_hash, validate_artifact, write_artifact  # noqa: E402
from trustforge.upgrade_adapters import (  # noqa: E402
    JournalCapacityError,
    SandboxAttestationAuthority,
)
from trustforge.upgrade_queue import UpgradeQueue  # noqa: E402


def _run(argv: list[str]) -> dict:
    completed = subprocess.run(argv, cwd=REPO, text=True, capture_output=True, check=False)
    return {"argv": argv, "returncode": completed.returncode, "stdout_tail": completed.stdout[-2000:], "stderr_tail": completed.stderr[-2000:]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--limit", type=int, default=24)
    parser.add_argument("--out", type=Path, default=REPO / "out" / "skill-sandbox.json")
    parser.add_argument("--with-replay", action="store_true")
    parser.add_argument("--replay-coin", default="BTC")
    parser.add_argument(
        "--proposal-id",
        help="trusted local-only submission to this durable upgrade proposal",
    )
    parser.add_argument("--queue-db", type=Path, help="SQLite queue path; defaults to TRUSTFORGE_SQLITE_PATH")
    args = parser.parse_args(argv)
    sandbox_authority = SandboxAttestationAuthority() if args.proposal_id else None
    queue = (
        UpgradeQueue(args.queue_db, sandbox_verifier=sandbox_authority)
        if args.proposal_id
        else None
    )
    proposal_binding = (
        queue.resolve_latest_sandbox_instance(args.proposal_id)
        if queue is not None and args.proposal_id
        else None
    )
    candidate = json.loads(args.artifact.read_text(encoding="utf-8"))
    if not isinstance(candidate, dict):
        parser.error("artifact must be a JSON object")
    validate_artifact(candidate)
    revision, stored = write_artifact(candidate)
    with tempfile.TemporaryDirectory(prefix="trustforge-skill-sandbox-") as temp:
        qa_path = Path(temp) / "question-bank.json"
        qa = _run([sys.executable, "scripts/run_question_bank.py", "--limit", str(args.limit), "--out", str(qa_path)])
        replay = None
        if args.with_replay:
            replay = _run([sys.executable, "scripts/run_historical_replay.py", "--coin", args.replay_coin, "--out", str(Path(temp) / "replay.json")])
    result = {
        "candidate": {"family": candidate["family"], "revision": revision, "stored_at": str(stored)},
        "question_bank": qa,
        "replay": replay,
        "passed": qa["returncode"] == 0 and (replay is None or replay["returncode"] == 0),
        "activation": "not activated; use manage_skill_change.py approve with this evidence after human review",
    }
    if proposal_binding is not None and queue is not None:
        result["proposal_binding"] = proposal_binding
        details = {"runner": "run_skill_sandbox.py", **result}
        details_json = json.dumps(
            details, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        assert sandbox_authority is not None
        issue_args = {
            "db_identity": str(queue.path.resolve(strict=False)),
            "proposal_id": proposal_binding["proposal_id"],
            "candidate_family": str(candidate["family"]),
            "candidate_revision": revision,
            "artifact_hash": f"sha256:{artifact_hash(candidate)}",
            "run_id": artifact_hash({
                "family": candidate["family"], "rules": candidate["rules"],
            }),
            "runner_version": "run_skill_sandbox.py/v2",
            "details_checksum": hashlib.sha256(
                details_json.encode("utf-8")
            ).hexdigest(),
            "passed": bool(result["passed"]),
            "completed_at": datetime.now(timezone.utc),
            "details": details,
        }
        try:
            issued = sandbox_authority.issue(**issue_args)
        except JournalCapacityError:
            queue.compact_sandbox_journal()
            issued = sandbox_authority.issue(**issue_args)
        result["queue_run"] = queue.record_sandbox(issued)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
