"""碳盤查模組單元測試 — src/trustforge/carbon.py。

⛔ 全程不打真 AWS/Bedrock：純計算模組，無網路呼叫。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trustforge.carbon import (
    CarbonEstimate,
    CarbonSummary,
    _classify_model,
    _resolve_region,
    aggregate_emissions,
    carbon_from_llm_events,
    estimate_emission,
    reset_cache,
)


@pytest.fixture(autouse=True)
def _reset_carbon_cache(monkeypatch):
    """每個測試前清除 carbon module 快取，確保隔離。"""
    reset_cache()
    # 清除可能干擾的環境變數
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    monkeypatch.delenv("TRUSTFORGE_CARBON_DATA_PATH", raising=False)
    yield
    reset_cache()


# ─── Model classification ────────────────────────────────────────────────────

class TestClassifyModel:
    def test_haiku_model(self):
        assert _classify_model("au.anthropic.claude-haiku-4-5-20251001-v1:0") == "haiku"

    def test_sonnet_model(self):
        assert _classify_model("us.anthropic.claude-sonnet-4-6") == "sonnet"

    def test_opus_model(self):
        assert _classify_model("anthropic.claude-opus-3") == "opus"

    def test_unknown_model(self):
        assert _classify_model("some-custom-model-v1") == "default"

    def test_none_model(self):
        assert _classify_model(None) == "default"

    def test_empty_string(self):
        assert _classify_model("") == "default"

    def test_case_insensitive(self):
        assert _classify_model("Anthropic.Claude-HAIKU-v5") == "haiku"


# ─── Region resolution ───────────────────────────────────────────────────────

class TestResolveRegion:
    def test_explicit_region(self):
        assert _resolve_region("eu-west-1") == "eu-west-1"

    def test_env_aws_region(self, monkeypatch):
        monkeypatch.setenv("AWS_REGION", "us-west-2")
        assert _resolve_region() == "us-west-2"

    def test_env_aws_default_region(self, monkeypatch):
        monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
        assert _resolve_region() == "eu-central-1"

    def test_aws_region_takes_priority(self, monkeypatch):
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
        assert _resolve_region() == "us-east-1"

    def test_fallback_to_config_default(self):
        # No env set, should use config default (ap-southeast-2)
        region = _resolve_region()
        assert region == "ap-southeast-2"


# ─── Core estimation ─────────────────────────────────────────────────────────

class TestEstimateEmission:
    def test_basic_haiku_estimation(self):
        result = estimate_emission(
            "au.anthropic.claude-haiku-4-5-20251001-v1:0",
            tokens_in=1000,
            tokens_out=500,
            region="ap-southeast-2",
        )
        assert isinstance(result, CarbonEstimate)
        assert result.model_class == "haiku"
        assert result.region == "ap-southeast-2"
        assert result.tokens_in == 1000
        assert result.tokens_out == 500
        assert result.total_tokens == 1500
        assert result.estimated_kwh > 0
        assert result.estimated_co2e_g > 0
        assert result.is_estimate is True
        assert result.methodology == "token-energy-grid-v1"

    def test_zero_tokens(self):
        result = estimate_emission("haiku-model", tokens_in=0, tokens_out=0, region="us-west-2")
        assert result.total_tokens == 0
        assert result.estimated_kwh == 0.0
        assert result.estimated_co2e_g == 0.0

    def test_none_model(self):
        result = estimate_emission(None, tokens_in=100, tokens_out=50, region="us-east-1")
        assert result.model_id == "unknown"
        assert result.model_class == "default"
        assert result.estimated_kwh > 0

    def test_unknown_region_uses_default(self):
        result = estimate_emission("haiku-test", tokens_in=100, tokens_out=50, region="mars-north-1")
        # Should use default carbon intensity (450.0)
        assert result.carbon_intensity_gco2e_kwh == 450.0

    def test_known_region_uses_specific_intensity(self):
        result = estimate_emission("haiku-test", tokens_in=100, tokens_out=50, region="us-west-2")
        assert result.carbon_intensity_gco2e_kwh == 102.0

    def test_higher_carbon_region_gives_higher_co2e(self):
        low_carbon = estimate_emission("haiku-x", tokens_in=1000, tokens_out=500, region="us-west-2")
        high_carbon = estimate_emission("haiku-x", tokens_in=1000, tokens_out=500, region="ap-south-1")
        assert high_carbon.estimated_co2e_g > low_carbon.estimated_co2e_g

    def test_larger_model_gives_higher_energy(self):
        haiku = estimate_emission("haiku-x", tokens_in=1000, tokens_out=500, region="us-east-1")
        sonnet = estimate_emission("sonnet-x", tokens_in=1000, tokens_out=500, region="us-east-1")
        opus = estimate_emission("opus-x", tokens_in=1000, tokens_out=500, region="us-east-1")
        assert haiku.estimated_kwh < sonnet.estimated_kwh < opus.estimated_kwh

    def test_formula_correctness(self):
        """Verify: energy_kwh = (total_tokens/1000) * kwh_per_1k * PUE
                   co2e_g = energy_kwh * carbon_intensity"""
        result = estimate_emission("haiku-test", tokens_in=2000, tokens_out=1000, region="ap-southeast-2")
        # haiku: 0.00015 kwh/1k tokens, ap-southeast-2: 550 gCO2e/kWh, PUE: 1.135
        expected_kwh = (3000 / 1000.0) * 0.00015 * 1.135
        expected_co2e = expected_kwh * 550.0
        assert abs(result.estimated_kwh - round(expected_kwh, 10)) < 1e-10
        assert abs(result.estimated_co2e_g - round(expected_co2e, 6)) < 1e-6


# ─── Aggregation ─────────────────────────────────────────────────────────────

class TestAggregateEmissions:
    def test_empty_list(self):
        summary = aggregate_emissions([])
        assert summary.total_tokens == 0
        assert summary.total_estimated_kwh == 0.0
        assert summary.total_estimated_co2e_g == 0.0
        assert summary.call_count == 0
        assert summary.breakdown_by_model == {}

    def test_single_estimate(self):
        est = estimate_emission("haiku-x", 1000, 500, region="us-east-1")
        summary = aggregate_emissions([est])
        assert summary.total_tokens == 1500
        assert summary.call_count == 1
        assert "haiku" in summary.breakdown_by_model

    def test_multiple_models(self):
        estimates = [
            estimate_emission("haiku-x", 100, 50, region="us-east-1"),
            estimate_emission("sonnet-x", 200, 100, region="us-east-1"),
            estimate_emission("haiku-y", 300, 150, region="us-east-1"),
        ]
        summary = aggregate_emissions(estimates)
        assert summary.call_count == 3
        assert summary.total_tokens == 100 + 50 + 200 + 100 + 300 + 150
        assert "haiku" in summary.breakdown_by_model
        assert "sonnet" in summary.breakdown_by_model
        assert summary.breakdown_by_model["haiku"]["calls"] == 2
        assert summary.breakdown_by_model["sonnet"]["calls"] == 1

    def test_co2e_kg_property(self):
        est = estimate_emission("haiku-x", 1000000, 500000, region="ap-south-1")
        summary = aggregate_emissions([est])
        assert abs(summary.total_estimated_co2e_kg - summary.total_estimated_co2e_g / 1000.0) < 1e-10


# ─── Ledger integration ──────────────────────────────────────────────────────

class TestCarbonFromLlmEvents:
    def test_filters_llm_cost_events(self):
        events = [
            {"tool": "session.start", "params": {}},
            {"tool": "llm.cost", "params": {"model": "haiku-x", "tokens_in": 100, "tokens_out": 50}},
            {"tool": "ingestion.source", "params": {}},
            {"tool": "llm.cost", "params": {"model": "sonnet-x", "tokens_in": 200, "tokens_out": 100}},
        ]
        summary = carbon_from_llm_events(events, region="us-east-1")
        assert summary.call_count == 2
        assert summary.total_tokens == 100 + 50 + 200 + 100

    def test_empty_events(self):
        summary = carbon_from_llm_events([], region="us-east-1")
        assert summary.call_count == 0
        assert summary.total_tokens == 0

    def test_no_llm_events(self):
        events = [
            {"tool": "session.start", "params": {}},
            {"tool": "ingestion.source", "params": {"source": "news"}},
        ]
        summary = carbon_from_llm_events(events, region="us-east-1")
        assert summary.call_count == 0

    def test_missing_params_handled(self):
        events = [
            {"tool": "llm.cost", "params": {}},
            {"tool": "llm.cost"},
        ]
        summary = carbon_from_llm_events(events, region="us-east-1")
        assert summary.call_count == 2
        assert summary.total_tokens == 0


# ─── Custom data path ────────────────────────────────────────────────────────

class TestCustomDataPath:
    def test_custom_data_via_env(self, tmp_path, monkeypatch):
        custom_data = {
            "regions": {"test-region-1": {"carbon_intensity_gco2e_kwh": 100.0, "pue": 1.0}},
            "default_region": "test-region-1",
            "default_carbon_intensity_gco2e_kwh": 100.0,
            "default_pue": 1.0,
            "model_energy_profiles": {
                "haiku": {"kwh_per_1k_tokens": 0.001},
                "default": {"kwh_per_1k_tokens": 0.001},
            },
        }
        data_file = tmp_path / "custom_carbon.json"
        data_file.write_text(json.dumps(custom_data), encoding="utf-8")
        monkeypatch.setenv("TRUSTFORGE_CARBON_DATA_PATH", str(data_file))
        reset_cache()

        result = estimate_emission("haiku-x", 1000, 0, region="test-region-1")
        # 1000 tokens / 1000 * 0.001 kwh/1k * PUE=1.0 = 0.001 kWh
        # 0.001 * 100 = 0.1 g CO2e
        assert result.estimated_kwh == 0.001
        assert result.estimated_co2e_g == 0.1

    def test_missing_data_file_uses_fallback(self, monkeypatch):
        monkeypatch.setenv("TRUSTFORGE_CARBON_DATA_PATH", "/nonexistent/path.json")
        reset_cache()

        result = estimate_emission("haiku-x", 1000, 0)
        # Should still work with fallback defaults
        assert result.estimated_kwh > 0
        assert result.carbon_intensity_gco2e_kwh == 450.0
