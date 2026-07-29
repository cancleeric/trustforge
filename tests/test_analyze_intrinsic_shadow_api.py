from __future__ import annotations

import pytest
import json
from dataclasses import asdict
from email.message import Message
from io import BytesIO

from trustforge import web
from trustforge import analysis_flow
from trustforge.comparison_contract import ComparisonReport, ComparisonRunResult
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


def _assert_intrinsic_delta_is_derived(assessment: dict) -> None:
    derived = round(
        sum(float(dimension["signed_delta"]) for dimension in assessment["dimensions"]),
        8,
    )
    if assessment["gate"]["passed"]:
        cap = float(assessment["total_delta_cap"])
        expected = max(-cap, min(cap, derived))
        assert assessment["total_delta"] == pytest.approx(expected)
        assert abs(assessment["total_delta"]) <= cap
    else:
        assert assessment["total_delta"] == 0.0


def test_intrinsic_delta_helper_accepts_legitimate_cap_clamping() -> None:
    _assert_intrinsic_delta_is_derived(
        {
            "dimensions": [{"signed_delta": 0.3}, {"signed_delta": 0.2}],
            "gate": {"passed": True},
            "total_delta": 0.08,
            "total_delta_cap": 0.08,
        }
    )


def test_real_btc_and_bnb_api_shadow_is_derived_without_changing_official_fields() -> None:
    for coin in ("BTC", "BNB"):
        original = report(coin)
        payload = web._build_analyze_json_payload(original, [], _Log())
        public = payload["report"]
        shadow = public["asset_intrinsic_assessment"]

        assert shadow is not None
        _assert_intrinsic_delta_is_derived(shadow)
        assert shadow["gate"]["passed"] is (coin == "BTC")
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


def test_comparison_recomputes_and_rejects_prefilled_promotion_signals() -> None:
    report_a = report("BTC")
    report_b = report("BNB")
    report_a.asset_intrinsic_assessment = {
        "mode": "official",
        "affects_official_score": True,
        "promotion_receipt": {"decision": "pass"},
    }
    report_b.asset_intrinsic_assessment = {
        "mode": "shadow",
        "affects_official_score": False,
        "official_state": {"state": "official"},
    }
    report_a.asset_intrinsic_official_state = {"state": "official", "reason": "SECRET"}
    report_b.asset_intrinsic_official_state = {"state": "official", "reason": "SECRET"}

    payload = web._build_comparison_json_payload(
        ComparisonRunResult(
            report_a=report_a,
            evidence_a=[],
            report_b=report_b,
            evidence_b=[],
            comparison=None,
            log=_Log(),
        )
    )

    for key, expected in (("report_a", report_a), ("report_b", report_b)):
        public = payload[key]
        shadow = public["asset_intrinsic_assessment"]
        assert shadow["mode"] == "shadow"
        assert shadow["affects_official_score"] is False
        assert "promotion_receipt" not in shadow
        assert "official_state" not in shadow
        assert "asset_intrinsic_official_state" not in public
        assert public["confidence"] == expected.confidence
        assert public["calibrated_confidence"] == expected.calibrated_confidence
        assert public["decision_state"] == expected.decision_state
        assert public["market_judgment"] == expected.market_judgment


def test_nested_comparison_supporting_reports_are_sanitized() -> None:
    report_a = report("BTC")
    report_b = report("BNB")
    report_a.asset_intrinsic_assessment = {
        "mode": "official",
        "metadata": {
            "raw-receipt": "SECRET",
            "authorityAlias": {"signature": "SECRET"},
        },
    }
    report_a.asset_intrinsic_official_state = {
        "state": "official",
        "reason": "REPORT_LEVEL_SECRET",
    }
    report_a.asset_context = {
        "asset_id": "asset:forged",
        "promotionReceipt": {"rawReceipt": "CONTEXT_SECRET"},
    }
    comparison = ComparisonReport(
        coin_a="BTC",
        coin_b="BNB",
        query="nested sanitizer",
        conclusion="insufficient data",
        supporting_report_a=report_a,
        supporting_report_b=report_b,
    )
    payload = web._build_comparison_json_payload(
        ComparisonRunResult(
            report_a=report_a,
            evidence_a=[],
            report_b=report_b,
            evidence_b=[],
            comparison=comparison,
            log=_Log(),
        )
    )

    nested = payload["comparison_report"]["supporting_report_a"]
    assert "SECRET" not in str(payload)
    assert "asset_intrinsic_official_state" not in nested
    assert "CONTEXT_SECRET" not in str(payload)
    assert nested["asset_context"]["asset_id"] == "asset:btc"
    assert nested["asset_intrinsic_assessment"]["mode"] == "shadow"
    assert nested["confidence"] == report_a.confidence
    assert nested["direction"] == report_a.direction
    assert nested["decision_state"] == report_a.decision_state
    assert nested["market_judgment"] == report_a.market_judgment


