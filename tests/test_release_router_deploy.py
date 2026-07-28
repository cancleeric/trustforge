from __future__ import annotations

import json
import hashlib
import errno
import os
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from scripts.deployment_readiness import (
    COORDINATION_LOCK_PATH as OPERATOR_COORDINATION_LOCK_PATH,
    LEDGER_PATH,
    _key_roles_for_command,
)
from scripts import install_router_release_artifact
from trustforge.release_router_runtime import COORDINATION_LOCK_PATH
from trustforge.signed_event_ledger import SECURITY_LEDGER_ROOT
from trustforge.signed_event_ledger import SignedEventLedger


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
        < result.stdout.index("systemctl start trustforge-release-router.service")
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
    assert "os.execv" not in source
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
    assert "_publish_swap(stage, args.target_root, backup, journal)" in source
    assert "os.replace(target, backup)" in source
    assert "os.replace(stage, target)" in source


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
    assert _key_roles_for_command("rebuild-checkpoint") == frozenset(
        {"control-public", "outcome-public", "control-private"}
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


@pytest.mark.parametrize("rollback_daemon_failure", [False, True])
def test_installer_failure_stops_new_service_and_restores_nginx(
    tmp_path, rollback_daemon_failure
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    trace = tmp_path / "trace"

    def command(name: str, body: str) -> None:
        path = fake_bin / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)

    command(
        "id",
        'if [ "${1:-}" = "-u" ]; then echo 0; '
        'elif [ "${1:-}" = "-nG" ]; then echo trustforge-release; fi\nexit 0\n',
    )
    command("systemd-sysusers", "exit 0\n")
    command("systemd-tmpfiles", "exit 0\n")
    command("usermod", "exit 0\n")
    command("nginx", 'if [ "${1:-}" = "-T" ]; then echo "user fake;"; fi\nexit 0\n')
    command(
        "systemctl",
        f'echo "$*" >>"{trace}"\n'
        f'if [ "${{1:-}}" = "stop" ]; then touch "{tmp_path}/stopped"; fi\n'
        f'if [ "${{1:-}}" = "daemon-reload" ] && '
        f'[ -f "{tmp_path}/stopped" ] && '
        f'[ "{str(rollback_daemon_failure).lower()}" = "true" ]; then exit 1; fi\n'
        'if [ "${1:-}" = "is-active" ]; then exit 0; fi\n'
        f'if [ "${{1:-}}" = "show" ]; then '
        f'if [ -f "{tmp_path}/pid-seen" ]; then echo 222; '
        f'else touch "{tmp_path}/pid-seen"; echo 111; fi; fi\nexit 0\n',
    )
    command("setpriv", "exit 0\n")
    fake_archive_digest = hashlib.sha256(b"{}").hexdigest()
    fake_release = tmp_path / f"root/opt/trustforge/releases/{fake_archive_digest}"
    command(
        "python3",
        f'case "${{1:-}}" in *write_release_rollback_evidence.py) '
        f'exec "{ROOT}/.venv/bin/python" "$@";; '
        f'*install_router_release_artifact.py) mkdir -p "{fake_release}"; '
        f'echo "{fake_release}";; esac\nexit 0\n',
    )
    command(
        "curl",
        f'echo "$*" >>"{trace}"\ncase "$*" in *--netrc-file*) exit 22;; esac\nexit 0\n',
    )
    command(
        "stat",
        'case "$*" in *"%a"*) echo 600;; *) echo 0;; esac\n',
    )
    command(
        "install",
        'prev=""; last=""; for arg in "$@"; do prev="$last"; last="$arg"; done\n'
        'cp "$prev" "$last"\n',
    )

    install_root = tmp_path / "root"
    nginx_target = install_root / "etc/nginx/snippets/trustforge-release-router.conf"
    nginx_target.parent.mkdir(parents=True)
    nginx_target.write_text("old nginx\n")
    for path in (
        install_root / "etc/trustforge/release-router-runtime.json",
        install_root / "etc/trustforge/release-router-runtime-keys.json",
        install_root / "var/lib/trustforge/security-ledger/control/bootstrap.json",
        install_root
        / "var/lib/trustforge/security-ledger/router-outcomes/bootstrap.json",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    netrc = tmp_path / "smoke.netrc"
    netrc.write_text("machine router.test login secret-user password secret-pass\n")
    netrc.chmod(0o600)
    ca = tmp_path / "ca.pem"
    ca.write_text("test-ca")
    evidence = tmp_path / "release-evidence.json"
    evidence_keys = tmp_path / "release-evidence-keys.json"
    artifact_a = tmp_path / "a.artifact"
    artifact_b = tmp_path / "b.artifact"
    manifests = tmp_path / "endpoint-manifests.json"
    router_archive = tmp_path / "router.tar"
    tree_manifest = tmp_path / "tree-manifest.json"
    runtime_lock = tmp_path / "runtime.lock"
    signed_unit = tmp_path / "signed-router.service"
    for path in (
        evidence,
        artifact_a,
        artifact_b,
        manifests,
        router_archive,
        tree_manifest,
        runtime_lock,
    ):
        path.write_text("{}")
    signed_unit.write_text(
        "[Service]\n"
        f"WorkingDirectory={fake_release}\n"
        f"ExecStart={fake_release}/.venv/bin/python -I "
        f"{fake_release}/scripts/release_router_service.py\n"
        f"Environment=TRUSTFORGE_RELEASE_DIGEST={fake_archive_digest}\n"
        "UnsetEnvironment=PYTHONPATH PYTHONHOME\n"
    )
    evidence.chmod(0o600)
    evidence_keys.write_text("{}")
    evidence_keys.chmod(0o400)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "TRUSTFORGE_INSTALL_ROOT": str(install_root),
        "TRUSTFORGE_SMOKE_NETRC": str(netrc),
        "TRUSTFORGE_SMOKE_CA": str(ca),
        "TRUSTFORGE_SMOKE_HOST": "router.test",
        "TRUSTFORGE_EXPECTED_RELEASE_EVIDENCE": str(evidence),
        "TRUSTFORGE_RELEASE_EVIDENCE_KEYS": str(evidence_keys),
        "TRUSTFORGE_A_ARTIFACT": str(artifact_a),
        "TRUSTFORGE_B_ARTIFACT": str(artifact_b),
        "TRUSTFORGE_ENDPOINT_MANIFESTS": str(manifests),
        "TRUSTFORGE_ROUTER_ARCHIVE": str(router_archive),
        "TRUSTFORGE_ROUTER_TREE_MANIFEST": str(tree_manifest),
        "TRUSTFORGE_RUNTIME_LOCK": str(runtime_lock),
        "TRUSTFORGE_SIGNED_UNIT": str(signed_unit),
    }

    result = subprocess.run(
        ["bash", str(ROOT / "deploy/install_release_router.sh")],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert nginx_target.read_text() == "old nginx\n"
    assert trace.exists(), result.stderr
    recorded = trace.read_text()
    assert "stop trustforge-release-router.service" in recorded
    assert "restart trustforge-release-router.service" in recorded
    assert "start trustforge-release-router.service" in recorded
    assert "enable trustforge-release-router.service" not in recorded
    assert "--netrc-file /dev/fd/9" in recorded
    assert "--insecure" not in recorded
    assert "secret-user" not in recorded
    assert "secret-pass" not in recorded
    evidence = install_root / "var/lib/trustforge/release-install-rollback-failed.json"
    if rollback_daemon_failure:
        assert result.returncode == 91
        assert evidence.stat().st_mode & 0o777 == 0o600
        payload = json.loads(evidence.read_text())
        assert payload["schema"] == "trustforge.release-install-rollback-failed/v2"
        assert payload["steps"] == [
            {
                "attempted": True,
                "error_code": 0,
                "name": "service-stop",
                "success": True,
            },
            {
                "attempted": True,
                "error_code": 0,
                "name": "artifact-restore",
                "success": True,
            },
            {
                "attempted": True,
                "error_code": 1,
                "name": "daemon-reload",
                "success": False,
            },
            {
                "attempted": True,
                "error_code": 0,
                "name": "service-health",
                "success": True,
            },
        ]
        assert payload["target_archive_sha256"] == fake_archive_digest
        assert len(payload["target_evidence_sha256"]) == 64
    else:
        assert result.returncode == 22
        assert not evidence.exists()


def test_release_install_evidence_binds_every_intended_artifact(tmp_path):
    names = (
        "unit",
        "runtime",
        "keys",
        "control-bootstrap",
        "control-events",
        "control-head",
        "outcome-bootstrap",
        "a-artifact",
        "b-artifact",
        "endpoint-manifests",
        "router-archive",
        "router-tree-manifest",
        "runtime-lock",
    )
    paths = {}
    private = Ed25519PrivateKey.generate()
    payload = {
        "schema": "trustforge.release-install-evidence/v1",
        "key_id": "release-1",
    }
    for name in names:
        path = tmp_path / name
        path.write_bytes((name + "\n").encode())
        paths[name] = path
    endpoint_private = Ed25519PrivateKey.generate()
    endpoint_public = (
        endpoint_private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    )
    endpoint_key_id = "endpoint-1"
    artifact_digests = {
        role: "sha256:"
        + hashlib.sha256(paths[f"{role}-artifact"].read_bytes()).hexdigest()
        for role in ("a", "b")
    }
    routing_private = Ed25519PrivateKey.generate()
    routing_key_id = "routing-1"
    policy = {
        "ratio_basis_points": 100,
        "request_cap": 1000,
        "timeout_ms": 1000,
        "routing_key_id": routing_key_id,
        "ramp_id": "release-test",
        "policy_digest": "sha256:" + "9" * 64,
    }
    control_private = Ed25519PrivateKey.generate()
    control_public = control_private.public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw
    )
    control_root = tmp_path / "ledger"
    control_directory = control_root / "control"
    ledger = SignedEventLedger(
        directory=control_directory,
        verification_keys={"control-1": control_public},
        event_permissions={
            "release-control": frozenset(
                {
                    "deployment_initialized",
                    "operator_stop",
                    "activation_prepared",
                    "activation_completed",
                    "activation_failed",
                }
            )
        },
        domain_keys={"release-control": frozenset({"control-1"})},
        signing_key_id="control-1",
        signing_private_key=control_private.private_bytes_raw(),
        signing_domain="release-control",
        ledger_role="release-control",
        bootstrap=True,
        coordination_root=control_root,
    )
    initialized = ledger.append(
        {
            "kind": "deployment_initialized",
            "target": "production",
            "target_confirmation": "test",
            "active": {
                "release_digest": artifact_digests["a"],
                "base_url": "http://127.0.0.1:8000",
                "manifest_key_id": endpoint_key_id,
            },
            "candidate": {
                "release_digest": artifact_digests["b"],
                "base_url": "http://127.0.0.1:8001",
                "manifest_key_id": endpoint_key_id,
            },
            "policy": policy,
            "evidence_bundle_digest": "sha256:" + "8" * 64,
            "stop_after_errors": 3,
        }
    )
    paths["control-bootstrap"] = control_directory / "bootstrap.json"
    paths["control-events"] = control_directory / "events.jsonl"
    paths["control-head"] = control_directory / "head.json"
    manifests_payload = {}
    for role in ("a", "b"):
        unsigned = {
            "schema": "trustforge.endpoint-manifest/v1",
            "artifact_digest": artifact_digests[role],
            "origin": f"http://127.0.0.1:{8000 if role == 'a' else 8001}",
            "key_id": endpoint_key_id,
        }
        manifests_payload[role] = {
            **unsigned,
            "signature": endpoint_private.sign(
                b"trustforge.endpoint-manifest.v1\x00"
                + json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
            ).hex(),
        }
    paths["endpoint-manifests"].write_text(
        json.dumps(
            {
                "schema": "trustforge.endpoint-manifest-bundle/v1",
                **manifests_payload,
                "public_keys": {endpoint_key_id: endpoint_public},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    outcome_private = Ed25519PrivateKey.generate()
    outcome_public = outcome_private.public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw
    )
    paths["keys"].write_text(
        json.dumps(
            {
                "control_event_public": {"control-1": control_public.hex()},
                "router_outcome_public": {"outcome-1": outcome_public.hex()},
                "router_outcome_private": {
                    "outcome-1": outcome_private.private_bytes_raw().hex()
                },
                "routing": {
                    routing_key_id: routing_private.public_key()
                    .public_bytes(Encoding.Raw, PublicFormat.Raw)
                    .hex()
                },
                "endpoint_manifest_public": {endpoint_key_id: endpoint_public},
                "authorization_public": {
                    "authorization-1": Ed25519PrivateKey.generate()
                    .public_key()
                    .public_bytes(Encoding.Raw, PublicFormat.Raw)
                    .hex()
                },
                "completion_public": {
                    "completion-1": Ed25519PrivateKey.generate()
                    .public_key()
                    .public_bytes(Encoding.Raw, PublicFormat.Raw)
                    .hex()
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    paths["runtime"].write_text(
        json.dumps(
            {
                "schema": "trustforge.release-router-runtime/v1",
                "a_artifact_digest": artifact_digests["a"],
                "b_artifact_digest": artifact_digests["b"],
                "endpoint_manifest_key_ids": [endpoint_key_id],
                "control_ledger_id": initialized["ledger_id"],
                "deployment_initialized_event_hash": initialized["event_hash"],
                "routing_policy": policy,
                "outcome_signing_key_id": "outcome-1",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    python_digest = hashlib.sha256(b"python-runtime").hexdigest()
    paths["router-tree-manifest"].write_text(
        json.dumps(
            {
                "schema": "trustforge.router-tree-manifest/v1",
                "entries": [
                    {
                        "path": ".venv/bin/python",
                        "type": "file",
                        "mode": "0555",
                        "sha256": python_digest,
                    },
                    {
                        "path": "scripts/release_router_service.py",
                        "type": "file",
                        "mode": "0444",
                        "sha256": hashlib.sha256(b"router").hexdigest(),
                    },
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    paths["runtime-lock"].write_text(
        json.dumps(
            {
                "schema": "trustforge.router-runtime-lock/v1",
                "tree_manifest_sha256": hashlib.sha256(
                    paths["router-tree-manifest"].read_bytes()
                ).hexdigest(),
                "python_sha256": python_digest,
                "packages": {"trustforge": "2026.7.28"},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    payload["control_ledger_id"] = initialized["ledger_id"]
    payload["control_ledger_head"] = initialized["event_hash"]
    for name in names:
        payload[name.replace("-", "_") + "_sha256"] = hashlib.sha256(
            paths[name].read_bytes()
        ).hexdigest()
    signature = private.sign(
        b"trustforge.release-install-evidence.v1\x00"
        + json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hex()
    payload["signature"] = signature
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    )
    evidence.chmod(0o600)
    keyring = tmp_path / "release-keys.json"
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    keyring.write_text(
        json.dumps({"release-1": public}, sort_keys=True, separators=(",", ":")) + "\n"
    )
    keyring.chmod(0o400)
    command = [
        str(ROOT / ".venv/bin/python"),
        str(ROOT / "scripts/verify_release_install_evidence.py"),
        "--evidence",
        str(evidence),
        "--public-keyring",
        str(keyring),
    ]
    for name in names:
        command.extend(("--" + name, str(paths[name])))

    subprocess.run(command, check=True)
    original_head = payload["control_ledger_head"]
    payload["control_ledger_head"] = "0" * 64
    unsigned_payload = {
        key: value for key, value in payload.items() if key != "signature"
    }
    payload["signature"] = private.sign(
        b"trustforge.release-install-evidence.v1\x00"
        + json.dumps(
            unsigned_payload, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hex()
    evidence.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    )
    ledger_mismatch = subprocess.run(command, capture_output=True, text=True)
    assert ledger_mismatch.returncode != 0
    assert "control ledger identity mismatch" in ledger_mismatch.stderr

    payload["control_ledger_head"] = original_head
    key_payload = json.loads(paths["keys"].read_text())
    key_payload["endpoint_manifest_public"] = {
        endpoint_key_id: Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
        .hex()
    }
    paths["keys"].write_text(
        json.dumps(key_payload, sort_keys=True, separators=(",", ":")) + "\n"
    )
    payload["keys_sha256"] = hashlib.sha256(paths["keys"].read_bytes()).hexdigest()
    unsigned_payload = {
        key: value for key, value in payload.items() if key != "signature"
    }
    payload["signature"] = private.sign(
        b"trustforge.release-install-evidence.v1\x00"
        + json.dumps(
            unsigned_payload, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hex()
    evidence.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    )
    key_mismatch = subprocess.run(command, capture_output=True, text=True)
    assert key_mismatch.returncode != 0
    assert "keys do not exactly match bundle" in key_mismatch.stderr

    paths["b-artifact"].write_text("tampered")
    failed = subprocess.run(command, capture_output=True, text=True)

    assert failed.returncode != 0
    assert "b_artifact_sha256" in failed.stderr


def test_content_addressed_router_artifact_rejects_unlisted_entries(tmp_path):
    source = tmp_path / "release_router_service.py"
    source.write_text("print('isolated-router')\n")
    python_runtime = tmp_path / "python"
    shutil.copy2(sys.executable, python_runtime)
    archive = tmp_path / "router.tar"
    with tarfile.open(archive, "w") as bundle:
        for directory in (".venv", ".venv/bin", "scripts"):
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            bundle.addfile(info)
        bundle.add(source, arcname="scripts/release_router_service.py")
        bundle.add(python_runtime, arcname=".venv/bin/python")
    manifest = tmp_path / "tree.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "trustforge.router-tree-manifest/v1",
                "entries": [
                    {
                        "path": ".venv/bin/python",
                        "type": "file",
                        "mode": "0555",
                        "sha256": hashlib.sha256(
                            python_runtime.read_bytes()
                        ).hexdigest(),
                    },
                    {
                        "path": "scripts/release_router_service.py",
                        "type": "file",
                        "mode": "0444",
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    },
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    releases = tmp_path / "releases"
    command = [
        str(ROOT / ".venv/bin/python"),
        str(ROOT / "scripts/install_router_release_artifact.py"),
        "--archive",
        str(archive),
        "--tree-manifest",
        str(manifest),
        "--releases-root",
        str(releases),
    ]

    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    release = Path(result.stdout.strip())
    assert release.name == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert (
        release / "scripts/release_router_service.py"
    ).read_bytes() == source.read_bytes()
    assert (
        stat.S_IMODE((release / "scripts/release_router_service.py").stat().st_mode)
        == 0o444
    )
    isolated = subprocess.run(
        [
            str(release / ".venv/bin/python"),
            "-I",
            str(release / "scripts/release_router_service.py"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert isolated.stdout == "isolated-router\n"

    (release / "scripts/release_router_service.py").chmod(0o555)
    mode_tamper = subprocess.run(command, capture_output=True, text=True)
    assert mode_tamper.returncode != 0
    assert "does not match manifest" in mode_tamper.stderr
    (release / "scripts/release_router_service.py").chmod(0o444)

    bad_manifest = tmp_path / "bad-tree.json"
    bad_manifest.write_text(
        json.dumps(
            {
                "schema": "trustforge.router-tree-manifest/v1",
                "entries": [
                    {
                        "path": "../escape",
                        "type": "file",
                        "mode": "0444",
                        "sha256": "0" * 64,
                    }
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    bad_command = command.copy()
    bad_command[5] = str(bad_manifest)
    failed = subprocess.run(bad_command, capture_output=True, text=True)
    assert failed.returncode != 0

    releases.chmod(0o777)
    unsafe_mode = subprocess.run(command, capture_output=True, text=True)
    assert unsafe_mode.returncode != 0
    assert "releases root" in unsafe_mode.stderr
    releases.chmod(0o755)

    releases_link = tmp_path / "releases-link"
    releases_link.symlink_to(releases, target_is_directory=True)
    symlink_command = command.copy()
    symlink_command[7] = str(releases_link)
    unsafe_symlink = subprocess.run(
        symlink_command, capture_output=True, text=True
    )
    assert unsafe_symlink.returncode != 0
    assert "releases root" in unsafe_symlink.stderr


def test_content_addressed_router_artifact_fails_closed_on_cross_device_publish(
    tmp_path, monkeypatch
):
    source = tmp_path / "release_router_service.py"
    source.write_text("print('router')\n")
    python_runtime = tmp_path / "python"
    shutil.copy2(sys.executable, python_runtime)
    archive = tmp_path / "router.tar"
    with tarfile.open(archive, "w") as bundle:
        for directory in (".venv", ".venv/bin", "scripts"):
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            bundle.addfile(info)
        bundle.add(source, arcname="scripts/release_router_service.py")
        bundle.add(python_runtime, arcname=".venv/bin/python")
    manifest = tmp_path / "tree.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "trustforge.router-tree-manifest/v1",
                "entries": [
                    {
                        "path": ".venv/bin/python",
                        "type": "file",
                        "mode": "0555",
                        "sha256": hashlib.sha256(
                            python_runtime.read_bytes()
                        ).hexdigest(),
                    },
                    {
                        "path": "scripts/release_router_service.py",
                        "type": "file",
                        "mode": "0444",
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    },
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    releases = tmp_path / "releases"
    releases.mkdir(mode=0o755)
    real_lstat = os.lstat

    def wrong_owner_lstat(path):
        info = real_lstat(path)
        if Path(path) == releases:
            values = list(info)
            values[4] = os.geteuid() + 1
            return os.stat_result(values)
        return info

    arguments = [
        "install_router_release_artifact.py",
        "--archive",
        str(archive),
        "--tree-manifest",
        str(manifest),
        "--releases-root",
        str(releases),
    ]
    with monkeypatch.context() as owner_patch:
        owner_patch.setattr(
            install_router_release_artifact.os, "lstat", wrong_owner_lstat
        )
        owner_patch.setattr(sys, "argv", arguments)
        with pytest.raises(SystemExit, match="owner-controlled"):
            install_router_release_artifact.main()

    monkeypatch.setattr(
        install_router_release_artifact.os,
        "replace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError(errno.EXDEV, "cross-device publish")
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        arguments,
    )

    with pytest.raises(OSError, match="cross-device publish"):
        install_router_release_artifact.main()
    assert not any(releases.iterdir())


def test_content_addressed_router_artifact_bounds_directory_member_bomb(
    tmp_path, monkeypatch
):
    source = tmp_path / "release_router_service.py"
    source.write_text("print('router')\n")
    python_runtime = tmp_path / "python"
    shutil.copy2(sys.executable, python_runtime)
    archive = tmp_path / "router.tar"
    with tarfile.open(archive, "w") as bundle:
        for directory in (".venv", ".venv/bin", "scripts"):
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            bundle.addfile(info)
        bundle.add(source, arcname="scripts/release_router_service.py")
        bundle.add(python_runtime, arcname=".venv/bin/python")
    manifest = tmp_path / "tree.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "trustforge.router-tree-manifest/v1",
                "entries": [
                    {
                        "path": ".venv/bin/python",
                        "type": "file",
                        "mode": "0555",
                        "sha256": hashlib.sha256(
                            python_runtime.read_bytes()
                        ).hexdigest(),
                    },
                    {
                        "path": "scripts/release_router_service.py",
                        "type": "file",
                        "mode": "0444",
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    },
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    releases = tmp_path / "releases"
    monkeypatch.setattr(install_router_release_artifact, "MAX_MEMBERS", 3)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "install_router_release_artifact.py",
            "--archive",
            str(archive),
            "--tree-manifest",
            str(manifest),
            "--releases-root",
            str(releases),
        ],
    )

    with pytest.raises(SystemExit, match="too many members"):
        install_router_release_artifact.main()
    assert not any(releases.iterdir())
