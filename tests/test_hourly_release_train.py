from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import hourly_release_train as train


def test_patch_bump_synchronizes_backend_and_frontend_versions(tmp_path):
    files = {
        "pyproject.toml": (
            '[project]\ndynamic = ["version"]\n'
            '[tool.setuptools.dynamic]\nversion = {attr = "trustforge._version.VERSION"}\n'
        ),
        "src/trustforge/__init__.py": (
            "from ._version import VERSION as __version__\n"
        ),
        "src/trustforge/_version.py": 'VERSION = "1.2.3"\n',
        "frontend/package.json": '{"version": "1.2.3"}\n',
        "frontend/package-lock.json": (
            '{"version": "1.2.3", "packages": {"": {"version": "1.2.3"}}}\n'
        ),
    }
    for relative, body in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")

    assert train.bump_patch_version(tmp_path) == "1.2.4"
    assert 'VERSION = "1.2.4"' in (
        tmp_path / "src/trustforge/_version.py"
    ).read_text(encoding="utf-8")
    assert '"version": "1.2.4"' in (
        tmp_path / "frontend/package.json"
    ).read_text(encoding="utf-8")


def test_backup_receipt_requires_archive_and_restore_verification(tmp_path, monkeypatch):
    train.OUT = tmp_path
    archive = tmp_path / "backup.tar.gz"
    archive.write_bytes(b"backup")

    def fake_run(*args, **kwargs):
        receipt = Path(kwargs["env"]["TRUSTFORGE_BACKUP_RECEIPT"])
        receipt.write_text(json.dumps({
            "schema": "trustforge.production-backup/v1",
            "run_id": "run",
            "archive": str(archive),
            "archive_sha256": train.hashlib.sha256(b"backup").hexdigest(),
            "restore_verified": True,
        }))

    monkeypatch.setattr(train.subprocess, "run", fake_run)
    assert train.require_backup_receipt("backup", "run") == tmp_path / "run-backup.json"


def test_backup_receipt_fails_closed_without_restore_verification(tmp_path, monkeypatch):
    train.OUT = tmp_path

    def fake_run(*args, **kwargs):
        receipt = Path(kwargs["env"]["TRUSTFORGE_BACKUP_RECEIPT"])
        receipt.write_text(json.dumps({"archive": str(tmp_path / "missing"), "restore_verified": False}))

    monkeypatch.setattr(train.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="verified restorable"):
        train.require_backup_receipt("backup", "run")


def test_lease_rejects_overlap(tmp_path):
    train.OUT = tmp_path
    with train.lease():
        with pytest.raises(RuntimeError, match="owns the lease"):
            with train.lease():
                pass


def test_lease_recovers_dead_owner(tmp_path, monkeypatch):
    train.OUT = tmp_path
    lock = tmp_path / "lease"
    lock.mkdir(parents=True)
    (lock / "owner.json").write_text(json.dumps({"pid": 99999999, "birth": "old", "token": "old"}))

    def dead_owner(_pid, _signal):
        raise ProcessLookupError

    monkeypatch.setattr(train.os, "kill", dead_owner)
    with train.lease():
        assert (lock / "owner.json").is_file()


def test_gate_localizes_uv_runtime_so_home_can_remain_denied(tmp_path, monkeypatch):
    home = tmp_path / "home"
    runtime = home / ".local/share/uv/python/cpython-3.12.8-macos-aarch64-none"
    interpreter = runtime / "bin/python3.12"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"python")
    sandbox = tmp_path / "gate"
    venv = sandbox / ".venv"
    venv_python = venv / "bin/python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(interpreter)
    (venv / "bin/python3").symlink_to(interpreter)
    (venv / "pyvenv.cfg").write_text(f"home = {runtime / 'bin'}\n")
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        target = Path(command[-1])
        (target / "bin").mkdir(parents=True)
        (target / "bin/python3.12").write_bytes(b"python")

    monkeypatch.setattr(train.subprocess, "run", fake_run)
    localized = train._localize_gate_interpreter(
        home=home,
        venv=venv,
        sandbox_root=sandbox,
    )

    assert localized == sandbox / ".python-runtime"
    assert calls == [["/bin/cp", "-cR", str(runtime), str(localized)]]
    assert venv_python.resolve() == localized / "bin/python3.12"
    assert f"home = {localized / 'bin'}" == (
        venv / "pyvenv.cfg"
    ).read_text().strip()


