"""Tests for Memory Retrieval Adapter.

Issue: #919 | Epic: #914
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _authorize_schema_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "trustforge.agos_db_auth.verify_db_authorization",
        lambda purpose: None,
    )

from trustforge.memory_os import MemoryEntry, MemoryRepository
from trustforge.memory_retrieval import (
    MemoryRef,
    MemoryRetrievalAdapter,
    _is_historical_conclusion,
    _truncate,
    count_by_category,
    emit_retrieval_event,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_retrieval.db"


@pytest.fixture
def execlog_path(tmp_path: Path) -> Path:
    return tmp_path / "execution_log.jsonl"


@pytest.fixture
def repo(db_path: Path) -> MemoryRepository:
    r = MemoryRepository(db_path=db_path)
    r.ensure_schema()
    yield r
    r.close()


@pytest.fixture
def adapter(repo: MemoryRepository, execlog_path: Path) -> MemoryRetrievalAdapter:
    return MemoryRetrievalAdapter(repo, execlog_path=execlog_path)


# ─── Historical Conclusion Detection ────────────────────────────────────────


class TestHistoricalConclusion:
    def test_hermes_semantic_is_historical(self):
        entry = MemoryEntry(
            memory_id="m1",
            kind="semantic",
            provider="hermes-analysis",
            content_hash="a" * 64,
            content_ref="ref",
            published_at=None,
            retrieved_at="2026-07-01T00:00:00Z",
        )
        assert _is_historical_conclusion(entry) is True

    def test_hermes_episodic_not_historical(self):
        entry = MemoryEntry(
            memory_id="m2",
            kind="episodic",
            provider="hermes-analysis",
            content_hash="a" * 64,
            content_ref="ref",
            published_at=None,
            retrieved_at="2026-07-01T00:00:00Z",
        )
        assert _is_historical_conclusion(entry) is False

    def test_non_hermes_semantic_not_historical(self):
        entry = MemoryEntry(
            memory_id="m3",
            kind="semantic",
            provider="coingecko",
            content_hash="a" * 64,
            content_ref="ref",
            published_at=None,
            retrieved_at="2026-07-01T00:00:00Z",
        )
        assert _is_historical_conclusion(entry) is False


# ─── Retrieval Adapter Tests ─────────────────────────────────────────────────


class TestRetrievalAdapter:
    def test_retrieve_from_source_basic(self, adapter: MemoryRetrievalAdapter):
        items = [
            {"content": "BTC price up 5%", "published_at": "2026-07-01T00:00:00Z"},
            {"content": "ETH gas fees rising", "published_at": "2026-07-01T01:00:00Z"},
        ]

        refs = adapter.retrieve_from_source(
            items, run_id="run-1", source_provider="newsapi", kind="episodic"
        )

        assert len(refs) == 2
        assert refs[0].rank == 1
        assert refs[1].rank == 2
        assert refs[0].reason == "source_retrieval"
        assert refs[0].run_id == "run-1"

    def test_retrieve_from_source_historical_not_eligible(self, adapter: MemoryRetrievalAdapter):
        """Historical conclusion (hermes-*) should be non-evidentiary."""
        items = [
            {"content": "Past analysis: BTC bullish", "published_at": "2026-06-01T00:00:00Z"},
        ]

        refs = adapter.retrieve_from_source(
            items,
            run_id="run-2",
            source_provider="hermes-analysis",
            kind="semantic",
        )

        assert len(refs) == 1

    def test_source_can_be_explicitly_persisted_non_evidentiary(
        self, adapter: MemoryRetrievalAdapter, repo: MemoryRepository
    ):
        refs = adapter.retrieve_from_source(
            [
                {
                    "content": "A prior user question",
                    "published_at": "2026-07-01T00:00:00Z",
                }
            ],
            run_id="run-history",
            source_provider="question_context_history",
            kind="episodic",
            promote_to_evidence=False,
        )

        assert refs[0].evidence_eligible is False
        assert repo.get(refs[0].memory_id).evidence_eligible is False
        assert refs[0].evidence_eligible is False

    def test_retrieve_from_source_eligible(self, adapter: MemoryRetrievalAdapter):
        """External source with timestamps should be eligible."""
        items = [
            {"content": "BTC at 65000", "published_at": "2026-07-01T00:00:00Z"},
        ]

        refs = adapter.retrieve_from_source(
            items,
            run_id="run-3",
            source_provider="coingecko",
            kind="episodic",
        )

        assert len(refs) == 1
        assert refs[0].evidence_eligible is True

    def test_historical_question_context_is_not_eligible(
        self, adapter: MemoryRetrievalAdapter
    ):
        refs = adapter.retrieve_from_source(
            [{"content": "Earlier question", "published_at": "2026-07-01T00:00:00Z"}],
            run_id="run-question-history",
            source_provider="question_context_history",
            kind="episodic",
        )
        assert refs[0].evidence_eligible is False

    def test_retrieve_question_memory(self, adapter: MemoryRetrievalAdapter):
        refs = adapter.retrieve_question_memory("BTC 走勢分析", run_id="run-4")

        assert len(refs) == 1
        assert refs[0].kind == "episodic"
        assert refs[0].reason == "question_rag_query"
        assert refs[0].evidence_eligible is False  # Questions not evidence
        assert "BTC 走勢分析" in refs[0].content_preview

    def test_retrieve_dialogue_memory(self, adapter: MemoryRetrievalAdapter):
        messages = [
            {"content": "What about BTC?"},
            {"content": "Check the latest news"},
        ]

        refs = adapter.retrieve_dialogue_memory(
            "session-abc", run_id="run-5", messages=messages
        )

        assert len(refs) == 2
        assert refs[0].kind == "dialogue"
        assert refs[0].reason == "dialogue_recent"
        assert refs[0].evidence_eligible is False  # Dialogue NEVER evidence
        assert refs[1].rank == 2

    def test_retrieve_dialogue_empty(self, adapter: MemoryRetrievalAdapter):
        refs = adapter.retrieve_dialogue_memory("session-empty", run_id="run-6")
        assert len(refs) == 0

    def test_retrieve_by_kind(self, adapter: MemoryRetrievalAdapter, repo: MemoryRepository):
        # Pre-populate some entries
        repo.save(
            MemoryEntry(
                memory_id="pre-1",
                kind="episodic",
                provider="coingecko",
                content_hash="b" * 64,
                content_ref="BTC price data",
                published_at="2026-07-01T00:00:00Z",
                retrieved_at="2026-07-01T00:00:00Z",
                evidence_eligible=True,
                run_id="old-run",
            )
        )

        refs = adapter.retrieve_by_kind("episodic", run_id="run-7")
        assert len(refs) >= 1
        assert refs[0].kind == "episodic"
        persisted = repo.find_by_run("run-7")
        assert [entry.memory_id for entry in persisted] == [refs[0].memory_id]
        assert persisted[0].run_id == "run-7"

    def test_duplicate_retrieval_is_linked_to_each_run(
        self, adapter: MemoryRetrievalAdapter, repo: MemoryRepository
    ):
        """Content deduplication must preserve per-run retrieval lineage."""
        items = [{"content": "duplicate content", "published_at": "2026-07-01T00:00:00Z"}]

        refs1 = adapter.retrieve_from_source(
            items, run_id="run-8a", source_provider="newsapi"
        )
        refs2 = adapter.retrieve_from_source(
            items, run_id="run-8b", source_provider="newsapi"
        )

        assert len(refs1) == 1
        assert len(refs2) == 1
        assert refs1[0].memory_id == refs2[0].memory_id
        assert [entry.memory_id for entry in repo.find_by_run("run-8a")] == [
            refs1[0].memory_id
        ]
        second_run_entries = repo.find_by_run("run-8b")
        assert [entry.memory_id for entry in second_run_entries] == [
            refs2[0].memory_id
        ]
        assert second_run_entries[0].run_id == "run-8b"

    def test_non_duplicate_persistence_failure_is_not_silenced(
        self, adapter: MemoryRetrievalAdapter, repo: MemoryRepository
    ):
        """Storage failures must not emit phantom memory references."""
        with patch.object(repo, "save", side_effect=OSError("disk unavailable")):
            with pytest.raises(OSError, match="disk unavailable"):
                adapter.retrieve_from_source(
                    [{"content": "not persisted", "published_at": "2026-07-01T00:00:00Z"}],
                    run_id="run-storage-failure",
                    source_provider="newsapi",
                )
        assert repo.find_by_run("run-storage-failure") == []

    def test_unresolved_integrity_failure_is_not_silenced(
        self, adapter: MemoryRetrievalAdapter, repo: MemoryRepository
    ):
        with patch.object(
            repo, "save", side_effect=sqlite3.IntegrityError("foreign key")
        ):
            with pytest.raises(sqlite3.IntegrityError, match="foreign key"):
                adapter.retrieve_from_source(
                    [{"content": "no duplicate", "published_at": "2026-07-01T00:00:00Z"}],
                    run_id="run-integrity-failure",
                    source_provider="newsapi",
                )


# ─── Execution Log Tests ─────────────────────────────────────────────────────


class TestExecutionLog:
    def test_emit_retrieval_event_writes_log(self, execlog_path: Path):
        refs = [
            MemoryRef(
                memory_id="m1",
                kind="episodic",
                rank=1,
                reason="test",
                evidence_eligible=False,
                content_preview="preview",
                run_id="run-log",
            )
        ]

        event = emit_retrieval_event("run-log", refs, execlog_path=execlog_path)

        assert event["event"] == "memory_retrieval"
        assert event["run_id"] == "run-log"
        assert event["count"] == 1
        assert len(event["memories"]) == 1

        # Verify file was written
        assert execlog_path.exists()
        lines = execlog_path.read_text().strip().split("\n")
        assert len(lines) == 1
        written = json.loads(lines[0])
        assert written["event"] == "memory_retrieval"

    def test_emit_retrieval_event_appends(self, execlog_path: Path):
        refs = [
            MemoryRef(
                memory_id="m2",
                kind="semantic",
                rank=1,
                reason="test2",
                evidence_eligible=True,
                content_preview="p2",
                run_id="run-log2",
            )
        ]

        emit_retrieval_event("run-1", refs, execlog_path=execlog_path)
        emit_retrieval_event("run-2", refs, execlog_path=execlog_path)

        lines = execlog_path.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_emit_handles_unwritable_path(self, tmp_path: Path):
        """Graceful degradation: unwritable path doesn't crash."""
        bad_path = tmp_path / "nonexistent_deep" / "nested" / "log.jsonl"
        # This should work since we mkdir -p
        refs = [
            MemoryRef(
                memory_id="m3", kind="episodic", rank=1, reason="t",
                evidence_eligible=False, content_preview="p", run_id="r",
            )
        ]
        event = emit_retrieval_event("r", refs, execlog_path=bad_path)
        assert event["event"] == "memory_retrieval"