def test_persisted_snapshot_and_http_route_sanitize_both_reports(monkeypatch) -> None:
    snapshots = {}
    for coin in ("BTC", "BNB"):
        stored_report = asdict(report(coin))
        stored_report["asset_intrinsic_assessment"] = {
            "mode": "official",
            "metadata": {"raw-receipt": "SECRET", "trustRoot": "SECRET"},
        }
        stored_report["asset_intrinsic_official_state"] = {
            "state": "official",
            "reason": "REPORT_LEVEL_SECRET",
        }
        stored_report["assetIntrinsicOfficialState"] = {
            "rawReceipt": "ALIAS_SECRET",
        }
        stored_report["risk_notices"] = [
            {
                "code": "forged",
                "severity": "warning",
                "message": "officialState.rawReceipt=NESTED_SECRET",
            }
        ]
        stored_report["asset_context"] = {
            "asset_id": "asset:forged",
            "promotionReceipt": {"rawReceipt": "CONTEXT_SECRET"},
        }
        snapshots[coin] = {
            "version": "test",
            "report": stored_report,
            "evidence": [],
            "trust_radar": {},
            "trust_components_aggregate": {},
            "price_provenance": {},
            "execution": {"run_id": coin, "nodes": []},
            "execution_log": [],
        }

    class FakeFlow:
        def __init__(self, readonly=False):
            assert readonly is True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def latest(self, coin, _question_type, _question):
            return snapshots[coin]

    monkeypatch.setattr(analysis_flow, "AnalysisFlow", FakeFlow)
    qs = {"coin": ["BTC"], "coin2": ["BNB"], "q": ["snapshot"]}
    code, body = web._handle_api_comparison_snapshot(qs)
    assert code == 200
    direct = json.loads(body)
    assert "SECRET" not in body
    assert "CONTEXT_SECRET" not in body
    assert "ALIAS_SECRET" not in body
    assert "NESTED_SECRET" not in body
    assert "asset_intrinsic_official_state" not in direct["data"]["report_a"]
    assert "asset_intrinsic_official_state" not in direct["data"]["report_b"]
    assert direct["data"]["report_a"]["asset_intrinsic_assessment"]["mode"] == "shadow"
    assert direct["data"]["report_b"]["asset_intrinsic_assessment"]["mode"] == "shadow"

    handler = web.Handler.__new__(web.Handler)
    handler.client_address = ("127.0.0.1", 12345)
    handler.path = "/api/comparison-snapshot?coin=BTC&coin2=BNB&q=snapshot"
    handler.wfile = BytesIO()
    handler.headers = Message()
    statuses = []
    handler.send_response = statuses.append
    handler.send_header = lambda *_args: None
    handler.end_headers = lambda: None
    handler.do_GET()
    routed_body = handler.wfile.getvalue().decode()
    assert statuses == [200]
    assert "SECRET" not in routed_body
    assert "ALIAS_SECRET" not in routed_body
    assert "NESTED_SECRET" not in routed_body
    routed = json.loads(routed_body)
    assert routed["data"]["report_a"]["asset_intrinsic_assessment"]["mode"] == "shadow"
    assert routed["data"]["report_b"]["asset_intrinsic_assessment"]["mode"] == "shadow"
    assert "asset_intrinsic_official_state" not in routed["data"]["report_a"]
    assert "asset_intrinsic_official_state" not in routed["data"]["report_b"]


