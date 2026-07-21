from trustforge.improvement import diagnose


def test_diagnostic_turns_observed_failures_latency_and_qa_gaps_into_review_only_proposals():
    report = diagnose(
        scheduler_runs=[{"failure_count": 1, "failures": ["coindesk:BTC"]}],
        connector_reliability={"sources": [{
            "source": "coindesk", "attempted_runs": 3, "failure_rate": 0.3333,
            "consecutive_successes": 1, "meets_reliability_gate": False,
        }]},
        question_bank={
            "failed": 1, "results": [{"gaps": ["evidence_required_fields"]}],
            "source_latency_ms": {"news": {"samples": 6, "p95": 2400}},
        },
        generated_at="2026-07-13T00:00:00Z",
    )

    assert report["status"] == "attention_required"
    assert {item["id"] for item in report["proposals"]} >= {
        "source-reliability-investigation", "report-contract-regression", "latency-news",
    }
    assert all(item["approval_required"] and not item["automatic_apply"] for item in report["proposals"])
    reliability = report["proposals"][0]["evidence"]["sources_below_gate"]
    assert reliability == [{"source": "coindesk", "failure_rate": 0.3333, "consecutive_successes": 1}]


def test_diagnostic_blocks_calibrator_proposal_until_leakage_safe_sample_gate_is_met():
    report = diagnose(replay={"available_snapshot_count": 4, "horizons": {}}, generated_at="2026-07-13T00:00:00Z")

    assert [item["id"] for item in report["proposals"]] == ["calibration-data-accumulation"]


def test_diagnostic_proposes_calibrator_upgrade_when_error_crosses_threshold():
    report = diagnose(replay={
        "available_snapshot_count": 120,
        "horizons": {"T+1": {
            "eligible_predictions": 120,
            "hit_rate": 0.52,
            "calibration_error": 0.17,
            "reliability": [],
        }},
    }, generated_at="2026-07-21T00:00:00Z")

    proposal = next(item for item in report["proposals"] if item["id"] == "confidence-calibrator-t+1")
    assert proposal["area"] == "historical-calibration"
    assert proposal["automatic_apply"] is False
    assert proposal["evidence"]["calibration_error"] == 0.17


def test_analysis_history_can_propose_outer_framework_experiments_but_never_apply_them():
    report = diagnose(analysis_history={
        "job_count": 40, "failed_jobs": 3, "retried_jobs": 6,
        "active_question_count": 20, "compared_question_pairs": 30,
        "similar_question_rate": 0.5, "stages": [{"stage": "trust_reasoning", "failures": 3}],
    }, generated_at="2026-07-16T00:00:00Z")
    proposals = {item["id"]: item for item in report["proposals"]}
    assert {"analysis-flow-reliability", "question-retrieval-diversification"} <= proposals.keys()
    assert all(item["approval_required"] and not item["automatic_apply"] for item in proposals.values())


def test_actual_historical_gaps_feed_outer_proposals_without_synthesizing_evidence():
    report = diagnose(historical_coverage={
        "from_date": "2021-07-17", "to_date": "2026-07-17",
        "capabilities": [
            {"source": "alternative-me-fng", "status": "ready"},
            {"source": "sec-gov", "status": "ready_partial"},
            {"source": "blockchain-com-charts", "status": "ready", "coins": ["BTC"]},
            {"source": "coingecko-market-range", "status": "credential_gated"},
        ],
        "coins": {
            "BTC": {
                "missing_dates": ["2024-10-26"],
                "sources": {
                    "alternative-me-fng": {"days": 1826, "coverage": 0.999453},
                    "sec-gov": {"days": 0, "coverage": 0},
                },
            },
            "ETH": {
                "missing_dates": [],
                "sources": {"blockchain-com-charts": {"days": 0, "coverage": 0}},
            },
        },
    }, generated_at="2026-07-17T00:00:00Z")

    proposal = next(item for item in report["proposals"] if item["id"] == "historical-archive-coverage")
    assert proposal["automatic_apply"] is False
    assert proposal["evidence"]["missing_snapshot_days"] == {"BTC": ["2024-10-26"]}
    assert proposal["evidence"]["source_gaps"]["sec-gov"]["coins"]["BTC"]["coverage"] == 0
    assert "ETH" not in proposal["evidence"]["source_gaps"]["blockchain-com-charts"]["coins"]
    assert proposal["evidence"]["gated_sources"] == ["coingecko-market-range"]
