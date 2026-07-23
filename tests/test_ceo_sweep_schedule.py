from __future__ import annotations

import importlib.util
import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
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
    assert "install_launch_agent.py" in installer
    assert "sed " not in installer
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
    assert "install_launch_agent.py" in installer
    assert "sed " not in installer
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


def test_ceo_health_watchdog_uses_active_heartbeat_until_run_timeout(tmp_path, monkeypatch):
    module = _load_script("ceo_health_watchdog_active", "ceo_health_watchdog.py")
    monkeypatch.setattr(module, "_pid_alive", lambda _pid: True)
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    status = tmp_path / "status.json"
    heartbeat = tmp_path / "heartbeat.json"
    heartbeat.write_text(json.dumps({"heartbeat_at": (now - timedelta(minutes=4)).isoformat()}))
    status.write_text(
        json.dumps(
            {
                "updated_at": (now - timedelta(hours=2)).isoformat(),
                "active": {"pid": 123, "started_at": (now - timedelta(minutes=20)).isoformat(), "heartbeat_path": str(heartbeat)},
            }
        )
    )
    os.utime(status, ((now - timedelta(hours=2)).timestamp(),) * 2)

    assert module.health_diagnostics(status, now=now)["reason"] == "cycle_active"
    stale_now = now + timedelta(minutes=6)
    assert module.health_diagnostics(status, now=stale_now)["reason"] == "active_run_timeout"


def test_runtime_guard_permissions_symlinks_and_stale_lock(tmp_path, monkeypatch):
    module = _load_script("ceo_runtime_guard_security", "ceo_runtime_guard.py")
    secure_dir = tmp_path / "secure"
    secure_file = secure_dir / "output.log"
    module.secure_file(secure_file)
    assert stat.S_IMODE(secure_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(secure_file.stat().st_mode) == 0o600
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        module.secure_directory(link)

    lock = secure_dir / "lock"
    old = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
    assert module.acquire_lock(lock, pid=99999, now=old, stale_seconds=60)["acquired"]
    monkeypatch.setattr(module, "_pid_alive", lambda _pid: False)
    recovered = module.acquire_lock(lock, pid=1234, now=old + timedelta(minutes=2), stale_seconds=60)
    assert recovered["acquired"] and recovered["pid"] == 1234
    secure_file.write_text("Authorization: Bearer ghp_supersecret\n")
    module.redact_file(secure_file)
    assert "ghp_supersecret" not in secure_file.read_text()
    assert stat.S_IMODE(secure_file.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"agent_exit": 124, "before": "a", "after": "b", "descendant": True, "clean": True}, "agent_exit_124"),
        ({"agent_exit": 0, "before": "a", "after": "a", "descendant": True, "clean": True}, "no_new_commit"),
        ({"agent_exit": 0, "before": "a", "after": "b", "descendant": False, "clean": True}, "invalid_commit_history"),
        ({"agent_exit": 0, "before": "a", "after": "b", "descendant": True, "clean": False}, "dirty_after_agent"),
    ],
)
def test_progress_requires_successful_agent_verified_commit_and_clean_lane(kwargs, reason):
    module = _load_script(f"ceo_runtime_progress_{reason}", "ceo_runtime_guard.py")
    assert module.classify_progress(**kwargs) == {"progress": False, "reason": reason}


def test_progress_accepts_only_new_verified_commit():
    module = _load_script("ceo_runtime_progress_success", "ceo_runtime_guard.py")
    assert module.classify_progress(agent_exit=0, before="a", after="b", descendant=True, clean=True) == {
        "progress": True, "reason": "new_verified_commit", "commit": "b",
    }