def test_real_api_analyze_comparison_uses_nested_public_sanitizer(monkeypatch) -> None:
    report_a = report("BTC")
    report_b = report("BNB")
    for supporting in (report_a, report_b):
        supporting.asset_intrinsic_assessment = {
            "mode": "official",
            "metadata": {"raw-receipt": "ASSESSMENT_SECRET"},
        }
        supporting.asset_intrinsic_official_state = {
            "state": "official",
            "reason": "REPORT_LEVEL_SECRET",
        }
        supporting.asset_context = {
            "asset_id": "asset:forged",
            "promotionReceipt": {"rawReceipt": "CONTEXT_SECRET"},
        }
        supporting.risk_notices = [
            {
                "code": "forged",
                "severity": "warning",
                "message": "officialState.rawReceipt=NESTED_SECRET",
            }
        ]
    comparison = ComparisonReport(
        coin_a="BTC",
        coin_b="BNB",
        query="api sanitizer",
        conclusion="insufficient data",
        supporting_report_a=report_a,
        supporting_report_b=report_b,
    )
    result = ComparisonRunResult(
        report_a=report_a,
        evidence_a=[],
        report_b=report_b,
        evidence_b=[],
        comparison=comparison,
        log=_Log(),
    )
    monkeypatch.setattr(web, "_analyze_enforce_caller_rate_limit", lambda *_a, **_k: None)
    monkeypatch.setattr(
        web,
        "_analyze_online_stance_force_offline_for_caller",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(web, "_dedup_analyze_call", lambda *_a, **_k: result)

    code, body = web._handle_api_analyze(
        {
            "coin": ["BTC"],
            "coin2": ["BNB"],
            "type": ["comparison"],
            "q": ["api sanitizer"],
        },
        client_ip="127.0.0.1",
    )

    assert code == 200
    assert "SECRET" not in body
    assert "CONTEXT_SECRET" not in body
    assert "NESTED_SECRET" not in body
    payload = json.loads(body)["data"]
    for key in ("report_a", "report_b"):
        assert "asset_intrinsic_official_state" not in payload[key]
        assert payload[key]["asset_intrinsic_assessment"]["mode"] == "shadow"
    for key in ("supporting_report_a", "supporting_report_b"):
        nested = payload["comparison_report"][key]
        assert "asset_intrinsic_official_state" not in nested
        assert nested["asset_intrinsic_assessment"]["mode"] == "shadow"


@pytest.mark.parametrize(
    "field",
    [
        "coin_a",
        "coin_b",
        "query",
        "conclusion",
        "dimensions",
        "confidence",
        "limits",
        "could_flip",
        "generated_at",
        "supporting_evidence_a",
        "supporting_evidence_b",
    ],
)
def test_comparison_public_schema_rejects_authority_payload_in_every_field(field) -> None:
    value = ComparisonReport(
        coin_a="BTC",
        coin_b="BNB",
        query="strict schema",
        conclusion="insufficient data",
        supporting_report_a=report("BTC"),
        supporting_report_b=report("BNB"),
    ).to_dict()
    value[field] = {"officialState": {"rawReceipt": "SECRET"}}
    assert web._public_comparison_report_dict(value) is None


@pytest.mark.parametrize("field", ["supporting_report_a", "supporting_report_b"])
def test_comparison_public_schema_nulls_invalid_supporting_report(field) -> None:
    value = ComparisonReport(
        coin_a="BTC",
        coin_b="BNB",
        query="strict schema",
        conclusion="insufficient data",
        supporting_report_a=report("BTC"),
        supporting_report_b=report("BNB"),
    ).to_dict()
    value[field] = {"officialState": {"rawReceipt": "SECRET"}}

    public = web._public_comparison_report_dict(value)

    assert public is not None
    assert public[field] is None
    assert "SECRET" not in str(public)


def test_comparison_public_schema_rejects_authority_text_in_conclusion() -> None:
    value = ComparisonReport(
        coin_a="BTC",
        coin_b="BNB",
        query="strict schema",
        conclusion="unexpected raw_receipt=SECRET escaped",
        supporting_report_a=report("BTC"),
        supporting_report_b=report("BNB"),
    ).to_dict()
    assert web._public_comparison_report_dict(value) is None


def test_comparison_public_schema_rejects_authority_text_in_evidence_value() -> None:
    value = ComparisonReport(
        coin_a="BTC",
        coin_b="BNB",
        query="strict schema",
        conclusion="insufficient data",
        supporting_report_a=report("BTC"),
        supporting_report_b=report("BNB"),
        supporting_evidence_a=[{"excerpt": "private_key=TOP-SECRET"}],
    ).to_dict()
    assert web._public_comparison_report_dict(value) is None


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


def test_noncanonical_server_asset_context_fails_closed(monkeypatch) -> None:
    class Context:
        def to_dict(self):
            return {
                "asset_id": "asset:btc",
                "promotionReceipt": {"rawReceipt": "SECRET"},
            }

    class Record:
        context = Context()

    class Repository:
        def by_symbol(self, _symbol, as_of=None):
            return Record()

    monkeypatch.setattr(web, "_ASSET_CONTEXT_REPOSITORY", Repository())
    public = web._public_report_dict(report("BTC"))
    assert public["asset_context"] is None
    assert public["asset_intrinsic_assessment"] is None
    assert "SECRET" not in str(public)


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
    forged.asset_intrinsic_official_state = {
        "state": "official",
        "reason": "REPORT_LEVEL_SECRET",
    }

    public = web._public_report_dict(forged)

    assert public["asset_intrinsic_assessment"] != forged.asset_intrinsic_assessment
    _assert_intrinsic_delta_is_derived(public["asset_intrinsic_assessment"])
    assert public["asset_intrinsic_assessment"]["asset_id"] == "asset:btc"
    assert public["asset_intrinsic_assessment"]["dimensions"]
    assert public["asset_intrinsic_assessment"]["gate"]["passed"] is True
    assert "asset_intrinsic_official_state" not in public
    assert "SECRET" not in str(public)


@pytest.mark.parametrize(
    "forged",
    [
        {"mode": "official", "affects_official_score": True},
        {"mode": "shadow", "affects_official_score": False, "official_state": {}},
        {
            "mode": "shadow",
            "affects_official_score": False,
            "promotion_receipt": {"decision": "pass"},
        },
        {
            "mode": "shadow",
            "affects_official_score": False,
            "release_capability": {"capability": "asset_intrinsic"},
        },
        {
            "mode": "shadow",
            "affects_official_score": False,
            "calibration_claim": {"brier": 0.0},
        },
        {
            "mode": "shadow",
            "affects_official_score": False,
            "metadata": {
                "raw-receipt": "SECRET",
                "authorityAlias": {"signature": "SECRET"},
            },
        },
    ],
)
def test_normal_response_rejects_every_promotion_signal(monkeypatch, forged) -> None:
    monkeypatch.setattr(web, "assess_intrinsic_shadow", lambda _view: forged)
    public = web._public_report_dict(report("BTC"))
    assert public["asset_intrinsic_assessment"] is None


@pytest.mark.parametrize("malformed_context", [[], "asset:btc"])
def test_malformed_prefilled_context_is_ignored_for_server_canonical_context(
    malformed_context,
) -> None:
    malformed = report("BTC")
    malformed.asset_context = malformed_context
    malformed.asset_intrinsic_assessment = {"total_delta": 0.08}

    public = web._public_report_dict(malformed)

    assert public["asset_context"]["asset_id"] == "asset:btc"
    assert public["risk_notices"] == []
    assert public["asset_intrinsic_assessment"]["asset_id"] == "asset:btc"


def test_existing_risk_notices_are_recomputed_from_trusted_context() -> None:
    existing = report("BTC")
    existing.risk_notices = [
        {
            "code": "existing",
            "severity": "info",
            "message": "officialState.rawReceipt=SECRET",
        }
    ]

    public = web._public_report_dict(existing)

    assert public["asset_context"]["asset_id"] == "asset:btc"
    assert public["risk_notices"] == []
    assert "SECRET" not in str(public)
    assert public["asset_intrinsic_assessment"]["asset_id"] == "asset:btc"
    _assert_intrinsic_delta_is_derived(public["asset_intrinsic_assessment"])
    assert public["asset_intrinsic_assessment"]["gate"]["passed"] is True


def test_persisted_report_removes_normalized_official_state_aliases() -> None:
    persisted = asdict(report("BTC"))
    persisted["assetIntrinsicOfficialState"] = {"rawReceipt": "ALIAS_SECRET"}
    persisted["risk_notices"] = [
        {
            "code": "forged",
            "severity": "warning",
            "message": "officialState.rawReceipt=NESTED_SECRET",
        }
    ]

    public = web._public_report_mapping(persisted)

    assert public is not None
    assert "ALIAS_SECRET" not in str(public)
    assert "NESTED_SECRET" not in str(public)
    assert public["risk_notices"] == []


def test_persisted_report_fails_soft_on_authority_in_retained_field() -> None:
    persisted = asdict(report("BTC"))
    persisted["market_judgment"] = "raw_receipt=RETAINED_SECRET"

    assert web._public_report_mapping(persisted) is None
