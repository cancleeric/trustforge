"""Read-only module status cards for upgrade-plane observability issues."""

from __future__ import annotations

from typing import Any


DATA_CONNECTORS = ("coindesk", "sec-gov", "alternative-me-fng", "coingecko-market-range", "news-rss-group")
INTELLIGENCE_GATES = ("embedding-index", "rag-memory", "rag-reranker", "contrarian-search")


def source_connector_upgrade_status(connectors: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    values = connectors or {}
    rows = []
    for source in DATA_CONNECTORS:
        item = values.get(source) or {}
        rows.append(
            {
                "source": source,
                "provider": item.get("provider", "builtin"),
                "agentcore_enabled": item.get("provider") == "agentcore",
                "status": item.get("status", "registered"),
                "automatic_apply": False,
            }
        )
    return {"kind": "source_connector_upgrade_status", "status": "ready", "connectors": rows}


def connector_routing_fallback_status(routes: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    values = routes or {}
    rows = []
    for source in DATA_CONNECTORS:
        item = values.get(source) or {}
        primary = item.get("primary", "live_connector")
        fallback = item.get("fallback", "cached_snapshot")
        rows.append(
            {
                "source": source,
                "primary": primary,
                "fallback": fallback,
                "active": item.get("active", primary),
                "fallback_available": bool(fallback),
            }
        )
    return {"kind": "connector_routing_fallback_status", "status": "ready", "routes": rows}


def source_frequency_timeout_status(config: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    values = config or {}
    rows = []
    for source in DATA_CONNECTORS:
        item = values.get(source) or {}
        rows.append(
            {
                "source": source,
                "frequency_seconds": int(item.get("frequency_seconds", 3600)),
                "timeout_seconds": float(item.get("timeout_seconds", 3.0)),
                "retry_attempts": int(item.get("retry_attempts", 2)),
            }
        )
    return {"kind": "source_frequency_timeout_status", "status": "ready", "sources": rows}


def scheduler_backpressure_status(metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    values = metrics or {}
    queue_depth = int(values.get("queue_depth", 0))
    dlq_depth = int(values.get("dlq_depth", 0))
    max_queue_depth = int(values.get("max_queue_depth", 100))
    status = "blocked" if dlq_depth else "backpressure" if queue_depth > max_queue_depth else "ready"
    return {
        "kind": "scheduler_backpressure_status",
        "status": status,
        "queue_depth": queue_depth,
        "dlq_depth": dlq_depth,
        "max_queue_depth": max_queue_depth,
        "automatic_retry": True,
        "requires_human_review": bool(dlq_depth),
    }


def embedding_index_model_gate_status(gates: dict[str, Any] | None = None) -> dict[str, Any]:
    values = gates or {}
    indexed_questions = int(values.get("indexed_questions", 0))
    min_indexed_questions = int(values.get("min_indexed_questions", 50))
    status = "pass" if indexed_questions >= min_indexed_questions else "locked"
    return {
        "kind": "embedding_index_model_gate_status",
        "status": status,
        "indexed_questions": indexed_questions,
        "min_indexed_questions": min_indexed_questions,
        "active_strategy": values.get("active_strategy", "keyword_baseline"),
        "candidate_strategy": values.get("candidate_strategy", "embedding_index"),
        "automatic_apply": False,
    }


def rag_memory_status(metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    values = metrics or {}
    return {
        "kind": "rag_memory_status",
        "status": values.get("status", "observed"),
        "retrieval_provider": values.get("retrieval_provider", "keyword_baseline"),
        "conversation_memory": values.get("conversation_memory", "session_only"),
        "indexed_questions": int(values.get("indexed_questions", 0)),
        "automatic_apply": False,
    }


def reranker_facet_model_gate_status(gates: dict[str, Any] | None = None) -> dict[str, Any]:
    values = gates or {}
    offline_eval_passed = bool(values.get("offline_eval_passed", False))
    facet_coverage = float(values.get("facet_coverage", 0.0))
    status = "ready" if offline_eval_passed and facet_coverage >= 0.8 else "locked"
    return {
        "kind": "reranker_facet_model_gate_status",
        "status": status,
        "offline_eval_passed": offline_eval_passed,
        "facet_coverage": facet_coverage,
        "candidate": values.get("candidate", "reranker_plus_facets"),
        "automatic_apply": False,
    }


def contrarian_search_status(metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    values = metrics or {}
    coverage = float(values.get("coverage", 0.0))
    status = "ready" if coverage >= 0.75 else "needs_more_evidence"
    return {
        "kind": "contrarian_search_status",
        "status": status,
        "coverage": coverage,
        "min_coverage": 0.75,
        "last_query_count": int(values.get("last_query_count", 0)),
        "automatic_apply": False,
    }


def observability_snapshot(data: dict[str, Any] | None = None) -> dict[str, Any]:
    values = data or {}
    return {
        "kind": "upgrade_module_observability_snapshot",
        "data_plane": {
            "source_connectors": source_connector_upgrade_status(values.get("source_connectors")),
            "routing_fallback": connector_routing_fallback_status(values.get("routes")),
            "frequency_timeout": source_frequency_timeout_status(values.get("source_timeouts")),
            "scheduler_backpressure": scheduler_backpressure_status(values.get("scheduler")),
        },
        "intelligence_plane": {
            "embedding_index": embedding_index_model_gate_status(values.get("embedding_index")),
            "rag_memory": rag_memory_status(values.get("rag_memory")),
            "reranker_facets": reranker_facet_model_gate_status(values.get("reranker_facets")),
            "contrarian_search": contrarian_search_status(values.get("contrarian_search")),
        },
    }
