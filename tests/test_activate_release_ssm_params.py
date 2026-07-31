"""Regression tests for ``deploy/lib/ssm_commands.sh`` ``build_ssm_commands_json``.

Locks down the AWS SSM ``commands`` JSON array construction that previously
broke due to manual shell-quote / JSON-escape nesting in
``deploy/activate_release.sh:217`` (AWS CLI ``ParamValidation: Expected ',',
received 'e'``). The post-verify step reported failure even though the
deployment itself had succeeded, and the subsequent rollback retry hit lock
contention (exit 98). The helper now uses jq so each command line becomes a
correctly-escaped JSON string.
"""
from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "deploy" / "lib" / "ssm_commands.sh"


def _build_commands(commands: list[str]) -> str:
    """Source the lib and run ``build_ssm_commands_json`` over ``commands``.

    Commands are fed via stdin so no shell quoting is needed at the call site;
    this mirrors how ``activate_release.sh`` pipes ``printf`` output into the
    helper.
    """
    stdin_data = "\n".join(commands) + ("\n" if commands else "")
    script = (
        "set -euo pipefail; "
        f"source {shlex.quote(str(LIB))}; "
        "build_ssm_commands_json"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        input=stdin_data,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"build_ssm_commands_json failed (exit {result.returncode}):\n"
        f"{result.stderr}"
    )
    return result.stdout.strip()


def test_emits_valid_json_array():
    out = _build_commands(["set -e", "echo hello"])
    data = json.loads(out)  # must not raise
    assert data == ["set -e", "echo hello"]


def test_preserves_single_quotes_in_command():
    """The original bug: a literal single quote inside a command (needed by
    ``grep -F '...'`` on the remote shell) was swallowed by manual shell
    escaping, breaking the JSON. jq must keep it verbatim."""
    cmd = (
        "grep -F 'TRUSTFORGE_SKILL_CHANGE_LOG="
        "/var/lib/trustforge/skill_changes.jsonl'"
    )
    out = _build_commands(["set -e", cmd])
    data = json.loads(out)
    assert data[1] == cmd
    assert "'" in data[1]


def test_original_failing_command_set_is_valid_json():
    """Exact regression: the ``activate_release.sh:217`` command set that used
    to produce ``ParamValidation: Expected ',', received 'e'``."""
    skill_log_path = "/var/lib/trustforge/skill_changes.jsonl"
    commands = [
        "set -e",
        "systemctl show trustforge-analysis-flow.service -p Environment "
        "--value | grep -F 'TRUSTFORGE_SKILL_CHANGE_LOG="
        + skill_log_path
        + "'",
        'echo "[activate] analysis-flow worker skill-log environment verified"',
    ]
    out = _build_commands(commands)
    data = json.loads(out)
    assert len(data) == 3
    assert data[0] == "set -e"
    assert "skill_changes.jsonl" in data[1]
    assert data[1].startswith("systemctl show")
    assert data[2] == (
        'echo "[activate] analysis-flow worker skill-log environment verified"'
    )


def test_escapes_embedded_double_quotes():
    """A command containing double quotes (e.g. ``echo "[activate] ..."``)
    must be JSON-escaped, not break the array."""
    cmd = 'echo "[activate] service restarted"'
    out = _build_commands(["set -e", cmd])
    data = json.loads(out)
    assert data[1] == cmd
