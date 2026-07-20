from trustforge.module_status import (
    connector_routing_fallback_status,
    contrarian_search_status,
    embedding_index_model_gate_status,
    observability_snapshot,
    reranker_facet_model_gate_status,
    scheduler_backpressure_status,
    source_connector_upgrade_status,
    source_frequency_timeout_status,
)


def test_data_plane_status_cards_have_defaults():
    assert source_connector_upgrade_status()["status"] == "ready"
    assert connector_routing_fallback_status()["routes"][0]["fallback_available"]
    assert source_frequency_timeout_status()["sources"][0]["timeout_seconds"] == 3.0


def test_scheduler_backpressure_reports_dlq_as_blocked():
    status = scheduler_backpressure_status({"queue_depth": 1, "dlq_depth": 2})

    assert status["status"] == "blocked"
    assert status["requires_human_review"]


def test_embedding_index_gate_locks_until_minimum_index_size():
    locked = embedding_index_model_gate_status({"indexed_questions": 10})
    passed = embedding_index_model_gate_status({"indexed_questions": 50})

    assert locked["status"] == "locked"
    assert passed["status"] == "pass"


def test_reranker_and_contrarian_thresholds():
    assert reranker_facet_model_gate_status({"offline_eval_passed": True, "facet_coverage": 0.8})["status"] == "ready"
    assert contrarian_search_status({"coverage": 0.75})["status"] == "ready"


def test_observability_snapshot_groups_planes():
    snapshot = observability_snapshot()

    assert "source_connectors" in snapshot["data_plane"]
    assert "embedding_index" in snapshot["intelligence_plane"]
