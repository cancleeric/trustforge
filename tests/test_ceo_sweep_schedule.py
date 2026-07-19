from __future__ import annotations

import importlib.util
import json
import os
import plistlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ceo_sweep_template_and_installer_are_half_hourly_without_editing_host_plist():
    tracked = plistlib.loads(
        (ROOT / "deploy/launchd/com.hurricanesoft.trustforge-ceo-sweep.plist").read_bytes()
    )
    template = (ROOT / "scripts/templates/com.hurricanesoft.trustforge-ceo-sweep.plist.in").read_text()
    installer = (ROOT / "scripts/install_ceo_half_hour_schedule.sh").read_text()
    compatibility_wrapper = (ROOT / "scripts/install_ceo_hourly_schedule.sh").read_text()

    assert tracked["StartInterval"] == 3600
    assert "<integer>1800</integer>" in template
    assert "<integer>3600</integer>" not in template
    assert "every 1800s" in installer
    assert "deprecated" not in installer.lower()
    assert "install_ceo_half_hour_schedule.sh" in compatibility_wrapper


def test_ceo_health_watchdog_launch_agent_is_independent_and_five_minutes():
    template_path = ROOT / "scripts/templates/com.hurricanesoft.trustforge-ceo-health-watchdog.plist.in"
    template = plistlib.loads(template_path.read_bytes())
    installer = (ROOT / "scripts/install_ceo_health_watchdog.sh").read_text()

    assert template["Label"] == "com.hurricanesoft.trustforge-ceo-health-watchdog"
    assert template["StartInterval"] == 300
    assert template["RunAtLoad"] is True
    assert "ceo_health_watchdog.py" in template["ProgramArguments"][1]
    assert "codex" not in template_path.read_text().lower()
    assert "launchctl bootout" in installer
    assert "launchctl bootstrap" in installer
    assert "install_ceo_health_watchdog.sh" not in (ROOT / "scripts/run_ceo_cycle.sh").read_text()


def test_ceo_health_watchdog_missing_and_corrupt_status_are_critical(tmp_path):
    module = _load_script("ceo_health_watchdog_missing", "ceo_health_watchdog.py")
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    status = tmp_path / "status.json"

    assert module.health_diagnostics(status, now=now)["reason"] == "status_missing"
    status.write_text("{broken")
    assert module.health_diagnostics(status, now=now)["reason"] == "status_corrupt"


def test_ceo_health_watchdog_accepts_fresh_timezone_aware_status(tmp_path):
    module = _load_script("ceo_health_watchdog_fresh", "ceo_health_watchdog.py")
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    status = tmp_path / "status.json"
    status.write_text(json.dumps({"updated_at": "2026-07-20T19:30:00+08:00"}))
    os.utime(status, (now.timestamp(), now.timestamp()))

    diagnostics = module.health_diagnostics(status, now=now)

    assert diagnostics["severity"] == "healthy"
    assert diagnostics["updated_at"] == "2026-07-20T11:30:00+00:00"


def test_ceo_health_watchdog_rejects_stale_and_future_status(tmp_path):
    module = _load_script("ceo_health_watchdog_stale", "ceo_health_watchdog.py")
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    status = tmp_path / "status.json"
    stale = now - timedelta(minutes=41)
    status.write_text(json.dumps({"updated_at": stale.isoformat()}))
    os.utime(status, (stale.timestamp(), stale.timestamp()))
    assert module.health_diagnostics(status, now=now)["reason"] == "status_stale"

    os.utime(status, (now.timestamp(), now.timestamp()))
    assert module.health_diagnostics(status, now=now)["reason"] == "status_stale"

    status.write_text(json.dumps({"updated_at": (now + timedelta(minutes=6)).isoformat()}))
    os.utime(status, (now.timestamp(), now.timestamp()))
    assert module.health_diagnostics(status, now=now)["reason"] == "status_timestamp_in_future"


