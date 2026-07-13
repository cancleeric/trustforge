from trustforge.improvement import diagnose


def test_diagnostic_turns_observed_failures_latency_and_qa_gaps_into_review_only_proposals():
    report = diagnose(
        scheduler_runs=[{"failure_count": 1, "failures": ["coindesk:BTC"]}],
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


def test_diagnostic_blocks_calibrator_proposal_until_leakage_safe_sample_gate_is_met():
    report = diagnose(replay={"available_snapshot_count": 4, "horizons": {}}, generated_at="2026-07-13T00:00:00Z")

    assert [item["id"] for item in report["proposals"]] == ["calibration-data-accumulation"]
