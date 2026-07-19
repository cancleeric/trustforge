#!/usr/bin/env python3
"""CEO sweep for e2e coverage, issues, PRs and active development planning."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORLD_FIRST_BAR = "world_first_progress"
DEPENDENCY_PATTERNS = (
    re.compile(r"(?:depends on|blocked by|requires)\s+#(\d+)", re.IGNORECASE),
    re.compile(r"(?:相依|阻擋於|需要)\s*#(\d+)", re.IGNORECASE),
)
PRIORITY_LABELS = {
    "p0-critical": 0,
    "ready-now": 1,
    "production": 0,
    "bug": 2,
    "security": 3,
    "release": 4,
    "e2e": 5,
    "cost": 6,
}


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


def _label_names(issue: dict) -> set[str]:
    return {
        str(label.get("name", "")).strip().lower()
        for label in issue.get("labels", [])
        if isinstance(label, dict)
    }


def _dependencies(issue: dict) -> list[int]:
    body = str(issue.get("body", ""))
    return sorted({int(match) for pattern in DEPENDENCY_PATTERNS for match in pattern.findall(body)})


def build_execution_queue(issues: object, prs: object, max_lanes: int) -> list[dict]:
    """Select distinct runnable issues; agents still validate dependencies before coding."""
    if not isinstance(issues, list):
        return []
    active_heads = {
        str(pr.get("headRefName", ""))
        for pr in prs if isinstance(pr, dict)
    } if isinstance(prs, list) else set()
    candidates = []
    for issue in issues:
        if not isinstance(issue, dict) or "number" not in issue:
            continue
        number = int(issue["number"])
        title = str(issue.get("title", ""))
        if title.startswith(("[Decision]", "[Plan]")) or "總控" in title:
            continue
        labels = _label_names(issue)
        dependencies = _dependencies(issue)
        issue_ref = re.compile(rf"(?:^|[-_/])(?:issue-)?{number}(?:[-_/]|$)")
        active_branch = next((head for head in active_heads if issue_ref.search(head)), None)
        if "blocked-external" in labels and active_branch is None:
            continue
        if "needs-evidence" in labels and active_branch is None:
            continue
        priority = min((PRIORITY_LABELS[label] for label in labels if label in PRIORITY_LABELS), default=20)
        if "ready-now" in labels:
            priority = 0
        candidates.append((priority, number, issue, dependencies, active_branch))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [
        {
            "lane": lane,
            "issue": number,
            "title": str(issue.get("title", "")),
            "dependencies": dependencies,
            "dependency_check": "agent_must_confirm_closed_before_implementation",
            "action": "continue_pr" if active_branch else "start_issue",
            "active_branch": active_branch,
        }
        for lane, (_, number, issue, dependencies, active_branch) in enumerate(candidates[:max_lanes], start=1)
    ]


def build_report(max_lanes: int = 1) -> dict:
    prs = _json_cmd(["gh", "pr", "list", "--state", "open", "--json", "number,title,headRefName,baseRefName,reviewRequests,mergeStateStatus,statusCheckRollup"])
    issues = _json_cmd(["gh", "issue", "list", "--state", "open", "--limit", "100", "--json", "number,title,body,labels,updatedAt"])
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
        "proposed_cpo_owner": "gray",
        "ceo_review_required_before_implementation": True,
        "proposed_ceo_role": "final_plan_gate_and_execution_dispatch",
        "operating_mode": "unattended_scoped_issue_lanes",
        "priority_order": [
            "open PRs with failing CI or missing reviewer",
            "open PRs with green CI but missing approval",
            "open issues labeled production, bug, e2e, cost or release",
            "completed issues missing GitHub evidence or closure",
            "unfinished development plan milestones",
            "new e2e coverage gaps",
        ],
        "proposed_development_dispatch": [
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
        "automation_boundary": "runner may dispatch issue lanes; production, main merges, releases, secrets and cost changes remain forbidden",
    }
    cpo_plan = {
        "proposed_author": "gray",
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
        "required_decision": "per_lane_auto_review_after_gray_plan",
        "implementation_gate": "Each lane must record CEO APPROVED after Gray's scoped plan before code changes",
        "progress_report_rule": "report after each milestone or after more than three PRs",
        "ollama_coding_endpoint": "http://yingdemacbook-pro.local:11434/",
        "ollama_boundary": "use only for coding assistance when reachable; do not use for non-code secrets or deployment authority",
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "ceo_continuous_development_inventory",
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
        "execution_queue": build_execution_queue(issues, prs, max_lanes),
        "decision": "dispatch_scoped_lanes_after_gray_plan_and_ceo_auto_review",
        "execution_status": "inventory_complete_runner_dispatch_pending",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run active CEO issue/PR planning sweep")
    parser.add_argument("--out", type=Path, default=REPO / "out" / "ceo-sweep-latest.json")
    parser.add_argument("--max-lanes", type=int, default=1)
    args = parser.parse_args(argv)
    report = build_report(max_lanes=max(1, args.max_lanes))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
