#!/usr/bin/env python3
"""Verify the references truth-audit evidence stays conservative."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT = ROOT / "docs" / "audit" / "REFERENCES-TRUTH-AUDIT.md"
DEFAULT_REFERENCES_EXPORT = Path("/tmp/trustforge-devlog/references.html")
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


def _export_lines(text: str) -> list[str]:
    text = re.sub(r"<[^>]+>", "\n", text)
    text = re.sub(r"&nbsp;?", " ", text)
    return [line.strip() for line in text.splitlines() if line.strip()]


def _export_line_matching(lines: list[str], pattern: str, subject: str) -> str:
    for line in lines:
        if re.search(pattern, line, re.IGNORECASE):
            return line
    raise AssertionError(f"references export missing conservative status: {subject}")


def _reject_verified(lines: list[str], patterns: dict[str, str]) -> None:
    for subject, pattern in patterns.items():
        if any(re.search(pattern, line, re.IGNORECASE) for line in lines):
            raise AssertionError(f"{subject} must not be marked verified in references export")


def verify_references_export(
    path: Path = DEFAULT_REFERENCES_EXPORT, *, require_present: bool = False
) -> list[str]:
    """Verify exported public references.html uses conservative v2 statuses when available."""
    if not path.exists():
        if require_present:
            raise AssertionError(f"references export is required but missing: {path}")
        return [f"references export not present; skipped {path}"]

    lines = _export_lines(path.read_text(encoding="utf-8"))
    checks: list[str] = []

    required_fragments = {
        "HOYA BIT historical OHLCV remains verified": r"HOYA BIT.{0,120}OHLCV.{0,120}(?:✅|verified)",
        "HOYA BIT live ticker remains blocked": r"HOYA BIT.{0,120}(?:live|ticker).{0,120}(?:⚠|blocked)",
        "GitHub Actions workflow disabled state is public": r"GitHub Actions.{0,160}(?:\.disabled|disabled|停用)",
        "Production deploy disabled state is public": r"(?:Production Deploy|deploy-production).{0,160}(?:\.disabled|disabled|停用)",
        "AgentCore routing remains unverified": r"AgentCore.{0,160}(?:🟡|implemented-not-verified|not verified|未驗證)",
        "manipulation detection remains informational-only": r"(?:manipulation|協同行為).{0,180}(?:informational-only|不扣分|🟡)",
    }
    for subject, pattern in required_fragments.items():
        _export_line_matching(lines, pattern, subject)
        checks.append(subject)

    _reject_verified(
        lines,
        {
            "HOYA BIT live ticker": r"HOYA BIT.{0,120}(?:live|ticker).{0,120}(?:✅|(?<!not-)(?<!not )verified)",
            "GitHub Actions CI": r"GitHub Actions.{0,160}(?:✅|(?<!not-)(?<!not )verified)",
            "AWS App Runner production evidence": r"App Runner.{0,160}(?:✅|(?<!not-)(?<!not )verified)",
            "EventBridge production evidence": r"EventBridge.{0,160}(?:✅|(?<!not-)(?<!not )verified)",
            "nginx production evidence": r"nginx.{0,160}(?:✅|(?<!not-)(?<!not )verified)",
            "AgentCore runtime routing": r"AgentCore.{0,120}(?:routing|runtime).{0,120}(?:✅|(?<!not-)(?<!not )verified)",
        },
    )
    checks.append("public references export rejects stale verified statuses")

    return checks


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

    calibration_line = _line_matching(lines, r"Guo.*Calibration")
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

    active_workflows = sorted(path.name for path in WORKFLOWS.glob("*.yml"))
    expected = ["ci.yml"] if (WORKFLOWS / "ci.yml").exists() else []
    if active_workflows != expected:
        rendered = ", ".join(active_workflows) or "(none)"
        raise AssertionError(f"unexpected active GitHub workflow files: {rendered}")
    checks.append("only the non-deployment CI workflow is active (or all disabled)")

    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--references-html", type=Path, default=DEFAULT_REFERENCES_EXPORT)
    parser.add_argument(
        "--require-references-export",
        action="store_true",
        help="fail when --references-html is missing instead of treating it as an optional local export",
    )
    args = parser.parse_args()
    checks = verify_audit(args.path)
    checks.extend(
        verify_references_export(
            args.references_html, require_present=args.require_references_export
        )
    )
    for check in checks:
        print(f"ok - {check}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
