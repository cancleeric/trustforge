from trustforge.modelhub_training import model_route_gate_status


def test_model_route_gate_stays_locked_when_dependencies_missing():
    status = model_route_gate_status({})

    assert status["status"] == "locked"
    assert status["active_route"] == "bedrock-direct"
    assert status["candidate_route"] == "agentcore-gateway"
    assert status["route_gate"] == "locked_until_dependencies_pass"
    assert not status["automatic_apply"]
    assert status["requires_human_approval"]


def test_model_route_gate_allows_dry_run_when_dependencies_pass():
    status = model_route_gate_status(
        {
            "historical-calibration": {"status": "ready_for_dry_run"},
            "rag-index": {"status": "pass"},
            "rag-reranker": {"status": "ready"},
        }
    )

    assert status["status"] == "ready_for_route_dry_run"
    assert status["route_gate"] == "dry_run_only"
    assert all(check["passed"] for check in status["dependencies"])
