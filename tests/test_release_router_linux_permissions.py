from __future__ import annotations

import os
import platform
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


ROOT = Path(__file__).resolve().parents[1]
RUN_PRIVILEGE_TEST = (
    platform.system() == "Linux"
    and os.geteuid() == 0
    and os.environ.get("TRUSTFORGE_RUN_PRIVILEGE_INTEGRATION") == "1"
    and all(
        shutil.which(tool)
        for tool in ("setpriv", "systemd-sysusers", "systemd-tmpfiles")
    )
)


@pytest.mark.skipif(
    not RUN_PRIVILEGE_TEST,
    reason="requires opt-in Linux root privilege-integration CI",
)
def test_linux_cross_uid_projection_and_writer_permissions(tmp_path):
    router = pwd.getpwnam("trustforge-router")
    operator = pwd.getpwnam("trustforge-operator")
    release_gid = __import__("grp").getgrnam("trustforge-release").gr_gid
    # pytest creates its session and worker parents as 0700.  The privilege
    # integration intentionally changes UID below, so every sandbox ancestor
    # below /tmp must be traversable without broadening the ledger directories.
    tmp_path.parent.parent.chmod(0o755)
    tmp_path.parent.chmod(0o755)
    tmp_path.chmod(0o755)
    lock_parent = tmp_path / "release-control"
    lock_parent.mkdir(mode=0o750)
    os.chown(lock_parent, 0, release_gid)
    coordination_lock = lock_parent / "coordination.lock"
    coordination_lock.touch(mode=0o660)
    os.chown(coordination_lock, 0, release_gid)
    root = tmp_path / "security-ledger"
    control = root / "control"
    outcomes = root / "router-outcomes"
    root.mkdir(mode=0o750)
    control.mkdir(mode=0o750)
    outcomes.mkdir(mode=0o750)
    os.chown(root, 0, release_gid)
    os.chown(control, operator.pw_uid, release_gid)
    os.chown(outcomes, router.pw_uid, release_gid)
    control_seed = tmp_path / "control.seed"
    outcome_seed = tmp_path / "outcome.seed"
    control_seed.write_bytes(b"c" * 32)
    outcome_seed.write_bytes(b"o" * 32)
    control_seed.chmod(0o400)
    outcome_seed.chmod(0o400)
    control_runtime_private = Ed25519PrivateKey.generate()
    control_runtime_public = control_runtime_private.public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw
    )
    outcome_runtime_public = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
    )
    provisioned = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/provision_release_ledgers.py"),
            "provision",
            "--root",
            str(root),
            "--control-key",
            str(control_seed),
            "--control-runtime-public",
            control_runtime_public.hex(),
            "--outcome-bootstrap-key",
            str(outcome_seed),
            "--outcome-runtime-public",
            outcome_runtime_public.hex(),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert not outcome_seed.exists()
    assert not control_seed.exists()
    provision_receipt = __import__("json").loads(provisioned.stdout)
    control_public_file = tmp_path / "control-public.json"
    outcome_public_file = tmp_path / "outcome-public.json"
    control_public_file.write_text(
        __import__("json").dumps(
            {
                provision_receipt["control_bootstrap_public"][
                    "key_id"
                ]: provision_receipt["control_bootstrap_public"]["public_key"],
                "control-runtime-1": control_runtime_public.hex(),
            }
        )
    )
    outcome_public_file.write_text(
        __import__("json").dumps(
            {
                provision_receipt["outcome_bootstrap_public"][
                    "key_id"
                ]: provision_receipt["outcome_bootstrap_public"]["public_key"],
                "router-outcome-runtime-1": outcome_runtime_public.hex(),
            }
        )
    )
    control_public_file.chmod(0o400)
    outcome_public_file.chmod(0o400)
    control_runtime_seed = tmp_path / "control-runtime.seed"
    control_runtime_seed.write_bytes(control_runtime_private.private_bytes_raw())
    control_runtime_seed.chmod(0o400)
    os.chown(control_runtime_seed, operator.pw_uid, operator.pw_gid)
    append_terminal = """
from pathlib import Path
from trustforge.signed_event_ledger import SignedEventLedger
seed = Path(__import__("sys").argv[1]).read_bytes()
root = Path(__import__("sys").argv[2])
lock = Path(__import__("sys").argv[3])
runtime = bytes.fromhex(__import__("sys").argv[4])
bootstrap_id = __import__("sys").argv[5]
bootstrap = bytes.fromhex(__import__("sys").argv[6])
kinds = frozenset({"deployment_initialized", "operator_stop", "activation_prepared", "activation_completed", "activation_failed"})
SignedEventLedger(
    directory=root / "control",
    verification_keys={bootstrap_id: bootstrap, "control-runtime-1": runtime},
    event_permissions={"release-control": kinds},
    domain_keys={"release-control": frozenset({bootstrap_id, "control-runtime-1"})},
    signing_key_id="control-runtime-1",
    signing_private_key=seed,
    signing_domain="release-control",
    ledger_role="release-control",
    coordination_root=root,
    coordination_lock_path=lock,
    coordination_lock_mode=0o660,
    coordination_lock_owner_uid=0,
    coordination_lock_group="trustforge-release",
    root_owner_uid=0,
    root_group="trustforge-release",
    root_mode=0o750,
    directory_owner_uid=__import__("os").geteuid(),
    directory_group="trustforge-release",
    directory_mode=0o750,
    file_mode=0o640,
).append({"kind": "operator_stop", "at": "2026-07-28T00:00:00+00:00", "checkpoint_floor_at": "2026-07-28T00:00:00+00:00"})
"""
    control_bootstrap = provision_receipt["control_bootstrap_public"]
    subprocess.run(
        [
            "setpriv",
            f"--reuid={operator.pw_uid}",
            f"--regid={operator.pw_gid}",
            "--init-groups",
            sys.executable,
            "-c",
            append_terminal,
            str(control_runtime_seed),
            str(root),
            str(coordination_lock),
            control_runtime_public.hex(),
            control_bootstrap["key_id"],
            control_bootstrap["public_key"],
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/migrate_release_ledgers.py"),
            "--source-root",
            str(root),
            "--target-root",
            str(root),
            "--control-public",
            str(control_public_file),
            "--outcome-public",
            str(outcome_public_file),
            "--coordination-lock",
            str(coordination_lock),
        ],
        check=True,
        timeout=10,
    )
    assert b'"kind":"operator_stop"' in (control / "events.jsonl").read_bytes()
    read_terminal = """
from pathlib import Path
from trustforge.signed_event_ledger import SignedEventLedger
root = Path(__import__("sys").argv[2])
lock = Path(__import__("sys").argv[3])
runtime = bytes.fromhex(__import__("sys").argv[4])
bootstrap_id = __import__("sys").argv[5]
bootstrap = bytes.fromhex(__import__("sys").argv[6])
kinds = frozenset({"deployment_initialized", "operator_stop", "activation_prepared", "activation_completed", "activation_failed"})
records = SignedEventLedger(
    directory=root / "control",
    verification_keys={bootstrap_id: bootstrap, "control-runtime-1": runtime},
    event_permissions={"release-control": kinds},
    domain_keys={"release-control": frozenset({bootstrap_id, "control-runtime-1"})},
    ledger_role="release-control",
    coordination_root=root,
    coordination_lock_path=lock,
    coordination_lock_mode=0o660,
    coordination_lock_owner_uid=0,
    coordination_lock_group="trustforge-release",
    root_owner_uid=0,
    root_group="trustforge-release",
    root_mode=0o750,
    directory_owner_uid=OPERATOR_UID,
    directory_group="trustforge-release",
    directory_mode=0o750,
    file_mode=0o640,
).read()
assert records[-1]["event"]["kind"] == "operator_stop"
"""
    subprocess.run(
        [
            "setpriv",
            f"--reuid={router.pw_uid}",
            f"--regid={router.pw_gid}",
            "--init-groups",
            sys.executable,
            "-c",
            read_terminal.replace("OPERATOR_UID", str(operator.pw_uid)),
            str(control_runtime_seed),
            str(root),
            str(coordination_lock),
            control_runtime_public.hex(),
            control_bootstrap["key_id"],
            control_bootstrap["public_key"],
        ],
        check=True,
    )
    assert (control / "bootstrap.json").stat().st_mode & 0o777 == 0o640
    assert (outcomes / "bootstrap.json").stat().st_mode & 0o777 == 0o640
    outcome_event = outcomes / "events.jsonl"
    outcome_event.touch(mode=0o640)
    os.chown(outcome_event, router.pw_uid, release_gid)
    subprocess.run(
        [
            "systemd-sysusers",
            "--dry-run",
            str(ROOT / "deploy/trustforge-release-router.sysusers.conf"),
        ],
        check=True,
    )
    subprocess.run(
        [
            "systemd-tmpfiles",
            "--dry-run",
            "--create",
            str(ROOT / "deploy/trustforge-release-router.tmpfiles.conf"),
        ],
        check=True,
    )
    for identity, own, projection in (
        (router, outcomes, control),
        (operator, control, outcomes),
    ):
        subprocess.run(
            [
                "setpriv",
                f"--reuid={identity.pw_uid}",
                f"--regid={identity.pw_gid}",
                "--init-groups",
                "sh",
                "-c",
                'test -r "$1/events.jsonl" && test ! -w "$1" && test -w "$2"',
                "permission-check",
                str(projection),
                str(own),
            ],
            check=True,
        )