def test_gate_rejects_non_uv_interpreter_below_home(tmp_path):
    home = tmp_path / "home"
    interpreter = home / "bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"python")
    venv = tmp_path / "gate/.venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin/python").symlink_to(interpreter)

    with pytest.raises(RuntimeError, match="outside the uv runtime root"):
        train._localize_gate_interpreter(
            home=home,
            venv=venv,
            sandbox_root=tmp_path / "gate",
        )


def test_frontend_identity_requires_release_sha_and_question_picker(monkeypatch):
    expected_sha = "abcdef1234567890abcdef1234567890abcdef12"
    responses = iter(
        [
            '<script type="module" src="/assets/index-release.js"></script>',
            f'bundle-{expected_sha[:7]}-隨機競賽題目-Random competition question',
        ]
    )
    monkeypatch.setattr(train, "run", lambda *args, **kwargs: next(responses))

    assert train.verify_frontend_identity(expected_sha) == "assets/index-release.js"


def test_frontend_identity_retries_public_reload_window(monkeypatch):
    expected_sha = "abcdef1234567890abcdef1234567890abcdef12"
    responses = iter(
        [
            '<script type="module" src="/assets/index-release.js"></script>',
            f"bundle-{expected_sha[:7]}-隨機競賽題目-Random competition question",
        ]
    )
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return next(responses)

    monkeypatch.setattr(train, "run", fake_run)
    train.verify_frontend_identity(expected_sha)
    assert len(calls) == 2
    for command in calls:
        assert command[command.index("--retry") + 1] == "10"
        assert command[command.index("--retry-delay") + 1] == "3"
        assert command[command.index("--retry-max-time") + 1] == "45"
        assert "--retry-all-errors" in command


@pytest.mark.parametrize(
    "bundle",
    [
        "bundle-wrongsha-隨機競賽題目-Random competition question",
        "bundle-abcdef1-without-picker",
    ],
)
def test_frontend_identity_fails_closed_on_stale_or_incomplete_bundle(monkeypatch, bundle):
    responses = iter(
        [
            '<script type="module" src="/assets/index-release.js"></script>',
            bundle,
        ]
    )
    monkeypatch.setattr(train, "run", lambda *args, **kwargs: next(responses))

    with pytest.raises(RuntimeError):
        train.verify_frontend_identity("abcdef1234567890abcdef1234567890abcdef12")


