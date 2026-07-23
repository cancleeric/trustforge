from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "trustforge_control.sh"


def _run_control(script: Path, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    process_env = os.environ.copy()
    process_env["TRUSTFORGE_PYTHON"] = sys.executable
    if env:
        process_env.update(env)
    return subprocess.run(
        ["bash", str(script), "not-a-command"],
        cwd="/",
        env=process_env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_control_script_has_valid_bash_syntax_and_no_personal_path() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "/Users/apple" not in source
    assert subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    ).returncode == 0


def test_control_resolves_root_relative_to_relocated_script(tmp_path: Path) -> None:
    repo = tmp_path / "relocated trustforge"
    script = repo / "scripts" / SCRIPT.name
    script.parent.mkdir(parents=True)
    shutil.copy2(SCRIPT, script)

    result = _run_control(script)

    assert result.returncode == 2
    assert "usage:" in result.stderr
    assert (repo / "out").is_dir()


def test_control_honors_canonical_home_override(tmp_path: Path) -> None:
    repo = tmp_path / "script checkout"
    script = repo / "scripts" / SCRIPT.name
    script.parent.mkdir(parents=True)
    shutil.copy2(SCRIPT, script)
    target = tmp_path / "runtime checkout"
    target.mkdir()

    result = _run_control(script, env={"TRUSTFORGE_HOME": str(target)})

    assert result.returncode == 2
    assert "usage:" in result.stderr
    assert (target / "out").is_dir()
    assert not (repo / "out").exists()
