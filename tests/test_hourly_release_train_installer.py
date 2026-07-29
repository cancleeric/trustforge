from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "scripts" / "install_hourly_release_train.sh"


def test_installer_persists_explicit_nonsecret_model(tmp_path: Path):
    model = "au.anthropic.claude-haiku-4-5-20251001-v1:0"
    env = dict(
        os.environ,
        HOME=str(tmp_path),
        TRUSTFORGE_HOME=str(ROOT),
        BEDROCK_MODEL_ID=model,
    )
    subprocess.run(
        [str(INSTALLER), "--no-enable", "--execute"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    plist = tmp_path / "Library/LaunchAgents/com.hurricanesoft.trustforge-hourly-release-train.plist"
    payload = plistlib.loads(plist.read_bytes())
    assert payload["EnvironmentVariables"]["BEDROCK_MODEL_ID"] == model


def test_installer_rejects_unsafe_model_before_writing_plist(tmp_path: Path):
    env = dict(
        os.environ,
        HOME=str(tmp_path),
        TRUSTFORGE_HOME=str(ROOT),
        BEDROCK_MODEL_ID="unsafe model;echo",
    )
    result = subprocess.run(
        [str(INSTALLER), "--no-enable"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert not (tmp_path / "Library/LaunchAgents").exists()
