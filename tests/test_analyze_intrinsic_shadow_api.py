from __future__ import annotations

import pytest

from trustforge import web
from trustforge.comparison_contract import ComparisonRunResult
from trustforge.schema import Report


class _Log:
    events: list = []

    def manifest(self) -> dict:
        return {"agent": "test"}


def report(coin: str, generated_at: str = "2026-07-28T00:00:00Z") -> Report:
    return Report(
        coin=coin,
        question_type="multi_source",
        question="shadow test",
        market_judgment="中性，不因 shadow 改變",
        facts=[],
        inferences=[],
        key_basis=[],
        confidence=0.61,
        limits=[],
        could_flip=[],
        contrarian=[],
        generated_at=generated_at,
        calibrated_confidence=0.73,
        decision_state="normal",
    )


def test_real_btc_and_bnb_api_shadow_is_zero_without_changing_official_fields() -> None:
    for coin in ("BTC", "BNB"):
        original = report(coin)
        payload = web._build_analyze_json_payload(original, [], _Log())
        public = payload["report"]
        shadow = public["asset_intrinsic_assessment"]

        assert shadow is not None
        assert shadow["total_delta"] == 0.0
        assert shadow["gate"]["passed"] is False
        assert public["confidence"] == original.confidence
        assert public["calibrated_confidence"] == original.calibrated_confidence
        assert public["decision_state"] == original.decision_state
        assert public["market_judgment"] == original.market_judgment


def test_comparison_attaches_independent_shadow_assessments() -> None:
    payload = web._build_comparison_json_payload(
        ComparisonRunResult(report_a=report("BTC"), evidence_a=[], report_b=report("BNB"), evidence_b=[], comparison=None, log=_Log())
    )

    assert payload["report_a"]["asset_intrinsic_assessment"]["asset_id"] == "asset:btc"
    assert payload["report_b"]["asset_intrinsic_assessment"]["asset_id"] == "asset:bnb"
    assert payload["report_a"]["asset_intrinsic_assessment"] is not payload["report_b"][
        "asset_intrinsic_assessment"
    ]


def test_invalid_or_pre_fact_generated_at_is_fail_soft_without_pit_leakage() -> None:
    for generated_at in ("not-a-time", "2026-07-26T23:59:59Z", "2026-07-28T00:00:00"):
        public = web._public_report_dict(report("BTC", generated_at))
        assert public["asset_intrinsic_assessment"] is None


def test_unknown_asset_without_intrinsic_profile_is_legacy_compatible() -> None:
    public = web._public_report_dict(report("DOGE"))
    assert public["asset_intrinsic_assessment"] is None


def test_intrinsic_repository_failure_is_fail_soft(monkeypatch) -> None:
    class BrokenRepository:
        def pit_view(self, asset_id, as_of):
            raise ValueError("malformed intrinsic input")

    monkeypatch.setattr(web, "_ASSET_INTRINSIC_REPOSITORY", BrokenRepository())
    public = web._public_report_dict(report("BTC"))
    assert public["asset_intrinsic_assessment"] is None


def test_runtime_paths_exist_and_repository_loads() -> None:
    assert web._ASSET_INTRINSIC_RECORDS_PATH.parts[-2:] == (
        "data",
        "asset_intrinsic_records.json",
    )
    assert web._ASSET_INTRINSIC_RECORDS_PATH.exists()
    original = web._ASSET_INTRINSIC_REPOSITORY
    try:
        web._ASSET_INTRINSIC_REPOSITORY = None
        assert web._asset_intrinsic_repository() is not None
    finally:
        web._ASSET_INTRINSIC_REPOSITORY = original


def test_forged_prefilled_assessment_is_ignored_and_recomputed() -> None:
    forged = report("BTC")
    forged.asset_intrinsic_assessment = {
        "mode": "shadow",
        "total_delta": 0.08,
        "gate": {"passed": True},
    }

    public = web._public_report_dict(forged)

    assert public["asset_intrinsic_assessment"] != forged.asset_intrinsic_assessment
    assert public["asset_intrinsic_assessment"]["total_delta"] == 0.0
    assert public["asset_intrinsic_assessment"]["gate"]["passed"] is False


@pytest.mark.parametrize("malformed_context", [[], "asset:btc"])
def test_malformed_prefilled_context_fails_soft_to_null(malformed_context) -> None:
    malformed = report("BTC")
    malformed.asset_context = malformed_context
    malformed.asset_intrinsic_assessment = {"total_delta": 0.08}

    public = web._public_report_dict(malformed)

    assert public["asset_context"] is None
    assert public["risk_notices"] == []
    assert public["asset_intrinsic_assessment"] is None


def test_existing_risk_notices_do_not_block_trusted_context_or_assessment() -> None:
    existing = report("BTC")
    existing.risk_notices = [
        {"code": "existing", "severity": "info", "message": "existing notice"}
    ]

    public = web._public_report_dict(existing)

    assert public["asset_context"]["asset_id"] == "asset:btc"
    assert public["risk_notices"] == existing.risk_notices
    assert public["asset_intrinsic_assessment"]["asset_id"] == "asset:btc"
    assert public["asset_intrinsic_assessment"]["total_delta"] == 0.0
