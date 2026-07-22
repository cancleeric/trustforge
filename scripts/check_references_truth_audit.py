#!/usr/bin/env python3
"""Verify the references truth-audit evidence stays conservative."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT = ROOT / "docs" / "audit" / "REFERENCES-TRUTH-AUDIT.md"
WORKFLOWS = ROOT / ".github" / "workflows"


def _line_matching(lines: list[str], pattern: str) -> str:
    regex = re.compile(pattern, re.IGNORECASE)
    for line in lines:
        if regex.search(line):
            return line
    raise AssertionError(f"missing audit line matching {pattern!r}")


def _require_status(line: str, expected: str, subject: str) -> None:
    if expected not in line:
        raise AssertionError(f"{subject} must include {expected!r}: {line}")


def verify_audit(path: Path = DEFAULT_AUDIT) -> list[str]:
    text = path.read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    checks: list[str] = []

    required_statuses = [
        "✅ verified",
        "🟡 implemented-not-verified",
        "🔬 research/experimental",
        "📚 reference/planned",
        "⛔ excluded",
        "⚠ blocked-external",
    ]
    for status in required_statuses:
        if status not in text:
            raise AssertionError(f"status legend missing {status!r}")
    checks.append("status legend covers conservative states")

    hoya_line = _line_matching(lines, r"HOYA BIT.*ticker|HOYA BIT.*live")
    _require_status(hoya_line, "⚠", "HOYA BIT live ticker")
    checks.append("HOYA BIT live ticker remains blocked until a formal contract exists")

    agentcore_line = _line_matching(lines, r"AgentCore.*registry|AgentCore.*routing")
    _require_status(agentcore_line, "🟡", "AgentCore runtime routing")
    checks.append("AgentCore runtime routing is not represented as production verified")

    calibration_line = _line_matching(lines, r"Guo.*Calibration|calibration.*verified")
    _require_status(calibration_line, "✅ verified", "calibration model artifact")
    checks.append("calibration status is tied to the committed model artifact evidence")

    rag_line = _line_matching(lines, r"Self-RAG|Lewis et al.*RAG")
    _require_status(rag_line, "📚", "RAG references")
    checks.append("RAG remains a reference/planned item")

    manipulation_line = _line_matching(lines, r"manipulation|協同行為")
    _require_status(manipulation_line, "🟡", "manipulation detection")
    checks.append("manipulation detection remains informational-only")

    taiwan_line = _line_matching(lines, r"MOPS|FSC|TWSE|TPEx|台灣")
    if "✅" in taiwan_line:
        raise AssertionError(f"Taiwan regulatory sources must not be verified yet: {taiwan_line}")
    checks.append("Taiwan regulatory sources are not marked verified")

    deploy_line = _line_matching(lines, r"deploy-production\.yml\.disabled|Production Deploy.*停用")
    _require_status(deploy_line, ".disabled", "production deploy workflow")
    checks.append("production deploy workflow is documented as disabled")

    active_workflows = sorted(WORKFLOWS.glob("*.yml"))
    if active_workflows:
        rendered = ", ".join(str(path.relative_to(ROOT)) for path in active_workflows)
        raise AssertionError(f"unexpected active GitHub workflow files: {rendered}")
    checks.append("repository has no active .github/workflows/*.yml files")

    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=DEFAULT_AUDIT)
    args = parser.parse_args()
    checks = verify_audit(args.path)
    for check in checks:
        print(f"ok - {check}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
