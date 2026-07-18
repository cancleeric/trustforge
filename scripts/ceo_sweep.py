#!/usr/bin/env python3
"""CEO sweep for e2e coverage, issues, PRs and active development planning."""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORLD_FIRST_BAR = "world_first_progress"


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
        "cpo_owner": "gray",
        "ceo_review_required_before_implementation": True,
        "ceo_role": "final_plan_gate_and_execution_dispatch",
        "operating_mode": "active_issue_pr_development",
        "priority_order": [
            "open PRs with failing CI or missing reviewer",
            "open PRs with green CI but missing approval",
            "open issues labeled production, bug, e2e, cost or release",
            "completed issues missing GitHub evidence or closure",
            "unfinished development plan milestones",
            "new e2e coverage gaps",
        ],
        "development_dispatch": [
            {"owner": "gray", "role": "CPO", "action": "write scoped development or optimization plan before implementation"},
            {"owner": "ceo", "role": "CEO", "action": "approve or reject the plan before code work starts"},
            {"owner": "deputy-analysis", "role": "background subagent", "action": "analyze issue, PR, branch, CI and e2e evidence"},
            {"owner": "deputy-implementation", "role": "background subagent", "action": "handle approved scoped code work without blocking CEO interaction"},
            {"owner": "harper", "role": "CISO", "action": "review security-sensitive changes before merge"},
        ],
        "merge_guardrails": [
            "reviewer required on every PR",
            "eye scan required before merge",
            "/codex-review required before merge",
            "security changes require harper plus /codex-review",
        ],
        "automation_boundary": "may plan, triage, assign, prepare code work and draft evidence; must not merge or deploy without gates",
    }
    cpo_plan = {
        "author": "gray",
        "objective": "continue developing and optimizing TrustForge toward world-class demo and engineering depth",
        "required_sections": [
            "ranked execution queue",
            "owner and next action",
            "acceptance criteria",
            "test evidence required before reporting done",
            "PR reviewer and merge gate",
            "security review requirement when applicable",
            "blocked reason",
        ],
        "forbidden_automation": [
            "no automatic merge",
            "no automatic deploy",
            "no security-sensitive merge without harper plus /codex-review",
            "no completion report without local verification",
        ],
    }
    ceo_review = {
        "decision": "approved_for_triage_and_planning_only",
        "implementation_gate": "CEO must approve a scoped CPO plan before code changes in each round",
        "progress_report_rule": "report after each milestone or after more than three PRs",
        "ollama_coding_endpoint": "http://yingdemacbook-pro.local:11434/",
        "ollama_boundary": "use only for coding assistance when reachable; do not use for non-code secrets or deployment authority",
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "ceo_sweep_recommendation",
        "cadence": "1 hour",
        WORLD_FIRST_BAR: {
            "question": "這一輪是否讓 TrustForge 更接近世界第一？",
            "answer": "Only count work that improves production reliability, evidence quality, e2e coverage, data depth, security, cost control, or demo readiness.",
        },
        "questions": questions,
        "cpo_plan": cpo_plan,
        "ceo_review": ceo_review,
        "development_plan": development_plan,
        "e2e": e2e,
        "issues": issues,
        "prs": prs,
        "decision": "recommend_plan_and_dispatch_for_interactive_ceo_execution",
        "execution_status": "not_executed_by_sweep",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run active CEO issue/PR planning sweep")
    parser.add_argument("--out", type=Path, default=REPO / "out" / "ceo-sweep-latest.json")
    args = parser.parse_args(argv)
    report = build_report()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
