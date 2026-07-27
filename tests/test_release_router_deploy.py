from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.deployment_readiness import _key_roles_for_command


ROOT = Path(__file__).resolve().parents[1]


def test_install_workflow_dry_run_is_explicit_and_non_mutating():
    result = subprocess.run(
        ["bash", str(ROOT / "deploy/install_release_router.sh"), "--dry-run"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "systemctl enable --now trustforge-release-router.service" in result.stdout
    assert "nginx -t" in result.stdout


def test_proxy_strips_external_identity_and_injects_authenticated_identity():
    config = (ROOT / "deploy/trustforge-release-router.nginx.conf").read_text()
    assert 'if ($remote_user = "") { return 401; }' in config
    assert 'proxy_set_header X-TrustForge-Stable-Subject "";' in config
    assert "proxy_set_header X-TrustForge-Trusted-Subject $remote_user;" in config
    assert "proxy_redirect off;" in config
    assert "unix:/run/trustforge/release-router.sock" in config


def test_systemd_uses_exact_runtime_inputs_and_bounded_resources():
    unit = (ROOT / "deploy/trustforge-release-router.service").read_text()
    assert "ReadOnlyPaths=/etc/trustforge/release-router-runtime.json" in unit
    assert "ReadOnlyPaths=/etc/trustforge/release-router-runtime-keys.json" in unit
    assert "ReadOnlyPaths=/etc/trustforge\n" not in unit
    assert "TasksMax=64" in unit
    assert "MemoryMax=256M" in unit


def test_operator_emergency_paths_are_artifact_and_extra_key_independent():
    source = (ROOT / "scripts/deployment_readiness.py").read_text()
    assert 'verify_retained_a = args.command in {"rollback-a", "complete"}' in source
    assert 'KEY_DIRECTORY / f"{name}.json"' in source
    assert _key_roles_for_command("status") == frozenset({"ledger"})
    assert _key_roles_for_command("stop") == frozenset(
        {"ledger", "authorization"}
    )
    assert _key_roles_for_command("complete") == frozenset(
        {"ledger", "completion"}
    )
