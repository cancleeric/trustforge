from trustforge import admin_config
from trustforge.admin_config import AdminConfig
from trustforge.hermes import autonomy_enabled, autonomous_cycle_plan, manifest
from trustforge.schema import COIN_POOL


def test_manifest_declares_bounded_tools_skills_and_formal_time_boundary():
    data = manifest()
    assert data["agent"] == "hermes"
    assert data["autonomy"]["coin_pool"] == list(COIN_POOL)
    assert isinstance(data["autonomy"]["enabled"], bool)
    assert data["autonomy"]["no_unbounded_network_access"] is True
    assert "run_started_at" in data["autonomy"]["formal_run_rule"]
    assert {tool["name"] for tool in data["tools"]} >= {"refresh_sources", "build_snapshots", "diagnose_improvement", "extract_claims", "assemble_report"}
    assert {skill["name"] for skill in data["skills"]} >= {"five-year-ohlcv-lineage", "evidence-contract", "contrarian-evidence", "report-contract", "bounded-self-improvement"}


def test_autonomous_cycle_uses_fixed_pool_and_existing_scheduler_actions():
    plan = autonomous_cycle_plan(["BTC", "ETH"])
    assert plan["coins"] == ["BTC", "ETH"]
    assert [item["tool"] for item in plan["actions"]] == [
        "refresh_sources", "build_snapshots", "cache_freshness_dashboard",
        "measure_connector_reliability", "measure_quality",
        "replay_history", "replay_history", "diagnose_improvement", "review_upgrades",
    ]
    assert "--snapshot" in plan["actions"][1]["argv"]
    assert plan["actions"][4]["argv"][-1] == "out/question-bank-latest.json"


def test_autonomy_defaults_local_on_and_production_off(monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTFORGE_RUNTIME_STATE_PATH", str(tmp_path / "runtime.json"))
    monkeypatch.delenv("TRUSTFORGE_RUNTIME_SWITCH", raising=False)
    monkeypatch.delenv("TRUSTFORGE_ALLOW_PRODUCTION_CONTINUOUS", raising=False)
    monkeypatch.delenv("TRUSTFORGE_ENV", raising=False)
    monkeypatch.delenv("TRUSTFORGE_HERMES_AUTONOMY_ENABLED", raising=False)
    monkeypatch.setenv("CACHE_BACKEND", "json")
    assert autonomy_enabled() == (True, "local_default")

    monkeypatch.setenv("CACHE_BACKEND", "dynamodb")
    assert autonomy_enabled() == (False, "production_default")

    monkeypatch.setenv("TRUSTFORGE_HERMES_AUTONOMY_ENABLED", "1")
    assert autonomy_enabled() == (False, "production_default")

    monkeypatch.setenv("TRUSTFORGE_RUNTIME_SWITCH", "on")
    assert autonomy_enabled() == (False, "production_guard")

    monkeypatch.setenv("TRUSTFORGE_ALLOW_PRODUCTION_CONTINUOUS", "1")
    assert autonomy_enabled() == (True, "env")


def test_production_admin_autonomy_setting_overrides_only_unset_default(monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTFORGE_RUNTIME_STATE_PATH", str(tmp_path / "runtime.json"))
    monkeypatch.setenv("CACHE_BACKEND", "dynamodb")
    monkeypatch.delenv("TRUSTFORGE_RUNTIME_SWITCH", raising=False)
    monkeypatch.delenv("TRUSTFORGE_ALLOW_PRODUCTION_CONTINUOUS", raising=False)
    monkeypatch.delenv("TRUSTFORGE_HERMES_AUTONOMY_ENABLED", raising=False)
    monkeypatch.setattr(
        admin_config,
        "get_config_cached",
        lambda: AdminConfig(hermes_autonomy_enabled=True, exists=True, version=1),
    )

    assert autonomy_enabled() == (True, "config")

    # Explicit runtime protection must continue to override the admin setting.
    monkeypatch.setenv("TRUSTFORGE_RUNTIME_SWITCH", "off")
    assert autonomy_enabled() == (False, "env")
