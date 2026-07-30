"""Regression: preflight_activation.sh must not crash on unbound pointer digests.

When pointers/candidate.json (or active.json) is empty/missing, the pointer
digest is never assigned in its JSON-parsing branch. Under ``set -u`` the later
``if [ -n "$DIGEST" ]`` guards referenced an unbound variable and the script
halted with ``未綁定的變數`` instead of reporting the missing pointer
gracefully. The script now presets these digests to empty (mirroring
deploy/activate_release.sh).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "deploy" / "preflight_activation.sh"


@pytest.fixture()
def fake_aws(tmp_path):
    """Minimal `aws` mock: account id for sts, empty body for s3 cp, fail the rest."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    aws = bin_dir / "aws"
    aws.write_text(
        "#!/usr/bin/env bash\n"
        'case "$1 $2" in\n'
        '  "sts get-caller-identity") echo "123456789012" ;;\n'
        '  "s3 cp") echo "" ;;\n'
        '  *) exit 1 ;;\n'
        "esac\n"
    )
    aws.chmod(0o755)
    return str(bin_dir)


def test_preflight_handles_missing_pointer_without_unbound_crash(fake_aws):
    env = {**os.environ, "PATH": f"{fake_aws}:{os.environ.get('PATH', '')}"}
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--target", "i-00000000000000000", "--skip-lock"],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO,
    )
    combined = proc.stdout + proc.stderr

    # Must NOT crash on an unbound variable under `set -u`.
    assert "未綁定的變數" not in combined
    assert "unbound variable" not in combined
    # Must gracefully report the missing candidate pointer.
    assert "[FAIL] [2] pointers/candidate.json missing or empty" in combined
    # Reaching the instance/SSM checks proves the script did not halt at the
    # [4] candidate-digest guard (the previous unbound crash site).
    assert "[7]" in combined or "[8]" in combined
