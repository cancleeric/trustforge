"""Tests for Memory OS schema, migration, and repository.

Issue: #916 | Epic: #914
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from trustforge.memory_os import (
    VALID_KINDS,
    VALID_RELATIONS,
    MemoryEntry,
    MemoryLink,
    MemoryRepository,
    memory_content_hash,
    rollback,
    _upgrade as upgrade,
    validate_evidence_eligible,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_memory.db"


@pytest.fixture
def repo(db_path: Path) -> MemoryRepository:
    r = MemoryRepository(db_path=db_path)
    r.ensure_schema()
    yield r
    r.close()


def _make_entry(**kwargs) -> MemoryEntry:
    defaults = {
        "memory_id": "",
        "kind": "episodic",
        "provider": "coingecko",
        "content_hash": "a" * 64,
        "content_ref": "ref://test",
        "published_at": "2026-07-01T00:00:00Z",
        "retrieved_at": "2026-07-01T00:01:00Z",
        "evidence_eligible": False,
    }
    defaults.update(kwargs)
    return MemoryEntry(**defaults)


# ─── Migration Tests ─────────────────────────────────────────────────────────


class TestMigration:
    def test_upgrade_creates_tables(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        upgrade(conn)

        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "memory_entries" in tables
        assert "memory_links" in tables
        assert "_meta" in tables
        conn.close()

    def test_upgrade_idempotent(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        upgrade(conn)
        upgrade(conn)  # second call should be no-op
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "memory_entries" in tables
        conn.close()

    def test_rollback_drops_tables(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        upgrade(conn)
        rollback(conn)

        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "memory_entries" not in tables
        assert "memory_links" not in tables
        conn.close()


# ─── Repository CRUD Tests ───────────────────────────────────────────────────


class TestRepositoryCRUD:
    def test_save_and_get_roundtrip(self, repo: MemoryRepository):
        entry = _make_entry()
        repo.save(entry)

        result = repo.get(entry.memory_id)
        assert result is not None
        assert result.kind == "episodic"
        assert result.provider == "coingecko"
        assert result.content_hash == "a" * 64
        assert result.evidence_eligible is False

    def test_save_duplicate_raises_integrity_error(self, repo: MemoryRepository):
        entry1 = _make_entry(provider="src1", content_hash="b" * 64)
        entry2 = _make_entry(provider="src1", content_hash="b" * 64)
        repo.save(entry1)

        with pytest.raises(sqlite3.IntegrityError, match="duplicate"):
            repo.save(entry2)

    def test_get_nonexistent_returns_none(self, repo: MemoryRepository):
        assert repo.get("nonexistent-id") is None

    def test_find_by_kind(self, repo: MemoryRepository):
        repo.save(_make_entry(kind="episodic", content_hash="c" * 64))
        repo.save(_make_entry(kind="semantic", content_hash="d" * 64))
        repo.save(_make_entry(kind="episodic", content_hash="e" * 64))

        results = repo.find_by_kind("episodic")
        assert len(results) == 2
        assert all(r.kind == "episodic" for r in results)

    def test_find_by_run(self, repo: MemoryRepository):
        run_id = "run-123"
        repo.save(_make_entry(run_id=run_id, content_hash="f" * 64))
        repo.save(_make_entry(run_id=run_id, content_hash="0" * 64))
        repo.save(_make_entry(run_id="other-run", content_hash="1" * 64))

        results = repo.find_by_run(run_id)
        assert len(results) == 2
        assert all(r.run_id == run_id for r in results)

    def test_find_eligible_evidence(self, repo: MemoryRepository):
        # Eligible entry
        repo.save(
            _make_entry(
                content_hash="2" * 64,
                evidence_eligible=True,
                kind="episodic",
                provider="coingecko",
                published_at="2026-07-01T00:00:00Z",
            )
        )
        # Non-eligible entry
        repo.save(_make_entry(content_hash="3" * 64, evidence_eligible=False))

        results = repo.find_eligible_evidence()
        assert len(results) == 1
        assert results[0].evidence_eligible is True

    def test_invalid_kind_raises(self, repo: MemoryRepository):
        with pytest.raises(ValueError, match="invalid memory kind"):
            repo.save(_make_entry(kind="invalid_kind"))


# ─── Evidence Eligibility Tests ──────────────────────────────────────────────


class TestEvidenceEligibility:
    def test_valid_entry_passes(self):
        entry = _make_entry(
            kind="episodic",
            provider="coingecko",
            published_at="2026-07-01T00:00:00Z",
            retrieved_at="2026-07-01T00:01:00Z",
            content_hash="a" * 64,
            evidence_eligible=True,
        )
        # Should not raise
        validate_evidence_eligible(entry)

    def test_missing_provider_fails(self):
        entry = _make_entry(provider="", evidence_eligible=True)
        with pytest.raises(ValueError, match="provider is required"):
            validate_evidence_eligible(entry)

    def test_missing_published_at_fails(self):
        entry = _make_entry(published_at=None, evidence_eligible=True)
        with pytest.raises(ValueError, match="published_at is required"):
            validate_evidence_eligible(entry)

    def test_missing_retrieved_at_fails(self):
        entry = _make_entry(retrieved_at="", evidence_eligible=True)
        with pytest.raises(ValueError, match="retrieved_at is required"):
            validate_evidence_eligible(entry)

    def test_invalid_hash_fails(self):
        entry = _make_entry(content_hash="short", evidence_eligible=True)
        with pytest.raises(ValueError, match="valid SHA-256"):
            validate_evidence_eligible(entry)

    def test_dialogue_kind_fails(self):
        entry = _make_entry(kind="dialogue", evidence_eligible=True)
        with pytest.raises(ValueError, match="dialogue memory cannot be evidence"):
            validate_evidence_eligible(entry)

    def test_historical_conclusion_fails(self):
        entry = _make_entry(
            kind="semantic",
            provider="hermes-analysis",
            evidence_eligible=True,
        )
        with pytest.raises(ValueError, match="historical conclusions"):
            validate_evidence_eligible(entry)

    def test_save_with_evidence_eligible_validates(self, repo: MemoryRepository):
        """Save with evidence_eligible=True triggers validation."""
        entry = _make_entry(
            kind="dialogue",
            content_hash="4" * 64,
            evidence_eligible=True,
        )
        with pytest.raises(ValueError, match="dialogue memory cannot be evidence"):
            repo.save(entry)


# ─── Link Tests ──────────────────────────────────────────────────────────────


class TestLinks:
    def test_link_creation_roundtrip(self, repo: MemoryRepository):
        e1 = _make_entry(content_hash="5" * 64)
        e2 = _make_entry(content_hash="6" * 64, provider="newsapi")
        repo.save(e1)
        repo.save(e2)

        repo.link(e1.memory_id, e2.memory_id, "derived_from")

        links = repo.get_links(e1.memory_id)
        assert len(links) == 1
        assert links[0].from_memory_id == e1.memory_id
        assert links[0].to_memory_id == e2.memory_id
        assert links[0].relation == "derived_from"

    def test_self_link_rejected(self, repo: MemoryRepository):
        e1 = _make_entry(content_hash="7" * 64)
        repo.save(e1)

        with pytest.raises(ValueError, match="self-link"):
            repo.link(e1.memory_id, e1.memory_id, "supports")

    def test_invalid_relation_rejected(self, repo: MemoryRepository):
        e1 = _make_entry(content_hash="8" * 64)
        e2 = _make_entry(content_hash="9" * 64, provider="social")
        repo.save(e1)
        repo.save(e2)

        with pytest.raises(ValueError, match="invalid relation"):
            repo.link(e1.memory_id, e2.memory_id, "unknown_relation")

    def test_duplicate_link_rejected(self, repo: MemoryRepository):
        e1 = _make_entry(content_hash="aa" * 32)
        e2 = _make_entry(content_hash="bb" * 32, provider="onchain")
        repo.save(e1)
        repo.save(e2)

        repo.link(e1.memory_id, e2.memory_id, "supports")

        with pytest.raises(sqlite3.IntegrityError):
            repo.link(e1.memory_id, e2.memory_id, "supports")

    def test_get_links_bidirectional(self, repo: MemoryRepository):
        e1 = _make_entry(content_hash="cc" * 32)
        e2 = _make_entry(content_hash="dd" * 32, provider="regulatory")
        repo.save(e1)
        repo.save(e2)

        repo.link(e1.memory_id, e2.memory_id, "supersedes")

        # Query from e2 should also find the link
        links = repo.get_links(e2.memory_id)
        assert len(links) == 1


# ─── Content Hash Tests ──────────────────────────────────────────────────────


class TestContentHash:
    def test_dict_hash_deterministic(self):
        data = {"key": "value", "num": 42}
        h1 = memory_content_hash(data)
        h2 = memory_content_hash(data)
        assert h1 == h2
        assert len(h1) == 64

    def test_string_hash_deterministic(self):
        text = "hello world"
        h1 = memory_content_hash(text)
        h2 = memory_content_hash(text)
        assert h1 == h2

    def test_sort_keys_matters(self):
        data1 = {"b": 1, "a": 2}
        data2 = {"a": 2, "b": 1}
        # Should produce same hash due to sort_keys=True
        assert memory_content_hash(data1) == memory_content_hash(data2)

    def test_different_content_different_hash(self):
        assert memory_content_hash("hello") != memory_content_hash("world")
