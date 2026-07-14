"""Keep the scheduled Hermes runtime aligned with the packaged app layout."""
from __future__ import annotations

from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "install_hermes_scheduler.sh"


def test_hermes_scheduler_sets_packaged_home_and_starts_timer():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "Environment=TRUSTFORGE_HOME=$APP_DIR" in script
    assert "systemctl enable --now hermes-cycle.timer" in script