def test_runtime_guard_validates_git_common_dir_and_rejects_symlink_lane(tmp_path, monkeypatch):
    module = _load_script("ceo_runtime_guard_git", "ceo_runtime_guard.py")
    for key in tuple(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    repo = tmp_path / "repo"
    lane = tmp_path / "lane"
    # pytest normally supplies an empty tmp_path, but an interrupted/concurrent
    # full-suite run can leave the numbered directory behind.  This fixture is
    # destructive only inside its pytest-owned directory and must not inherit a
    # stale repository, remote, commit, or worktree registration.
    shutil.rmtree(repo, ignore_errors=True)
    shutil.rmtree(lane, ignore_errors=True)
    git_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, env=git_env)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True, env=git_env)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True, env=git_env)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://example.invalid/repo.git"], check=True, env=git_env)
    (repo / "README").write_text("test\n")
    subprocess.run(["git", "-C", str(repo), "add", "README"], check=True, env=git_env)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], check=True, capture_output=True, env=git_env)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", str(lane)], check=True, capture_output=True, env=git_env)

    assert module.validate_lane(repo, lane)["valid"] is True
    lane_link = tmp_path / "lane-link"
    lane_link.symlink_to(lane, target_is_directory=True)
    with pytest.raises(ValueError, match="canonical|symlink"):
        module.validate_lane(repo, lane_link)


def test_plist_installer_handles_xml_characters_atomically_and_rejects_symlink(tmp_path):
    module = _load_script("install_launch_agent_security", "install_launch_agent.py")
    root = tmp_path / "repo&|<name>"
    (root / "scripts").mkdir(parents=True)
    python = Path(sys.executable).resolve()
    codex = root / "codex&|"
    gh = root / "gh<cli>"
    codex.write_text("")
    gh.write_text("")
    destination_dir = tmp_path / "Launch&|Agents"
    destination_dir.mkdir()
    destination = destination_dir / "sweep.plist"

    payload = module.payload("sweep", root, python, codex, gh)
    module.prepare_logs(payload)
    module.install_plist(destination, payload)

    parsed = plistlib.loads(destination.read_bytes())
    assert parsed["WorkingDirectory"] == str(root)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert stat.S_IMODE((root / "out/ceo-cycle").stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "out/ceo-cycle/launchd.out.log").stat().st_mode) == 0o600
    assert not list(destination_dir.glob(".sweep.plist.*"))
    symlink = destination_dir / "linked.plist"
    symlink.symlink_to(destination)
    with pytest.raises(ValueError, match="symlink"):
        module.install_plist(symlink, payload)


def test_agent_environment_excludes_cloud_github_and_production_variables(tmp_path):
    module = _load_script("ceo_agent_exec_environment", "ceo_agent_exec.py")
    environment = module.isolated_environment(
        path="/usr/bin:/bin", codex_home="/safe/codex", lane="1", issue="283", home=tmp_path,
    )

    assert set(environment) == {"PATH", "HOME", "LANG", "CODEX_HOME", "TRUSTFORGE_CEO_LANE", "TRUSTFORGE_CEO_ISSUE"}
    assert not any(key.startswith(("AWS", "GH_", "GITHUB", "TRUSTFORGE_ENV")) for key in environment)
    source = tmp_path / "source-codex"
    source.mkdir()
    (source / "auth.json").write_text('{"token":"secret"}')
    isolated = module.prepare_minimal_codex_home(source, tmp_path / "agent-home")
    assert isolated is not None
    assert {path.name for path in isolated.iterdir()} == {"auth.json"}
    assert stat.S_IMODE((isolated / "auth.json").stat().st_mode) == 0o600
    module.remove_minimal_codex_home(isolated)
    assert not isolated.exists()


def test_ceo_sweep_builds_continuous_development_inventory(monkeypatch):
    spec = importlib.util.spec_from_file_location("ceo_sweep", ROOT / "scripts/ceo_sweep.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_json_cmd", lambda _args: [])

    report = module.build_report()

    assert report["cadence"] == "30 minutes"
    assert report["mode"] == "ceo_half_hour_issue_pr_development"
    assert report["execution_status"] == "issue_pr_lane_dispatch_required"
    assert "one_scoped_issue_pr_lane" in report["decision"]
    assert report["cpo_plan"]["proposed_author"] == "gray"
    assert report["ceo_review"]["required_decision"] == "per_lane_auto_review_after_gray_plan"
    assert report["development_plan"]["operating_mode"] == "unattended_scoped_issue_lanes"
    assert report["development_plan"]["skip_prevention"]["failure_reason"] == (
        "runnable_issue_queue_not_exhausted_but_no_issue_pr_opened"
    )
    assert "fall_through_to_next_runnable_issue" in report["skip_prevention_gate"]
    serialized = json.dumps(report).lower()
    assert "approved_for" not in serialized
    assert '"author"' not in serialized


