"""Deterministic pre-Bronze quality gates for connector documents."""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Iterable

from .data_contracts import DOCUMENT_SCHEMA_VERSION
from .ingestion.base import Document


@dataclass(frozen=True)
class QuarantinedDocument:
    document: Document
    reason_codes: tuple[str, ...]


def validate_documents(
    documents: Iterable[Document], *, now: float, future_tolerance_seconds: float = 300.0,
) -> tuple[list[Document], list[QuarantinedDocument]]:
    accepted: list[Document] = []
    quarantined: list[QuarantinedDocument] = []
    seen_ids: set[str] = set()
    seen_content: set[str] = set()
    for document in documents:
        reasons: list[str] = []
        if document.schema_version != DOCUMENT_SCHEMA_VERSION:
            reasons.append("schema_version_mismatch")
        if not document.id.strip(): reasons.append("missing_id")
        if not document.source.strip(): reasons.append("missing_source")
        if not document.kind.strip(): reasons.append("missing_kind")
        if not document.text.strip(): reasons.append("missing_text")
        if not math.isfinite(float(document.ts)) or float(document.ts) < 0:
            reasons.append("invalid_timestamp")
        elif document.ts > now + future_tolerance_seconds:
            reasons.append("future_timestamp")
        content_key = hashlib.sha256(
            f"{document.source}\0{document.url}\0{document.text}".encode("utf-8")
        ).hexdigest()
        if document.id in seen_ids: reasons.append("duplicate_id_in_batch")
        if content_key in seen_content: reasons.append("duplicate_content_in_batch")
        seen_ids.add(document.id)
        seen_content.add(content_key)
        if reasons:
            quarantined.append(QuarantinedDocument(document, tuple(reasons)))
        else:
            accepted.append(document)
    return accepted, quarantined
