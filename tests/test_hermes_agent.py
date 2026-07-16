from trustforge.hermes import autonomous_cycle_plan, manifest
from trustforge.schema import COIN_POOL


def test_manifest_declares_bounded_tools_skills_and_formal_time_boundary():
    data = manifest()
    assert data["agent"] == "hermes"
    assert data["autonomy"]["coin_pool"] == list(COIN_POOL)
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