def test_ceo_lane_prompt_requires_fallback_after_blocked_candidate():
    prompt = (ROOT / "scripts/prompts/ceo-development-loop.md").read_text()

    assert "Parent runner contract" in prompt
    assert "fall through next runnable queue candidate" in prompt
    assert "cannot end only blocked/dependency" in prompt
    assert "issue PR open review" in prompt


def test_ceo_cycle_runner_does_not_fail_normal_backlog_over_lane_capacity():
    runner = (ROOT / "scripts/run_ceo_cycle.sh").read_text()
    normal_capacity_limited_backlog = {
        "queue_count": 2,
        "dispatched_count": 1,
        "failures": 0,
        "setup_failures": 0,
    }
    failures = (
        normal_capacity_limited_backlog["failures"]
        + normal_capacity_limited_backlog["setup_failures"]
    )
    if (
        normal_capacity_limited_backlog["dispatched_count"] == 0
        and normal_capacity_limited_backlog["queue_count"] > 0
    ):
        failures += 1

    assert failures == 0
    assert 'DISPATCHED_COUNT="${#pids[@]}"' in runner
    assert "if (( DISPATCHED_COUNT == 0 )); then" in runner
    assert "QUEUE_COUNT > DISPATCHED_COUNT" not in runner
    assert "runnable_issue_queue_not_exhausted_but_no_issue_pr_opened" not in runner
    assert '"dispatched_lanes":%s' not in runner
    assert "dispatch_required_but_no_lane_started" in runner