def test_production_deploy_includes_backend_and_frontend(monkeypatch, tmp_path):
    calls = []

    def fake_subprocess_run(command, **kwargs):
        calls.append((command, kwargs))

    monkeypatch.setattr(train.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(train, "production_identity", lambda: ("a" * 40, "b" * 64))
    monkeypatch.setattr(
        train,
        "capture_active_pointer",
        lambda digest: {"digest": digest, "version": "v0.27.4"},
    )
    monkeypatch.setattr(train, "verify_runtime_identity", lambda digest: None)
    monkeypatch.setattr(train, "verify_frontend_identity", lambda sha: "assets/index-release.js")
    monkeypatch.setattr(train, "production_instance", lambda: "i-production")
    monkeypatch.setattr(
        train,
        "capture_frontend_state",
        lambda instance, sha: (
            "/opt/trustforge/frontend/releases/previous",
            "/etc/nginx/trustforge-sites/react-http.conf",
            "/opt/trustforge/.frontend-rollback-aaaaaaaaaaaa.tar.gz",
        ),
    )
    monkeypatch.setattr(train, "discard_frontend_snapshot", lambda *args: None)

    result = train.deploy_production(tmp_path, "a" * 40, "release/auto-20260729")

    assert [call[0][-1] for call in calls] == [
        "TRUSTFORGE_BOOTSTRAP=0 bash deploy/deploy_ec2.sh",
        "bash deploy/deploy_frontend_nginx.sh",
    ]
    assert calls[1][1]["env"]["VITE_GIT_SHA"] == "a" * 40
    assert result == {
        "git_sha": "a" * 40,
        "artifact_digest": "b" * 64,
        "frontend_asset": "assets/index-release.js",
    }


def test_production_deploy_stops_before_frontend_when_backend_identity_mismatches(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        train.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command),
    )
    monkeypatch.setattr(train, "production_identity", lambda: ("b" * 40, "c" * 64))
    monkeypatch.setattr(
        train,
        "capture_active_pointer",
        lambda digest: {"digest": digest, "version": "v0.27.4"},
    )
    rollback_calls = []
    monkeypatch.setattr(
        train,
        "restore_backend",
        lambda *args: rollback_calls.append(args),
    )

    with pytest.raises(RuntimeError, match="active SHA"):
        train.deploy_production(tmp_path, "a" * 40, "release/auto-20260729")

    assert calls == [["/bin/zsh", "-lc", "TRUSTFORGE_BOOTSTRAP=0 bash deploy/deploy_ec2.sh"]]
    assert rollback_calls == [
        (
            tmp_path,
            "b" * 40,
            "c" * 64,
            {"digest": "c" * 64, "version": "v0.27.4"},
        )
    ]


def test_production_deploy_restores_backend_when_backend_command_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(train, "production_identity", lambda: ("p" * 40, "q" * 64))
    monkeypatch.setattr(
        train,
        "capture_active_pointer",
        lambda digest: {"digest": digest, "version": "v0.27.4"},
    )
    monkeypatch.setattr(
        train.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("backend post-activation failed")),
    )
    rollback_calls = []
    monkeypatch.setattr(
        train,
        "restore_backend",
        lambda *args: rollback_calls.append(args),
    )

    with pytest.raises(RuntimeError, match="backend post-activation failed"):
        train.deploy_production(tmp_path, "a" * 40, "release/auto-20260729")

    assert rollback_calls == [
        (
            tmp_path,
            "p" * 40,
            "q" * 64,
            {"digest": "q" * 64, "version": "v0.27.4"},
        )
    ]


def test_production_deploy_restores_backend_when_frontend_fails(monkeypatch, tmp_path):
    calls = []

    def fake_subprocess_run(command, **kwargs):
        calls.append(command)
        if command[-1] == "bash deploy/deploy_frontend_nginx.sh":
            raise RuntimeError("frontend failed")

    monkeypatch.setattr(train.subprocess, "run", fake_subprocess_run)
    identities = iter([("p" * 40, "q" * 64), ("a" * 40, "b" * 64)])
    monkeypatch.setattr(train, "production_identity", lambda: next(identities))
    monkeypatch.setattr(
        train,
        "capture_active_pointer",
        lambda digest: {"digest": digest, "version": "v0.27.4"},
    )
    monkeypatch.setattr(train, "verify_runtime_identity", lambda digest: None)
    monkeypatch.setattr(train, "production_instance", lambda: "i-production")
    monkeypatch.setattr(
        train,
        "capture_frontend_state",
        lambda instance, sha: (
            "/opt/trustforge/frontend/releases/previous",
            "/etc/nginx/trustforge-sites/react-http.conf",
            "/opt/trustforge/.frontend-rollback-aaaaaaaaaaaa.tar.gz",
        ),
    )
    frontend_rollbacks = []
    monkeypatch.setattr(
        train,
        "restore_frontend",
        lambda *args: frontend_rollbacks.append(args),
    )
    rollback_calls = []
    monkeypatch.setattr(
        train,
        "restore_backend",
        lambda *args: rollback_calls.append(args),
    )

    with pytest.raises(RuntimeError, match="frontend failed"):
        train.deploy_production(tmp_path, "a" * 40, "release/auto-20260729")

    assert frontend_rollbacks == [
        (
            "i-production",
            "/opt/trustforge/frontend/releases/previous",
            "/etc/nginx/trustforge-sites/react-http.conf",
            "/opt/trustforge/.frontend-rollback-aaaaaaaaaaaa.tar.gz",
        )
    ]
    assert rollback_calls == [
        (
            tmp_path,
            "p" * 40,
            "q" * 64,
            {"digest": "q" * 64, "version": "v0.27.4"},
        )
    ]


