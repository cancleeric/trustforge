from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.deployment_readiness import (
    COORDINATION_LOCK_PATH as OPERATOR_COORDINATION_LOCK_PATH,
    LEDGER_PATH,
    _key_roles_for_command,
)
from trustforge.release_router_runtime import COORDINATION_LOCK_PATH
from trustforge.signed_event_ledger import SECURITY_LEDGER_ROOT


ROOT = Path(__file__).resolve().parents[1]


def test_install_workflow_dry_run_is_explicit_and_non_mutating():
    result = subprocess.run(
        ["bash", str(ROOT / "deploy/install_release_router.sh"), "--dry-run"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "systemctl start trustforge-release-router.service" in result.stdout
    assert "systemctl enable trustforge-release-router.service" in result.stdout
    assert result.stdout.index("curl --unix-socket") < result.stdout.index(
        "systemctl enable trustforge-release-router.service"
    )
    assert "nginx -t" in result.stdout
    assert "systemd-sysusers" in result.stdout
    assert "systemd-tmpfiles --create" in result.stdout
    assert "resolve the configured worker user" in result.stdout
    assert "usermod -a -G trustforge-release" in result.stdout
    assert (
            result.stdout.index("systemd-sysusers")
            < result.stdout.index("systemd-tmpfiles --create")
            < result.stdout.index(
                "systemctl start trustforge-release-router.service"
            )
    )


def test_proxy_strips_external_identity_and_injects_authenticated_identity():
    config = (ROOT / "deploy/trustforge-release-router.nginx.conf").read_text()
    assert 'if ($remote_user = "") { return 401; }' in config
    assert 'proxy_set_header X-TrustForge-Stable-Subject "";' in config
    assert "proxy_set_header X-TrustForge-Trusted-Subject $remote_user;" in config
    assert "proxy_redirect off;" in config
    assert "unix:/run/trustforge/release-router.sock" in config


def test_systemd_uses_exact_runtime_inputs_and_bounded_resources():
    unit = (ROOT / "deploy/trustforge-release-router.service").read_text()
    assert "User=trustforge-router" in unit
    assert "Group=trustforge-release" in unit
    assert "ReadOnlyPaths=/etc/trustforge/release-router-runtime.json" in unit
    assert "ReadOnlyPaths=/etc/trustforge/release-router-runtime-keys.json" in unit
    assert "ReadOnlyPaths=/etc/trustforge\n" not in unit
    assert "ReadOnlyPaths=/var/lib/trustforge/security-ledger/control" in unit
    assert "ReadWritePaths=/var/lib/trustforge/security-ledger/router-outcomes" in unit
    assert "ReadWritePaths=/var/lib/trustforge/security-ledger\n" not in unit
    assert "ReadWritePaths=/run/trustforge-release-control/coordination.lock" in unit
    assert "InaccessiblePaths=/etc/trustforge/deployment-keys" in unit
    assert "ExecStartPre=/usr/bin/touch" not in unit
    assert "TasksMax=64" in unit
    assert "MemoryMax=256M" in unit
    assert "AF_INET6" in unit
    assert str(COORDINATION_LOCK_PATH) == (
        "/run/trustforge-release-control/coordination.lock"
    )
    assert OPERATOR_COORDINATION_LOCK_PATH == COORDINATION_LOCK_PATH
    control_source = (ROOT / "src/trustforge/deployment_control.py").read_text()
    assert "socket.AF_INET6 if address.version == 6" in control_source
    assert "(str(address), int(parsed.port), 0, 0)" in control_source


def test_lock_is_preprovisioned_in_nonwritable_root_owned_parent():
    tmpfiles = (ROOT / "deploy/trustforge-release-router.tmpfiles.conf").read_text()
    sysusers = (ROOT / "deploy/trustforge-release-router.sysusers.conf").read_text()
    assert "d /run/trustforge-release-control 0750 root trustforge-release" in tmpfiles
    assert (
        "f /run/trustforge-release-control/coordination.lock "
        "0660 root trustforge-release" in tmpfiles
    )
    assert "u trustforge-router" in sysusers
    assert "u trustforge-operator" in sysusers
    assert "m trustforge-router trustforge-release" in sysusers
    assert "m trustforge-operator trustforge-release" in sysusers
    assert (
        "d /var/lib/trustforge/security-ledger 0750 root trustforge-release" in tmpfiles
    )
    assert (
        "d /var/lib/trustforge/security-ledger/control 0750 "
        "trustforge-operator trustforge-release" in tmpfiles
    )
    assert (
        "d /var/lib/trustforge/security-ledger/router-outcomes 0750 "
        "trustforge-router trustforge-release" in tmpfiles
    )
    parent_mode = int(
        next(
            line.split()[2] for line in tmpfiles.splitlines() if line.startswith("d ")
        ),
        8,
    )
    lock_mode = int(
        next(
            line.split()[2] for line in tmpfiles.splitlines() if line.startswith("f ")
        ),
        8,
    )
    assert parent_mode & 0o020 == 0  # router group cannot unlink/recreate
    assert lock_mode & 0o060 == 0o060  # both identities can lock stable inode


def test_installer_fails_closed_when_nginx_worker_user_is_unknown():
    source = (ROOT / "deploy/install_release_router.sh").read_text()
    assert "nginx -T 2>&1" in source
    assert '[[ -n "$NGINX_WORKER_USER" ]]' in source
    assert 'id "$NGINX_WORKER_USER"' in source
    assert 'usermod -a -G trustforge-release "$NGINX_WORKER_USER"' in source
    assert "grep -Fx trustforge-release" in source
    assert 's.connect("/run/trustforge/release-router.sock")' in source
    assert "exit 78" in source


def test_root_provisioner_separates_bootstrap_signers_and_consumes_outcome_key():
    source = (ROOT / "scripts/provision_release_ledgers.py").read_text()
    assert "ledger provisioning requires root" in source
    assert 'pwd.getpwnam("trustforge-operator")' in source
    assert 'pwd.getpwnam("trustforge-router")' in source
    assert '"trustforge-operator",' in source
    assert '"control",' in source
    assert '"trustforge-router"' in source
    assert "pass_fds=(descriptor,)" in source
    assert "_consume_seed(" in source
    assert "os.unlink(path.name, dir_fd=parent_fd)" in source
    assert "os.fsync(parent_fd)" in source
    assert "router-outcome-bootstrap-1" in source
    assert "router-outcome-runtime-1" in source
    main = source[source.index("def main()") :]
    assert main.index('pwd.getpwnam("trustforge-operator")') < main.index("_run_as(")


def test_migration_authenticates_both_chains_before_staging_or_swap():
    source = (ROOT / "scripts/migrate_release_ledgers.py").read_text()
    main = source[source.index("def main()") :]
    stage_create = main.index("stage.mkdir(")
    source_verify = main.index("read_from_exclusively_locked_fd")
    assert source_verify < stage_create
    assert "projection.read()" in source
    assert "fcntl.flock(coordination, fcntl.LOCK_EX)" in source
    assert "read_from_exclusively_locked_fd" in source
    assert "ALLOWED_FIXED_FILES" in source
    assert "_write_journal" in source
    assert "_recover(" in source
    assert "os.replace(args.target_root, backup)" in source
    assert "os.replace(stage, args.target_root)" in source


def test_operator_emergency_paths_are_artifact_and_extra_key_independent():
    source = (ROOT / "scripts/deployment_readiness.py").read_text()
    runtime_source = (ROOT / "src/trustforge/release_router_runtime.py").read_text()
    assert 'pwd.getpwnam("trustforge-operator").pw_uid' in source
    assert 'pwd.getpwnam("trustforge-router").pw_uid' in source
    assert '"root_owner_uid": 0' in source
    assert '"root_mode": 0o750' in source
    assert '"directory_mode": 0o750' in source
    assert '"file_mode": 0o640' in source
    assert 'pwd.getpwnam("trustforge-operator").pw_uid' in runtime_source
    assert 'pwd.getpwnam("trustforge-router").pw_uid' in runtime_source
    assert 'verify_retained_a = args.command in {"rollback-a", "complete"}' in source
    assert 'KEY_DIRECTORY / f"{name}.json"' in source
    assert _key_roles_for_command("status") == frozenset(
        {"control-public", "outcome-public"}
    )
    assert _key_roles_for_command("stop") == frozenset(
        {
            "control-public",
            "outcome-public",
            "control-private",
            "authorization-public",
        }
    )
    assert _key_roles_for_command("complete") == frozenset(
        {
            "control-public",
            "outcome-public",
            "control-private",
            "completion-public",
        }
    )
    assert LEDGER_PATH == SECURITY_LEDGER_ROOT
    assert "outcome-private" not in _key_roles_for_command("initialize")
    assert "outcome-private" not in _key_roles_for_command("start")
    assert "outcome-private" not in _key_roles_for_command("promote")