@pytest.mark.parametrize(
    "source",
    [
        "open_prs",
        "open_issues",
        "merged_prs",
    ],
)
def test_ceo_sweep_fails_closed_for_each_inventory_query(monkeypatch, source):
    spec = importlib.util.spec_from_file_location(f"ceo_sweep_failure_{source}", ROOT / "scripts/ceo_sweep.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def fake_json_cmd(args):
        command_source = "merged_prs" if args[1] == "api" else ("open_prs" if args[1] == "pr" else "open_issues")
        if command_source == source:
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


def test_execution_queue_excludes_open_dependencies_and_keeps_all_candidates():
    module = _load_script("ceo_sweep_dependencies", "ceo_sweep.py")
    issues = [
        {"number": 1, "title": "dependent", "body": "Blocked by #2", "labels": []},
        {"number": 2, "title": "external dependency", "body": "", "labels": [{"name": "blocked-external"}]},
        *[{"number": number, "title": f"candidate {number}", "body": "", "labels": []} for number in range(3, 11)],
    ]

    queue = module.build_execution_queue(issues, [], max_lanes=1)

    assert [item["issue"] for item in queue] == list(range(3, 11))


def test_merged_pr_pagination_indexes_more_than_one_hundred_results():
    module = _load_script("ceo_sweep_pages", "ceo_sweep.py")
    pages = [
        [
            {"number": number, "body": f"Fixes #{number}", "head": {"ref": f"fix/issue-{number}"}, "base": {"ref": "develop"}, "merged_at": "2026-07-20T00:00:00Z"}
            for number in range(start, end)
        ]
        for start, end in ((1, 101), (101, 121))
    ]

    normalized = module._normalize_merged_pr_pages(pages)
    ownership = module.merged_pr_ownership(normalized)

    assert len(normalized) == 120
    assert set(ownership) == set(range(1, 121))


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


def test_cycle_status_records_completed_failed_and_history(tmp_path):
    module = _load_script("ceo_cycle_state_history", "ceo_cycle_state.py")
    events = "\n".join(
        [
            'dispatched\t1',
            'completed\t1\t{"lane":1,"commit":"abc","dispatched_at":"2026-07-20T01:00:00Z","commit_at":"2026-07-20T01:05:00Z"}',
            'failed\t2\t{"lane":2,"reason":"no_new_commit","dispatched_at":"2026-07-20T01:01:00Z"}',
            'progress\t1',
        ]
    )
    payload = module.payload_from_events(events, process_success=False, load_diagnostics={"capacity": 2})

    state = module.record_cycle(tmp_path / "status.json", **payload)

    assert state["completed"][0]["commit"] == "abc"
    assert state["failed"][0]["reason"] == "no_new_commit"
    assert state["history"]["last_dispatch_at"] == "2026-07-20T01:01:00Z"
    assert state["history"]["last_commit_at"] == "2026-07-20T01:05:00Z"
    assert state["active"] is False


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
    launcher = (ROOT / "scripts/ceo_agent_exec.py").read_text()
    assert 'approval_policy="never"' in launcher
    assert "sandbox_workspace_write.network_access=false" in launcher
    assert '"--sandbox", "workspace-write"' in launcher
    assert "timeout=args.timeout_seconds" in launcher
    assert "umask 077" in runner
    assert "TRUSTFORGE_CEO_MAX_LANES:-1" in runner
    assert "worktree add" in runner and "if ! git" in runner
    assert "fetch origin develop" in runner and "if ! git" in runner
    assert "checkout --detach" in runner
    assert "merge-base --is-ancestor" in runner
    progress_helper = (ROOT / "scripts/ceo_runtime_guard.py").read_text()
    assert "dirty_after_agent" in progress_helper and "no_new_commit" in progress_helper
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
    assert "/codex-review" in prompt
    assert "request at least one reviewer" in prompt
    assert "harper (ciso) and gray (cpo)" in prompt
    installer = (ROOT / "scripts/install_ceo_half_hour_schedule.sh").read_text()
    template = (ROOT / "scripts/templates/com.hurricanesoft.trustforge-ceo-sweep.plist.in").read_text()
    assert "command -v gh" in installer
    assert "install_launch_agent.py" in installer


def test_ceo_sweep_report_exposes_pr_open_and_merge_review_gates(monkeypatch):
    module = _load_script("ceo_sweep_review_gates", "ceo_sweep.py")
    monkeypatch.setattr(module, "_json_cmd", lambda _args: [])

    report = module.build_report(max_lanes=1)
    development_plan = report["development_plan"]

    assert development_plan["pr_open_guardrails"] == [
        "reviewer request required when every PR is opened",
        "leave PR open for human review unless explicit merge approval exists",
    ]
    assert "eye scan or breaking-change analysis required before merge" in development_plan["merge_guardrails"]
    assert "/codex-review adversarial review required before merge" in development_plan["merge_guardrails"]
    assert (
        "security changes require harper (CISO) plus gray (CPO) review before merge"
        in development_plan["merge_guardrails"]
    )

    serialized = json.dumps(development_plan).lower()
    assert "green ci" not in serialized
    assert "local targeted verification" in serialized


def test_ceo_lane_prompt_does_not_require_github_ci_or_full_pre_push_gate():
    prompt = (ROOT / "scripts/prompts/ceo-development-loop.md").read_text()

    assert "GitHub Actions CI is not an automated merge gate" in prompt
    assert "Do not require full" in prompt
    assert "pre-push-style local suite before opening review PR" in prompt
    assert "focused local verification" in prompt

def test_ci_runs_on_develop_and_main_and_pre_push_is_full_local_gate():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    hook = (ROOT / ".githooks/pre-push").read_text()

    assert "workflow_dispatch:" in workflow
    assert "pull_request:" in workflow
    assert "push:" in workflow
    assert workflow.count("- develop") == 2
    assert workflow.count("- main") == 2
    assert "contents: read" in workflow
    assert "cancel-in-progress: true" in workflow
    assert "pull_request_target:" not in workflow
    assert "secrets." not in workflow
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
