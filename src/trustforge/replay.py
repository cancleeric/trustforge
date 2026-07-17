"""Point-in-time source snapshots for Hermes historical replay.

Snapshots are captured forward from the moment this module is deployed.  The
system must not reconstruct a past day from today's cache: doing so leaks future
information and would make calibration claims invalid.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import time
from typing import Any, Iterable

from .ingestion.cache import (
    CacheBackend,
    CacheWriteResult,
    cache_get,
    cache_key,
    cache_set_if_newer,
)
from .schema import iso_utc

SOURCE_SNAPSHOT_HISTORY_SOURCE = "__source_snapshot_history__"
SOURCE_SNAPSHOT_BACKFILL_SOURCE = "__source_snapshot_backfill__"
SOURCE_SNAPSHOT_HISTORY_TTL_SECONDS = 5 * 366 * 24 * 60 * 60


def source_snapshot_history_key(coin: str, date_str: str) -> str:
    return cache_key(SOURCE_SNAPSHOT_HISTORY_SOURCE, f"{coin.upper()}:{date_str}")


def source_snapshot_backfill_key(coin: str, date_str: str) -> str:
    """Keep retrieved-later archives isolated from forward-captured snapshots."""
    return cache_key(SOURCE_SNAPSHOT_BACKFILL_SOURCE, f"{coin.upper()}:{date_str}")


def capture_source_snapshot(
    backend: CacheBackend, coin: str, source_names: Iterable[str], *, captured_at: float,
) -> CacheWriteResult:
    """Persist the exact cache inputs available at a UTC daily snapshot boundary.

    Each source retains its own `fetched_at`; empty or absent sources are listed
    explicitly.  The caller must supply the capture time once per cycle so all
    coins share a stable point-in-time boundary.
    """
    date = datetime.fromtimestamp(captured_at, tz=timezone.utc).strftime("%Y-%m-%d")
    sources: list[dict[str, Any]] = []
    missing: list[str] = []
    for name in sorted(set(source_names)):
        entry = cache_get(backend, cache_key(name, coin))
        if entry is None:
            missing.append(name)
            continue
        documents = []
        for raw_document in entry.get("docs") or []:
            if not isinstance(raw_document, dict):
                continue
            document = dict(raw_document)
            # `ts` is the normalized source publication time.  Keep the raw
            # field too, but expose an explicit archive contract so replay
            # consumers cannot confuse publication time with fetch time.
            document["published_at"] = iso_utc(float(document.get("ts", 0.0) or 0.0)) or None
            documents.append(document)
        sources.append({
            "source": name,
            "fetched_at": float(entry.get("fetched_at", 0.0) or 0.0),
            "documents": documents,
        })
    snapshot = {
        "coin": coin.upper(),
        "snapshot_at": iso_utc(captured_at),
        "snapshot_epoch": captured_at,
        "sources": sources,
        "missing_sources": missing,
    }
    # A primary-only write makes replay evidence portable across workers.  A
    # local fallback is useful for development cache reads, but not sufficient
    # evidence for a competition historical claim.
    return cache_set_if_newer(
        backend, source_snapshot_history_key(coin, date), [snapshot],
        fetched_at=captured_at, ttl_seconds=SOURCE_SNAPSHOT_HISTORY_TTL_SECONDS,
        allow_json_fallback=False,
    )


def load_source_snapshot(
    backend: CacheBackend,
    coin: str,
    date: str,
    *,
    at_or_before: float | None = None,
    archive_type: str | None = None,
) -> dict[str, Any] | None:
    """Load a captured source set without fetching or synthesizing data.

    ``at_or_before`` is the formal run boundary.  A snapshot captured after it
    is deliberately unavailable, even when its daily key matches the requested
    date.  This closes the common same-day future-leakage hole.
    """
    keys = [source_snapshot_history_key(coin, date)]
    if archive_type == "backfilled_archive":
        keys = [source_snapshot_backfill_key(coin, date), source_snapshot_history_key(coin, date)]
    else:
        keys.append(source_snapshot_backfill_key(coin, date))
    entry = None
    snapshot = None
    for key in keys:
        candidate_entry = cache_get(backend, key)
        docs = candidate_entry.get("docs") if candidate_entry else None
        if not docs or not isinstance(docs[0], dict):
            continue
        candidate = dict(docs[0])
        if archive_type is not None and candidate.get("archive_type") != archive_type:
            continue
        entry = candidate_entry
        snapshot = candidate
        break
    docs = entry.get("docs") if entry else None
    if not docs or snapshot is None:
        return None
    snapshot_epoch = float(snapshot.get("snapshot_epoch", 0.0) or 0.0)
    if at_or_before is not None and (not snapshot_epoch or snapshot_epoch > at_or_before):
        return None
    return snapshot


def store_backfilled_source_snapshot(
    backend: CacheBackend, coin: str, date: str, sources: list[dict[str, Any]], *, snapshot_epoch: float,
    provider_manifest: dict[str, Any], retrieved_at: float | None = None,
) -> CacheWriteResult:
    """Persist a historical source slice without pretending it was fetched then.

    ``snapshot_epoch`` is the formal historical boundary. ``retrieved_at`` is
    today (or another actual retrieval timestamp), and is deliberately kept
    separate. Every document must carry a real publication timestamp no later
    than the boundary; otherwise the whole slice is rejected.
    """
    if datetime.fromtimestamp(snapshot_epoch, tz=timezone.utc).strftime("%Y-%m-%d") != date:
        raise ValueError("backfill date must match snapshot_epoch UTC date")
    normalized_sources: list[dict[str, Any]] = []
    for source in sources:
        name = str(source.get("source", ""))
        documents = source.get("documents")
        if not name or not isinstance(documents, list):
            raise ValueError("backfill source requires name and document list")
        copied = []
        for document in documents:
            required = ("published_at", "retrieved_at", "provider", "license", "content_sha256")
            if not isinstance(document, dict) or not all(document.get(field) for field in required):
                raise ValueError("backfilled documents require published_at, retrieved_at, provider, license, content_sha256")
            # Callers supply ISO publication time; only past information is legal.
            published = datetime.fromisoformat(str(document["published_at"]).replace("Z", "+00:00")).timestamp()
            if published > snapshot_epoch:
                raise ValueError("backfilled document crosses historical run boundary")
            hash_payload = {key: value for key, value in document.items() if key != "content_sha256"}
            actual_hash = hashlib.sha256(
                json.dumps(hash_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if str(document["content_sha256"]) != actual_hash:
                raise ValueError("backfilled document content hash mismatch")
            copied.append(dict(document))
        normalized_sources.append({"source": name, "fetched_at": None, "documents": copied})
    key = source_snapshot_backfill_key(coin, date)
    existing_entry = cache_get(backend, key)
    existing = None
    if existing_entry and isinstance(existing_entry.get("docs"), list) and existing_entry["docs"]:
        candidate = existing_entry["docs"][0]
        if isinstance(candidate, dict) and candidate.get("archive_type") == "backfilled_archive":
            existing = candidate
    if existing:
        by_source = {str(item.get("source")): dict(item) for item in existing.get("sources", []) if isinstance(item, dict) and item.get("source")}
        for incoming in normalized_sources:
            name = incoming["source"]
            current = by_source.get(name, {"source": name, "fetched_at": None, "documents": []})
            documents = {
                str(document.get("content_sha256")): document
                for document in current.get("documents", []) if isinstance(document, dict) and document.get("content_sha256")
            }
            documents.update({str(document["content_sha256"]): document for document in incoming["documents"]})
            current["documents"] = list(documents.values())
            by_source[name] = current
        normalized_sources = list(by_source.values())
        providers = {
            json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")): item
            for item in (existing.get("provider_manifest", {}).get("providers", []) + provider_manifest.get("providers", []))
            if isinstance(item, dict)
        }
        provider_manifest = {"providers": list(providers.values())}
    manifest_text = json.dumps(provider_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    snapshot = {
        "coin": coin.upper(), "snapshot_at": iso_utc(snapshot_epoch), "snapshot_epoch": snapshot_epoch,
        "archive_type": "backfilled_archive", "retrieved_at": iso_utc(retrieved_at or time.time()),
        "provider_manifest": provider_manifest, "provider_manifest_sha256": hashlib.sha256(manifest_text.encode()).hexdigest(),
        "sources": normalized_sources, "missing_sources": [],
    }
    return cache_set_if_newer(
        backend, key, [snapshot], fetched_at=retrieved_at or time.time(),
        ttl_seconds=SOURCE_SNAPSHOT_HISTORY_TTL_SECONDS, allow_json_fallback=False,
    )
