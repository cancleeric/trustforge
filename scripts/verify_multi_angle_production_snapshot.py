#!/usr/bin/env python3
"""Read-only acceptance gate for #808–#811 production multi-angle evidence.

This rejects a missing target snapshot, synthetic/manual payloads, or provenance
that proves only job metadata instead of the prompt context actually committed by
the Claim Extraction stage.

Usage: python3 scripts/verify_multi_angle_production_snapshot.py \
    <deployed_snapshot_id> [--sqlite-path /shared/trustforge.sqlite3]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from trustforge.bedrock import (  # noqa: E402
    _CLAIM_EXTRACTION_CONTEXT_VERSION,
    claim_extraction_prompt_context,
)

MODES = {"risk", "sentiment", "fundamentals", "news", "catalyst"}
REQUIRED_STAGES = {
    "source_ingestion",
    "claim_extraction",
    "trust_reasoning",
    "evidence_assembly",
    "report_delivery",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _connect_readonly(path: Path) -> sqlite3.Connection:
    """Open an existing SQLite projection without any write capability."""
    resolved = path.resolve()
    uri = f"file:{quote(str(resolved), safe='/')}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _claim_context_matches(payload: dict, *, mode: str, question: str) -> bool:
    receipt = payload.get("claim_extraction_context")
    if not isinstance(receipt, dict):
        return False
    return receipt == {
        "contract_version": _CLAIM_EXTRACTION_CONTEXT_VERSION,
        "mode": mode,
        "question_sha256": _sha256(question),
        "prompt_context_sha256": _sha256(
            claim_extraction_prompt_context(mode, question)
        ),
        "model_invoked": True,
    }


def _has_matching_execution_receipt(payload: dict, receipt: dict) -> bool:
    events = payload.get("execution_log")
    if not isinstance(events, list):
        return False
    return any(
        isinstance(event, dict)
        and event.get("tool") == "bedrock.claim_extraction_context"
        and isinstance(event.get("params"), dict)
        and all(event["params"].get(key) == value for key, value in receipt.items())
        for event in events
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify one newly deployed production multi-angle snapshot."
    )
    parser.add_argument(
        "snapshot_id",
        help="Snapshot ID returned by the newly deployed BTC five-angle run.",
    )
    parser.add_argument(
        "--sqlite-path",
        type=Path,
        default=Path("out/trustforge.sqlite3"),
        help="Read-only path to the shared production SQLite projection.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    snapshot_id = args.snapshot_id.strip()
    if not snapshot_id:
        fail("deployed snapshot_id must not be empty")
    path = args.sqlite_path
    if not path.exists():
        fail(f"production acceptance store unavailable: {path}")
    conn = _connect_readonly(path)
    conn.row_factory = sqlite3.Row
    try:
        snapshot = conn.execute(
            "SELECT coin FROM analysis_snapshots WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()
        if snapshot is None or snapshot["coin"] != "BTC":
            fail(f"required production snapshot unavailable: {snapshot_id}")
        jobs = conn.execute(
            "SELECT job_id,mode,question,state FROM analysis_jobs WHERE snapshot_id=?",
            (snapshot_id,),
        ).fetchall()
        by_mode = {row["mode"]: row for row in jobs if row["mode"] in MODES}
        if set(by_mode) != MODES or any(
            not row["question"].strip() for row in by_mode.values()
        ):
            fail("five mode-specific production jobs with non-empty questions are required")
        if len({row["question"] for row in by_mode.values()}) != len(MODES):
            fail("five mode-specific production jobs must not share an identical question")

        prompt_context_hashes: set[str] = set()
        for mode, job in by_mode.items():
            stages = {
                row["stage"]
                for row in conn.execute(
                    "SELECT stage FROM analysis_stage_runs WHERE job_id=? AND state='completed'",
                    (job["job_id"],),
                )
            }
            if stages != REQUIRED_STAGES:
                fail(f"{mode} does not have a complete real pipeline sequence")
            result = conn.execute(
                "SELECT payload_json FROM analysis_results "
                "WHERE snapshot_id=? AND coin='BTC' AND mode=? AND job_id=? "
                "ORDER BY published_at DESC LIMIT 1",
                (snapshot_id, mode, job["job_id"]),
            ).fetchone()
            if result is None:
                fail(f"{mode} has no persisted angle result for its production job")
            try:
                angle_payload = json.loads(result["payload_json"])
            except (TypeError, json.JSONDecodeError):
                fail(f"{mode} angle result payload is invalid JSON")
            if not _claim_context_matches(
                angle_payload, mode=mode, question=job["question"]
            ):
                fail(f"{mode} does not prove its actual Claim Extraction prompt context")
            receipt = angle_payload["claim_extraction_context"]
            if not _has_matching_execution_receipt(angle_payload, receipt):
                fail(f"{mode} execution log lacks matching Claim Extraction context receipt")
            prompt_context_hashes.add(receipt["prompt_context_sha256"])

        if len(prompt_context_hashes) != len(MODES):
            fail("five angles reuse a prompt-context hash; semantic branches are not distinct")

        report = conn.execute(
            "SELECT payload_json FROM analysis_results WHERE snapshot_id=? AND mode='multi_angle'",
            (snapshot_id,),
        ).fetchone()
        if report is None:
            fail("production synthesis result is missing")
        try:
            payload = json.loads(report["payload_json"])
        except (TypeError, json.JSONDecodeError):
            fail("production synthesis payload is invalid JSON")
        required = {
            "direction_divergences",
            "completeness_gaps",
            "evidence_overlaps",
            "evidence_independence",
        }
        if not required <= payload.keys():
            fail("synthesis payload lacks separated comparison dimensions")
        if (
            payload["evidence_independence"] == 0
            and "沒有獨立交叉佐證" not in " ".join(payload.get("limits", []))
        ):
            fail("0% independence payload lacks required no-independent-corroboration limit")
        print(json.dumps(
            {"ok": True, "snapshot_id": snapshot_id, "modes": sorted(MODES)},
            ensure_ascii=False,
        ))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
