from __future__ import annotations

import importlib.util
import json
import plistlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_all_ceo_sweep_schedules_are_hourly():
    tracked = plistlib.loads(
        (ROOT / "deploy/launchd/com.hurricanesoft.trustforge-ceo-sweep.plist").read_bytes()
    )
    installer = (ROOT / "scripts/templates/com.hurricanesoft.trustforge-ceo-sweep.plist.in").read_text()

    assert tracked["StartInterval"] == 3600
    assert "<integer>3600</integer>" in installer
    assert "<integer>1800</integer>" not in installer


def test_ceo_sweep_builds_continuous_development_inventory(monkeypatch):
    spec = importlib.util.spec_from_file_location("ceo_sweep", ROOT / "scripts/ceo_sweep.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_json_cmd", lambda _args: [])

    report = module.build_report()

    assert report["cadence"] == "1 hour"
    assert report["mode"] == "ceo_continuous_development_inventory"
    assert report["execution_status"] == "inventory_complete_runner_dispatch_pending"
    assert "gray_plan_and_ceo_auto_review" in report["decision"]
    assert report["cpo_plan"]["proposed_author"] == "gray"
    assert report["ceo_review"]["required_decision"] == "per_lane_auto_review_after_gray_plan"
    assert report["development_plan"]["operating_mode"] == "unattended_scoped_issue_lanes"
    serialized = json.dumps(report).lower()
    assert "approved_for" not in serialized
    assert '"author"' not in serialized


def test_execution_queue_is_prioritized_dependency_aware_and_continues_active_pr():
    spec = importlib.util.spec_from_file_location("ceo_sweep_queue", ROOT / "scripts/ceo_sweep.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    issues = [
        {"number": 1, "title": "not active", "body": "", "labels": [{"name": "bug"}]},
        {"number": 12, "title": "normal", "body": "", "labels": []},
        {"number": 11, "title": "bug", "body": "Blocked by #7", "labels": [{"name": "bug"}]},
        {"number": 10, "title": "active", "body": "", "labels": [{"name": "production"}]},
    ]
    prs = [{"headRefName": "fix/issue-10-active"}]

    queue = module.build_execution_queue(issues, prs, max_lanes=3)

    assert [item["issue"] for item in queue] == [10, 1, 11]
    assert queue[0]["action"] == "continue_pr"
    assert queue[0]["active_branch"] == "fix/issue-10-active"
    assert queue[2]["dependencies"] == [7]
    assert queue[1]["lane"] == 2


def test_execution_queue_skips_external_blockers_without_active_pr():
    spec = importlib.util.spec_from_file_location("ceo_sweep_blockers", ROOT / "scripts/ceo_sweep.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    issues = [
        {"number": 8, "title": "credentials", "body": "", "labels": [{"name": "blocked-external"}]},
        {"number": 9, "title": "continue", "body": "", "labels": [{"name": "blocked-external"}]},
        {"number": 10, "title": "ready", "body": "", "labels": [{"name": "ready-now"}]},
    ]

    queue = module.build_execution_queue(issues, [{"headRefName": "fix/issue-9-continue"}], max_lanes=3)

    assert [item["issue"] for item in queue] == [10, 9]
    assert queue[1]["action"] == "continue_pr"


def test_lane_guard_bounds_concurrency_by_load():
    spec = importlib.util.spec_from_file_location("ceo_lane_guard", ROOT / "scripts/ceo_lane_guard.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.lane_capacity(8, 2.0, 6, 0.85) == 4
    assert module.lane_capacity(8, 7.0, 6, 0.85) == 0
    assert module.lane_capacity(8, 0.0, 2, 0.85) == 2


def test_runner_and_prompt_enforce_unattended_safety_contract():
    runner = (ROOT / "scripts/run_ceo_cycle.sh").read_text()
    prompt = (ROOT / "scripts/prompts/ceo-development-loop.md").read_text().lower()

    assert "worktree add --detach" in runner
    assert "approval_policy=\"never\"" in runner
    assert "--sandbox workspace-write" in runner
    assert "gray (cpo)" in prompt
    assert "act as ceo" in prompt
    assert "never deploy production" in prompt
    assert "merge develop to main" in prompt
    assert "secrets" in prompt
    assert "cost caps" in prompt


def test_active_docs_do_not_claim_old_ceo_sweep_contract():
    paths = [ROOT / "docs/README.md", *(ROOT / "docs/plans").glob("*.md"), *(ROOT / "docs/qa").glob("*.md")]
    text = "\n".join(path.read_text() for path in paths)

    assert "30 分鐘 CEO sweep" not in text
    assert "30 分鐘 sweep" not in text