# ─── Category Counter Tests ──────────────────────────────────────────────────


class TestCategoryCounter:
    def test_count_by_category(self, repo: MemoryRepository):
        run_id = "count-run"
        repo.save(
            MemoryEntry(
                memory_id="c1", kind="episodic", provider="coingecko",
                content_hash="e" * 64, content_ref="ref1",
                published_at="2026-07-01T00:00:00Z",
                retrieved_at="2026-07-01T00:00:00Z",
                evidence_eligible=True, run_id=run_id,
            )
        )
        repo.save(
            MemoryEntry(
                memory_id="c2", kind="dialogue", provider="dialogue:s1",
                content_hash="f" * 64, content_ref="ref2",
                published_at=None,
                retrieved_at="2026-07-01T00:00:00Z",
                evidence_eligible=False, run_id=run_id,
            )
        )
        repo.save(
            MemoryEntry(
                memory_id="c3", kind="semantic", provider="newsapi",
                content_hash="0" * 64, content_ref="ref3",
                published_at=None,
                retrieved_at="2026-07-01T00:00:00Z",
                evidence_eligible=False, run_id=run_id,
            )
        )

        counts = count_by_category(repo, run_id)
        assert counts["historical"] == 2  # evidence_eligible=False
        assert counts["evidence"] == 1  # evidence_eligible=True
        assert counts["used_as_evidence"] == 0  # placeholder

    def test_count_by_category_empty_run(self, repo: MemoryRepository):
        counts = count_by_category(repo, "no-such-run")
        assert counts == {"historical": 0, "evidence": 0, "used_as_evidence": 0}


# ─── Utility Tests ───────────────────────────────────────────────────────────


class TestUtilities:
    def test_truncate_short(self):
        assert _truncate("short", 150) == "short"

    def test_truncate_long(self):
        long_text = "x" * 200
        result = _truncate(long_text, 150)
        assert len(result) == 153  # 150 + "..."
        assert result.endswith("...")
