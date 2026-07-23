"""Point-in-time RAG gold-set builder and retrieval evaluator.

This module is intentionally pure.  It neither stores nor promotes labels and
never turns feedback, repeated answers, or retrieval output into Evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .calibration_dataset import (
    _canonical_value_tokens,
    _event_anchor,
    _preflight_event,
)
from .learning_event_contract import LearningEvent

_MAX_EVENTS = 100_000
_MAX_BYTES = 16 * 1024 * 1024
_MAX_NODES = 1_000_000
_MAX_FIELD_BYTES = 64 * 1024
_MAX_UNIQUE_CUTOFFS = 16
_SUPPORTED_SCHEMA = "learning-event.v1"
_REVIEWER = "gray-cpo"
_LABEL_FIELDS = {
    "label_id", "analysis_id", "query_id", "label", "answer", "citations",
    "reviewer", "reviewer_role", "reviewer_authority_sha256", "reason",
    "reviewed_at", "gold_version", "supersedes_label_id",
}
_AUTHORITY_FIELDS = {
    "reviewer_id", "role", "tenant_id", "valid_from", "valid_until",
    "credential_sha256",
}
_APPROVAL_FIELDS = {
    "approval_id", "label_identity", "label_event_sha256", "label_id",
    "query_id", "decision", "reason", "reviewer_id", "reviewed_at",
    "tenant_id",
}
_CITATION_FIELDS = {"evidence_identity", "claim"}
_LABEL_ENUM = {"approved_answer", "must_abstain"}
_EVIDENCE_FIELDS = {
    "evidence_id", "claim", "source_url", "status", "supersedes_identity",
    "snapshot_id", "job_id",
}
_RAG_RETRIEVAL_EVENT_FIELDS = {
    "historical_answer_id", "question", "event_type", "query_id", "answer",
    "citations", "abstained", "snapshot_id", "job_id", "snapshot_sha256",
    "retrieval_version", "query_as_of",
}
_RAG_FEEDBACK_EVENT_FIELDS = {
    "historical_answer_id", "question", "event_type", "query_id",
    "retrieval_identity", "feedback", "vote", "eligible_as_gold",
    "eligible_as_evidence",
}


class RagGoldSetError(ValueError):
    """A RAG evaluation input is untrusted, ambiguous, or non-reproducible."""


@dataclass(frozen=True)
class ReviewerAuthorityRegistry:
    """Caller-supplied trust anchor; event payloads cannot add reviewers."""

    tenant_id: str
    version: str
    valid_from: str
    valid_until: str
    records: Mapping[str, Mapping[str, Any]]
    registry_sha256: str

    def canonical(self) -> dict[str, Any]:
        value = {
            "tenant_id": self.tenant_id, "version": self.version,
            "valid_from": _utc(_parse(self.valid_from, "registry.valid_from")),
            "valid_until": _utc(_parse(self.valid_until, "registry.valid_until")),
            "records": _clone_exact_json(self.records, "registry.records"),
        }
        if not _parse(value["valid_from"], "registry.valid_from") < _parse(
            value["valid_until"], "registry.valid_until"
        ):
            raise RagGoldSetError("registry effective window is invalid")
        if self.registry_sha256 != _sha256(value):
            raise RagGoldSetError("reviewer registry checksum mismatch")
        value["registry_sha256"] = self.registry_sha256
        return value

    def resolve(self, reviewer_id: str, *, tenant_id: str, at: datetime) -> dict[str, Any]:
        canonical = self.canonical()
        if canonical["tenant_id"] != tenant_id:
            raise RagGoldSetError("reviewer registry tenant mismatch")
        if not _parse(canonical["valid_from"], "registry.valid_from") <= at < _parse(
            canonical["valid_until"], "registry.valid_until"
        ):
            raise RagGoldSetError("reviewer registry is not effective")
        if reviewer_id != _REVIEWER:
            raise RagGoldSetError("gold-set reviewer must be gray-cpo")
        if set(canonical["records"]) != {_REVIEWER}:
            raise RagGoldSetError("reviewer registry records schema is not exact")
        raw = self.records.get(reviewer_id)
        if type(raw) is not dict or set(raw) != _AUTHORITY_FIELDS:
            raise RagGoldSetError("reviewer is absent from trusted authority registry")
        record = _clone_exact_json(raw, "reviewer authority")
        if record["reviewer_id"] != reviewer_id or record["role"] != "cpo":
            raise RagGoldSetError("reviewer authority role is invalid")
        if record["tenant_id"] != tenant_id:
            raise RagGoldSetError("reviewer authority tenant mismatch")
        if not _parse(record["valid_from"], "authority.valid_from") <= at < _parse(
            record["valid_until"], "authority.valid_until"
        ):
            raise RagGoldSetError("reviewer authority is expired or not yet valid")
        _hex(record["credential_sha256"], "authority.credential_sha256")
        return record


@dataclass(frozen=True)
class ApprovalStoreSnapshot:
    """Independent caller-trusted proof that exact label events were approved."""

    tenant_id: str
    version: str
    valid_from: str
    valid_until: str
    records: tuple[Mapping[str, Any], ...]
    root_sha256: str
    checksum: str

    def canonical(self, *, tenant_id: str, cutoff: datetime) -> dict[str, Any]:
        if self.tenant_id != tenant_id:
            raise RagGoldSetError("approval store tenant mismatch")
        _text(self.version, "approval store version")
        valid_from = _parse(self.valid_from, "approval_store.valid_from")
        valid_until = _parse(self.valid_until, "approval_store.valid_until")
        if not valid_from < valid_until or not valid_from <= cutoff < valid_until:
            raise RagGoldSetError("approval store is outside its effective window")
        if type(self.records) is not tuple:
            raise RagGoldSetError("approval store records must be an exact tuple")
        scoped = []
        approval_ids, label_identities = set(), set()
        total_bytes = 0
        stream_state = {
            "nodes": 0, "node_budget": _MAX_NODES, "source": "approval store",
        }
        for raw in self.records:
            if not isinstance(raw, Mapping):
                raise RagGoldSetError("approval record must be an object")
            # Trusted routing metadata is checked before scoped quota/hash.
            if raw.get("tenant_id") != tenant_id:
                continue
            reviewed_at = _parse(raw.get("reviewed_at"), "approval.reviewed_at")
            if reviewed_at > cutoff:
                continue
            chunks = []
            try:
                for token in _canonical_value_tokens(
                    raw, state=stream_state, depth=1
                ):
                    total_bytes += len(token.encode("utf-8"))
                    if total_bytes > _MAX_BYTES:
                        raise RagGoldSetError(
                            "approval store exceeds scoped UTF-8 byte limit"
                        )
                    chunks.append(token)
                record = json.loads("".join(chunks))
            except (ValueError, TypeError, RecursionError) as exc:
                raise RagGoldSetError(str(exc)) from exc
            if len(scoped) >= _MAX_EVENTS:
                raise RagGoldSetError("approval store exceeds scoped record limit")
            if set(record) != _APPROVAL_FIELDS:
                raise RagGoldSetError("approval record schema is not exact")
            for field in (
                "approval_id", "label_identity", "label_id", "query_id",
                "decision", "reason", "reviewer_id",
            ):
                _text(record[field], f"approval.{field}")
            _hex(record["label_event_sha256"], "approval.label_event_sha256")
            if record["decision"] not in _LABEL_ENUM:
                raise RagGoldSetError("approval decision is invalid")
            if record["reviewer_id"] != _REVIEWER:
                raise RagGoldSetError("approval reviewer is invalid")
            if record["approval_id"] in approval_ids:
                raise RagGoldSetError("duplicate one-time approval_id")
            if record["label_identity"] in label_identities:
                raise RagGoldSetError("duplicate approval for label event")
            approval_ids.add(record["approval_id"])
            label_identities.add(record["label_identity"])
            record["reviewed_at"] = _utc(reviewed_at)
            scoped.append(record)
        scoped.sort(key=lambda item: (item["label_identity"], item["approval_id"]))
        root = _sha256(scoped)
        unsigned = {
            "tenant_id": tenant_id, "version": self.version,
            "valid_from": _utc(valid_from), "valid_until": _utc(valid_until),
            "records": scoped, "root_sha256": root, "count": len(scoped),
        }
        if self.root_sha256 != root or self.checksum != _sha256(unsigned):
            raise RagGoldSetError("approval store root/checksum mismatch")
        return {**unsigned, "checksum": self.checksum}


@dataclass(frozen=True)
class RagGoldSetPolicy:
    tenant_id: str
    dataset_as_of: str
    gold_version: str
    producer_version: str
    gold_set_revision: int = 1
    previous_manifest_sha256: str | None = None
    max_unique_query_cutoffs: int = _MAX_UNIQUE_CUTOFFS
    approval_store_version: str | None = None
    approval_store_root_sha256: str | None = None
    approval_store_checksum: str | None = None
    approval_store_count: int | None = None
    minimum_labels: int = 1

    def canonical(self) -> dict[str, Any]:
        value = asdict(self)
        for field in ("tenant_id", "gold_version", "producer_version"):
            _text(value[field], field)
        value["dataset_as_of"] = _utc(_parse(value["dataset_as_of"], "dataset_as_of"))
        if type(self.minimum_labels) is not int or self.minimum_labels < 1:
            raise RagGoldSetError("minimum_labels must be a positive integer")
        if type(self.gold_set_revision) is not int or self.gold_set_revision < 1:
            raise RagGoldSetError("gold_set_revision must be positive")
        if self.gold_set_revision == 1 and self.previous_manifest_sha256 is not None:
            raise RagGoldSetError("initial gold set cannot have previous manifest")
        if self.gold_set_revision > 1:
            _hex(self.previous_manifest_sha256, "previous_manifest_sha256")
        if self.max_unique_query_cutoffs != _MAX_UNIQUE_CUTOFFS:
            raise RagGoldSetError("max_unique_query_cutoffs must use frozen policy limit")
        for field in (
            "approval_store_root_sha256", "approval_store_checksum",
        ):
            if value[field] is not None:
                _hex(value[field], field)
        if value["approval_store_version"] is not None:
            _text(value["approval_store_version"], "approval_store_version")
        if value["approval_store_count"] is not None and (
            type(value["approval_store_count"]) is not int
            or value["approval_store_count"] < 0
        ):
            raise RagGoldSetError("approval_store_count is invalid")
        return value


@dataclass(frozen=True)
class RetrievalEvaluationPolicy:
    tenant_id: str
    query_as_of: str
    evaluator_version: str
    minimum_gold_queries: int = 1
    max_unique_query_cutoffs: int = _MAX_UNIQUE_CUTOFFS

    def canonical(self) -> dict[str, Any]:
        value = asdict(self)
        for field in ("tenant_id", "evaluator_version"):
            _text(value[field], field)
        value["query_as_of"] = _utc(_parse(value["query_as_of"], "query_as_of"))
        if type(self.minimum_gold_queries) is not int or self.minimum_gold_queries < 1:
            raise RagGoldSetError("minimum_gold_queries must be a positive integer")
        if self.max_unique_query_cutoffs != _MAX_UNIQUE_CUTOFFS:
            raise RagGoldSetError("max_unique_query_cutoffs must use frozen policy limit")
        return value


def build_rag_gold_set(
    label_events: Iterable[LearningEvent],
    *,
    retrieval_events: Iterable[LearningEvent],
    feedback_events: Iterable[LearningEvent],
    evidence_events: Iterable[LearningEvent],
    policy: RagGoldSetPolicy,
    trusted_reviewer_registry: ReviewerAuthorityRegistry,
    trusted_approval_store: ApprovalStoreSnapshot,
    previous_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an immutable PIT manifest from explicitly reviewed human labels."""

    frozen = policy.canonical()
    cutoff = _parse(frozen["dataset_as_of"], "dataset_as_of")
    approval_store = trusted_approval_store.canonical(
        tenant_id=policy.tenant_id, cutoff=cutoff
    )
    approval_binding = {
        "approval_store_version": approval_store["version"],
        "approval_store_root_sha256": approval_store["root_sha256"],
        "approval_store_checksum": approval_store["checksum"],
        "approval_store_count": approval_store["count"],
    }
    for field, actual in approval_binding.items():
        if frozen[field] is not None and frozen[field] != actual:
            raise RagGoldSetError(f"policy {field} does not match trusted approval store")
        frozen[field] = actual
    approvals_by_label = {
        record["label_identity"]: record for record in approval_store["records"]
    }
    retrieval_input = _collect_rag_events(
        retrieval_events, tenant_id=policy.tenant_id, cutoff=cutoff, event_type="rag-retrieval.v1"
    )
    feedback_input = _collect_rag_events(
        feedback_events, tenant_id=policy.tenant_id, cutoff=cutoff, event_type="rag-feedback.v1"
    )
    _canonical_cutoff_set(
        retrieval_input["payloads"], maximum_cutoff=cutoff,
        maximum_unique=frozen["max_unique_query_cutoffs"],
    )
    evidence = _build_evidence_snapshot(
        evidence_events, tenant_id=policy.tenant_id, cutoff=cutoff
    )
    allowed_evidence = set(evidence["current_identities"])
    retrieval_snapshots = _snapshot_cache(
        retrieval_input["payloads"], evidence["event_objects"],
        tenant_id=policy.tenant_id, maximum_cutoff=cutoff,
        maximum_unique=frozen["max_unique_query_cutoffs"],
    )
    for retrieval_payload in retrieval_input["payloads"]:
        _validate_retrieval_semantics(
            retrieval_payload, retrieval_snapshots[retrieval_payload["query_as_of"]]
        )
    retrieval_identities = set(retrieval_input["identities"])
    if any(
        payload["retrieval_identity"] not in retrieval_identities
        for payload in feedback_input["payloads"]
    ):
        raise RagGoldSetError("feedback retrieval identity is dangling")
    for payload in feedback_input["payloads"]:
        retrieval_payload = retrieval_input["by_identity"][payload["retrieval_identity"]]
        if payload["query_id"] != retrieval_payload["query_id"]:
            raise RagGoldSetError("feedback cannot cross query lineage")
    _verify_previous_manifest(previous_manifest, frozen, trusted_reviewer_registry)
    events = _bounded_visible_events(
        label_events, tenant_id=policy.tenant_id, cutoff=cutoff, source="gold labels"
    )
    by_id: dict[str, tuple[LearningEvent, dict[str, Any]]] = {}
    for event in events:
        if event.schema_version != _SUPPORTED_SCHEMA or event.kind != "human_gold_label":
            raise RagGoldSetError("gold input must be human_gold_label learning-event.v1")
        payload = _clone_exact_json(event.payload, "gold label payload")
        if set(payload) != _LABEL_FIELDS:
            raise RagGoldSetError("gold label payload schema is not exact")
        for field in ("label_id", "analysis_id", "query_id", "reason", "gold_version"):
            _text(payload[field], f"label.{field}")
        if type(payload["answer"]) is not str:
            raise RagGoldSetError("label.answer must be a string")
        if len(payload["answer"].encode("utf-8")) > _MAX_FIELD_BYTES:
            raise RagGoldSetError("label.answer exceeds UTF-8 byte limit")
        if payload["label"] not in _LABEL_ENUM:
            raise RagGoldSetError("label must use the exact gold decision enum")
        if payload["label"] == "must_abstain" and payload["answer"]:
            raise RagGoldSetError("must_abstain label cannot carry an answer")
        if payload["label_id"] != event.entity_id:
            raise RagGoldSetError("label_id must match canonical event entity_id")
        if payload["gold_version"] != policy.gold_version:
            raise RagGoldSetError("gold label version mismatch")
        reviewed_at = _parse(payload["reviewed_at"], "label.reviewed_at")
        if reviewed_at > _parse(event.available_time, "label.available_time"):
            raise RagGoldSetError("label cannot be available before review")
        authority = trusted_reviewer_registry.resolve(
            payload["reviewer"], tenant_id=policy.tenant_id, at=reviewed_at
        )
        if payload["reviewer_role"] != authority["role"]:
            raise RagGoldSetError("label reviewer role mismatch")
        if payload["reviewer_authority_sha256"] != _sha256(authority):
            raise RagGoldSetError("label reviewer authority hash mismatch")
        approval = approvals_by_label.get(event.identity)
        expected_approval = {
            "label_identity": event.identity,
            "label_event_sha256": _sha256(_event_anchor(event)),
            "label_id": payload["label_id"],
            "query_id": payload["query_id"],
            "decision": payload["label"],
            "reason": payload["reason"],
            "reviewer_id": payload["reviewer"],
            "reviewed_at": _utc(reviewed_at),
            "tenant_id": event.tenant_id,
        }
        if approval is None or any(
            approval[field] != value for field, value in expected_approval.items()
        ):
            raise RagGoldSetError("label lacks exact independent approval record")
        citations = _citations(payload["citations"])
        if payload["label"] == "approved_answer" and not citations:
            raise RagGoldSetError("gold answer requires at least one evidence citation")
        current_evidence = {item["identity"]: item for item in evidence["current"]}
        if not {item["evidence_identity"] for item in citations} <= allowed_evidence:
            raise RagGoldSetError("gold citation is absent from trusted evidence snapshot")
        if any(
            current_evidence[item["evidence_identity"]]["claim"] != item["claim"]
            for item in citations
        ):
            raise RagGoldSetError("gold citation claim does not match current Evidence")
        row = {
            "label_id": payload["label_id"],
            "label_identity": event.identity,
            "revision": event.revision,
            "query_id": payload["query_id"],
            "answer": payload["answer"],
            "label": payload["label"],
            "citations": citations,
            "reviewer_id": payload["reviewer"],
            "reviewer_authority_sha256": payload["reviewer_authority_sha256"],
            "reviewer_registry_version": trusted_reviewer_registry.version,
            "reviewer_registry_sha256": trusted_reviewer_registry.registry_sha256,
            "approval_id": approval["approval_id"],
            "approval_store_version": approval_store["version"],
            "approval_store_root_sha256": approval_store["root_sha256"],
            "approval_store_checksum": approval_store["checksum"],
            "reason": payload["reason"],
            "reviewed_at": _utc(reviewed_at),
            "available_time": event.available_time,
            "supersedes_label_id": payload["supersedes_label_id"],
            "source_provenance": {
                "source": event.provenance["source"],
                "version": event.provenance["version"],
                "checksum": event.provenance["checksum"],
            },
        }
        if row["label_id"] in by_id:
            raise RagGoldSetError("duplicate label_id")
        by_id[row["label_id"]] = (event, row)

    if set(approvals_by_label) != {event.identity for event, _ in by_id.values()}:
        raise RagGoldSetError("approval store contains extra scoped label approvals")

    _validate_supersession(by_id)
    selected: dict[str, dict[str, Any]] = {}
    for _, row in by_id.values():
        current = selected.get(row["query_id"])
        if current is not None and row["revision"] == current["revision"]:
            raise RagGoldSetError("conflicting gold revisions for one query")
        if current is None or row["revision"] > current["revision"]:
            selected[row["query_id"]] = row
    rows = sorted(selected.values(), key=lambda row: (row["query_id"], row["label_id"]))
    status = "complete" if len(rows) >= policy.minimum_labels else "insufficient_data"
    anchors = sorted(
        (_event_anchor(event) for event, _ in by_id.values()),
        key=lambda item: (item["identity"], _sha256(item)),
    )
    manifest: dict[str, Any] = {
        "kind": "rag-gold-set.v1",
        "status": status,
        "policy": frozen,
        "classification": "human_reviewed_non_evidentiary_gold",
        "eligible_as_evidence": False,
        "authority": {
            "required_reviewer": _REVIEWER,
            "source": "caller_trusted_registry",
            "registry_version": trusted_reviewer_registry.version,
            "registry_sha256": trusted_reviewer_registry.registry_sha256,
        },
        "approval_store": {
            "version": approval_store["version"],
            "root_sha256": approval_store["root_sha256"],
            "checksum": approval_store["checksum"],
            "count": approval_store["count"],
        },
        "evidence_snapshot": {
            "snapshot_id": evidence["snapshot_id"],
            "job_id": evidence["job_id"],
            "snapshot_sha256": evidence["snapshot_sha256"],
            "event_root_sha256": evidence["event_root_sha256"],
            "event_count": evidence["event_count"],
        },
        "input_roots": {
            "labels_sha256": _sha256(anchors),
            "retrievals_sha256": retrieval_input["root_sha256"],
            "feedback_sha256": feedback_input["root_sha256"],
            "evidence_sha256": evidence["event_root_sha256"],
        },
        "input_counts": {
            "labels": len(by_id), "retrievals": retrieval_input["count"],
            "feedback": feedback_input["count"], "evidence": evidence["event_count"],
        },
        "row_count": len(rows),
        "rows_sha256": _sha256(rows),
        "rows": rows,
    }
    manifest["manifest_sha256"] = _sha256(manifest)
    return manifest


