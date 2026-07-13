import runpy
from pathlib import Path
from types import SimpleNamespace

from trustforge.question_bank import all_cases
from trustforge.schema import QuestionType


_RUNNER = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts" / "run_question_bank.py")
)


def test_question_bank_is_large_deterministic_and_covers_all_official_types():
    cases = all_cases()
    assert len(cases) == 240
    assert len({case.id for case in cases}) == 240
    assert {case.question_type for case in cases} == set(QuestionType)
    assert sum(case.question_type == QuestionType.MULTI_SOURCE for case in cases) == 60
    assert sum(case.question_type == QuestionType.HYPOTHESIS for case in cases) == 60
    assert sum(case.question_type == QuestionType.COMPARISON for case in cases) == 120


def test_question_bank_exercises_government_crawler_and_execution_observability():
    tags = {tag for case in all_cases() for tag in case.coverage_tags}
    assert {"government", "crawler", "execution_log", "five_year_lineage"} <= tags
    assert all(case.origin.startswith("TrustForge original") for case in all_cases())


def test_question_runner_rejects_incomplete_source_execution_event_contract():
    report = SimpleNamespace(market_judgment="judgment", key_basis=["basis"], limits=["limit"], could_flip=["flip"])
    evidence = [SimpleNamespace(to_dict=lambda: {
        "source": "official-ohlcv", "fetched_at": "2026-01-01T00:00:00Z",
        "content_reference": "reference", "related_claim": "claim",
    })]
    incomplete_log = SimpleNamespace(
        events=[{"tool": "ingestion.source", "params": {"source": "news"}}], elapsed=lambda: 1.0,
    )
    complete_log = SimpleNamespace(
        events=[{"tool": "ingestion.source", "params": {
            "source": "news", "kind": "news", "duration_ms": 12.0,
            "document_count": 1, "outcome": "ok",
        }}], elapsed=lambda: 1.0,
    )

    assert "source_execution_event_contract" in _RUNNER["_validate"](report, evidence, incomplete_log)
    assert _RUNNER["_validate"](report, evidence, complete_log) == []