def test_production_deploy_restores_both_layers_when_public_frontend_check_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(train.subprocess, "run", lambda *args, **kwargs: None)
    identities = iter([("p" * 40, "q" * 64), ("a" * 40, "b" * 64)])
    monkeypatch.setattr(train, "production_identity", lambda: next(identities))
    monkeypatch.setattr(
        train,
        "capture_active_pointer",
        lambda digest: {"digest": digest, "version": "v0.27.4"},
    )
    monkeypatch.setattr(train, "verify_runtime_identity", lambda digest: None)
    monkeypatch.setattr(train, "production_instance", lambda: "i-production")
    monkeypatch.setattr(
        train,
        "capture_frontend_state",
        lambda instance, sha: (
            "/opt/trustforge/frontend/releases/previous",
            "/etc/nginx/trustforge-sites/react-http.conf",
            "/opt/trustforge/.frontend-rollback-aaaaaaaaaaaa.tar.gz",
        ),
    )
    monkeypatch.setattr(
        train,
        "verify_frontend_identity",
        lambda sha: (_ for _ in ()).throw(RuntimeError("public bundle stale")),
    )
    frontend_rollbacks = []
    backend_rollbacks = []
    monkeypatch.setattr(
        train,
        "restore_frontend",
        lambda *args: frontend_rollbacks.append(args),
    )
    monkeypatch.setattr(
        train,
        "restore_backend",
        lambda *args: backend_rollbacks.append(args),
    )

    with pytest.raises(RuntimeError, match="public bundle stale"):
        train.deploy_production(tmp_path, "a" * 40, "release/auto-20260729")

    assert frontend_rollbacks == [
        (
            "i-production",
            "/opt/trustforge/frontend/releases/previous",
            "/etc/nginx/trustforge-sites/react-http.conf",
            "/opt/trustforge/.frontend-rollback-aaaaaaaaaaaa.tar.gz",
        )
    ]
    assert backend_rollbacks == [
        (
            tmp_path,
            "p" * 40,
            "q" * 64,
            {"digest": "q" * 64, "version": "v0.27.4"},
        )
    ]


def test_restore_backend_reactivates_verified_previous_artifact(monkeypatch, tmp_path):
    expected_sha = "a" * 40
    expected_digest = "b" * 64

    def fake_run(command, **kwargs):
        if command[:3] == ["aws", "s3", "cp"]:
            return json.dumps({"git_sha": expected_sha})
        raise AssertionError(command)

    monkeypatch.setattr(train, "run", fake_run)
    monkeypatch.setattr(train, "production_instance", lambda: "i-0123456789abcdef0")
    calls = []
    monkeypatch.setattr(
        train.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )
    monkeypatch.setattr(
        train,
        "production_identity",
        lambda: (expected_sha, expected_digest),
    )
    runtime_checks = []
    monkeypatch.setattr(
        train,
        "verify_runtime_identity",
        lambda digest: runtime_checks.append(digest),
    )

    train.restore_backend(
        tmp_path,
        expected_sha,
        expected_digest,
        {"digest": expected_digest, "version": "v0.27.4", "uploaded_at": "legacy"},
    )

    assert calls[0][0][:4] == ["aws", "s3", "cp", "-"]
    assert json.loads(calls[0][1]["input"]) == {
        "digest": expected_digest,
        "uploaded_at": "legacy",
        "version": "v0.27.4",
    }
    assert calls[1][0] == [
        "bash",
        "deploy/activate_release.sh",
        "--target",
        "i-0123456789abcdef0",
    ]
    assert runtime_checks == [expected_digest]


