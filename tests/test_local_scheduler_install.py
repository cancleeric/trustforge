from __future__ import annotations

import importlib.util
import os
import plistlib
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _generator():
    spec = importlib.util.spec_from_file_location(
        "portable_launch_agent", ROOT / "scripts/install_launch_agent.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launch_agent_generator_renders_all_local_kinds(tmp_path):
    module = _generator()
    python = Path(sys.executable).resolve()
    for kind in ("refresh", "analysis", "web"):
        destination = tmp_path / f"{kind}.plist"
        payload = module.payload(kind, ROOT.resolve(), python, None)
        module.prepare_logs(payload)
        module.install_plist(destination, payload)
        parsed = plistlib.loads(destination.read_bytes())
        assert parsed["WorkingDirectory"].startswith(str(ROOT.resolve()))
        assert parsed["EnvironmentVariables"]["TRUSTFORGE_HOME"] == str(ROOT.resolve())


def test_linux_render_only_never_calls_systemctl_and_ui_is_opt_in(tmp_path):
    fake_systemctl = tmp_path / "systemctl"
    marker = tmp_path / "called"
    fake_systemctl.write_text(f"#!/bin/sh\ntouch {marker}\n")
    fake_systemctl.chmod(0o755)
    output = tmp_path / "units"
    env = {
        **os.environ,
        "TRUSTFORGE_SCHEDULER_OS": "Linux",
        "TRUSTFORGE_SYSTEMCTL": str(fake_systemctl),
        "TRUSTFORGE_HOME": str(ROOT.resolve()),
        "TRUSTFORGE_PYTHON": str(Path(sys.executable).resolve()),
    }
    log_path = ROOT / "out/logs/com.hurricanesoft.trustforge-local-refresh.out.log"
    before = log_path.stat().st_mtime_ns if log_path.exists() else None
    subprocess.run(
        [str(ROOT / "deploy/install_local_scheduler.sh"), "--render-only", "--output-dir", str(output)],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    assert not marker.exists()
    assert (output / "trustforge-local-refresh.timer").exists()
    assert not (output / "trustforge-local-frontend.service").exists()
    all_text = "\n".join(path.read_text() for path in output.iterdir())
    assert str(ROOT.resolve()) in all_text
    assert "--user" not in all_text
    after = log_path.stat().st_mtime_ns if log_path.exists() else None
    assert after == before


def test_systemd_renderer_quotes_spaces_percent_and_is_atomic(tmp_path):
    module = _generator()
    repo = tmp_path / "repo with space % value"
    (repo / "scripts").mkdir(parents=True)
    destination = tmp_path / "units"
    destination.mkdir()
    units = module.systemd_units("refresh", repo, Path(sys.executable).resolve())
    for name, content in units.items():
        module.install_bytes(destination / name, content)
    service = (destination / "trustforge-local-refresh.service").read_text()
    assert f'WorkingDirectory="{str(repo).replace("%", "%%")}"' in service
    assert "repo with space %% value/scripts/run_local_refresh.sh\"" in service
    assert service.startswith(module.MANAGED_MARKER)
    linked = destination / "linked.service"
    linked.symlink_to(destination / "trustforge-local-refresh.service")
    try:
        module.install_bytes(linked, b"unsafe")
    except ValueError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("symlink destination was accepted")


def test_systemd_analysis_and_web_match_local_launchd_environment(tmp_path):
    module = _generator()
    repo = tmp_path / "repo % with space"
    (repo / "scripts").mkdir(parents=True)
    python = Path(sys.executable).resolve()
    for kind in ("analysis", "web"):
        text = next(iter(module.systemd_units(kind, repo, python).values())).decode()
        launchd_environment = module.payload(kind, repo, python, None)["EnvironmentVariables"]
        for key, value in launchd_environment.items():
            assert module._systemd_quote(f"{key}={value}") in text


def test_install_rejects_unmanaged_same_name_on_both_platforms(tmp_path):
    not_found = tmp_path / "not-found"
    not_found.write_text('#!/bin/sh\necho "Could not find service" >&2\nexit 3\n')
    not_found.chmod(0o755)
    for platform, filename in (
        ("Linux", "trustforge-local-web.service"),
        ("Darwin", "com.hurricanesoft.trustforge-local-web.plist"),
    ):
        output = tmp_path / platform
        output.mkdir()
        owned_by_user = output / filename
        owned_by_user.write_text("user owned")
        result = subprocess.run(
            [str(ROOT / "deploy/install_local_scheduler.sh"), "--render-only", "--output-dir", str(output)],
            env={
                **os.environ,
                "TRUSTFORGE_SCHEDULER_OS": platform,
                "TRUSTFORGE_HOME": str(ROOT.resolve()),
                "TRUSTFORGE_PYTHON": str(Path(sys.executable).resolve()),
                "TRUSTFORGE_PLUTIL": "/usr/bin/true",
                "TRUSTFORGE_LAUNCHCTL": str(not_found),
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert owned_by_user.read_text() == "user owned"


def test_linux_enable_failure_removes_fresh_transaction_files(tmp_path):
    output = tmp_path / "units"
    output.mkdir()
    log = tmp_path / "systemctl.log"
    fake = tmp_path / "systemctl"
    fake.write_text(
        f'#!/bin/sh\necho "$*" >>"{log}"\n'
        '[ "$2" = is-enabled ] && echo disabled && exit 1\n'
        '[ "$2" = is-active ] && echo inactive && exit 3\n'
        '[ "$2" = enable ] && exit 9\n'
        'exit 0\n'
    )
    fake.chmod(0o755)
    result = subprocess.run(
        [str(ROOT / "deploy/install_local_scheduler.sh"), "--output-dir", str(output)],
        env={
            **os.environ,
            "TRUSTFORGE_SCHEDULER_OS": "Linux",
            "TRUSTFORGE_HOME": str(ROOT.resolve()),
            "TRUSTFORGE_PYTHON": str(Path(sys.executable).resolve()),
            "TRUSTFORGE_SYSTEMCTL": str(fake),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    calls = log.read_text().splitlines()
    assert any(line == "--user daemon-reload" for line in calls)
    assert any("--user enable --now" in line for line in calls)
    assert any("--user disable --now" in line for line in calls)
    assert not list(output.glob("trustforge-*"))


def test_linux_rollback_restores_only_previously_enabled_units(tmp_path):
    output = tmp_path / "units"
    output.mkdir()
    base_env = {
        **os.environ,
        "TRUSTFORGE_SCHEDULER_OS": "Linux",
        "TRUSTFORGE_HOME": str(ROOT.resolve()),
        "TRUSTFORGE_PYTHON": str(Path(sys.executable).resolve()),
    }
    subprocess.run(
        [str(ROOT / "deploy/install_local_scheduler.sh"), "--render-only", "--output-dir", str(output)],
        check=True, env=base_env, capture_output=True, text=True,
    )
    log = tmp_path / "systemctl.log"
    fake = tmp_path / "systemctl"
    fake.write_text(
        f'#!/bin/sh\necho "$*" >>"{log}"\n'
        '[ "$2" = is-enabled ] && [ "$3" = trustforge-local-web.service ] && echo enabled && exit 0\n'
        '[ "$2" = is-enabled ] && echo disabled && exit 1\n'
        '[ "$2" = is-active ] && [ "$3" = trustforge-analysis-flow.service ] && echo active && exit 0\n'
        '[ "$2" = is-active ] && echo inactive && exit 3\n'
        '[ "$2" = enable ] && [ ! -f "' + str(tmp_path / "failed") + '" ] && '
        f'touch "{tmp_path / "failed"}" && exit 9\nexit 0\n'
    )
    fake.chmod(0o755)
    result = subprocess.run(
        [str(ROOT / "deploy/install_local_scheduler.sh"), "--output-dir", str(output)],
        env={**base_env, "TRUSTFORGE_SYSTEMCTL": str(fake)},
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    calls = log.read_text().splitlines()
    rollback_enable = [line for line in calls if "--user enable " in line][-1]
    assert rollback_enable.endswith("trustforge-local-web.service")
    assert "--now" not in rollback_enable
    rollback_start = [line for line in calls if "--user start " in line][-1]
    assert rollback_start.endswith("trustforge-analysis-flow.service")
    assert "trustforge-local-web.service" not in rollback_start
    assert not any("--user enable --now trustforge-local-refresh.service" in line for line in calls)


def test_linux_install_state_query_error_fails_before_replacing_or_enabling(tmp_path):
    output = tmp_path / "units"
    output.mkdir()
    base_env = {
        **os.environ,
        "TRUSTFORGE_SCHEDULER_OS": "Linux",
        "TRUSTFORGE_HOME": str(ROOT.resolve()),
        "TRUSTFORGE_PYTHON": str(Path(sys.executable).resolve()),
    }
    subprocess.run(
        [str(ROOT / "deploy/install_local_scheduler.sh"), "--render-only", "--output-dir", str(output)],
        check=True, env=base_env, capture_output=True, text=True,
    )
    before = {path.name: path.read_bytes() for path in output.glob("trustforge-*")}
    log = tmp_path / "systemctl.log"
    fake = tmp_path / "systemctl-error"
    fake.write_text(f'#!/bin/sh\necho "$*" >>"{log}"\necho "Failed to connect to bus" >&2\nexit 1\n')
    fake.chmod(0o755)
    result = subprocess.run(
        [str(ROOT / "deploy/install_local_scheduler.sh"), "--output-dir", str(output)],
        env={**base_env, "TRUSTFORGE_SYSTEMCTL": str(fake)},
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert {path.name: path.read_bytes() for path in output.glob("trustforge-*")} == before
    calls = log.read_text()
    assert "daemon-reload" not in calls and " enable " not in calls


def test_macos_render_only_never_queries_launchctl(tmp_path):
    output = tmp_path / "agents"
    output.mkdir()
    called = tmp_path / "called"
    fake = tmp_path / "launchctl"
    fake.write_text(f'#!/bin/sh\ntouch "{called}"\nexit 9\n')
    fake.chmod(0o755)
    subprocess.run(
        [str(ROOT / "deploy/install_local_scheduler.sh"), "--render-only", "--output-dir", str(output)],
        check=True,
        env={
            **os.environ,
            "TRUSTFORGE_SCHEDULER_OS": "Darwin",
            "TRUSTFORGE_HOME": str(ROOT.resolve()),
            "TRUSTFORGE_PYTHON": str(Path(sys.executable).resolve()),
            "TRUSTFORGE_LAUNCHCTL": str(fake),
            "TRUSTFORGE_PLUTIL": "/usr/bin/true",
        },
        capture_output=True, text=True,
    )
    assert not called.exists()


def test_macos_partial_bootstrap_failure_removes_all_fresh_files(tmp_path):
    output = tmp_path / "agents"
    output.mkdir()
    count = tmp_path / "count"
    launchctl = tmp_path / "launchctl"
    launchctl.write_text(
        f'#!/bin/sh\n[ "$1" = print ] && echo "Could not find service" >&2 && exit 3\n'
        f'[ "$1" != bootstrap ] && exit 0\n'
        f'n=$(cat "{count}" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" >"{count}"\n'
        '[ "$n" -eq 2 ] && exit 9\nexit 0\n'
    )
    launchctl.chmod(0o755)
    result = subprocess.run(
        [str(ROOT / "deploy/install_local_scheduler.sh"), "--output-dir", str(output)],
        env={
            **os.environ,
            "TRUSTFORGE_SCHEDULER_OS": "Darwin",
            "TRUSTFORGE_HOME": str(ROOT.resolve()),
            "TRUSTFORGE_PYTHON": str(Path(sys.executable).resolve()),
            "TRUSTFORGE_LAUNCHCTL": str(launchctl),
            "TRUSTFORGE_PLUTIL": "/usr/bin/true",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert not list(output.glob("com.hurricanesoft*.plist"))


def test_macos_install_rejects_loaded_without_file_and_query_error(tmp_path):
    for mode in ("loaded", "query-error"):
        output = tmp_path / mode
        output.mkdir()
        fake = tmp_path / f"launchctl-{mode}"
        fake.write_text("#!/bin/sh\n" + ("exit 0\n" if mode == "loaded" else 'echo "transport error" >&2\nexit 5\n'))
        fake.chmod(0o755)
        result = subprocess.run(
            [str(ROOT / "deploy/install_local_scheduler.sh"), "--output-dir", str(output)],
            env={
                **os.environ,
                "TRUSTFORGE_SCHEDULER_OS": "Darwin",
                "TRUSTFORGE_HOME": str(ROOT.resolve()),
                "TRUSTFORGE_PYTHON": str(Path(sys.executable).resolve()),
                "TRUSTFORGE_LAUNCHCTL": str(fake),
                "TRUSTFORGE_PLUTIL": "/usr/bin/true",
            },
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert not list(output.glob("*.plist"))


def test_macos_rollback_reloads_only_previously_loaded_labels(tmp_path):
    output = tmp_path / "agents"
    output.mkdir()
    base_env = {
        **os.environ,
        "TRUSTFORGE_SCHEDULER_OS": "Darwin",
        "TRUSTFORGE_HOME": str(ROOT.resolve()),
        "TRUSTFORGE_PYTHON": str(Path(sys.executable).resolve()),
        "TRUSTFORGE_PLUTIL": "/usr/bin/true",
    }
    not_found = tmp_path / "not-found-launchctl"
    not_found.write_text('#!/bin/sh\necho "Could not find service" >&2\nexit 3\n')
    not_found.chmod(0o755)
    base_env["TRUSTFORGE_LAUNCHCTL"] = str(not_found)
    subprocess.run(
        [str(ROOT / "deploy/install_local_scheduler.sh"), "--render-only", "--output-dir", str(output)],
        check=True, env=base_env, capture_output=True, text=True,
    )
    log = tmp_path / "launchctl.log"
    count = tmp_path / "bootstrap-count"
    fake = tmp_path / "launchctl"
    fake.write_text(
        f'#!/bin/sh\necho "$*" >>"{log}"\n'
        '[ "$1" = print ] && echo "$2" | grep -q local-web && exit 0\n'
        '[ "$1" = print ] && echo "Could not find service" >&2 && exit 3\n'
        f'[ "$1" = bootstrap ] || exit 0\nn=$(cat "{count}" 2>/dev/null || echo 0); '
        f'n=$((n+1)); echo "$n" >"{count}"\n[ "$n" -eq 2 ] && exit 9\nexit 0\n'
    )
    fake.chmod(0o755)
    result = subprocess.run(
        [str(ROOT / "deploy/install_local_scheduler.sh"), "--output-dir", str(output)],
        env={**base_env, "TRUSTFORGE_LAUNCHCTL": str(fake)},
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    bootstraps = [line for line in log.read_text().splitlines() if line.startswith("bootstrap ")]
    assert sum("local-refresh" in line for line in bootstraps) == 1
    assert sum("local-web" in line for line in bootstraps) == 1


def test_uninstaller_preserves_same_name_unmanaged_unit(tmp_path):
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    unmanaged = unit_dir / "trustforge-local-web.service"
    unmanaged.write_text("[Unit]\nDescription=user owned\n")
    fake = tmp_path / "systemctl"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    subprocess.run(
        [str(ROOT / "deploy/uninstall_local_scheduler.sh")],
        check=True,
        env={
            **os.environ,
            "TRUSTFORGE_SCHEDULER_OS": "Linux",
            "TRUSTFORGE_SYSTEMD_USER_DIR": str(unit_dir),
            "TRUSTFORGE_SYSTEMCTL": str(fake),
        },
        capture_output=True,
        text=True,
    )
    assert unmanaged.read_text() == "[Unit]\nDescription=user owned\n"


def test_uninstaller_preserves_managed_files_when_stop_fails(tmp_path):
    module = _generator()
    failing = tmp_path / "manager"
    failing.write_text(
        '#!/bin/sh\n'
        '[ "$2" = is-active ] || [ "$2" = is-enabled ] || [ "$1" = print ] && exit 0\n'
        "exit 7\n"
    )
    failing.chmod(0o755)
    linux_dir = tmp_path / "units"
    linux_dir.mkdir()
    linux_file = linux_dir / "trustforge-local-web.service"
    linux_file.write_text(f"{module.MANAGED_MARKER}\n[Unit]\n")
    linux = subprocess.run(
        [str(ROOT / "deploy/uninstall_local_scheduler.sh")],
        env={
            **os.environ,
            "TRUSTFORGE_SCHEDULER_OS": "Linux",
            "TRUSTFORGE_SYSTEMD_USER_DIR": str(linux_dir),
            "TRUSTFORGE_SYSTEMCTL": str(failing),
        },
        capture_output=True,
        text=True,
    )
    assert linux.returncode != 0 and linux_file.exists()

    mac_dir = tmp_path / "agents"
    mac_dir.mkdir()
    mac_file = mac_dir / "com.hurricanesoft.trustforge-local-web.plist"
    payload = module.payload("web", ROOT.resolve(), Path(sys.executable).resolve(), None)
    module.install_plist(mac_file, payload)
    mac = subprocess.run(
        [str(ROOT / "deploy/uninstall_local_scheduler.sh")],
        env={
            **os.environ,
            "TRUSTFORGE_SCHEDULER_OS": "Darwin",
            "TRUSTFORGE_LAUNCH_AGENT_DIR": str(mac_dir),
            "TRUSTFORGE_LAUNCHCTL": str(failing),
            "TRUSTFORGE_PYTHON": str(Path(sys.executable).resolve()),
        },
        capture_output=True,
        text=True,
    )
    assert mac.returncode != 0 and mac_file.exists()


def test_macos_bootstrap_failure_restores_previous_plist(tmp_path):
    module = _generator()
    output = tmp_path / "agents"
    output.mkdir()
    label = "com.hurricanesoft.trustforge-local-refresh"
    previous_path = output / f"{label}.plist"
    module.install_plist(
        previous_path,
        module.payload("refresh", ROOT.resolve(), Path(sys.executable).resolve(), None),
    )
    previous = previous_path.read_bytes()
    launchctl = tmp_path / "launchctl"
    launchctl.write_text(
        '#!/bin/sh\n'
        '[ "$1" = print ] && echo "$2" | grep -q local-refresh && exit 0\n'
        '[ "$1" = print ] && echo "Could not find service" >&2 && exit 3\n'
        '[ "$1" = bootstrap ] && exit 9\nexit 0\n'
    )
    launchctl.chmod(0o755)
    plutil = tmp_path / "plutil"
    plutil.write_text("#!/bin/sh\nexit 0\n")
    plutil.chmod(0o755)
    result = subprocess.run(
        [str(ROOT / "deploy/install_local_scheduler.sh"), "--output-dir", str(output)],
        env={
            **os.environ,
            "TRUSTFORGE_SCHEDULER_OS": "Darwin",
            "TRUSTFORGE_HOME": str(ROOT.resolve()),
            "TRUSTFORGE_PYTHON": str(Path(sys.executable).resolve()),
            "TRUSTFORGE_LAUNCHCTL": str(launchctl),
            "TRUSTFORGE_PLUTIL": str(plutil),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert (output / f"{label}.plist").read_bytes() == previous
    assert "rollback incomplete" in result.stderr


def test_uninstall_removes_benign_unloaded_and_inactive_managed_files(tmp_path):
    module = _generator()
    linux_dir = tmp_path / "units"
    linux_dir.mkdir()
    linux_file = linux_dir / "trustforge-local-web.service"
    linux_file.write_text(f"{module.MANAGED_MARKER}\n[Unit]\n")
    benign = tmp_path / "benign"
    benign.write_text(
        '#!/bin/sh\n'
        '[ "$2" = show-environment ] && exit 0\n'
        '[ "$2" = daemon-reload ] && exit 0\n'
        '[ "$2" = is-active ] && echo inactive && exit 3\n'
        '[ "$2" = is-enabled ] && echo disabled && exit 1\n'
        '[ "$1" = print ] && echo "Could not find service" >&2 && exit 3\n'
        'exit 1\n'
    )
    benign.chmod(0o755)
    linux = subprocess.run(
        [str(ROOT / "deploy/uninstall_local_scheduler.sh")],
        env={
            **os.environ,
            "TRUSTFORGE_SCHEDULER_OS": "Linux",
            "TRUSTFORGE_SYSTEMD_USER_DIR": str(linux_dir),
            "TRUSTFORGE_SYSTEMCTL": str(benign),
        },
        capture_output=True, text=True,
    )
    assert linux.returncode == 0 and not linux_file.exists()

    mac_dir = tmp_path / "agents"
    mac_dir.mkdir()
    mac_file = mac_dir / "com.hurricanesoft.trustforge-local-web.plist"
    module.install_plist(
        mac_file, module.payload("web", ROOT.resolve(), Path(sys.executable).resolve(), None)
    )
    mac = subprocess.run(
        [str(ROOT / "deploy/uninstall_local_scheduler.sh")],
        env={
            **os.environ,
            "TRUSTFORGE_SCHEDULER_OS": "Darwin",
            "TRUSTFORGE_LAUNCH_AGENT_DIR": str(mac_dir),
            "TRUSTFORGE_LAUNCHCTL": str(benign),
            "TRUSTFORGE_PYTHON": str(Path(sys.executable).resolve()),
        },
        capture_output=True, text=True,
    )
    assert mac.returncode == 0 and not mac_file.exists()

    module.install_plist(
        mac_file, module.payload("web", ROOT.resolve(), Path(sys.executable).resolve(), None)
    )
    query_error = tmp_path / "query-error"
    query_error.write_text('#!/bin/sh\necho "launchd transport error" >&2\nexit 5\n')
    query_error.chmod(0o755)
    errored = subprocess.run(
        [str(ROOT / "deploy/uninstall_local_scheduler.sh")],
        env={
            **os.environ,
            "TRUSTFORGE_SCHEDULER_OS": "Darwin",
            "TRUSTFORGE_LAUNCH_AGENT_DIR": str(mac_dir),
            "TRUSTFORGE_LAUNCHCTL": str(query_error),
            "TRUSTFORGE_PYTHON": str(Path(sys.executable).resolve()),
        },
        capture_output=True, text=True,
    )
    assert errored.returncode != 0 and mac_file.exists()


def test_nonhistorical_scheduler_assets_have_no_legacy_home():
    legacy_home = "/Users/" + "apple"
    paths = [
        *ROOT.joinpath("scripts").glob("*"),
        *ROOT.joinpath("deploy").rglob("*"),
        *ROOT.joinpath("tests").glob("*"),
    ]
    offenders = [
        str(path.relative_to(ROOT))
        for path in paths
        if path.is_file() and legacy_home in path.read_text(errors="ignore")
    ]
    assert offenders == []


def test_uninstaller_targets_only_fixed_units_and_preserves_data_contract():
    source = (ROOT / "deploy/uninstall_local_scheduler.sh").read_text()
    assert "trustforge.sqlite3" not in source
    assert "out/logs" not in source
    assert 'find "$TARGET"' not in source
    assert "*" not in "\n".join(
        line for line in source.splitlines() if "rm " in line
    )
