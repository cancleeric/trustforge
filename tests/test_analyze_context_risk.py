from __future__ import annotations

from trustforge import web
from trustforge.schema import Report


class _Log:
    events: list = []

    def manifest(self) -> dict:
        return {"agent": "test"}


def _report(coin: str, *, generated_at: str = "2026-07-23T00:00:00Z") -> Report:
    return Report(
        coin=coin,
        question_type="multi_source",
        question="context risk test",
        market_judgment="中性",
        facts=[],
        inferences=[],
        key_basis=[],
        confidence=0.5,
        limits=[],
        could_flip=[],
        contrarian=[],
        generated_at=generated_at,
        calibrated_confidence=0.5,
        decision_state="normal",
    )


def test_single_asset_analyze_payload_adds_public_context_and_risk_notices() -> None:
    payload = web._build_analyze_json_payload(_report("ARB"), [], _Log())

    report = payload["report"]
    assert report["asset_context"]["asset_id"] == "asset:arb"
    assert report["asset_context"]["symbol"] == "ARB"
    assert {notice["code"] for notice in report["risk_notices"]} >= {
        "layer_2_dependency",
        "governance_token",
    }
    assert "source" not in report["asset_context"]
    assert "fetched_at" not in report["asset_context"]
    assert "valid_from" not in report["asset_context"]


def test_asset_context_seed_data_uses_runtime_copied_data_path() -> None:
    assert web._ASSET_CONTEXT_RECORDS_PATH.parts[-2:] == ("data", "asset_context_records.json")
    assert web._ASSET_CONTEXT_RECORDS_PATH.exists()

    original_repository = web._ASSET_CONTEXT_REPOSITORY
    try:
        web._ASSET_CONTEXT_REPOSITORY = None
        assert web._asset_context_repository() is not None
    finally:
        web._ASSET_CONTEXT_REPOSITORY = original_repository


def test_comparison_payload_adds_context_per_report_without_internal_fields() -> None:
    payload = web._build_comparison_json_payload(_report("ARB"), [], _report("BTC"), [], _Log())

    report_a = payload["report_a"]
    report_b = payload["report_b"]
    assert report_a["asset_context"]["asset_id"] == "asset:arb"
    assert report_a["risk_notices"]
    assert report_b["asset_context"] is None
    assert report_b["risk_notices"] == []


def test_old_report_snapshots_missing_context_fields_stay_readable() -> None:
    report = _report("BTC")
    public = web._public_report_dict(report)

    assert public["asset_context"] is None
    assert public["risk_notices"] == []


def test_malformed_generated_at_disables_context_without_crashing() -> None:
    payload = web._build_analyze_json_payload(
        _report("ARB", generated_at="not-a-timestamp"), [], _Log()
    )
    report = payload["report"]

    assert report["asset_context"] is None
    assert report["risk_notices"] == []