def test_capture_active_pointer_preserves_complete_legacy_pointer(monkeypatch):
    expected_digest = "b" * 64
    pointer = {
        "digest": expected_digest,
        "uploaded_at": "2026-07-28T00:00:00Z",
        "version": "v0.27.4-g4e91340",
    }
    monkeypatch.setattr(train, "run", lambda *args, **kwargs: json.dumps(pointer))

    assert train.capture_active_pointer(expected_digest) == pointer


def test_capture_active_pointer_rejects_digest_drift(monkeypatch):
    monkeypatch.setattr(
        train,
        "run",
        lambda *args, **kwargs: json.dumps(
            {"digest": "c" * 64, "version": "v0.27.4"}
        ),
    )

    with pytest.raises(RuntimeError, match="changed during pre-state"):
        train.capture_active_pointer("b" * 64)


def test_restore_frontend_atomically_switches_to_previous_release(monkeypatch):
    calls = []
    monkeypatch.setattr(
        train,
        "run_ssm",
        lambda instance, commands: calls.append((instance, commands)) or "",
    )

    train.restore_frontend(
        "i-production",
        "/opt/trustforge/frontend/releases/previous-123",
        "/etc/nginx/trustforge-sites/react-http.conf",
        "/opt/trustforge/.frontend-rollback-aaaaaaaaaaaa.tar.gz",
    )

    assert calls[0][0] == "i-production"
    assert "mv -Tf" in "\n".join(calls[0][1])
    assert "tar -C / -xzf" in "\n".join(calls[0][1])
    assert "/etc/nginx/conf.d/trustforge.conf.rollback" in "\n".join(calls[0][1])
    assert "rm -rf /etc/nginx/trustforge-sites" in calls[0][1]
    assert "systemctl daemon-reload" in calls[0][1]
    assert "nginx -t" in calls[0][1]
    assert "systemctl reload nginx" in calls[0][1]
    assert "for attempt in $(seq 1 15)" in "\n".join(calls[0][1])


@pytest.mark.parametrize(
    ("remote_output", "expected_target", "expected_nginx_target"),
    [
        (
            "/opt/trustforge/frontend/releases/previous-123\n"
            "/etc/nginx/trustforge-sites/react-http.conf",
            "/opt/trustforge/frontend/releases/previous-123",
            "/etc/nginx/trustforge-sites/react-http.conf",
        ),
        ("__ABSENT__\n__ABSENT__", "__ABSENT__", "__ABSENT__"),
    ],
)
def test_capture_frontend_state_includes_live_config_snapshot(
    monkeypatch,
    remote_output,
    expected_target,
    expected_nginx_target,
):
    calls = []
    monkeypatch.setattr(
        train,
        "run_ssm",
        lambda instance, commands: calls.append((instance, commands)) or remote_output,
    )

    target, nginx_target, snapshot = train.capture_frontend_state("i-production", "a" * 40)

    assert target == expected_target
    assert nginx_target == expected_nginx_target
    assert snapshot == "/opt/trustforge/.frontend-rollback-aaaaaaaaaaaa.tar.gz"
    command_text = "\n".join(calls[0][1])
    assert "etc/nginx/trustforge-sites" in command_text
    assert "etc/systemd/system/trustforge.service" in command_text