def evaluate_rag_retrieval(
    retrievals: Iterable[LearningEvent],
    *,
    gold_manifest: Mapping[str, Any],
    policy: RetrievalEvaluationPolicy,
    trusted_label_events: Iterable[LearningEvent],
    trusted_retrieval_events: Iterable[LearningEvent],
    trusted_feedback_events: Iterable[LearningEvent],
    trusted_evidence_events: Iterable[LearningEvent],
    trusted_reviewer_registry: ReviewerAuthorityRegistry,
    trusted_approval_store: ApprovalStoreSnapshot,
    previous_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate citations/abstention against a verified frozen gold manifest."""

    frozen = policy.canonical()
    evaluation_cutoff = _parse(frozen["query_as_of"], "query_as_of")
    collected = _collect_rag_events(
        retrievals, tenant_id=policy.tenant_id, cutoff=evaluation_cutoff,
        event_type="rag-retrieval.v1",
    )
    _canonical_cutoff_set(
        collected["payloads"], maximum_cutoff=evaluation_cutoff,
        maximum_unique=frozen["max_unique_query_cutoffs"],
    )
    evidence_input = _bounded_visible_events(
        trusted_evidence_events, tenant_id=policy.tenant_id,
        cutoff=evaluation_cutoff, source="evaluation evidence",
    )
    manifest = _verify_manifest(gold_manifest, tenant_id=policy.tenant_id)
    rebuilt = build_rag_gold_set(
        trusted_label_events,
        retrieval_events=trusted_retrieval_events,
        feedback_events=trusted_feedback_events,
        evidence_events=evidence_input,
        policy=RagGoldSetPolicy(**manifest["policy"]),
        trusted_reviewer_registry=trusted_reviewer_registry,
        trusted_approval_store=trusted_approval_store,
        previous_manifest=previous_manifest,
    )
    if rebuilt != manifest:
        raise RagGoldSetError("gold manifest does not match supplied scoped label events")
    if _parse(manifest["policy"]["dataset_as_of"], "gold.dataset_as_of") > _parse(
        frozen["query_as_of"], "query_as_of"
    ):
        raise RagGoldSetError("gold manifest is newer than evaluation cutoff")
    evidence = _build_evidence_snapshot(
        evidence_input, tenant_id=policy.tenant_id, cutoff=evaluation_cutoff,
    )
    visible = collected["payloads"]
    retrieval_snapshots = _snapshot_cache(
        visible, evidence["event_objects"], tenant_id=policy.tenant_id,
        maximum_cutoff=evaluation_cutoff,
        maximum_unique=frozen["max_unique_query_cutoffs"],
    )
    retrieval_snapshots.setdefault(frozen["query_as_of"], evidence)
    by_query: dict[str, dict[str, Any]] = {}
    for item in visible:
        snapshot = retrieval_snapshots[item["query_as_of"]]
        citations = _validate_retrieval_semantics(item, snapshot)
        if _utc(_parse(item["query_as_of"], "retrieval.query_as_of")) != frozen["query_as_of"]:
            raise RagGoldSetError("retrieval query cutoff mismatch")
        if not {c["evidence_identity"] for c in citations} <= set(
            evidence["current_identities"]
        ):
            raise RagGoldSetError("retrieval cites stale, revoked, or unknown evidence")
        if item["query_id"] in by_query:
            raise RagGoldSetError("duplicate retrieval query_id")
        by_query[item["query_id"]] = {
            **item, "citations": citations,
            "_identity": collected["identity_by_query"][item["query_id"]],
            "_evidence_snapshot": snapshot,
        }

    rows = []
    for gold in manifest["rows"]:
        retrieval = by_query.get(gold["query_id"])
        if retrieval is None:
            rows.append(_evaluation_row(gold, None, evidence))
        else:
            rows.append(_evaluation_row(gold, retrieval, evidence))
    rows.sort(key=lambda row: row["query_id"])
    sufficient = (
        manifest["status"] == "complete"
        and len(rows) >= policy.minimum_gold_queries
    )
    answered = [row for row in rows if row["outcome"] == "answered"]
    aligned_rows = [row for row in answered if row["citation_aligned"] is not None]
    decision_rows = [row for row in rows if row["decision_correct"] is not None]
    answer_rows = [row for row in rows if row["answer_exact"] is not None]
    report: dict[str, Any] = {
        "kind": "rag-retrieval-evaluation.v1",
        "status": "complete" if sufficient else "insufficient_data",
        "classification": "evaluation_only",
        "eligible_as_evidence": False,
        "policy": frozen,
        "gold_manifest_sha256": manifest["manifest_sha256"],
        "query_time_evidence": {
            cutoff: {
                "snapshot_id": snapshot["snapshot_id"],
                "job_id": snapshot["job_id"],
                "snapshot_sha256": snapshot["snapshot_sha256"],
                "event_root_sha256": snapshot["event_root_sha256"],
                "event_count": snapshot["event_count"],
                "current": snapshot["current"],
                "lineage_version": "evidence-current-lineage.v1",
            }
            for cutoff, snapshot in sorted(retrieval_snapshots.items())
        },
        "input_root_sha256": collected["root_sha256"],
        "metrics": {
            "gold_query_count": len(rows),
            "answered_count": len(answered),
            "explicit_abstention_count": sum(
                row["outcome"] == "explicit_abstention" for row in rows
            ),
            "missing_count": sum(row["outcome"] == "missing" for row in rows),
            "decision_evaluated_count": len(decision_rows),
            "decision_accuracy": (
                sum(row["decision_correct"] for row in decision_rows)
                / len(decision_rows) if decision_rows else None
            ),
            "citation_alignment_rate": (
                sum(row["citation_aligned"] for row in aligned_rows)
                / len(aligned_rows) if aligned_rows else None
            ),
            "answer_evaluated_count": len(answer_rows),
            "exact_answer_rate": (
                sum(row["answer_exact"] for row in answer_rows) / len(answer_rows)
                if answer_rows else None
            ),
        },
        "rows": rows,
    }
    report["report_sha256"] = _sha256(report)
    return report


def _evaluation_row(
    gold: Mapping[str, Any], retrieval: Mapping[str, Any] | None,
    evaluation_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    if retrieval is None:
        return {
            "query_id": gold["query_id"], "outcome": "missing",
            "abstained": None, "decision_correct": None,
            "answer_exact": None, "citation_aligned": None,
            "gold_label_identity": gold["label_identity"],
            "retrieval_identity": None, "snapshot_id": None, "job_id": None,
            "evidence_lineage": [],
            "query_time_evidence_snapshot_sha256": evaluation_snapshot["snapshot_sha256"],
            "query_time_evidence_root_sha256": evaluation_snapshot["event_root_sha256"],
            "query_time_evidence_count": evaluation_snapshot["event_count"],
            "query_time_evidence_lineage": evaluation_snapshot["current"],
        }
    if retrieval["abstained"]:
        snapshot = retrieval["_evidence_snapshot"]
        return {
            "query_id": gold["query_id"], "outcome": "explicit_abstention",
            "abstained": True,
            "decision_correct": gold["label"] == "must_abstain",
            "answer_exact": None, "citation_aligned": None,
            "gold_label_identity": gold["label_identity"],
            "retrieval_identity": retrieval["_identity"],
            "snapshot_id": retrieval["snapshot_id"], "job_id": retrieval["job_id"],
            "evidence_lineage": [],
            "query_time_evidence_snapshot_sha256": snapshot["snapshot_sha256"],
            "query_time_evidence_root_sha256": snapshot["event_root_sha256"],
            "query_time_evidence_count": snapshot["event_count"],
            "query_time_evidence_lineage": snapshot["current"],
        }
    gold_ids = {item["evidence_identity"] for item in gold["citations"]}
    actual_ids = {item["evidence_identity"] for item in retrieval["citations"]}
    current_by_identity = {
        item["identity"]: item for item in retrieval["_evidence_snapshot"]["current"]
    }
    return {
        "query_id": gold["query_id"], "outcome": "answered", "abstained": False,
        "decision_correct": (
            gold["label"] == "approved_answer"
            and retrieval["answer"] == gold["answer"]
        ),
        "answer_exact": (
            retrieval["answer"] == gold["answer"]
            if gold["label"] == "approved_answer" else None
        ),
        "citation_aligned": bool(actual_ids) and actual_ids <= gold_ids,
        "gold_label_identity": gold["label_identity"],
        "retrieval_identity": retrieval["_identity"],
        "snapshot_id": retrieval["snapshot_id"], "job_id": retrieval["job_id"],
        "evidence_lineage": [
            {
                "identity": identity,
                "claim_sha256": current_by_identity[identity]["claim_sha256"],
                "provenance_checksum": current_by_identity[identity]["provenance_checksum"],
                "snapshot_id": current_by_identity[identity]["snapshot_id"],
                "job_id": current_by_identity[identity]["job_id"],
            }
            for identity in sorted(actual_ids)
        ],
        "query_time_evidence_snapshot_sha256": retrieval["_evidence_snapshot"]["snapshot_sha256"],
        "query_time_evidence_root_sha256": retrieval["_evidence_snapshot"]["event_root_sha256"],
        "query_time_evidence_count": retrieval["_evidence_snapshot"]["event_count"],
        "query_time_evidence_lineage": retrieval["_evidence_snapshot"]["current"],
    }


def _validate_supersession(
    values: Mapping[str, tuple[LearningEvent, dict[str, Any]]]
) -> None:
    successors: Counter[str] = Counter()
    roots_by_query: Counter[str] = Counter()
    heads = set(values)
    for label_id, (_, row) in values.items():
        predecessor = row["supersedes_label_id"]
        if predecessor is None:
            if row["revision"] != 1:
                raise RagGoldSetError("non-initial label revision requires predecessor")
            roots_by_query[row["query_id"]] += 1
            continue
        successors[predecessor] += 1
        heads.discard(predecessor)
        _text(predecessor, "supersedes_label_id")
        seen = {label_id}
        cursor = predecessor
        while cursor is not None:
            if cursor in seen:
                raise RagGoldSetError("gold label supersession cycle")
            seen.add(cursor)
            previous = values.get(cursor)
            if previous is None:
                raise RagGoldSetError("dangling superseded gold label")
            previous_row = previous[1]
            if previous_row["query_id"] != row["query_id"]:
                raise RagGoldSetError("gold label cannot supersede another query")
            if previous_row["revision"] >= row["revision"]:
                raise RagGoldSetError("gold label revisions must increase")
            if previous_row["revision"] + 1 != row["revision"]:
                raise RagGoldSetError("gold label revisions must be continuous")
            cursor = previous_row["supersedes_label_id"]
    queries = {row["query_id"] for _, row in values.values()}
    if any(roots_by_query[query] != 1 for query in queries):
        raise RagGoldSetError("each query must have exactly one gold root")
    if any(count > 1 for count in successors.values()):
        raise RagGoldSetError("gold supersession fork is forbidden")
    heads_by_query = Counter(values[label_id][1]["query_id"] for label_id in heads)
    if any(heads_by_query[query] != 1 for query in queries):
        raise RagGoldSetError("each query must have exactly one gold head")


def _verify_manifest(value: Mapping[str, Any], *, tenant_id: str) -> dict[str, Any]:
    manifest = _clone_exact_json(value, "gold manifest")
    fields = {
        "kind", "status", "policy", "classification", "eligible_as_evidence",
        "authority", "approval_store", "evidence_snapshot", "input_roots",
        "input_counts", "row_count",
        "rows_sha256", "rows",
        "manifest_sha256",
    }
    if set(manifest) != fields or manifest["kind"] != "rag-gold-set.v1":
        raise RagGoldSetError("gold manifest schema or kind is invalid")
    if manifest["classification"] != "human_reviewed_non_evidentiary_gold":
        raise RagGoldSetError("gold classification is invalid")
    if manifest["eligible_as_evidence"] is not False:
        raise RagGoldSetError("gold manifest cannot be Evidence")
    if type(manifest["authority"]) is not dict or set(manifest["authority"]) != {
        "required_reviewer", "source", "registry_version", "registry_sha256"
    } or manifest["authority"]["required_reviewer"] != _REVIEWER or (
        manifest["authority"]["source"] != "caller_trusted_registry"
    ):
        raise RagGoldSetError("gold authority binding is invalid")
    if type(manifest["evidence_snapshot"]) is not dict or set(
        manifest["evidence_snapshot"]
    ) != {
        "snapshot_id", "job_id", "snapshot_sha256",
        "event_root_sha256", "event_count",
    }:
        raise RagGoldSetError("gold evidence snapshot binding is invalid")
    _hex(manifest["evidence_snapshot"]["snapshot_sha256"], "gold.evidence_snapshot")
    if type(manifest["approval_store"]) is not dict or set(
        manifest["approval_store"]
    ) != {"version", "root_sha256", "checksum", "count"}:
        raise RagGoldSetError("gold approval store binding is invalid")
    _hex(manifest["approval_store"]["root_sha256"], "gold approval root")
    _hex(manifest["approval_store"]["checksum"], "gold approval checksum")
    if type(manifest["approval_store"]["count"]) is not int or (
        manifest["approval_store"]["count"] < 0
    ):
        raise RagGoldSetError("gold approval count is invalid")
    if type(manifest["policy"]) is not dict or set(manifest["policy"]) != {
        "tenant_id", "dataset_as_of", "gold_version", "producer_version",
        "minimum_labels", "gold_set_revision", "previous_manifest_sha256",
        "max_unique_query_cutoffs",
        "approval_store_version", "approval_store_root_sha256",
        "approval_store_checksum", "approval_store_count",
    }:
        raise RagGoldSetError("gold policy schema is invalid")
    if manifest["policy"]["tenant_id"] != tenant_id:
        raise RagGoldSetError("gold manifest tenant mismatch")
    if {
        "version": manifest["policy"]["approval_store_version"],
        "root_sha256": manifest["policy"]["approval_store_root_sha256"],
        "checksum": manifest["policy"]["approval_store_checksum"],
        "count": manifest["policy"]["approval_store_count"],
    } != manifest["approval_store"]:
        raise RagGoldSetError("gold policy/approval store binding mismatch")
    if manifest["status"] not in {"complete", "insufficient_data"}:
        raise RagGoldSetError("gold manifest status is invalid")
    if type(manifest["rows"]) is not list or type(manifest["row_count"]) is not int:
        raise RagGoldSetError("gold manifest rows are invalid")
    if manifest["row_count"] != len(manifest["rows"]):
        raise RagGoldSetError("gold manifest row count mismatch")
    if type(manifest["input_roots"]) is not dict or set(manifest["input_roots"]) != {
        "labels_sha256", "retrievals_sha256", "feedback_sha256", "evidence_sha256"
    }:
        raise RagGoldSetError("gold input roots are invalid")
    for root in manifest["input_roots"].values():
        _hex(root, "gold input root")
    if type(manifest["input_counts"]) is not dict or set(manifest["input_counts"]) != {
        "labels", "retrievals", "feedback", "evidence"
    } or any(type(count) is not int or count < 0 for count in manifest["input_counts"].values()):
        raise RagGoldSetError("gold input counts are invalid")
    if manifest["rows_sha256"] != _sha256(manifest["rows"]):
        raise RagGoldSetError("gold rows checksum mismatch")
    unsigned = {key: item for key, item in manifest.items() if key != "manifest_sha256"}
    if manifest["manifest_sha256"] != _sha256(unsigned):
        raise RagGoldSetError("gold manifest checksum mismatch")
    expected_status = (
        "complete"
        if manifest["row_count"] >= manifest["policy"]["minimum_labels"]
        else "insufficient_data"
    )
    if manifest["status"] != expected_status:
        raise RagGoldSetError("gold manifest status/count mismatch")
    approval_ids = set()
    for row in manifest["rows"]:
        if type(row) is not dict or set(row) != {
            "label_id", "label_identity", "revision", "query_id", "label", "answer",
            "citations", "reviewer_id", "reviewer_authority_sha256",
            "reviewer_registry_version", "reviewer_registry_sha256",
            "approval_id", "approval_store_version",
            "approval_store_root_sha256", "approval_store_checksum",
            "reason", "reviewed_at",
            "available_time", "supersedes_label_id", "source_provenance",
        }:
            raise RagGoldSetError("gold row schema is invalid")
        if row["reviewer_id"] != _REVIEWER:
            raise RagGoldSetError("gold row reviewer is invalid")
        _hex(row["reviewer_authority_sha256"], "gold reviewer authority")
        _hex(row["reviewer_registry_sha256"], "gold reviewer registry")
        _text(row["approval_id"], "gold approval_id")
        if row["approval_id"] in approval_ids:
            raise RagGoldSetError("gold rows contain duplicate approval_id")
        approval_ids.add(row["approval_id"])
        _hex(row["approval_store_root_sha256"], "gold approval root")
        _hex(row["approval_store_checksum"], "gold approval checksum")
        if {
            "version": row["approval_store_version"],
            "root_sha256": row["approval_store_root_sha256"],
            "checksum": row["approval_store_checksum"],
        } != {
            "version": manifest["approval_store"]["version"],
            "root_sha256": manifest["approval_store"]["root_sha256"],
            "checksum": manifest["approval_store"]["checksum"],
        }:
            raise RagGoldSetError("gold row approval provenance mismatch")
        if type(row["source_provenance"]) is not dict or set(
            row["source_provenance"]
        ) != {"source", "version", "checksum"}:
            raise RagGoldSetError("gold row source provenance is invalid")
        checksum = row["source_provenance"]["checksum"]
        if type(checksum) is not str or not checksum.startswith("sha256:"):
            raise RagGoldSetError("gold provenance checksum is invalid")
        _hex(checksum.removeprefix("sha256:"), "gold provenance checksum")
        _citations(row["citations"])
    return manifest


def _build_evidence_snapshot(
    events: Iterable[LearningEvent], *, tenant_id: str, cutoff: datetime
) -> dict[str, Any]:
    visible = _bounded_visible_events(
        events, tenant_id=tenant_id, cutoff=cutoff, source="evidence"
    )
    by_identity: dict[str, tuple[LearningEvent, dict[str, Any]]] = {}
    by_entity: dict[str, list[tuple[LearningEvent, dict[str, Any]]]] = {}
    for event in visible:
        if event.schema_version != _SUPPORTED_SCHEMA or event.kind != "evidentiary":
            raise RagGoldSetError("evidence input must be evidentiary learning-event.v1")
        payload = _clone_exact_json(event.payload, "evidence payload")
        if set(payload) != _EVIDENCE_FIELDS:
            raise RagGoldSetError("evidence payload schema is not exact")
        if payload["evidence_id"] != event.entity_id:
            raise RagGoldSetError("evidence_id must match event entity_id")
        if payload["status"] not in {"current", "revoked"}:
            raise RagGoldSetError("evidence status is invalid")
        for field in ("claim", "source_url", "snapshot_id", "job_id"):
            _text(payload[field], f"evidence.{field}")
        if event.identity in by_identity:
            raise RagGoldSetError("duplicate evidence identity")
        by_identity[event.identity] = (event, payload)
        by_entity.setdefault(event.entity_id, []).append((event, payload))
    current: list[dict[str, Any]] = []
    for entity_id, revisions in by_entity.items():
        revisions.sort(key=lambda pair: pair[0].revision)
        for index, (event, payload) in enumerate(revisions):
            if event.revision != index + 1:
                raise RagGoldSetError("evidence revisions must be continuous")
            predecessor = payload["supersedes_identity"]
            if index == 0:
                if predecessor is not None:
                    raise RagGoldSetError("initial evidence cannot supersede an identity")
            elif predecessor != revisions[index - 1][0].identity:
                raise RagGoldSetError("evidence lineage is dangling or forked")
        head_event, head_payload = revisions[-1]
        if head_payload["status"] == "current":
            current.append({
                "entity_id": entity_id, "identity": head_event.identity,
                "revision": head_event.revision,
                "snapshot_id": head_payload["snapshot_id"],
                "job_id": head_payload["job_id"],
                "claim": head_payload["claim"],
                "claim_sha256": _sha256(head_payload["claim"]),
                "source_url_sha256": _sha256(head_payload["source_url"]),
                "provenance_checksum": head_event.provenance["checksum"],
            })
    current.sort(key=lambda item: item["identity"])
    anchors = sorted(
        (_event_anchor(event) for event, _ in by_identity.values()),
        key=lambda item: (item["identity"], _sha256(item)),
    )
    event_root = _sha256(anchors)
    binding_seed = {
        "tenant_id": tenant_id,
        "as_of": _utc(cutoff),
        "event_root_sha256": event_root,
        "lineage_version": "evidence-current-lineage.v1",
    }
    snapshot_id = f"evidence-snapshot:{_sha256(binding_seed)}"
    job_id = f"evidence-query:{_sha256({**binding_seed, 'purpose': 'rag-evaluation'})}"
    unsigned = {
        "tenant_id": tenant_id, "as_of": _utc(cutoff),
        "snapshot_id": snapshot_id, "job_id": job_id,
        "lineage_version": "evidence-current-lineage.v1",
        "current": current,
    }
    return {
        **unsigned,
        "current_identities": [item["identity"] for item in current],
        "snapshot_sha256": _sha256(unsigned),
        "event_root_sha256": event_root,
        "event_count": len(anchors),
        "event_objects": visible,
    }


def _snapshot_cache(
    payloads: Iterable[Mapping[str, Any]], evidence_events: Iterable[LearningEvent], *,
    tenant_id: str, maximum_cutoff: datetime, maximum_unique: int,
) -> dict[str, dict[str, Any]]:
    cutoffs = _canonical_cutoff_set(
        payloads, maximum_cutoff=maximum_cutoff, maximum_unique=maximum_unique
    )
    cache: dict[str, dict[str, Any]] = {}
    for cutoff in cutoffs:
        cache[cutoff] = _build_evidence_snapshot(
            evidence_events, tenant_id=tenant_id, cutoff=_parse(cutoff, "query cutoff")
        )
    return cache


def _canonical_cutoff_set(
    payloads: Iterable[Mapping[str, Any]], *, maximum_cutoff: datetime,
    maximum_unique: int,
) -> tuple[str, ...]:
    cutoffs = set()
    for payload in payloads:
        cutoff = _utc(_parse(payload.get("query_as_of"), "retrieval.query_as_of"))
        if _parse(cutoff, "retrieval.query_as_of") > maximum_cutoff:
            raise RagGoldSetError("retrieval query cutoff exceeds dataset cutoff")
        cutoffs.add(cutoff)
        if len(cutoffs) > maximum_unique:
            raise RagGoldSetError("retrieval input exceeds unique query cutoff limit")
    return tuple(sorted(cutoffs))


def _validate_retrieval_semantics(
    payload: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> list[dict[str, str]]:
    for field in (
        "historical_answer_id", "question", "query_id", "snapshot_id", "job_id",
        "snapshot_sha256", "retrieval_version", "query_as_of",
    ):
        _text(payload.get(field), f"retrieval.{field}")
    _hex(payload["snapshot_sha256"], "retrieval.snapshot_sha256")
    _parse(payload["query_as_of"], "retrieval.query_as_of")
    if type(payload.get("answer")) is not str:
        raise RagGoldSetError("retrieval.answer must be an exact string")
    if len(payload["answer"].encode("utf-8")) > _MAX_FIELD_BYTES:
        raise RagGoldSetError("retrieval.answer exceeds UTF-8 byte limit")
    if type(payload.get("abstained")) is not bool:
        raise RagGoldSetError("retrieval.abstained must be an exact boolean")
    citations = _citations(payload.get("citations"))
    if payload["abstained"] and (payload["answer"] or citations):
        raise RagGoldSetError("abstention cannot carry answer or citations")
    if not payload["abstained"] and (not payload["answer"] or not citations):
        raise RagGoldSetError("answered retrieval requires answer and citations")
    if payload["snapshot_sha256"] != snapshot["snapshot_sha256"]:
        raise RagGoldSetError("retrieval evidence snapshot is stale or forged")
    if payload["snapshot_id"] != snapshot["snapshot_id"]:
        raise RagGoldSetError("retrieval evidence snapshot_id is stale or forged")
    if payload["job_id"] != snapshot["job_id"]:
        raise RagGoldSetError("retrieval evidence job_id is stale or forged")
    current_by_identity = {
        current["identity"]: current for current in snapshot["current"]
    }
    citations = _citations(payload["citations"])
    if not {citation["evidence_identity"] for citation in citations} <= set(
        current_by_identity
    ):
        raise RagGoldSetError("retrieval cites stale, revoked, or unknown evidence")
    if any(
        current_by_identity[citation["evidence_identity"]]["claim"]
        != citation["claim"]
        for citation in citations
    ):
        raise RagGoldSetError("retrieval citation claim does not match current Evidence")
    return citations


def _collect_rag_events(
    events: Iterable[LearningEvent], *, tenant_id: str, cutoff: datetime,
    event_type: str,
) -> dict[str, Any]:
    visible = _bounded_visible_events(
        events, tenant_id=tenant_id, cutoff=cutoff, source=event_type
    )
    expected = (
        _RAG_RETRIEVAL_EVENT_FIELDS
        if event_type == "rag-retrieval.v1"
        else _RAG_FEEDBACK_EVENT_FIELDS
    )
    payloads, anchors, identities, by_identity, identity_by_query = [], [], set(), {}, {}
    for event in visible:
        if event.schema_version != _SUPPORTED_SCHEMA or event.kind != "historical_non_evidentiary":
            raise RagGoldSetError("RAG observation must be historical_non_evidentiary")
        payload = _clone_exact_json(event.payload, f"{event_type} payload")
        if set(payload) != expected or payload["event_type"] != event_type:
            raise RagGoldSetError(f"{event_type} payload schema is not exact")
        if event.identity in identities:
            raise RagGoldSetError(f"duplicate {event_type} identity")
        identities.add(event.identity)
        if payload["historical_answer_id"] != event.entity_id:
            raise RagGoldSetError("RAG observation id must match event entity_id")
        if event_type == "rag-feedback.v1":
            if payload["eligible_as_gold"] is not False or payload["eligible_as_evidence"] is not False:
                raise RagGoldSetError("feedback cannot be gold or Evidence")
            if type(payload["vote"]) is not int:
                raise RagGoldSetError("feedback vote must be an integer")
            for field in (
                "historical_answer_id", "question", "query_id",
                "retrieval_identity", "feedback",
            ):
                _text(payload[field], f"feedback.{field}")
        else:
            for field in ("snapshot_id", "job_id", "snapshot_sha256", "query_as_of"):
                _text(payload[field], f"retrieval.{field}")
            _hex(payload["snapshot_sha256"], "retrieval.snapshot_sha256")
            payload["query_as_of"] = _utc(
                _parse(payload["query_as_of"], "retrieval.query_as_of")
            )
        payloads.append(payload)
        by_identity[event.identity] = payload
        if event_type == "rag-retrieval.v1":
            if payload["query_id"] in identity_by_query:
                # Gold collection can retain repeated runs, but their identity
                # cannot be represented ambiguously in a single evaluation.
                identity_by_query[payload["query_id"]] = None
            else:
                identity_by_query[payload["query_id"]] = event.identity
        anchors.append(_event_anchor(event))
    anchors.sort(key=lambda item: (item["identity"], _sha256(item)))
    payloads.sort(key=lambda item: (
        item["query_id"], item.get("retrieval_identity", ""),
        item["historical_answer_id"],
    ))
    return {
        "payloads": payloads, "root_sha256": _sha256(anchors),
        "count": len(anchors), "identities": sorted(identities),
        "by_identity": by_identity, "identity_by_query": identity_by_query,
    }


def _verify_previous_manifest(
    previous: Mapping[str, Any] | None, policy: Mapping[str, Any],
    registry: ReviewerAuthorityRegistry,
) -> None:
    if policy["gold_set_revision"] == 1:
        if previous is not None:
            raise RagGoldSetError("initial gold set cannot supply previous manifest")
        return
    if previous is None:
        raise RagGoldSetError("previous gold manifest is required")
    verified = _verify_manifest(previous, tenant_id=policy["tenant_id"])
    if verified["manifest_sha256"] != policy["previous_manifest_sha256"]:
        raise RagGoldSetError("previous gold manifest checksum mismatch")
    if verified["policy"]["gold_set_revision"] + 1 != policy["gold_set_revision"]:
        raise RagGoldSetError("gold set manifest revisions must be continuous")
    if verified["policy"]["gold_version"] != policy["gold_version"]:
        raise RagGoldSetError("gold set version cannot change inside a revision chain")
    if _parse(policy["dataset_as_of"], "dataset_as_of") < _parse(
        verified["policy"]["dataset_as_of"], "previous.dataset_as_of"
    ):
        raise RagGoldSetError("gold dataset cutoff cannot move backward")
    if (
        verified["authority"]["registry_version"] != registry.version
        or verified["authority"]["registry_sha256"] != registry.registry_sha256
    ):
        raise RagGoldSetError("reviewer registry change requires a new authorized chain")
    if verified["approval_store"] != {
        "version": policy["approval_store_version"],
        "root_sha256": policy["approval_store_root_sha256"],
        "checksum": policy["approval_store_checksum"],
        "count": policy["approval_store_count"],
    }:
        raise RagGoldSetError(
            "approval store change requires a new explicitly authorized chain"
        )


def _citations(value: Any) -> list[dict[str, str]]:
    if type(value) is not list:
        raise RagGoldSetError("citations must be an exact list")
    result = []
    seen = set()
    for item in value:
        if type(item) is not dict or set(item) != _CITATION_FIELDS:
            raise RagGoldSetError("citation schema is not exact")
        _text(item["evidence_identity"], "citation.evidence_identity")
        _text(item["claim"], "citation.claim")
        if item["evidence_identity"] in seen:
            raise RagGoldSetError("duplicate citation identity")
        seen.add(item["evidence_identity"])
        result.append(dict(item))
    return sorted(result, key=lambda item: (item["evidence_identity"], item["claim"]))


def _bounded_visible_events(
    events: Iterable[LearningEvent], *, tenant_id: str, cutoff: datetime, source: str
) -> list[LearningEvent]:
    visible, total_bytes, total_nodes, count = [], 0, 0, 0
    for event in events:
        if not isinstance(event, LearningEvent):
            raise RagGoldSetError(f"{source} must contain LearningEvent")
        if event.tenant_id != tenant_id:
            continue
        if _parse(event.available_time, f"{source}.available_time") > cutoff:
            continue
        count += 1
        if count > _MAX_EVENTS:
            raise RagGoldSetError(f"{source} exceeds event count limit")
        try:
            size, nodes = _preflight_event(
                event, source=source, byte_budget=_MAX_BYTES - total_bytes,
                node_budget=_MAX_NODES - total_nodes,
            )
        except ValueError as exc:
            raise RagGoldSetError(str(exc)) from exc
        total_bytes += size
        total_nodes += nodes
        visible.append(event)
    return visible


def _clone_exact_json(value: Any, field: str) -> Any:
    state = {"nodes": 0, "node_budget": _MAX_NODES, "source": field}
    chunks, total = [], 0
    try:
        for token in _canonical_value_tokens(value, state=state, depth=1):
            total += len(token.encode("utf-8"))
            if total > _MAX_BYTES:
                raise RagGoldSetError(f"{field} exceeds UTF-8 byte limit")
            chunks.append(token)
        return json.loads("".join(chunks))
    except (ValueError, TypeError, RecursionError) as exc:
        raise RagGoldSetError(str(exc)) from exc


def _sha256(value: Any) -> str:
    try:
        data = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise RagGoldSetError("value is not finite canonical JSON") from None
    return hashlib.sha256(data).hexdigest()


def _text(value: Any, field: str) -> None:
    if type(value) is not str or not value.strip():
        raise RagGoldSetError(f"{field} is required")
    if len(value.encode("utf-8")) > _MAX_FIELD_BYTES:
        raise RagGoldSetError(f"{field} exceeds UTF-8 byte limit")


def _hex(value: Any, field: str) -> None:
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise RagGoldSetError(f"{field} must be lowercase sha256")


def _parse(value: Any, field: str) -> datetime:
    if type(value) is not str:
        raise RagGoldSetError(f"{field} must be ISO-8601")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise RagGoldSetError(f"{field} is invalid") from None
    if parsed.tzinfo is None:
        raise RagGoldSetError(f"{field} must be timezone aware")
    return parsed.astimezone(timezone.utc)


def _utc(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