def test_ceo_health_watchdog_writes_alert_atomically_and_clears_when_fresh(tmp_path):
    module = _load_script("ceo_health_watchdog_alert", "ceo_health_watchdog.py")
    now = datetime.now(timezone.utc)
    status = tmp_path / "status.json"
    alert = tmp_path / "health-alert.json"

    critical = module.run_watchdog(status, alert, now=now)
    assert critical["severity"] == "critical"
    assert json.loads(alert.read_text())["reason"] == "status_missing"
    assert not list(tmp_path.glob(".health-alert.json.*"))

    status.write_text(json.dumps({"updated_at": now.isoformat()}))
    os.utime(status, (now.timestamp(), now.timestamp()))
    assert module.run_watchdog(status, alert, now=now)["severity"] == "healthy"
    assert not alert.exists()


def test_ceo_sweep_builds_continuous_development_inventory(monkeypatch):
    spec = importlib.util.spec_from_file_location("ceo_sweep", ROOT / "scripts/ceo_sweep.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_json_cmd", lambda _args: [])

    report = module.build_report()

    assert report["cadence"] == "30 minutes"
    assert report["mode"] == "ceo_continuous_development_inventory"
    assert report["execution_status"] == "inventory_complete_runner_dispatch_pending"
    assert "gray_plan_and_ceo_auto_review" in report["decision"]
    assert report["cpo_plan"]["proposed_author"] == "gray"
    assert report["ceo_review"]["required_decision"] == "per_lane_auto_review_after_gray_plan"
    assert report["development_plan"]["operating_mode"] == "unattended_scoped_issue_lanes"
    serialized = json.dumps(report).lower()
    assert "approved_for" not in serialized
    assert '"author"' not in serialized


