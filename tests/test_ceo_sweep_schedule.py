from __future__ import annotations

import importlib.util
import json
import plistlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_all_ceo_sweep_schedules_are_hourly():
    tracked = plistlib.loads(
        (ROOT / "deploy/launchd/com.hurricanesoft.trustforge-ceo-sweep.plist").read_bytes()
    )
    installer = (ROOT / "scripts/install_ceo_half_hour_schedule.sh").read_text()

    assert tracked["StartInterval"] == 3600
    assert "<integer>3600</integer>" in installer
    assert "<integer>1800</integer>" not in installer


def test_ceo_sweep_is_truthful_about_recommendation_only(monkeypatch):
    spec = importlib.util.spec_from_file_location("ceo_sweep", ROOT / "scripts/ceo_sweep.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_json_cmd", lambda _args: [])

    report = module.build_report()

    assert report["cadence"] == "1 hour"
    assert report["mode"] == "ceo_sweep_recommendation"
    assert report["execution_status"] == "not_executed_by_sweep"
    assert "interactive_ceo_execution" in report["decision"]
    assert report["cpo_plan"]["proposed_author"] == "gray"
    assert report["ceo_review"]["required_decision"] == "pending_interactive_ceo_review"
    assert report["development_plan"]["operating_mode"] == "recommendation_only"
    serialized = json.dumps(report).lower()
    assert "approved_for" not in serialized
    assert '"author"' not in serialized
