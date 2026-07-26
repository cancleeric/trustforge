import runpy
from pathlib import Path
from types import SimpleNamespace

from itertools import combinations

from trustforge.question_bank import _COMPARISONS, _MULTI_SOURCE, all_cases
from trustforge.schema import COIN_POOL, QuestionType


_RUNNER = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts" / "run_question_bank.py")
)


def test_question_bank_is_large_deterministic_and_covers_all_official_coins():
    """題庫規模跟著 `COIN_POOL` 動態推導：每幣 12 題 multi-source + 12 題
    hypothesis，每個幣別配對各 12 題 comparison（不寫死幣數，加減幣自動跟著算）。
    """
    cases = all_cases()
    per_coin_prompts = len(_MULTI_SOURCE)
    per_pair_prompts = len(_COMPARISONS)
    expected_multi = per_coin_prompts * len(COIN_POOL)
    expected_hypothesis = per_coin_prompts * len(COIN_POOL)
    expected_comparison = per_pair_prompts * len(list(combinations(COIN_POOL, 2)))
    expected_total = expected_multi + expected_hypothesis + expected_comparison

    assert len(cases) == expected_total
    assert len({case.id for case in cases}) == expected_total
    assert {case.question_type for case in cases} == set(QuestionType)
    assert sum(case.question_type == QuestionType.MULTI_SOURCE for case in cases) == expected_multi
    assert sum(case.question_type == QuestionType.HYPOTHESIS for case in cases) == expected_hypothesis
    assert sum(case.question_type == QuestionType.COMPARISON for case in cases) == expected_comparison


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
