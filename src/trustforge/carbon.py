"""碳盤查模組 — LLM token 碳排放估算。

將既有 `execlog.record_llm_cost()` 記錄的 model_id + tokens_in + tokens_out
轉換為 estimated kWh 與 CO₂e（克），供碳足跡報表與 ESG 儀表板使用。

換算公式：
    total_tokens = tokens_in + tokens_out
    energy_kwh   = (total_tokens / 1000) * kwh_per_1k_tokens(model_class) * PUE
    co2e_g       = energy_kwh * carbon_intensity_gco2e_kwh(region)

資料來源：
    - AWS Region 碳強度：data/carbon_intensity.json（可更新）
    - 每 token 能耗基準：Luccioni et al. 2023 外推值
    - PUE：AWS 公開永續報告 (~1.135)

⚠️ 所有輸出均為 ESTIMATES，非精確量測。方法論說明見 data/carbon_intensity.json。

約束：
    - 純 stdlib，零第三方依賴
    - 不修改 execlog.py / budget_guard.py 核心邏輯
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ─── Data loading ────────────────────────────────────────────────────────────

_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "carbon_intensity.json"

_cached_data: dict[str, Any] | None = None


def _load_carbon_data() -> dict[str, Any]:
    """Load carbon intensity data from JSON config (cached after first load)."""
    global _cached_data
    if _cached_data is not None:
        return _cached_data

    config_path = os.getenv("TRUSTFORGE_CARBON_DATA_PATH", "")
    path = Path(config_path) if config_path else _DATA_PATH

    if path.exists():
        with open(path, encoding="utf-8") as f:
            _cached_data = json.load(f)
    else:
        # Fallback defaults if data file missing
        _cached_data = {
            "regions": {},
            "default_carbon_intensity_gco2e_kwh": 450.0,
            "default_pue": 1.135,
            "default_region": "ap-southeast-2",
            "model_energy_profiles": {
                "default": {"kwh_per_1k_tokens": 0.00035},
            },
        }
    return _cached_data


def reset_cache() -> None:
    """Clear cached data (for testing)."""
    global _cached_data
    _cached_data = None


# ─── Model classification ────────────────────────────────────────────────────

def _classify_model(model_id: str | None) -> str:
    """Classify a Bedrock model_id into an energy profile class.

    Matches against known substrings in the model identifier.
    Returns one of: 'haiku', 'sonnet', 'opus', 'default'.
    """
    if not model_id:
        return "default"

    model_lower = model_id.lower()

    if "haiku" in model_lower:
        return "haiku"
    if "sonnet" in model_lower:
        return "sonnet"
    if "opus" in model_lower:
        return "opus"

    return "default"


# ─── Region resolution ───────────────────────────────────────────────────────

def _resolve_region(region: str | None = None) -> str:
    """Resolve the effective AWS region for carbon intensity lookup.

    Priority: explicit param > env AWS_REGION > env AWS_DEFAULT_REGION > config default.
    """
    if region:
        return region
    env_region = os.getenv("AWS_REGION", "").strip()
    if env_region:
        return env_region
    env_default = os.getenv("AWS_DEFAULT_REGION", "").strip()
    if env_default:
        return env_default
    data = _load_carbon_data()
    return data.get("default_region", "ap-southeast-2")


# ─── Core estimation ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CarbonEstimate:
    """Result of a single LLM call carbon footprint estimation."""

    model_id: str
    model_class: str
    region: str
    tokens_in: int
    tokens_out: int
    total_tokens: int
    estimated_kwh: float
    estimated_co2e_g: float
    carbon_intensity_gco2e_kwh: float
    pue: float
    methodology: str = "token-energy-grid-v1"
    is_estimate: bool = True


def estimate_emission(
    model_id: str | None,
    tokens_in: int,
    tokens_out: int,
    *,
    region: str | None = None,
) -> CarbonEstimate:
    """Estimate CO2e emissions for a single LLM call.

    Args:
        model_id: Bedrock model identifier (e.g. 'au.anthropic.claude-haiku-4-5-20251001-v1:0')
        tokens_in: Input token count
        tokens_out: Output token count
        region: AWS region override (default: from env or config)

    Returns:
        CarbonEstimate with estimated kWh and CO2e grams.
    """
    data = _load_carbon_data()
    effective_region = _resolve_region(region)
    model_class = _classify_model(model_id)

    # Get region-specific carbon intensity and PUE
    region_data = data.get("regions", {}).get(effective_region, {})
    carbon_intensity = region_data.get(
        "carbon_intensity_gco2e_kwh",
        data.get("default_carbon_intensity_gco2e_kwh", 450.0),
    )
    pue = region_data.get("pue", data.get("default_pue", 1.135))

    # Get model energy profile
    profiles = data.get("model_energy_profiles", {})
    profile = profiles.get(model_class, profiles.get("default", {}))
    kwh_per_1k = profile.get("kwh_per_1k_tokens", 0.00035)

    # Calculate
    total_tokens = int(tokens_in or 0) + int(tokens_out or 0)
    energy_kwh = (total_tokens / 1000.0) * kwh_per_1k * pue
    co2e_g = energy_kwh * carbon_intensity

    return CarbonEstimate(
        model_id=model_id or "unknown",
        model_class=model_class,
        region=effective_region,
        tokens_in=int(tokens_in or 0),
        tokens_out=int(tokens_out or 0),
        total_tokens=total_tokens,
        estimated_kwh=round(energy_kwh, 10),
        estimated_co2e_g=round(co2e_g, 6),
        carbon_intensity_gco2e_kwh=carbon_intensity,
        pue=pue,
    )


# ─── Aggregation utilities ───────────────────────────────────────────────────

@dataclass
class CarbonSummary:
    """Aggregated carbon footprint summary."""

    total_tokens: int = 0
    total_estimated_kwh: float = 0.0
    total_estimated_co2e_g: float = 0.0
    call_count: int = 0
    breakdown_by_model: dict[str, dict[str, float]] | None = None

    @property
    def total_estimated_co2e_kg(self) -> float:
        return self.total_estimated_co2e_g / 1000.0


def aggregate_emissions(estimates: list[CarbonEstimate]) -> CarbonSummary:
    """Aggregate multiple CarbonEstimate results into a summary.

    Args:
        estimates: List of individual call estimates.

    Returns:
        CarbonSummary with totals and per-model breakdown.
    """
    summary = CarbonSummary(breakdown_by_model={})

    for est in estimates:
        summary.total_tokens += est.total_tokens
        summary.total_estimated_kwh += est.estimated_kwh
        summary.total_estimated_co2e_g += est.estimated_co2e_g
        summary.call_count += 1

        model_key = est.model_class
        if model_key not in summary.breakdown_by_model:
            summary.breakdown_by_model[model_key] = {
                "tokens": 0,
                "kwh": 0.0,
                "co2e_g": 0.0,
                "calls": 0,
            }
        summary.breakdown_by_model[model_key]["tokens"] += est.total_tokens
        summary.breakdown_by_model[model_key]["kwh"] += est.estimated_kwh
        summary.breakdown_by_model[model_key]["co2e_g"] += est.estimated_co2e_g
        summary.breakdown_by_model[model_key]["calls"] += 1

    # Round totals
    summary.total_estimated_kwh = round(summary.total_estimated_kwh, 10)
    summary.total_estimated_co2e_g = round(summary.total_estimated_co2e_g, 6)

    return summary


# ─── Ledger integration helper ───────────────────────────────────────────────

def carbon_from_llm_events(events: list[dict], *, region: str | None = None) -> CarbonSummary:
    """Compute carbon summary from execution log `llm.cost` events.

    This is the primary integration point: pass the events list from an
    ExecutionLog and get back the carbon footprint for that run.

    Args:
        events: List of event dicts from ExecutionLog (filters for tool=='llm.cost').
        region: AWS region override.

    Returns:
        CarbonSummary for all LLM calls in the event list.
    """
    estimates = []
    for event in events:
        if event.get("tool") != "llm.cost":
            continue
        params = event.get("params", {})
        model = params.get("model")
        tokens_in = params.get("tokens_in", 0)
        tokens_out = params.get("tokens_out", 0)
        estimates.append(estimate_emission(model, tokens_in, tokens_out, region=region))

    return aggregate_emissions(estimates)
