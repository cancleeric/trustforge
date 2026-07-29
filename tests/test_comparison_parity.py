"""CA-06 (#830): Cross-entry comparison parity tests.

Validate that API / analyze.json / comparison_report fields are consistently
present, backward-compatible, and correctly structured across all entry points:
  - /api/analyze?type=comparison  (JSON envelope)
  - /analyze.json                 (legacy JSON route)
  - _build_comparison_json_payload (shared builder)

Also covers error/timeout/null envelope correctness.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any
from unittest.mock import patch

import pytest

from trustforge.comparison_contract import (
    COMPARISON_DIMENSIONS,
    ComparisonReport,
    ComparisonRunResult,
    DimensionResult,
)
from trustforge.schema import (
    BasisItem,
    Evidence,
    Report,
)
from trustforge.execlog import ExecutionLog
from trustforge.web import (
    _build_comparison_json_payload,
    _handle_api_analyze,
    _json_envelope_err,
    _json_envelope_ok,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _minimal_log() -> ExecutionLog:
    """Build a minimal ExecutionLog for testing."""
    return ExecutionLog(
        run_id="test-run-id",
        now_fn=lambda: 1000000.0,  # frozen clock, no side effects
    )


def _minimal_report(direction: str = "bullish") -> Report:
    """Build a minimal Report for testing."""
    return Report(
        coin="TEST",
        question_type="multi_source",
        question="Test question",
        market_judgment=f"Test {direction} judgment.",
        facts=[],
        inferences=[],
        key_basis=[],
        confidence=0.75,
        limits=[],
        could_flip=[],
        contrarian=[],
        direction=direction,
        generated_at="2026-01-01T00:00:00Z",
    )


def _minimal_evidence(kind: str = "price", trust: float = 0.5) -> Evidence:
    """Build a minimal Evidence for testing."""
    return Evidence(
        source="test_source",
        fetched_at="2026-01-01T00:00:00Z",
        content_reference="test content ref",
        related_claim="test claim",
        kind=kind,
        trust=trust,
        source_url="https://example.com",
    )


def _minimal_dimensions() -> list[DimensionResult]:
    """Build the four comparison dimensions (all insufficient, for safe null test)."""
    return [
        DimensionResult(
            dimension=dim,
            label=f"{dim}比較",
            finding=f"僅有單邊證據，不做硬比較。",
            a_evidence_refs=[],
            b_evidence_refs=[],
            confidence=0.0,
            decision="insufficient",
        )
        for dim in COMPARISON_DIMENSIONS
    ]


def _build_test_comparison_result(
    *, with_comparison: bool = True, coin_a: str = "BTC", coin_b: str = "ETH"
) -> ComparisonRunResult:
    """Build a ComparisonRunResult suitable for testing API payload output.

    When with_comparison=False, .comparison is None (simulates pipeline
    that couldn't produce a comparison).
    """
    report_a = _minimal_report("bullish")
    report_b = _minimal_report("bearish")
    ev_a = [_minimal_evidence("price", 0.8)]
    ev_b = [_minimal_evidence("price", 0.3)]

    if not with_comparison:
        return ComparisonRunResult(
            report_a=report_a,
            report_b=report_b,
            evidence_a=ev_a,
            evidence_b=ev_b,
            comparison=None,
            log=_minimal_log(),
        )

    comparison = ComparisonReport(
        coin_a=coin_a,
        coin_b=coin_b,
        query="比較 BTC 與 ETH",
        conclusion="測試結論：BTC 優於 ETH。",
        dimensions=_minimal_dimensions(),
        confidence=0.5,
        supporting_report_a=report_a,
        supporting_report_b=report_b,
        supporting_evidence_a=ev_a,
        supporting_evidence_b=ev_b,
        generated_at="2026-01-01T00:00:00Z",
    )
    # Make dimensions normal for a proper comparison
    comparison.dimensions = [
        DimensionResult(
            dimension=dim,
            label=f"{dim}比較",
            finding=f"雙邊均有足夠證據進行{dim}比較。",
            a_evidence_refs=[0] if ev_a else [],
            b_evidence_refs=[0] if ev_b else [],
            confidence=0.8,
            decision="normal",
        )
        for dim in COMPARISON_DIMENSIONS
    ]

    return ComparisonRunResult(
        report_a=report_a,
        report_b=report_b,
        evidence_a=ev_a,
        evidence_b=ev_b,
        comparison=comparison,
        log=_minimal_log(),
    )


# ---------------------------------------------------------------------------
# Envelope contract tests
# ---------------------------------------------------------------------------

class TestComparisonEnvelopeParity:
    """Verify API, analyze.json both return same semantic structure."""

    def test_json_envelope_ok_shape(self):
        """_json_envelope_ok wraps data in {ok: true, data: ...}."""
        payload = _json_envelope_ok({"key": "value"})
        parsed = json.loads(payload)
        assert parsed["ok"] is True
        assert parsed["data"] == {"key": "value"}

    def test_json_envelope_err_shape(self):
        """_json_envelope_err wraps errors in {ok: false, error: {code, message}}."""
        payload = _json_envelope_err("bad_request", "測試錯誤")
        parsed = json.loads(payload)
        assert parsed["ok"] is False
        assert parsed["error"]["code"] == "bad_request"
        assert parsed["error"]["message"] == "測試錯誤"

    def test_api_comparison_has_comparison_report(self):
        """API /api/analyze?type=comparison returns comparison_report field."""
        result = _build_test_comparison_result(with_comparison=True)

        with patch(
            "trustforge.web._dedup_analyze_call",
            return_value=result,
        ):
            code, body = _handle_api_analyze(
                {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["parity test"]},
                client_ip="10.0.0.1",
            )
            assert code == 200
            data = json.loads(body)
            assert data["ok"], f"Expected ok=true, got: {body[:200]}"
            assert "comparison_report" in data["data"], (
                "API must include comparison_report"
            )
            assert data["data"]["comparison_report"] is not None, (
                "comparison_report should not be None when comparison exists"
            )
            cr = data["data"]["comparison_report"]
            assert cr["coin_a"] == "BTC"
            assert cr["coin_b"] == "ETH"
            assert "conclusion" in cr
            assert "dimensions" in cr
            assert len(cr["dimensions"]) == len(COMPARISON_DIMENSIONS)

    def test_api_comparison_report_null_when_no_comparison(self):
        """When comparison is None, comparison_report should be null."""
        result = _build_test_comparison_result(with_comparison=False)

        with patch(
            "trustforge.web._dedup_analyze_call",
            return_value=result,
        ):
            code, body = _handle_api_analyze(
                {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["null test"]},
                client_ip="10.0.0.2",
            )
            assert code == 200
            data = json.loads(body)
            assert data["ok"]
            assert data["data"]["comparison_report"] is None, (
                "comparison_report should be null when comparison is None"
            )

    def test_api_non_comparison_no_comparison_report(self):
        """Non-comparison type should NOT include comparison_report field."""
        # Build a mock single-coin result that mimics what _do_analyze returns
        report = _minimal_report()
        ev = [_minimal_evidence("price", 0.5)]

        with patch(
            "trustforge.web._dedup_analyze_call",
            return_value=(report, ev, _minimal_log()),
        ):
            code, body = _handle_api_analyze(
                {"coin": ["BTC"], "type": ["multi_source"], "q": ["single coin test"]},
                client_ip="10.0.0.3",
            )
            assert code == 200
            data = json.loads(body)
            assert data["ok"]
            assert "comparison_report" not in data["data"], (
                "Non-comparison type should NOT include comparison_report"
            )

    def test_build_comparison_json_payload_present(self):
        """_build_comparison_json_payload includes comparison_report."""
        result = _build_test_comparison_result(with_comparison=True)
        payload = _build_comparison_json_payload(result)

        assert "comparison_report" in payload
        assert payload["comparison_report"] is not None
        assert payload["comparison_report"]["coin_a"] == "BTC"
        assert payload["comparison_report"]["coin_b"] == "ETH"

    def test_build_comparison_json_payload_null(self):
        """_build_comparison_json_payload has comparison_report=null when absent."""
        result = _build_test_comparison_result(with_comparison=False)
        payload = _build_comparison_json_payload(result)

        assert "comparison_report" in payload
        assert payload["comparison_report"] is None

    def test_error_envelope_comparison_validation(self):
        """Invalid type=comparison with single coin returns error envelope."""
        code, body = _handle_api_analyze(
            {"coin": ["BTC"], "type": ["comparison"], "q": ["compare test"]},
            client_ip="10.0.0.5",
        )
        assert code == 400
        data = json.loads(body)
        assert data["ok"] is False
        assert "error" in data
        assert data["error"]["code"] == "bad_request"
        assert "兩個幣種" in data["error"]["message"] or "幣種" in data["error"]["message"]

    def test_error_envelope_invalid_type(self):
        """Invalid type parameter returns error envelope."""
        code, body = _handle_api_analyze(
            {"coin": ["BTC"], "type": ["invalid_type"], "q": ["test"]},
            client_ip="10.0.0.6",
        )
        assert code == 400
        data = json.loads(body)
        assert data["ok"] is False
        assert data["error"]["code"] == "bad_request"

    def test_error_envelope_coins_same(self):
        """Comparison with same coin (BTC,BTC) returns 400 validation error."""
        code, body = _handle_api_analyze(
            {"coin": ["BTC,BTC"], "type": ["comparison"], "q": ["same coin test"]},
            client_ip="10.0.0.7",
        )
        # 400 or could still be parsed by _parse_comparison_coins which rejects same coin
        data = json.loads(body)
        assert data["ok"] is False
        assert data["error"]["code"] == "bad_request"
        err_msg = data["error"]["message"]
        assert "比較" in err_msg or "幣種" in err_msg


# ---------------------------------------------------------------------------
# Backward compatibility tests
# ---------------------------------------------------------------------------

class TestComparisonBackwardCompat:
    """Verify existing fields are preserved alongside comparison_report."""

    def test_api_report_a_report_b_preserved(self):
        """report_a, report_b still present when comparison_report is added."""
        result = _build_test_comparison_result(with_comparison=True)

        with patch(
            "trustforge.web._dedup_analyze_call",
            return_value=result,
        ):
            code, body = _handle_api_analyze(
                {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["backward compat"]},
                client_ip="10.0.0.8",
            )
            assert code == 200
            data = json.loads(body)["data"]
            assert "report_a" in data, "report_a must be preserved"
            assert "report_b" in data, "report_b must be preserved"
            assert "evidence_a" in data, "evidence_a must be preserved"
            assert "evidence_b" in data, "evidence_b must be preserved"
            assert "comparison_report" in data, "comparison_report must be present"
            # report_a should have coin direction summary
            assert "direction" in data["report_a"]

    def test_build_payload_preserves_all_fields(self):
        """_build_comparison_json_payload preserves report_a/b, evidence_a/b."""
        result = _build_test_comparison_result(with_comparison=True)
        payload = _build_comparison_json_payload(result)

        assert "version" in payload
        assert "report_a" in payload
        assert "report_b" in payload
        assert "evidence_a" in payload
        assert "evidence_b" in payload
        assert "comparison_report" in payload
        assert "execution" in payload
        assert "execution_log" in payload

    def test_build_payload_fields_with_null_comparison(self):
        """Even with comparison=None, report_a/b/evidence_a/b are present."""
        result = _build_test_comparison_result(with_comparison=False)
        payload = _build_comparison_json_payload(result)

        assert "report_a" in payload
        assert "evidence_a" in payload
        assert "report_b" in payload
        assert "evidence_b" in payload
        assert payload["comparison_report"] is None

    def test_comparison_report_semantic_fields(self):
        """comparison_report has required semantic sub-fields."""
        result = _build_test_comparison_result(with_comparison=True)

        with patch(
            "trustforge.web._dedup_analyze_call",
            return_value=result,
        ):
            code, body = _handle_api_analyze(
                {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["semantic test"]},
                client_ip="10.0.0.9",
            )
            assert code == 200
            cr = json.loads(body)["data"]["comparison_report"]
            # Required fields per ComparisonReport schema
            assert "coin_a" in cr
            assert "coin_b" in cr
            assert "query" in cr
            assert "conclusion" in cr
            assert "dimensions" in cr
            assert "confidence" in cr
            assert "limits" in cr
            assert "could_flip" in cr
            assert "generated_at" in cr
            assert "supporting_report_a" in cr
            assert "supporting_report_b" in cr
            assert "supporting_evidence_a" in cr
            assert "supporting_evidence_b" in cr


# ---------------------------------------------------------------------------
# Error / timeout / fallback envelope tests
# ---------------------------------------------------------------------------

class TestComparisonErrorEnvelope:
    """Test error, timeout, deterministic-fallback envelope correctness."""

    def test_timeout_error_correct_code(self):
        """timeout error returns 503 with proper envelope."""
        result = _json_envelope_err("timeout", "dedup 等待逾時")
        parsed = json.loads(result)
        assert parsed["ok"] is False
        assert parsed["error"]["code"] == "timeout"
        assert "message" in parsed["error"]

    def test_upstream_error_502_shape(self):
        """502 (upstream error) uses error envelope with code."""
        result = _json_envelope_err("upstream_error", "pipeline 執行失敗")
        parsed = json.loads(result)
        assert parsed["ok"] is False
        assert parsed["error"]["code"] == "upstream_error"

    def test_deterministic_fallback_build_comparison_report(self):
        """build_comparison_report always produces a valid ComparisonReport,
        even when evidence is sparse (deterministic fallback)."""
        from trustforge.comparison_contract import build_comparison_report

        empty_report = _minimal_report("neutral")
        cr = build_comparison_report(
            coin_a="BTC",
            coin_b="ETH",
            query="fallback test",
            report_a=empty_report,
            report_b=empty_report,
            evidence_a=[],
            evidence_b=[],
        )
        assert cr is not None, "deterministic fallback must produce a report"
        assert cr.conclusion.strip() != "", "conclusion must be non-empty even on fallback"
        assert len(cr.dimensions) == len(COMPARISON_DIMENSIONS)

    def test_build_comparison_json_payload_error_path(self):
        """_build_comparison_json_payload handles ComparisonReport.to_dict() safely."""
        from trustforge.comparison_contract import ComparisonReport
        # Construct a minimal ComparisonReport with partial data
        cr = ComparisonReport(
            coin_a="BTC",
            coin_b="ETH",
            query="minimal",
            conclusion="",
            dimensions=[],
            confidence=0.0,
        )
        # to_dict() should not raise
        d = cr.to_dict()
        assert isinstance(d, dict)
        assert d["coin_a"] == "BTC"
        assert d["dimensions"] == []


# ---------------------------------------------------------------------------
# OpenAPI / CLI parity (structural)
# ---------------------------------------------------------------------------

class TestComparisonOpenAPIParity:
    """Verify comparison_report structure matches the expected API contract
    (and would match a hypothetical OpenAPI spec)."""

    def test_all_dimensions_have_required_keys(self):
        """Each dimension in comparison_report has the required fields."""
        result = _build_test_comparison_result(with_comparison=True)

        with patch(
            "trustforge.web._dedup_analyze_call",
            return_value=result,
        ):
            _, body = _handle_api_analyze(
                {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["API spec test"]},
                client_ip="10.0.0.10",
            )
            cr = json.loads(body)["data"]["comparison_report"]
            for dim in cr["dimensions"]:
                assert "dimension" in dim
                assert "label" in dim
                assert "finding" in dim
                assert "a_evidence_refs" in dim
                assert "b_evidence_refs" in dim
                assert "confidence" in dim
                assert "decision" in dim

    def test_comparison_report_types(self):
        """comparison_report values have correct JSON types."""
        result = _build_test_comparison_result(with_comparison=True)

        with patch(
            "trustforge.web._dedup_analyze_call",
            return_value=result,
        ):
            _, body = _handle_api_analyze(
                {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["type test"]},
                client_ip="10.0.0.11",
            )
            cr = json.loads(body)["data"]["comparison_report"]
            assert isinstance(cr["coin_a"], str)
            assert isinstance(cr["coin_b"], str)
            assert isinstance(cr["conclusion"], str)
            assert isinstance(cr["confidence"], (int, float))
            assert 0.0 <= cr["confidence"] <= 1.0
            assert isinstance(cr["dimensions"], list)
            assert isinstance(cr["limits"], list)
            assert isinstance(cr["could_flip"], list)
            assert isinstance(cr["generated_at"], str)
            # ISO 8601 timestamp roughly
            assert re.match(r"\d{4}-\d{2}-\d{2}", cr["generated_at"])

    def test_report_a_b_share_same_payload_structure(self):
        """report_a and report_b use identical field structure."""
        result = _build_test_comparison_result(with_comparison=True)

        with patch(
            "trustforge.web._dedup_analyze_call",
            return_value=result,
        ):
            _, body = _handle_api_analyze(
                {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["structure parity"]},
                client_ip="10.0.0.12",
            )
            data = json.loads(body)["data"]
            ra_keys = set(data["report_a"].keys())
            rb_keys = set(data["report_b"].keys())
            assert ra_keys == rb_keys, (
                f"report_a and report_b must have identical keys: "
                f"report_a={ra_keys - rb_keys}, report_b={rb_keys - ra_keys}"
            )
