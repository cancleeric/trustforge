#!/usr/bin/env python3
"""Validate an outer-skill candidate without changing the active pointer."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from trustforge.skills import artifact_hash, validate_artifact, write_artifact  # noqa: E402


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
    args = parser.parse_args(argv)
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
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
