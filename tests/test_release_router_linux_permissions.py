from __future__ import annotations

import os
import platform
import pwd
import shutil
import subprocess
from pathlib import Path

import pytest


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
    root = tmp_path / "security-ledger"
    control = root / "control"
    outcomes = root / "router-outcomes"
    root.mkdir(mode=0o750)
    control.mkdir(mode=0o750)
    outcomes.mkdir(mode=0o750)
    os.chown(root, 0, release_gid)
    os.chown(control, operator.pw_uid, release_gid)
    os.chown(outcomes, router.pw_uid, release_gid)
    for directory, owner in ((control, operator.pw_uid), (outcomes, router.pw_uid)):
        event = directory / "events.jsonl"
        event.touch(mode=0o640)
        os.chown(event, owner, release_gid)

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
