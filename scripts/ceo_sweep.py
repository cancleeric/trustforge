#!/usr/bin/env python3
"""CEO sweep for e2e coverage, issues, PRs and next development planning."""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _run(args: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(args, cwd=REPO, text=True, capture_output=True, timeout=20, check=False)
    except Exception as exc:
        return False, str(exc)
    return result.returncode == 0, result.stdout.strip() or result.stderr.strip()


def _json_cmd(args: list[str]) -> object:
    ok, out = _run(args)
    if not ok or not out:
        return {"error": out or "command failed", "args": args}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"error": "invalid json", "output": out[:1000], "args": args}


def _e2e_inventory() -> dict:
    files = sorted(str(path.relative_to(REPO)) for path in (REPO / "frontend" / "src").rglob("*test.tsx"))
    e2e_like = [path for path in files if any(key in path.lower() for key in ("dashboard", "history", "compare", "analyze", "workspace"))]
    return {"test_files": len(files), "workflow_like_tests": e2e_like}


def build_report() -> dict:
    prs = _json_cmd(["gh", "pr", "list", "--state", "open", "--json", "number,title,headRefName,baseRefName,reviewRequests,mergeStateStatus,statusCheckRollup"])
    issues = _json_cmd(["gh", "issue", "list", "--state", "open", "--limit", "20", "--json", "number,title,labels,updatedAt"])
    e2e = _e2e_inventory()
    questions = [
        {
            "q": "e2e 覆蓋率是否正在往世界第一前進？",
            "a": "優先檢查 Analyze、Compare、History、workspace navigation 與 release gate；缺口應升級成 issue 或 PR 任務。",
        },
        {
            "q": "是否有 issue 需要本輪處理？",
            "a": "查看 open issues 的 UX、CI、production、cost 類標籤；高風險 issue 必須進 CEO 計劃審查。",
        },
        {
            "q": "是否有 PR 卡住 merge gate？",
            "a": "每個 PR 必須有 reviewer、CI 綠、eye scan 與 /codex-review 紀錄；缺任一項不得 merge。",
        },
    ]
    development_plan = {
        "cpo_plan_required": True,
        "ceo_review_required_before_implementation": True,
        "priority_order": [
            "open PRs with failing CI or missing reviewer",
            "open issues labeled production, bug, e2e, cost or release",
            "unfinished development plan milestones",
            "new e2e coverage gaps",
        ],
        "development_dispatch": [
            "assign one owner per PR blocker and record the next action",
            "convert selected issue into a scoped plan with acceptance tests",
            "schedule unfinished plan work into now/next/later with a verification gate",
            "turn high-risk e2e gaps into concrete implementation tasks",
        ],
        "merge_guardrails": [
            "reviewer required on every PR",
            "eye scan required before merge",
            "/codex-review required before merge",
            "security changes require harper plus /codex-review",
        ],
        "automation_boundary": "may plan, triage, assign and prepare code work; must not merge or deploy without gates",
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "ceo_sweep_plan_and_dispatch",
        "cadence": "30 minutes",
        "questions": questions,
        "development_plan": development_plan,
        "e2e": e2e,
        "issues": issues,
        "prs": prs,
        "decision": "plan_dispatch_and_develop_after_ceo_gate_no_auto_merge",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run read-only CEO sweep")
    parser.add_argument("--out", type=Path, default=REPO / "out" / "ceo-sweep-latest.json")
    args = parser.parse_args(argv)
    report = build_report()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