def test_execute_records_combined_production_deploy_result(monkeypatch, tmp_path):
    monkeypatch.setattr(train, "OUT", tmp_path / "out")
    monkeypatch.setattr(train, "require_clean_root", lambda: None)
    monkeypatch.setattr(train, "gate", lambda worktree: None)
    monkeypatch.setattr(train, "bump_patch_version", lambda worktree: "0.27.5")
    monkeypatch.setattr(train, "require_backup_receipt", lambda command, run_id: tmp_path / "backup.json")
    monkeypatch.setattr(train, "production_identity", lambda: ("c" * 40, "d" * 64))
    deployed = {
        "git_sha": "b" * 40,
        "artifact_digest": "e" * 64,
        "frontend_asset": "assets/index-release.js",
    }
    monkeypatch.setattr(train, "deploy_production", lambda *args: deployed)

    def fake_run(command, *, cwd=train.ROOT, capture=False):
        if command[:3] == ["git", "rev-list", "--left-right"]:
            return "0 1"
        if command[:3] == ["git", "rev-parse", "origin/main"]:
            return "a" * 40
        if command[:3] == ["git", "worktree", "add"]:
            Path(command[4]).mkdir(parents=True)
            return ""
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return ("b" if Path(cwd).name == "main" else "f") * 40
        return ""

    monkeypatch.setattr(train, "run", fake_run)
    monkeypatch.setattr(
        train.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    recorded = []
    monkeypatch.setattr(
        train,
        "record",
        lambda receipt: recorded.append(receipt) or tmp_path / "receipt.json",
    )

    assert train.execute(SimpleNamespace(dry_run=False)) == tmp_path / "receipt.json"
    assert recorded[-1]["status"] == "completed"
    assert recorded[-1]["steps"][-1] == {"production_deploy": "passed", **deployed}


def test_execute_does_not_noop_when_public_frontend_is_stale(monkeypatch, tmp_path):
    monkeypatch.setattr(train, "OUT", tmp_path / "out")
    monkeypatch.setattr(train, "require_clean_root", lambda: None)
    monkeypatch.setattr(train, "gate", lambda worktree: None)
    monkeypatch.setattr(train, "require_backup_receipt", lambda command, run_id: tmp_path / "backup.json")
    monkeypatch.setattr(train, "production_identity", lambda: ("a" * 40, "d" * 64))
    monkeypatch.setattr(train, "verify_runtime_identity", lambda digest: None)
    monkeypatch.setattr(
        train,
        "verify_frontend_identity",
        lambda sha: (_ for _ in ()).throw(RuntimeError("stale frontend")),
    )
    deployed = {
        "git_sha": "a" * 40,
        "artifact_digest": "d" * 64,
        "frontend_asset": "assets/index-release.js",
    }
    deploy_calls = []
    monkeypatch.setattr(
        train,
        "deploy_production",
        lambda *args: deploy_calls.append(args) or deployed,
    )

    def fake_run(command, *, cwd=train.ROOT, capture=False):
        if command[:3] == ["git", "rev-list", "--left-right"]:
            return "0 0"
        if command[:3] == ["git", "rev-parse", "origin/main"]:
            return "a" * 40
        if command[:3] == ["git", "worktree", "add"]:
            Path(command[4]).mkdir(parents=True)
            return ""
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return "a" * 40
        return ""

    monkeypatch.setattr(train, "run", fake_run)
    monkeypatch.setattr(
        train.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    recorded = []
    monkeypatch.setattr(
        train,
        "record",
        lambda receipt: recorded.append(receipt) or tmp_path / "receipt.json",
    )

    train.execute(SimpleNamespace(dry_run=False))

    assert deploy_calls
    assert recorded[-1]["status"] == "completed"
    assert "frontend_drift" not in recorded[-1]
    assert "preflight_drift" not in recorded[-1]
    assert recorded[-1]["resolved_preflight_drift"]["frontend"] == "stale frontend"
    assert recorded[-1]["post_deploy_verification"] == {
        "runtime": "passed",
        "frontend": "passed",
        "verified_main_sha": "a" * 40,
        "frontend_asset": "assets/index-release.js",
    }