@pytest.mark.parametrize(
    ("failed_command", "source"),
    [
        (("pr", "open"), "open_prs"),
        (("issue", "open"), "open_issues"),
        (("pr", "merged"), "merged_prs"),
    ],
)
def test_ceo_sweep_fails_closed_for_each_inventory_query(monkeypatch, failed_command, source):
    spec = importlib.util.spec_from_file_location(f"ceo_sweep_failure_{source}", ROOT / "scripts/ceo_sweep.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def fake_json_cmd(args):
        state = args[args.index("--state") + 1]
        if args[1] == failed_command[0] and state == failed_command[1]:
            return {"error": f"{source} unavailable", "args": args}
        return []

    monkeypatch.setattr(module, "_json_cmd", fake_json_cmd)
    report = module.build_report(max_lanes=2)

    assert report["execution_status"] == "inventory_failed"
    assert report["execution_queue"] == []
    assert report["inventory_errors"] == [{"source": source, "error": f"{source} unavailable"}]


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

    assert [item["issue"] for item in queue] == [10, 1, 11, 12]
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


def test_execution_queue_skips_tracking_and_evidence_only_issues():
    spec = importlib.util.spec_from_file_location("ceo_sweep_non_coding", ROOT / "scripts/ceo_sweep.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    issues = [
        {"number": 1, "title": "[P0] delivery 總控", "body": "", "labels": [{"name": "P0-critical"}]},
        {"number": 2, "title": "demo evidence", "body": "", "labels": [{"name": "needs-evidence"}]},
        {"number": 3, "title": "ready subset", "body": "", "labels": [{"name": "ready-now"}]},
        {"number": 4, "title": "bug", "body": "", "labels": [{"name": "bug"}]},
    ]

    queue = module.build_execution_queue(issues, [], max_lanes=4)

    assert [item["issue"] for item in queue] == [3, 4]


def test_lane_guard_bounds_concurrency_by_load():
    spec = importlib.util.spec_from_file_location("ceo_lane_guard", ROOT / "scripts/ceo_lane_guard.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.lane_capacity(8, 2.0, 6, 0.85) == 4
    assert module.lane_capacity(8, 7.0, 6, 0.85) == 1
    assert module.lane_capacity(8, 12.0, 6, 0.85) == 0
    assert module.lane_capacity(8, 0.0, 2, 0.85) == 2
    diagnostics = module.load_diagnostics(8, 12.0, 6, 0.85)
    assert diagnostics | {"retry_at": "ignored"} == {
        "capacity": 0, "cpu_count": 8, "load_1m": 12.0, "load_budget": 6.8,
        "max_lanes": 6, "max_load_per_cpu": 0.85, "blocked": True,
        "reason": "load_at_or_above_hard_limit", "denied_reason": "load_at_or_above_hard_limit",
        "retry_at": "ignored",
    }
    assert diagnostics["retry_at"].endswith("+00:00")


def test_merged_pr_ownership_skips_stale_issue_and_keeps_fallbacks():
    spec = importlib.util.spec_from_file_location("ceo_sweep_ownership", ROOT / "scripts/ceo_sweep.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    issues = [{"number": number, "title": f"issue {number}", "body": "", "labels": []} for number in (199, 200, 201)]
    merged = [{"number": 10, "body": "Fixes #199", "headRefName": "fix/misc", "baseRefName": "develop", "mergedAt": "2026-07-20T00:00:00Z"}]
    ownership = module.merged_pr_ownership(merged)

    classified = module.classify_issues(issues, ownership)
    queue = module.build_execution_queue(classified, [], 1, ownership)

    assert classified[0]["development_status"] == "implemented_waiting_release"
    assert [item["issue"] for item in queue] == [200, 201]


def test_merged_pr_ownership_requires_closing_keyword_or_issue_branch():
    spec = importlib.util.spec_from_file_location("ceo_sweep_mentions", ROOT / "scripts/ceo_sweep.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prs = [
        {"number": 1, "body": "Related to #199", "headRefName": "docs/mention-199", "baseRefName": "develop", "mergedAt": "now"},
        {"number": 2, "body": "", "headRefName": "fix/issue-200-title", "baseRefName": "develop", "mergedAt": "now"},
    ]
    assert set(module.merged_pr_ownership(prs)) == {200}


def test_lane_cleanliness_ignores_only_untracked_root_venv():
    spec = importlib.util.spec_from_file_location("ceo_lane_cleanliness", ROOT / "scripts/ceo_lane_cleanliness.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.blocking_status_entries("?? .venv/bin/python\0") == []
    assert module.blocking_status_entries("?? nested/.venv/file\0")
    assert module.blocking_status_entries(" M .venv/tracked\0")
    assert module.blocking_status_entries("?? unknown.txt\0")


def test_watchdog_warns_escalates_and_resets_atomically(tmp_path):
    spec = importlib.util.spec_from_file_location("ceo_cycle_state", ROOT / "scripts/ceo_cycle_state.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    status = tmp_path / "status.json"
    load = {"capacity": 1, "cpu_count": 8, "load_1m": 1.0, "load_budget": 6.8, "reason": "within_limit"}
    kwargs = {"selected": [1], "dispatched": [], "skipped": [], "blocked": [], "process_success": True, "progress": [], "load_diagnostics": load}

    assert module.record_cycle(status, **kwargs)["watchdog_severity"] is None
    assert module.record_cycle(status, **kwargs)["watchdog_severity"] == "warning"
    assert json.loads((tmp_path / "alert.json").read_text())["severity"] == "warning"
    assert module.record_cycle(status, **kwargs)["watchdog_severity"] == "critical"
    dispatched_without_progress = module.record_cycle(
        status, selected=[1], dispatched=[1], skipped=[], blocked=[], process_success=True,
        progress=[], load_diagnostics=load,
    )
    assert dispatched_without_progress["consecutive_zero_dispatch"] == 0
    assert dispatched_without_progress["consecutive_no_progress"] == 4
    reset = module.record_cycle(
        status, selected=[1], dispatched=[1], skipped=[], blocked=[], process_success=False,
        progress=[1], load_diagnostics=load,
    )
    assert reset["consecutive_zero_dispatch"] == 0
    assert reset["consecutive_no_progress"] == 0
    assert reset["process_success"] is False
    assert reset["development_progress"] is True
    assert reset["load_diagnostics"] == load
    assert not (tmp_path / "alert.json").exists()


def test_watchdog_recovers_from_corrupt_status(tmp_path):
    spec = importlib.util.spec_from_file_location("ceo_cycle_state_corrupt", ROOT / "scripts/ceo_cycle_state.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    status = tmp_path / "status.json"
    status.write_text("{broken")
    state = module.record_cycle(
        status, selected=[], dispatched=[], skipped=[], blocked=[], process_success=True,
        progress=[], load_diagnostics={"capacity": 0},
    )
    assert state["consecutive_zero_dispatch"] == 1
    assert state["consecutive_no_progress"] == 1
    assert json.loads(status.read_text()) == state


def test_cycle_event_payload_preserves_blocked_lane_paths():
    spec = importlib.util.spec_from_file_location("ceo_cycle_state_events", ROOT / "scripts/ceo_cycle_state.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    diagnostics = {"clean": False, "blockers": ["?? unknown.txt", " M tracked.py"], "path": "/tmp/lane-2"}

    payload = module.payload_from_events(
        f"selected\t283\nblocked\t283\t{json.dumps(diagnostics)}\n",
        process_success=True,
        load_diagnostics={"capacity": 2},
    )

    assert payload["blocked"] == [{**diagnostics, "issue": 283}]
    assert payload["load_diagnostics"] == {"capacity": 2}


def test_cycle_status_preserves_inventory_error_summary(tmp_path):
    spec = importlib.util.spec_from_file_location("ceo_cycle_state_inventory", ROOT / "scripts/ceo_cycle_state.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    error = {"reason": "inventory_error", "errors": [{"source": "open_issues", "error": "connection failed"}]}
    payload = module.payload_from_events(
        f"blocked\t0\t{json.dumps(error)}\n",
        process_success=False,
        load_diagnostics={"capacity": 1},
    )

    state = module.record_cycle(tmp_path / "status.json", **payload)

    assert state["process_success"] is False
    assert state["inventory_errors"] == error["errors"]


def test_runner_and_prompt_enforce_unattended_safety_contract():
    runner = (ROOT / "scripts/run_ceo_cycle.sh").read_text()
    prompt = (ROOT / "scripts/prompts/ceo-development-loop.md").read_text().lower()

    assert "worktree add --detach" in runner
    assert "approval_policy=\"never\"" in runner
    assert "sandbox_workspace_write.network_access=true" in runner
    assert "--sandbox workspace-write" in runner
    assert '"inventory_failed"' in runner
    assert "[WARNING]" in runner
    assert "[CRITICAL]" in runner
    assert "gray (cpo)" in prompt
    assert "act as ceo" in prompt
    assert "never deploy production" in prompt
    assert "merge develop to main" in prompt
    assert "secrets" in prompt
    assert "cost caps" in prompt
    assert "never bypass that hook" in prompt
    installer = (ROOT / "scripts/install_ceo_half_hour_schedule.sh").read_text()
    template = (ROOT / "scripts/templates/com.hurricanesoft.trustforge-ceo-sweep.plist.in").read_text()
    assert "command -v gh" in installer
    assert "__PATH__" in template


def test_ci_is_manual_and_pre_push_is_full_local_gate():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    hook = (ROOT / ".githooks/pre-push").read_text()

    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "backend tests" in hook
    assert "data contracts" in hook
    assert "source stub scan" in hook
    assert "competition QA" in hook
    assert "frontend dependencies" in hook
    assert "frontend tests" in hook
    assert "frontend lint" in hook
    assert "frontend build" in hook
    assert "git diff --check" in hook


def test_active_agent_contract_uses_half_hour_ceo_sweep():
    text = (ROOT / "AGENTS.md").read_text()

    assert "runs every 30 minutes" in text
    assert "runs hourly" not in text
