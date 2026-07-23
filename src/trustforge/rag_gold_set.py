"""RAG feedback and versioned gold-set provenance contracts."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from .learning_event_contract import LearningEvent, LearningEventError, make_learning_event

_AUTOMATION_REVIEWER_TOKENS = ("bot", "agent", "automation", "codex", "gpt", "llm", "service")
_INJECTION_MARKERS = ("ignore previous", "system prompt", "developer message", "exfiltrate", "jailbreak")


def build_gold_label_event(
    *,
    analysis_id: str,
    label: str,
    reviewer: str,
    reason: str,
    version: str,
    observed_at: str,
) -> LearningEvent:
    if not _human_reviewer(reviewer):
        raise LearningEventError("gold label requires named human reviewer")
    if not reason.strip() or not version.strip():
        raise LearningEventError("gold label requires reason and version")
    return make_learning_event(
        kind="human_gold_label",
        identity=f"gold-set:{version}:{analysis_id}",
        event_time=observed_at,
        available_time=observed_at,
        as_of_time=observed_at,
        provenance={"source": "rag-gold-set", "collector": reviewer, "observed_at": observed_at},
        payload={
            "label_id": f"{version}:{analysis_id}",
            "analysis_id": analysis_id,
            "reviewer": reviewer,
            "label": label,
            "reason": reason,
            "gold_set_version": version,
        },
    )


def build_feedback_diagnostic_event(
    *,
    analysis_id: str,
    feedback: str,
    reviewer: str,
    observed_at: str,
) -> LearningEvent:
    if _looks_injected(feedback):
        raise LearningEventError("feedback poisoning detected")
    digest = hashlib.sha256(feedback.encode("utf-8")).hexdigest()[:16]
    return make_learning_event(
        kind="candidate_diagnostic",
        identity=f"rag-feedback:{analysis_id}:{digest}",
        event_time=observed_at,
        available_time=observed_at,
        as_of_time=observed_at,
        provenance={"source": "rag-feedback", "collector": reviewer, "observed_at": observed_at},
        payload={
            "diagnostic_id": f"rag-feedback:{analysis_id}:{digest}",
            "analysis_id": analysis_id,
            "reason": "reviewer_feedback_candidate",
            "feedback_sha256": hashlib.sha256(feedback.encode("utf-8")).hexdigest(),
        },
    )


def evaluate_retrieval_result(
    *,
    analysis_id: str,
    retrieval_results: Iterable[dict[str, Any]],
    minimum_citations: int = 2,
) -> dict[str, Any]:
    citations = []
    for result in retrieval_results:
        if result.get("kind") == "historical_answer":
            continue
        if result.get("kind") != "evidence_candidate":
            continue
        citation_id = result.get("citation_id")
        source_url = result.get("source_url")
        if isinstance(citation_id, str) and citation_id and isinstance(source_url, str) and source_url:
            citations.append({"citation_id": citation_id, "source_url": source_url})
    if len(citations) < minimum_citations:
        return {
            "analysis_id": analysis_id,
            "decision": "abstain",
            "reason": "insufficient_citation_evidence",
            "citations": citations,
        }
    return {
        "analysis_id": analysis_id,
        "decision": "candidate_supported",
        "reason": "citation_binding_met",
        "citations": sorted(citations, key=lambda item: item["citation_id"]),
        "query_sha256": hashlib.sha256(
            json.dumps(citations, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }


def _human_reviewer(value: str) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    lowered = value.lower()
    return not any(token in lowered for token in _AUTOMATION_REVIEWER_TOKENS)


def _looks_injected(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _INJECTION_MARKERS)
